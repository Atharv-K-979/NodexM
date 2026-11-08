"""Database tables.

A Thread is one conversation in the sidebar. Each question in it is a Run.
A Run reads Sources, extracts claims, and stores the verified claim clusters
as Claim rows, each backed by Evidence quotes. Watches re-run a topic on a
schedule and keep LedgerClaims across runs; ChangeEvents record what moved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _json(default=dict):
    return Field(default_factory=default, sa_column=Column(JSON, nullable=False))


def _text(default: str = ""):
    return Field(default=default, sa_column=Column(Text, nullable=False))


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    name: str = Field(default="", max_length=120)
    password_hash: str = Field(max_length=255)
    preferences: dict = _json()
    gemini_key_enc: Optional[str] = Field(default=None, max_length=1024)
    tavily_key_enc: Optional[str] = Field(default=None, max_length=1024)
    created_at: datetime = Field(default_factory=utcnow)


class Thread(SQLModel, table=True):
    __tablename__ = "threads"
    id: str = Field(primary_key=True, max_length=32)
    user_id: int = Field(foreign_key="users.id", index=True)
    title: str = Field(default="New thread", max_length=300)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class Watch(SQLModel, table=True):
    __tablename__ = "watches"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    thread_id: Optional[str] = Field(default=None, foreign_key="threads.id")
    topic: str = Field(max_length=500)
    profile: dict = _json()
    cadence: str = Field(default="weekly", max_length=32)
    cron: Optional[str] = Field(default=None, max_length=64)
    next_run_at: Optional[datetime] = Field(default=None, index=True)
    active: bool = Field(default=True)
    email_alerts: bool = Field(default=True)
    last_run_id: Optional[str] = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    __tablename__ = "runs"
    id: str = Field(primary_key=True, max_length=32)
    user_id: int = Field(foreign_key="users.id", index=True)
    thread_id: Optional[str] = Field(default=None, foreign_key="threads.id", index=True)
    watch_id: Optional[int] = Field(default=None, foreign_key="watches.id", index=True)
    question: str = Field(max_length=2000)
    standalone_question: Optional[str] = Field(default=None, max_length=2000)
    requested_mode: str = Field(default="auto", max_length=16)
    mode: Optional[str] = Field(default=None, max_length=16)
    max_mode: str = Field(default="deep", max_length=16)
    profile: dict = _json()
    status: str = Field(default="queued", max_length=16, index=True)
    error: Optional[str] = Field(default=None, max_length=2000)
    trace: list = _json(list)
    result: dict = _json()
    usage: dict = _json()
    cost_usd: float = Field(default=0.0)
    used_own_keys: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class Source(SQLModel, table=True):
    __tablename__ = "sources"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    idx: int
    url: str = Field(max_length=2048)
    title: str = Field(default="", max_length=600)
    domain: str = Field(default="", max_length=255)
    source_type: str = Field(default="web", max_length=32)
    searcher: str = Field(default="web", max_length=32)
    published_at: Optional[str] = Field(default=None, max_length=32)
    credibility: float = Field(default=0.5)
    credibility_reasons: list = _json(list)
    duplicate_of: Optional[int] = None
    snippet: str = _text()
    text: str = _text()
    sub_question: str = Field(default="", max_length=1000)


class Claim(SQLModel, table=True):
    """One verified claim cluster in a run."""

    __tablename__ = "claims"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    key: str = Field(max_length=16)
    text: str = _text()
    status: str = Field(max_length=16, index=True)  # agreed | disputed | single_source
    confidence: float = Field(default=0.0)
    note: str = _text()
    source_indices: list = _json(list)
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON, nullable=True))
    ledger_claim_id: Optional[int] = Field(default=None, foreign_key="ledger_claims.id")


class Evidence(SQLModel, table=True):
    __tablename__ = "evidence"
    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claims.id", index=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    source_idx: int
    statement: str = _text()
    quote: str = _text()
    char_start: int = -1
    char_end: int = -1
    stance: str = Field(default="supports", max_length=16)


class Draft(SQLModel, table=True):
    __tablename__ = "drafts"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", unique=True, index=True, max_length=32)
    content_md: str = _text()
    content_html: str = _text()
    version: int = 1
    status: str = Field(default="pending_review", max_length=16)
    critic_score: Optional[float] = None
    audit_score: Optional[float] = None
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LedgerClaim(SQLModel, table=True):
    """A claim tracked across every run of a Watch."""

    __tablename__ = "ledger_claims"
    id: Optional[int] = Field(default=None, primary_key=True)
    watch_id: int = Field(foreign_key="watches.id", index=True)
    text: str = _text()
    status: str = Field(max_length=16)  # agreed | disputed | single_source | stale
    confidence: float = 0.0
    embedding: Optional[list] = Field(default=None, sa_column=Column(JSON, nullable=True))
    first_seen_run: str = Field(max_length=32)
    last_seen_run: str = Field(max_length=32)
    misses: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChangeEvent(SQLModel, table=True):
    __tablename__ = "change_events"
    id: Optional[int] = Field(default=None, primary_key=True)
    watch_id: int = Field(foreign_key="watches.id", index=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    ledger_claim_id: int = Field(foreign_key="ledger_claims.id")
    kind: str = Field(max_length=24)  # new | status_changed | stale | returned
    from_status: Optional[str] = Field(default=None, max_length=16)
    to_status: Optional[str] = Field(default=None, max_length=16)
    claim_text: str = _text()
    detail: str = _text()
    created_at: datetime = Field(default_factory=utcnow)


class CacheEntry(SQLModel, table=True):
    __tablename__ = "cache_entries"
    key: str = Field(primary_key=True, max_length=128)
    value: dict = _json()
    expires_at: datetime = Field(index=True)
