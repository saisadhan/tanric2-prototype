from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

# --- password hashing ---------------------------------------------------------------
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return 'salt_hex$hash_hex' using PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    # constant-time comparison to avoid timing attacks
    return hmac.compare_digest(candidate.hex(), hash_hex)


# --- user store (in-memory; seeded for the prototype) -------------------------------
@dataclass
class User:
    user_id: str          # this is the `owner` used for storage isolation
    display_name: str
    password_hash: str


# Two seeded demo accounts. Passwords are hashed at import time, never stored raw.
_USERS: dict[str, User] = {}


def _seed_users() -> None:
    from .. import config
    seed = [
        (config.SEED_USER_PRIMARY_ID, config.SEED_USER_PRIMARY_NAME,
         config.SEED_USER_PRIMARY_PASSWORD),
        (config.SEED_USER_SECONDARY_ID, config.SEED_USER_SECONDARY_NAME,
         config.SEED_USER_SECONDARY_PASSWORD),
    ]
    for uid, name, pw in seed:
        _USERS[uid] = User(user_id=uid, display_name=name, password_hash=hash_password(pw))


_seed_users()


# --- sessions -----------------------------------------------------------------------
from .. import config as _config
_SESSION_TTL_SECONDS = _config.SESSION_TTL_SECONDS  # default 8h, override via .env
# token -> (user_id, expires_at)
_SESSIONS: dict[str, tuple[str, float]] = {}


def login(username: str, password: str):
    """Verify credentials; on success mint and return (session_token, user)."""
    user = _USERS.get(username)
    if user is None:
        # Run a dummy verify so a missing user takes ~the same time as a wrong
        # password (avoids username-enumeration via timing).
        verify_password(password, hash_password("dummy"))
        return None
    if not verify_password(password, user.password_hash):
        return None
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = (user.user_id, time.time() + _SESSION_TTL_SECONDS)
    return token, user


def logout(token: str | None) -> None:
    if token:
        _SESSIONS.pop(token, None)


def resolve_owner(session_token: str | None) -> str | None:
    """Map a session token to a user id (the storage `owner`).

    None / invalid / expired -> None (anonymous: public datasets only).
    This keeps the exact same contract the rest of the app already depends on.
    """
    if not session_token:
        return None
    entry = _SESSIONS.get(session_token)
    if entry is None:
        return None
    user_id, expires_at = entry
    if time.time() > expires_at:
        _SESSIONS.pop(session_token, None)
        return None
    return user_id


def get_user(user_id: str):
    return _USERS.get(user_id)
