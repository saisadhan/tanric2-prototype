"""
Central configuration for TANRIC 2.0.

All environment-driven settings are read HERE, in one place, and the rest of the
codebase imports from this module instead of touching os.environ directly. This
means:
  - there is a single, documented list of every knob the system has;
  - a .env file (loaded automatically below) configures the whole app;
  - swapping local <-> cloud, or changing credentials, never requires code edits.

Precedence: real environment variables override .env file values, which override
the built-in defaults. So in production you set real env vars (or container/secret
manager values) and the .env file is just a convenience for local development.

Never commit a real .env file with secrets — commit .env.example instead.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (the directory two levels above this file).
# override=False means real environment variables win over the .env file.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None and raw.strip() else default


# --- storage -----------------------------------------------------------------------
STORAGE: str = (_get("TANRIC_STORAGE", "local") or "local").lower()
DATA_ROOT: str = _get("TANRIC_DATA_ROOT", "data") or "data"
S3_BUCKET: str | None = _get("TANRIC_S3_BUCKET")
S3_ENDPOINT: str | None = _get("TANRIC_S3_ENDPOINT")
S3_REGION: str | None = _get("TANRIC_S3_REGION")

# --- auth / sessions ---------------------------------------------------------------
SESSION_TTL_SECONDS: int = _get_int("TANRIC_SESSION_TTL_SECONDS", 60 * 60 * 8)

# Seed account passwords. In production these come from a real user store / secret
# manager; here they default to demo values but can be overridden via .env so no
# real password need ever live in source.
SEED_USER_PRIMARY_ID: str = _get("TANRIC_SEED_USER_PRIMARY_ID", "saisadhan") or "saisadhan"
SEED_USER_PRIMARY_NAME: str = _get("TANRIC_SEED_USER_PRIMARY_NAME", "Sai Sadhan Saravanan") or "Sai Sadhan Saravanan"
SEED_USER_PRIMARY_PASSWORD: str = _get("TANRIC_SEED_USER_PRIMARY_PASSWORD", "sai-password") or "sai-password"
SEED_USER_SECONDARY_ID: str = _get("TANRIC_SEED_USER_SECONDARY_ID", "bob") or "bob"
SEED_USER_SECONDARY_NAME: str = _get("TANRIC_SEED_USER_SECONDARY_NAME", "Bob (Lab B)") or "Bob (Lab B)"
SEED_USER_SECONDARY_PASSWORD: str = _get("TANRIC_SEED_USER_SECONDARY_PASSWORD", "bob-password") or "bob-password"

# --- aws credentials (used by boto3 for the object backend) ------------------------
# boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the environment itself;
# we surface them here only so they can also live in .env for local MinIO demos.
AWS_ACCESS_KEY_ID: str | None = _get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY: str | None = _get("AWS_SECRET_ACCESS_KEY")
