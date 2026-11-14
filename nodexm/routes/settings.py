from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import settings
from ..crypto import encrypt
from ..db import get_session
from ..models import Run, User
from ..notify import email_enabled
from ..quota import allowance
from ..search import search_available
from ..serializers import user_out

router = APIRouter(prefix="/api", tags=["settings"])


class PreferencesIn(BaseModel):
    default_mode: Literal["auto", "quick", "deep"] = "auto"
    region: Literal["Global", "EU", "US", "UK", "APAC"] = "Global"
    depth: Literal["summary", "analyst", "technical"] = "analyst"


class SettingsIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    preferences: Optional[PreferencesIn] = None


class KeysIn(BaseModel):
    """Send a key to save it, an empty string to remove it, or omit it to leave it unchanged."""

    gemini_api_key: Optional[str] = Field(default=None, max_length=300)
    tavily_api_key: Optional[str] = Field(default=None, max_length=300)


@router.get("/config")
def public_config() -> dict:
    return {
        "free_quick_runs": settings.free_quick_runs,
        "free_deep_runs": settings.free_deep_runs,
        "server_llm": bool(settings.gemini_api_key),
        "web_search": search_available(settings.tavily_api_key),
        "email_alerts": email_enabled(),
        "google_client_id": settings.google_client_id or None,
    }


@router.get("/settings")
def get_settings(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return {"user": user_out(user), "allowance": allowance(session, user).as_dict()}


@router.put("/settings")
def update_settings(body: SettingsIn, user: User = Depends(get_current_user),
                    session: Session = Depends(get_session)) -> dict:
    if body.name is not None:
        user.name = body.name.strip()
    if body.preferences is not None:
        user.preferences = body.preferences.model_dump()
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"user": user_out(user), "allowance": allowance(session, user).as_dict()}


@router.put("/settings/keys")
def update_keys(body: KeysIn, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    if body.gemini_api_key is not None:
        user.gemini_key_enc = encrypt(body.gemini_api_key.strip()) if body.gemini_api_key.strip() else None
    if body.tavily_api_key is not None:
        user.tavily_key_enc = encrypt(body.tavily_api_key.strip()) if body.tavily_api_key.strip() else None
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"user": user_out(user), "allowance": allowance(session, user).as_dict()}


@router.get("/usage")
def usage(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    rows = session.exec(select(Run.status, func.count(), func.sum(Run.cost_usd))
                        .where(Run.user_id == user.id).group_by(Run.status)).all()
    return {
        "allowance": allowance(session, user).as_dict(),
        "runs_by_status": {s: n for s, n, _ in rows},
        "total_cost_usd": round(sum(c or 0 for _, _, c in rows), 4),
    }
