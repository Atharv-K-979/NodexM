"""Encrypts users' own API keys at rest. The key is derived from the JWT secret
unless NODEXM_ENCRYPTION_KEY is set."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    raw = os.getenv("NODEXM_ENCRYPTION_KEY") or settings.jwt_secret
    key = base64.urlsafe_b64encode(hashlib.sha256(("nodexm-keys:" + raw).encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "…" + value[-2:]
