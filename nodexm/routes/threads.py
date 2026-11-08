from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import Session, select

from ..auth import get_current_user
from ..db import get_session
from ..jobs import RunRefused, create_run
from ..models import ChangeEvent, Claim, Draft, Evidence, Run, Source, Thread, User, Watch
from ..serializers import run_detail, thread_out

router = APIRouter(prefix="/api/threads", tags=["threads"])


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    mode: Literal["auto", "quick", "deep"] = "auto"


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)


def own_thread(session: Session, thread_id: str, user: User) -> Thread:
    thread = session.get(Thread, thread_id)
    if not thread or thread.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
    return thread


def delete_runs(session: Session, run_ids: list[str]) -> None:
    if not run_ids:
        return
    for model in (Evidence, ChangeEvent, Draft, Source):
        session.exec(delete(model).where(model.run_id.in_(run_ids)))
    session.exec(delete(Claim).where(Claim.run_id.in_(run_ids)))
    session.exec(delete(Run).where(Run.id.in_(run_ids)))


@router.get("")
def list_threads(q: str = "", limit: int = 100, user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)) -> list[dict]:
    stmt = select(Thread).where(Thread.user_id == user.id)
    if q.strip():
        stmt = stmt.where(Thread.title.ilike(f"%{q.strip()}%"))
    threads = session.exec(stmt.order_by(Thread.updated_at.desc()).limit(min(limit, 500))).all()
    return [thread_out(session, t) for t in threads]


@router.post("", status_code=201)
def ask(body: AskIn, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    try:
        run = create_run(session, user, question=body.question, mode=body.mode)
    except RunRefused as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))
    return {"thread_id": run.thread_id, "run_id": run.id}


@router.get("/{thread_id}")
def get_thread(thread_id: str, user: User = Depends(get_current_user),
               session: Session = Depends(get_session)) -> dict:
    return thread_out(session, own_thread(session, thread_id, user), detail=True)


@router.post("/{thread_id}/runs", status_code=201)
def follow_up(thread_id: str, body: AskIn, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)) -> dict:
    thread = own_thread(session, thread_id, user)
    try:
        run = create_run(session, user, question=body.question, mode=body.mode, thread=thread)
    except RunRefused as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))
    return run_detail(session, run)


@router.patch("/{thread_id}")
def rename(thread_id: str, body: RenameIn, user: User = Depends(get_current_user),
           session: Session = Depends(get_session)) -> dict:
    thread = own_thread(session, thread_id, user)
    thread.title = body.title.strip()
    session.add(thread)
    session.commit()
    return thread_out(session, thread)


@router.delete("/{thread_id}", status_code=204)
def delete_thread(thread_id: str, user: User = Depends(get_current_user),
                  session: Session = Depends(get_session)) -> None:
    thread = own_thread(session, thread_id, user)
    runs = session.exec(select(Run).where(Run.thread_id == thread.id)).all()
    if any(r.status in ("queued", "running") for r in runs):
        raise HTTPException(status.HTTP_409_CONFLICT, "Stop the running answer before deleting this thread.")
    for w in session.exec(select(Watch).where(Watch.thread_id == thread.id)).all():
        w.thread_id = None
        session.add(w)
    delete_runs(session, [r.id for r in runs if r.watch_id is None])
    for r in runs:  # keep watch history, just detach it from the thread
        if r.watch_id is not None:
            r.thread_id = None
            session.add(r)
    session.flush()
    session.delete(thread)
    session.commit()
