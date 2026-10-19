from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Callable

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from database import get_session
from models import User

JWT_SECRET = os.environ.get("NODEXM_JWT_SECRET") or secrets.token_hex(32)
JWT_ALG = "HS256"
JWT_TTL = timedelta(hours=72)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(datetime.utcnow().timestamp()),
        "exp": int((datetime.utcnow() + JWT_TTL).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


async def _get_auth_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization: Bearer <token> header",
        )
    token = authorization.split(" ", 1)[1].strip()
    claims = decode_token(token)
    user_id = int(claims["sub"])
    user = session.exec(select(User).where(User.id == user_id)).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


get_current_user: Callable[..., User] = _get_auth_user
