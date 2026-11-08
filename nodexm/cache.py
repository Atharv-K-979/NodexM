"""Small database-backed cache for search results and fetched pages."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from sqlmodel import Session, delete

from .config import settings
from .db import engine
from .models import CacheEntry, utcnow

log = logging.getLogger("nodexm.cache")


def make_key(namespace: str, *parts) -> str:
    raw = json.dumps([namespace, *parts], sort_keys=True, default=str)
    return f"{namespace}:{hashlib.sha256(raw.encode()).hexdigest()[:48]}"


def get(key: str):
    try:
        with Session(engine) as s:
            row = s.get(CacheEntry, key)
            if row and row.expires_at > utcnow():
                return row.value.get("v")
    except Exception as e:  # cache must never break a run
        log.debug("cache get failed: %s", e)
    return None


def put(key: str, value, ttl_hours: int | None = None) -> None:
    ttl = settings.cache_ttl_hours if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return
    try:
        with Session(engine) as s:
            row = s.get(CacheEntry, key) or CacheEntry(key=key, value={}, expires_at=utcnow())
            row.value = {"v": value}
            row.expires_at = utcnow() + timedelta(hours=ttl)
            s.add(row)
            s.commit()
    except Exception as e:
        log.debug("cache put failed: %s", e)


def purge_expired() -> None:
    try:
        with Session(engine) as s:
            s.exec(delete(CacheEntry).where(CacheEntry.expires_at < utcnow()))
            s.commit()
    except Exception as e:
        log.debug("cache purge failed: %s", e)
