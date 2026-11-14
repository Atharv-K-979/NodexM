from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from ..auth import create_token, get_current_user, hash_password, verify_password
from ..db import get_session
from ..google_auth import verify_google_token
from ..models import User
from ..quota import allowance
from ..serializers import user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failed: dict[str, deque] = defaultdict(deque)
MAX_FAILED, WINDOW = 8, 15 * 60


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class GoogleIn(BaseModel):
    credential: str = Field(min_length=20, max_length=8192, description="ID token from the Google button")


def _session_payload(session: Session, user: User) -> dict:
    return {"token": create_token(user), "user": user_out(user), "allowance": allowance(session, user).as_dict()}


@router.post("/register", status_code=201)
def register(body: RegisterIn, session: Session = Depends(get_session)) -> dict:
    email = body.email.lower()
    if session.exec(select(User).where(User.email == email)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists. Sign in instead.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    return _session_payload(session, user)


@router.post("/login")
def login(body: LoginIn, session: Session = Depends(get_session)) -> dict:
    email = body.email.lower()
    attempts = _failed[email]
    now = time.monotonic()
    while attempts and now - attempts[0] > WINDOW:
        attempts.popleft()
    if len(attempts) >= MAX_FAILED:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")
    user = session.exec(select(User).where(User.email == email)).first()
    if user and not user.password_hash:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account uses Google sign-in. Use “Continue with Google”.")
    if not user or not verify_password(body.password, user.password_hash):
        attempts.append(now)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")
    attempts.clear()
    return _session_payload(session, user)


@router.get("/me")
def me(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return {"user": user_out(user), "allowance": allowance(session, user).as_dict()}


@router.post("/google")
def google_sign_in(body: GoogleIn, session: Session = Depends(get_session)) -> dict:
    """Sign in (or sign up) with a Google ID token.

    An existing account with the same verified email is linked to the Google account, so people
    who registered with a password can also use the Google button.
    """
    claims = verify_google_token(body.credential)
    sub, email = str(claims["sub"]), str(claims["email"]).lower()
    user = session.exec(select(User).where(User.google_sub == sub)).first()
    if user is None:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is not None and user.google_sub and user.google_sub != sub:
            raise HTTPException(status.HTTP_409_CONFLICT, "This email is linked to a different Google account.")
    if user is None:
        user = User(email=email, name=str(claims.get("name") or "")[:120], password_hash="")
    user.google_sub = sub
    if claims.get("picture"):
        user.avatar_url = str(claims["picture"])[:1024]
    if not user.name and claims.get("name"):
        user.name = str(claims["name"])[:120]
    session.add(user)
    session.commit()
    session.refresh(user)
    return _session_payload(session, user)
