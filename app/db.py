"""Слой базы данных herness (SQLite).

Схема покрывает: пользователей и сессии, базу НТД, загруженные документы,
проверки с найденными замечаниями, реестр моделей ИИ и журнал действий.

Все обращения идут через короткоживущие соединения — этого достаточно для
SQLite и исключает проблемы с многопоточностью uvicorn.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',   -- admin | user
    is_active     INTEGER NOT NULL DEFAULT 1,
    full_name     TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- База нормативно-технических документов.
CREATE TABLE IF NOT EXISTS ntd_documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL,            -- «СП 484.1311500.2020»
    title          TEXT NOT NULL DEFAULT '',
    revision       TEXT NOT NULL DEFAULT '', -- год/редакция
    status         TEXT NOT NULL DEFAULT 'unknown', -- active|superseded|unknown
    superseded_by  TEXT NOT NULL DEFAULT '',
    source_file    TEXT NOT NULL DEFAULT '',
    text_extracted INTEGER NOT NULL DEFAULT 0,      -- 0/1
    text_chars     INTEGER NOT NULL DEFAULT 0,
    pages          INTEGER NOT NULL DEFAULT 0,
    text_path      TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    checked_at     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(code)
);

-- Пункты НТД, по которым формируются ссылки в замечаниях.
CREATE TABLE IF NOT EXISTS ntd_clauses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES ntd_documents(id) ON DELETE CASCADE,
    number      TEXT NOT NULL,        -- «6.1.4», «п. 5.2»
    text        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clauses_doc ON ntd_clauses(document_id);
CREATE INDEX IF NOT EXISTS idx_clauses_num ON ntd_clauses(document_id, number);

-- Загруженные пользователем файлы (исходные данные проверки).
CREATE TABLE IF NOT EXISTS uploads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id      INTEGER REFERENCES checks(id) ON DELETE SET NULL,
    original_name TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    extension     TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'unknown', -- cable_journal|specification|plan|calculation|other|unknown
    parsed_ok     INTEGER NOT NULL DEFAULT 0,
    parse_error   TEXT NOT NULL DEFAULT '',
    parsed_meta   TEXT NOT NULL DEFAULT '{}',
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL
);

-- Проверки (запуски анализа комплекта документации).
CREATE TABLE IF NOT EXISTS checks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'local',
    system_kind  TEXT NOT NULL DEFAULT 'mixed',
    status       TEXT NOT NULL DEFAULT 'created', -- created|running|done|failed
    started_at   TEXT,
    finished_at  TEXT,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    summary      TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

-- Перечень проверок: что выполнялось, что нет и почему.
CREATE TABLE IF NOT EXISTS check_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id   INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
    code       TEXT NOT NULL,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL,   -- passed|failed|skipped|error
    reason     TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_check ON check_steps(check_id);

-- Замечания.
CREATE TABLE IF NOT EXISTS findings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id      INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
    severity      TEXT NOT NULL,          -- critical | noncritical | info
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    ntd_document  TEXT NOT NULL DEFAULT '',  -- код документа
    ntd_clause    TEXT NOT NULL DEFAULT '',  -- пункт
    location      TEXT NOT NULL DEFAULT '',  -- лист/файл/позиция
    recommendation TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'rule', -- rule | ai | manual
    confidence    TEXT NOT NULL DEFAULT '',      -- для замечаний от ИИ
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_check ON findings(check_id);

-- Реестр моделей ИИ: локальные (Ollama) и облачные (добавляются вручную).
CREATE TABLE IF NOT EXISTS ai_models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,         -- local | cloud
    provider     TEXT NOT NULL DEFAULT '', -- ollama | openai-compatible | anthropic | ...
    base_url     TEXT NOT NULL DEFAULT '',
    model_id     TEXT NOT NULL DEFAULT '',
    api_key      TEXT NOT NULL DEFAULT '',
    is_enabled   INTEGER NOT NULL DEFAULT 1,
    is_default   INTEGER NOT NULL DEFAULT 0,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(name)
);

-- Журнал действий пользователей.
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    login      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_uploads_check ON uploads(check_id);
CREATE INDEX IF NOT EXISTS idx_checks_created ON checks(created_at);
"""


def utcnow() -> str:
    """Отметка времени в ISO-8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """Открывает соединение с настроенными pragma."""
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Контекстный менеджер: коммит при успехе, откат при ошибке."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(admin_login: str, admin_password: str) -> None:
    """Создаёт схему и учётную запись администратора, если её нет."""
    from .security import hash_password  # локальный импорт: избегаем цикла

    with db() as conn:
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'").fetchone()
        if row["n"] == 0:
            conn.execute(
                """INSERT INTO users (login, password_hash, role, is_active, full_name, created_at)
                   VALUES (?, ?, 'admin', 1, ?, ?)""",
                (admin_login, hash_password(admin_password), "Администратор", utcnow()),
            )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def log_action(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    login: str,
    action: str,
    target: str = "",
    details: str = "",
) -> None:
    """Пишет запись в журнал действий."""
    conn.execute(
        """INSERT INTO audit_log (user_id, login, action, target, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, login, action, target, details, utcnow()),
    )


def db_file() -> Path:
    return config.DB_PATH
