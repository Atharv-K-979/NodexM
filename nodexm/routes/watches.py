from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlmodel import Session, select

from ..auth import get_current_user
from ..cadence import CADENCES, cron_for, next_run_time
from ..db import get_session
from ..jobs import RunRefused, create_run
from ..models import ChangeEvent, Claim, LedgerClaim, Run, Thread, User, Watch, utcnow
from ..serializers import alert_quality, change_out, run_detail, watch_out
from .threads import delete_runs

router = APIRouter(prefix="/api/watches", tags=["watches"])
Cadence = Literal["daily", "workdays", "weekly", "biweekly", "monthly", "manual"]


class WatchIn(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    cadence: Cadence = "weekly"
    mode: Literal["auto", "quick", "deep"] = "auto"
    email_alerts: bool = True
    thread_id: Optional[str] = None


class FeedbackIn(BaseModel):
    verdict: Optional[Literal["real", "noise"]] = Field(default=None, description="null clears the verdict")


class WatchPatch(BaseModel):
    topic: Optional[str] = Field(default=None, min_length=3, max_length=500)
    cadence: Optional[Cadence] = None
    mode: Optional[Literal["auto", "quick", "deep"]] = None
    email_alerts: Optional[bool] = None
    active: Optional[bool] = None


def own_watch(session: Session, watch_id: int, user: User) -> Watch:
    watch = session.get(Watch, watch_id)
    if not watch or watch.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Watch not found.")
    return watch


@router.get("")
def list_watches(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(select(Watch).where(Watch.user_id == user.id).order_by(Watch.created_at.desc())).all()
    return [watch_out(session, w) for w in rows]


@router.get("/changes")
def recent_changes(limit: int = 30, user: User = Depends(get_current_user),
                   session: Session = Depends(get_session)) -> dict:
    """Confirmed changes across all of the user's Watches, newest first, plus alert precision."""
    watches = {w.id: w for w in session.exec(select(Watch).where(Watch.user_id == user.id)).all()}
    events = session.exec(select(ChangeEvent).where(ChangeEvent.watch_id.in_(list(watches) or [-1]))
                          .order_by(ChangeEvent.created_at.desc()).limit(min(max(limit, 1), 200))).all()
    return {"changes": [change_out(e, watches[e.watch_id]) for e in events],
            "quality": alert_quality(session, list(watches))}


@router.get("/cadences")
def cadences() -> list[dict]:
    return [{"value": k, "label": v[1]} for k, v in CADENCES.items()]


@router.post("", status_code=201)
def create_watch(body: WatchIn, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    if body.thread_id:
        thread = session.get(Thread, body.thread_id)
        if not thread or thread.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found.")
        if session.exec(select(Watch).where(Watch.thread_id == thread.id)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "This thread is already watched.")
    cron = cron_for(body.cadence)
    watch = Watch(user_id=user.id, thread_id=body.thread_id, topic=body.topic.strip(), profile={"mode": body.mode},
                  cadence=body.cadence, cron=cron, next_run_at=next_run_time(cron), email_alerts=body.email_alerts)
    session.add(watch)
    session.commit()
    session.refresh(watch)
    return watch_out(session, watch, detail=True)


@router.get("/{watch_id}")
def get_watch(watch_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return watch_out(session, own_watch(session, watch_id, user), detail=True)


@router.patch("/{watch_id}")
def update_watch(watch_id: int, body: WatchPatch, user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)) -> dict:
    watch = own_watch(session, watch_id, user)
    if body.topic is not None:
        watch.topic = body.topic.strip()
    if body.mode is not None:
        watch.profile = {**(watch.profile or {}), "mode": body.mode}
    if body.email_alerts is not None:
        watch.email_alerts = body.email_alerts
    if body.cadence is not None:
        watch.cadence, watch.cron = body.cadence, cron_for(body.cadence)
    if body.active is not None:
        watch.active = body.active
    watch.next_run_at = next_run_time(watch.cron) if watch.active else None
    session.add(watch)
    session.commit()
    return watch_out(session, watch, detail=True)


@router.post("/{watch_id}/changes/{change_id}/feedback")
def change_feedback(watch_id: int, change_id: int, body: FeedbackIn, user: User = Depends(get_current_user),
                    session: Session = Depends(get_session)) -> dict:
    """Mark an alert as a real change or as noise. This is how alert precision is measured."""
    watch = own_watch(session, watch_id, user)
    event = session.get(ChangeEvent, change_id)
    if not event or event.watch_id != watch.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Change not found.")
    event.feedback = body.verdict
    event.feedback_at = utcnow() if body.verdict else None
    session.add(event)
    session.commit()
    return {"change": change_out(event), "quality": alert_quality(session, [watch.id])}


@router.post("/{watch_id}/run", status_code=201)
def run_watch(watch_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    watch = own_watch(session, watch_id, user)
    thread = session.get(Thread, watch.thread_id) if watch.thread_id else None
    try:
        run = create_run(session, user, question=watch.topic, mode=(watch.profile or {}).get("mode", "auto"),
                         thread=thread, watch=watch)
    except RunRefused as e:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(e))
    if not watch.thread_id:
        watch.thread_id = run.thread_id
        session.add(watch)
        session.commit()
    return run_detail(session, run)


@router.delete("/{watch_id}", status_code=204)
def delete_watch(watch_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> None:
    watch = own_watch(session, watch_id, user)
    runs = session.exec(select(Run).where(Run.watch_id == watch.id)).all()
    if any(r.status in ("queued", "running") for r in runs):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the current run to finish before deleting.")
    session.exec(delete(ChangeEvent).where(ChangeEvent.watch_id == watch.id))
    for r in runs:
        if r.thread_id:  # keep answers that are visible in a thread
            r.watch_id = None
            session.add(r)
    session.flush()
    for c in session.exec(select(Claim).where(Claim.run_id.in_([r.id for r in runs]))).all():
        c.ledger_claim_id = None
        session.add(c)
    session.exec(delete(LedgerClaim).where(LedgerClaim.watch_id == watch.id))
    delete_runs(session, [r.id for r in runs if not r.thread_id])
    session.delete(watch)
    session.commit()
