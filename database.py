from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

DB_DEFAULT = "sqlite:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "nodexm.db"
)
DATABASE_URL = os.environ.get("DATABASE_URL", DB_DEFAULT)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)


def init_db() -> None:
    """Create tables if missing. Safe to call on every startup."""
    SQLModel.metadata.create_all(engine)


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Transaction-scoped session for non-FastAPI callers (CLI, scheduler)."""
    with Session(engine, autoflush=True) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency generator."""
    with Session(engine, autoflush=True) as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
