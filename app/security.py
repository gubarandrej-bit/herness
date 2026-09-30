"""Хеширование паролей и управление сессиями."""
import secrets
from datetime import datetime, timezone
from passlib.hash import bcrypt
from . import config
from .db import db, utcnow


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.verify(password, password_hash)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    with db() as conn:
        conn.execute(
            """INSERT INTO sessions (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, utcnow(), utcnow() + f"+{config.SESSION_TTL_SECONDS}s"),
        )
    return token


def validate_session(token: str | None) -> dict | None:
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
        from datetime import timezone

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
        res = conn.execute(
            "DELETE FROM sessions WHERE expires_at < ?", (utcnow(),)
        )
        return res.rowcount