"""Хеширование паролей и управление сессиями.

Использует hashlib.pbkdf2_hmac (SHA-256) — без ограничения в 72 байта,
которое есть у bcrypt в некоторых версиях библиотеки.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from . import config
from .db import db, utcnow

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_LEN = 32


def _hash_raw(password: str) -> str:
    """Возвращает строку формата: pbkdf2_sha256:{iterations}:{salt}:{hash}"""
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_HASH_LEN)
    parts = [
        "pbkdf2_sha256",
        str(_PBKDF2_ITERATIONS),
        salt.hex(),
        dk.hex(),
    ]
    return ":".join(parts)


def hash_password(password: str) -> str:
    return _hash_raw(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, iterations_hex, salt_hex, expected_hex = password_hash.split(":")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_hex)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
        return dk == expected
    except (ValueError, AttributeError, IndexError):
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    with db() as conn:
        conn.execute(
            """INSERT INTO sessions (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, utcnow(), utcnow() + f"+{config.SESSION_TTL_SECONDS}s"),
        )
    return token


def validate_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT u.id, u.login, u.role, u.full_name, s.expires_at
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND u.is_active = 1""",
            (token,),
        ).fetchone()
        if not row:
            return None
        expires = datetime.fromisoformat(row["expires_at"])
        if expires < datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        return dict(row)


def delete_session(token: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_expired_sessions() -> int:
    with db() as conn:
        res = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))
        return res.rowcount