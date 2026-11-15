"""Authentication (ARCHITECTURE.md, A13).

  * No password configured: PIA only ever listens on loopback (the CLI refuses anything else) and needs no login.
  * A password configured (`pia set-password`, or the PIA_PASSWORD environment variable): every API call needs a
    session cookie, obtained by logging in. Sessions are random tokens stored hashed, HttpOnly, SameSite=Strict.
  * Devices (the Windows companion) use bearer tokens, checked in pia.notify.engine.

Standard library only: scrypt for the password, SHA-256 for tokens (they are already high-entropy), constant-time
comparisons throughout.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

from pia.store.db import get_setting, iso, set_setting

COOKIE = "pia_session"
SESSION_DAYS = 30
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def set_password(conn: sqlite3.Connection, password: str) -> None:
    if len(password) < 10:
        raise ValueError("choose a password of at least 10 characters")
    set_setting(conn, "auth_password", hash_password(password))
    conn.execute("DELETE FROM sessions")  # changing the password signs every browser out
    conn.commit()


def clear_password(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key = 'auth_password'")
    conn.execute("DELETE FROM sessions")
    conn.commit()


def password_configured(conn: sqlite3.Connection, environ=os.environ) -> bool:
    return bool(environ.get("PIA_PASSWORD") or get_setting(conn, "auth_password"))


def check_password(conn: sqlite3.Connection, password: str, environ=os.environ) -> bool:
    env_password = environ.get("PIA_PASSWORD")
    if env_password:
        return hmac.compare_digest(password.encode(), env_password.encode())
    stored = get_setting(conn, "auth_password")
    return bool(stored) and verify_password(password, stored)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
    conn.execute("INSERT INTO sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)", (_token_hash(token), iso(now), iso(now + timedelta(days=SESSION_DAYS))))
    conn.commit()
    return token


def session_valid(conn: sqlite3.Connection, token: str | None, now: datetime) -> bool:
    if not token:
        return False
    row = conn.execute("SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)).fetchone()
    return bool(row) and row["expires_at"] > iso(now)


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()


class LoginThrottle:
    """Slows password guessing: after MAX_FAILURES wrong passwords, logins are refused for a minute.
    Global rather than per address, because behind a proxy every request can share one address."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures = 0
        self._locked_until = 0.0

    def locked(self) -> bool:
        return self._clock() < self._locked_until

    def failed(self) -> None:
        self._failures += 1
        if self._failures >= MAX_FAILURES:
            self._locked_until = self._clock() + LOCKOUT_SECONDS
            self._failures = 0

    def succeeded(self) -> None:
        self._failures = 0
