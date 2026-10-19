from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import (
    JSON,
    Column,
    Field,
    SQLModel,
)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    password_hash: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Watch(SQLModel, table=True):
    """A monitored topic with preferences, schedule, and history.

    Columns match brief §7: watches(id, user_id, topic, profile_json,
    cron_schedule, next_run_at, active, created_at)
    """

    __tablename__ = "watches"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    topic: str = Field(max_length=500)
    profile_json: dict = Field(sa_column=Column(JSON), default_factory=dict)
    cron_schedule: Optional[str] = Field(default=None, max_length=64)
    next_run_at: Optional[datetime] = Field(default=None)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Run(SQLModel, table=True):
    """A single pipeline execution tied to a Watch.

    Columns match brief §7: runs(id, watch_id, status, started_at, finished_at)
    """

    __tablename__ = "runs"
    id: str = Field(primary_key=True, max_length=32)
    watch_id: Optional[int] = Field(default=None, foreign_key="watches.id", index=True)
    status: str = Field(default="queued", max_length=32, index=True)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)
    trace_json: list = Field(sa_column=Column(JSON), default_factory=list)
    state_snapshot_json: Optional[dict] = Field(
        sa_column=Column(JSON), default_factory=lambda: None
    )
    error: Optional[str] = Field(default=None, max_length=1024)
    topic_cache: Optional[str] = Field(default=None, max_length=500)
    profile_cache_json: dict = Field(sa_column=Column(JSON), default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SourceRow(SQLModel, table=True):
    """Scraped source document tied to a run.

    Columns match brief §7: sources(id, run_id, url, title, scraped_text)
    """

    __tablename__ = "sources"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    url: str = Field(max_length=2048)
    title: str = Field(max_length=500)
    scraped_text: str = Field(default="")
    snippet: Optional[str] = Field(default=None)
    idx: Optional[int] = Field(default=None)


class ClaimRow(SQLModel, table=True):
    """Cross-source verified claim tied to a run.

    Columns match brief §7: claims(id, run_id, text, status, supporting_source_ids, note)
    """

    __tablename__ = "claims"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True, max_length=32)
    text: str
    status: str = Field(max_length=32, index=True)  # agreed / disputed / single_source
    supporting_source_ids: Optional[list] = Field(
        sa_column=Column(JSON), default_factory=list
    )
    conflicting_source_ids: Optional[list] = Field(
        sa_column=Column(JSON), default_factory=list
    )
    source_index: Optional[int] = Field(default=None)
    note: Optional[str] = Field(default=None, max_length=2048)


class Draft(SQLModel, table=True):
    """Editable draft output tied to a Run (one Draft per Run → 1:1 via run_id).

    Columns match brief §7: drafts(id, run_id, content_md, critic_score, version, approved_at)
    """

    __tablename__ = "drafts"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(unique=True, foreign_key="runs.id", index=True, max_length=32)
    content_md: str = Field(default="")
    content_html: Optional[str] = Field(default=None)
    disputed_callout_md: Optional[str] = Field(default=None)
    claims_summary_json: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    verified_json: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    critic_score: Optional[int] = Field(default=None)
    version: int = Field(default=1)
    status: str = Field(default="pending_pipeline", max_length=32, index=True)
    approved_at: Optional[datetime] = Field(default=None)
    delivery_status: Optional[str] = Field(default=None, max_length=64)
    cached_state_json: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OnboardingSession(SQLModel, table=True):
    """Tracks per-user onboarding chat state for Stage 5 7-step flow."""

    __tablename__ = "onboarding_sessions"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    step: int = Field(default=0)
    answers_json: dict = Field(sa_column=Column(JSON), default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    watch_id: Optional[int] = Field(default=None, foreign_key="watches.id")
    status: str = Field(default="active", max_length=32, index=True)
