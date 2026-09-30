"""Конфигурация herness.

Все пути и режимы берутся из переменных окружения, чтобы один и тот же
образ работал и в Docker, и локально при разработке.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- пути -------------------------------------------------------------

DATA_DIR = Path(os.environ.get("HERNESS_DATA_DIR", "./data")).resolve()
DB_PATH = Path(os.environ.get("HERNESS_DB", str(DATA_DIR / "db" / "herness.db"))).resolve()
UPLOADS_DIR = Path(os.environ.get("HERNESS_UPLOADS", str(DATA_DIR / "uploads"))).resolve()
REPORTS_DIR = Path(os.environ.get("HERNESS_REPORTS", str(DATA_DIR / "reports"))).resolve()

# Каталог с исходными НТД внутри репозитория (docs/ntd).
REPO_ROOT = Path(__file__).resolve().parent.parent
NTD_SOURCE_DIR = REPO_ROOT / "docs" / "ntd"

# --- режимы работы ----------------------------------------------------

# local  — только локальные модели (Ollama), внешние вызовы запрещены
# cloud  — только облачные модели, добавленные администратором
# hybrid — сначала локальная, при её отсутствии или ошибке — облачная
DEFAULT_MODE = os.environ.get("HERNESS_DEFAULT_MODE", "local")
VALID_MODES = ("local", "cloud", "hybrid")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "").strip()

# --- сессии и безопасность -------------------------------------------

# Секрет для подписи cookie. Если не задан — генерируется при первом
# запуске и сохраняется в каталоге данных (иначе сессии слетали бы при
# каждом перезапуске).
SECRET_FILE = DATA_DIR / "secret.key"

SESSION_COOKIE = "herness_session"
SESSION_TTL_SECONDS = int(os.environ.get("HERNESS_SESSION_TTL", str(12 * 3600)))

# --- параметры загрузки ----------------------------------------------

MAX_UPLOAD_BYTES = int(os.environ.get("HERNESS_MAX_UPLOAD_MB", "200")) * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".xls", ".xlsx", ".xlsm",
    ".doc", ".docx",
    ".pdf",
    ".dwg", ".dxf",
}

# Учётная запись администратора, создаваемая при инициализации БД.
DEFAULT_ADMIN_LOGIN = os.environ.get("HERNESS_ADMIN_LOGIN", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("HERNESS_ADMIN_PASSWORD", "admin123")


def ensure_dirs() -> None:
    """Создаёт рабочие каталоги. Вызывается при старте приложения."""
    for path in (DATA_DIR, DB_PATH.parent, UPLOADS_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_secret() -> str:
    """Возвращает секрет подписи сессий, создавая его при первом запуске."""
    env_secret = os.environ.get("HERNESS_SECRET")
    if env_secret:
        return env_secret
    ensure_dirs()
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    import secrets

    value = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(value, encoding="utf-8")
    try:
        SECRET_FILE.chmod(0o600)
    except OSError:
        # Windows не всегда поддерживает chmod — это не критично.
        pass
    return value
