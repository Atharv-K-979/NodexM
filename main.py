from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from auth import (
    create_token,
    get_current_user,
    hash_password,
    verify_password,
)
from database import engine, get_session, init_db
from models import (
    ClaimRow,
    Draft,
    OnboardingSession,
    Run,
    SourceRow,
    User,
    Watch,
)
from onboarding import (
    ONBOARDING_STEPS,
    cadence_for_humans,
    derive_cron,
    derive_profile,
    next_run_time,
)
from pipeline import regenerate_section, run_research_pipeline_stream

RUN_TRACE_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = defaultdict(list)
RUN_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------------------
# Lifespan & app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(engine) as s:
        demo_email = "demo@nodexm.local"
        demo = s.exec(select(User).where(User.email == demo_email)).first()
        if not demo:
            demo = User(email=demo_email, password_hash=hash_password("demo1234"))
            s.add(demo)
            s.commit()
            s.refresh(demo)
    yield
    RUN_TRACE_SUBSCRIBERS.clear()
    RUN_LOCKS.clear()


app = FastAPI(title="NodexM v2 API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class RunNowRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    user_profile: dict[str, Any] = Field(default_factory=dict)


class DraftUpdate(BaseModel):
    content_md: str


class RegenerateSectionRequest(BaseModel):
    section_name: str
    user_instruction: str = ""


class WatchCreate(BaseModel):
    topic: str
    profile: dict = Field(default_factory=dict)
    cron_schedule: Optional[str] = None
    cadence: Optional[str] = None
    active: bool = True


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OnboardingNextRequest(BaseModel):
    session_id: Optional[int] = None
    answer: Any = None


# ---------------------------------------------------------------------------
# Optional auth: fall back to demo user so Stage 2/3 frontend works without login
# ---------------------------------------------------------------------------
async def current_user_opt(
    authorization: Optional[str] = None,
    session: Session = Depends(get_session),
) -> User:
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return await get_current_user(authorization, session)  # type: ignore[call-arg]
        except HTTPException:
            pass
    demo = session.exec(
        select(User).where(User.email == "demo@nodexm.local")
    ).first()
    if not demo:
        demo = User(email="demo@nodexm.local", password_hash=hash_password("demo1234"))
        session.add(demo)
        session.commit()
        session.refresh(demo)
    return demo  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enqueue_trace(run_id: str, event: dict) -> None:
    for q in list(RUN_TRACE_SUBSCRIBERS.get(run_id, [])):
        try:
            q.put_nowait(event)
        except Exception:
            pass


def _write_trace_db(session: Session, run_id: str, entry: dict) -> None:
    run = session.get(Run, run_id)
    if not run:
        return
    trace = list(run.trace_json or [])
    trace.append(entry)
    run.trace_json = trace
    session.add(run)


def _save_run_outputs(
    session: Session,
    run_id: str,
    pipeline_done_data: dict,
    topic: str,
    user_profile: dict,
) -> None:
    run = session.get(Run, run_id)
    if not run:
        return
    data = pipeline_done_data or {}
    run.trace_json = list(data.get("trace") or run.trace_json or [])
    state_keys = ("sub_questions", "report", "verified", "critique", "revision_count", "sources")
    snap = {k: data.get(k) for k in state_keys}
    snap["topic"] = topic
    snap["user_profile"] = user_profile
    run.state_snapshot_json = snap
    session.add(run)

    sources = list(data.get("sources") or [])
    for src in sources:
        row = SourceRow(
            run_id=run_id,
            url=src.get("url", ""),
            title=src.get("title", ""),
            scraped_text=src.get("text", ""),
            snippet=src.get("snippet"),
            idx=src.get("idx"),
        )
        session.add(row)

    verified = data.get("verified") or {}
    for c in verified.get("agreed") or []:
        session.add(
            ClaimRow(
                run_id=run_id,
                text=c.get("claim", ""),
                status="agreed",
                supporting_source_ids=c.get("supporting_source_indices")
                or c.get("supporting_source_ids")
                or [],
                note=c.get("note"),
            )
        )
    for c in verified.get("disputed") or []:
        session.add(
            ClaimRow(
                run_id=run_id,
                text=c.get("claim", ""),
                status="disputed",
                supporting_source_ids=c.get("supporting_source_indices") or [],
                conflicting_source_ids=c.get("conflicting_source_indices")
                or c.get("conflicting_source_ids")
                or [],
                note=c.get("note"),
            )
        )
    for c in verified.get("single_source") or []:
        session.add(
            ClaimRow(
                run_id=run_id,
                text=c.get("claim", ""),
                status="single_source",
                source_index=c.get("source_index"),
                supporting_source_ids=(
                    [c["source_index"]] if c.get("source_index") is not None else []
                ),
                note=c.get("note"),
            )
        )

    formatter_output = data.get("formatter_output") or {}
    critique = data.get("critique") or {}
    existing = session.exec(select(Draft).where(Draft.run_id == run_id)).first()
    if existing is None:
        draft = Draft(
            run_id=run_id,
            content_md=formatter_output.get("content_md", data.get("report", "")),
            content_html=formatter_output.get("content_html"),
            disputed_callout_md=formatter_output.get("disputed_callout_md"),
            claims_summary_json=formatter_output.get("claims_summary"),
            verified_json=verified if verified else None,
            critic_score=critique.get("score"),
            version=1,
            status="pending_review",
            cached_state_json={
                "topic": topic,
                "user_profile": user_profile,
                "report": data.get("report"),
                "verified": verified,
                "sources": sources,
                "_sources_index": None,
                "_verified_json": json.dumps(verified or {}),
            },
        )
        session.add(draft)
    else:
        existing.content_md = formatter_output.get("content_md", data.get("report", existing.content_md))
        existing.content_html = formatter_output.get("content_html") or existing.content_html
        existing.disputed_callout_md = formatter_output.get("disputed_callout_md") or existing.disputed_callout_md
        existing.claims_summary_json = formatter_output.get("claims_summary") or existing.claims_summary_json
        existing.verified_json = verified if verified else existing.verified_json
        existing.critic_score = critique.get("score") if critique.get("score") is not None else existing.critic_score
        existing.status = "pending_review"
        existing.updated_at = datetime.utcnow()
        existing.cached_state_json = {
            "topic": topic,
            "user_profile": user_profile,
            "report": data.get("report"),
            "verified": verified,
            "sources": sources,
            "_sources_index": None,
            "_verified_json": json.dumps(verified or {}),
        }
        session.add(existing)


async def _execute_pipeline(
    run_id: str,
    topic: str,
    user_profile: dict,
    watch_id: Optional[int] = None,
) -> None:
    lock = RUN_LOCKS[run_id]
    async with lock:
        with Session(engine, autoflush=True) as session:
            run = session.get(Run, run_id)
            if run is None:
                run = Run(
                    id=run_id,
                    watch_id=watch_id,
                    status="running",
                    started_at=datetime.utcnow(),
                    topic_cache=topic,
                    profile_cache_json=user_profile,
                    trace_json=[],
                )
                session.add(run)
            else:
                run.status = "running"
                run.started_at = datetime.utcnow()
                run.topic_cache = topic or run.topic_cache
                run.profile_cache_json = user_profile or run.profile_cache_json
                session.add(run)
            session.commit()

        try:
            async for evt in run_research_pipeline_stream(topic, user_profile):
                _enqueue_trace(run_id, evt)
                if evt.get("type") == "trace":
                    with Session(engine, autoflush=True) as session:
                        _write_trace_db(session, run_id, evt["data"])
                        session.commit()
                elif evt.get("type") == "done":
                    with Session(engine, autoflush=True) as session:
                        _save_run_outputs(session, run_id, evt.get("data") or {}, topic, user_profile)
                        run2 = session.get(Run, run_id)
                        if run2:
                            run2.status = "completed"
                            run2.finished_at = datetime.utcnow()
                            session.add(run2)
                        session.commit()
        except Exception as e:
            with Session(engine, autoflush=True) as session:
                run3 = session.get(Run, run_id)
                if run3:
                    run3.status = "failed"
                    run3.error = str(e)[:1024]
                    run3.finished_at = datetime.utcnow()
                    session.add(run3)
                session.commit()
            _enqueue_trace(run_id, {"type": "error", "data": {"message": str(e)}})
        finally:
            sentinel = {"type": "__done__", "data": {}}
            for q in list(RUN_TRACE_SUBSCRIBERS.get(run_id, [])):
                try:
                    q.put_nowait(sentinel)
                except Exception:
                    pass
            RUN_TRACE_SUBSCRIBERS.pop(run_id, None)


def _ensure_run_owned(session: Session, run_id: str, user_id: int) -> Run:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    watch_id = run.watch_id
    if watch_id is not None:
        watch = session.get(Watch, watch_id)
        if watch and watch.user_id != user_id:
            raise HTTPException(403, "Run does not belong to user")
    return run


# ---------------------------------------------------------------------------
# Auth (public)
# ---------------------------------------------------------------------------
@app.post("/api/auth/register")
def register(req: RegisterRequest, session: Session = Depends(get_session)) -> dict:
    existing = session.exec(select(User).where(User.email == req.email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=req.email, password_hash=hash_password(req.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_token(user.id, user.email)
    return {"user": {"id": user.id, "email": user.email}, "token": token}


@app.post("/api/auth/login")
def login(req: LoginRequest, session: Session = Depends(get_session)) -> dict:
    user = session.exec(select(User).where(User.email == req.email)).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = create_token(user.id, user.email)
    return {"user": {"id": user.id, "email": user.email}, "token": token}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user_opt)) -> dict:
    return {"id": user.id, "email": user.email, "is_demo": user.email == "demo@nodexm.local"}


# ---------------------------------------------------------------------------
# Onboarding chat (7 steps; one question per turn)
# ---------------------------------------------------------------------------
@app.get("/api/onboarding/schema")
def onboarding_schema() -> dict:
    return {"steps": ONBOARDING_STEPS}


@app.post("/api/onboarding/next")
def onboarding_next(
    req: OnboardingNextRequest,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    if req.session_id:
        ob = session.get(OnboardingSession, req.session_id)
        if not ob or ob.user_id != user.id:
            raise HTTPException(404, "Session not found")
    else:
        ob = OnboardingSession(user_id=user.id, step=0, answers_json={}, status="active")
        session.add(ob)
        session.commit()
        session.refresh(ob)

    if ob.status == "complete":
        return {
            "session_id": ob.id,
            "status": "complete",
            "watch_id": ob.watch_id,
            "answers": ob.answers_json,
        }

    if ob.step > 0 and req.answer is not None:
        prev_step = ONBOARDING_STEPS[ob.step - 1]
        answers = dict(ob.answers_json or {})
        answers[prev_step["name"]] = req.answer
        ob.answers_json = answers

    if ob.step >= len(ONBOARDING_STEPS):
        ob.status = "complete"
        watch = _create_watch_from_onboarding(session, user, ob)
        ob.watch_id = watch.id
        ob.updated_at = datetime.utcnow()
        session.add(ob)
        session.commit()
        return {
            "session_id": ob.id,
            "status": "complete",
            "watch_id": ob.watch_id,
            "answers": ob.answers_json,
        }

    nxt = ONBOARDING_STEPS[ob.step]
    ob.step += 1
    ob.updated_at = datetime.utcnow()
    session.add(ob)
    session.commit()
    return {
        "session_id": ob.id,
        "step": ob.step,
        "question": nxt,
        "answers_so_far": ob.answers_json,
    }


def _create_watch_from_onboarding(session: Session, user: User, ob: OnboardingSession) -> Watch:
    a = dict(ob.answers_json or {})
    topic = str(a.get("topic") or f"Watch for {user.email}")
    cadence = a.get("cadence")
    cron = derive_cron(cadence) if isinstance(cadence, str) else None
    profile = derive_profile(a)
    watch = Watch(
        user_id=user.id,
        topic=topic,
        profile_json=profile,
        cron_schedule=cron,
        next_run_at=next_run_time(cron),
        active=True,
    )
    session.add(watch)
    session.commit()
    session.refresh(watch)
    return watch


@app.get("/api/onboarding/sessions")
def onboarding_list(
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> list[dict]:
    rows = session.exec(
        select(OnboardingSession)
        .where(OnboardingSession.user_id == user.id)
        .order_by(OnboardingSession.id.desc())
    ).all()
    return [
        {
            "id": s.id,
            "step": s.step,
            "status": s.status,
            "watch_id": s.watch_id,
            "answers": s.answers_json,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }
        for s in rows
    ]


# ---------------------------------------------------------------------------
# Health (public)
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health(session: Session = Depends(get_session)) -> dict:
    try:
        run_count = session.exec(select(Run)).all().__len__()
        draft_count = session.exec(select(Draft)).all().__len__()
        watch_count = session.exec(select(Watch)).all().__len__()
    except Exception:
        run_count = draft_count = watch_count = 0
    return {
        "status": "ok",
        "version": "0.3.0",
        "runs": run_count,
        "drafts": draft_count,
        "watches": watch_count,
        "db": engine.url.database,
    }


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@app.post("/api/runs", status_code=202)
async def create_run(
    req: RunNowRequest,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    run_id = uuid.uuid4().hex[:16]
    run = Run(
        id=run_id,
        watch_id=None,
        status="queued",
        topic_cache=req.topic,
        profile_cache_json=req.user_profile,
        trace_json=[],
    )
    session.add(run)
    session.commit()
    asyncio.create_task(_execute_pipeline(run_id, req.topic, req.user_profile))
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/runs/{run_id}")
def get_run(
    run_id: str,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    run = _ensure_run_owned(session, run_id, user.id)
    return {
        "id": run.id,
        "watch_id": run.watch_id,
        "topic": run.topic_cache,
        "user_profile": run.profile_cache_json,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trace_count": len(run.trace_json or []),
        "error": run.error,
    }


@app.get("/api/runs")
def list_runs(
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> list[dict]:
    own_watch_ids = [
        w.id for w in session.exec(select(Watch).where(Watch.user_id == user.id)).all()
    ]
    rows = session.exec(
        select(Run).order_by(Run.created_at.desc()).limit(50)
    ).all()
    out = []
    for r in rows:
        if r.watch_id is None or r.watch_id in own_watch_ids:
            out.append(
                {
                    "id": r.id,
                    "watch_id": r.watch_id,
                    "topic": r.topic_cache,
                    "status": r.status,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
            )
    return out


@app.get("/api/runs/{run_id}/trace")
async def stream_trace(
    run_id: str,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    run = _ensure_run_owned(session, run_id, user.id)

    queue: asyncio.Queue = asyncio.Queue()
    RUN_TRACE_SUBSCRIBERS.setdefault(run_id, []).append(queue)

    snapshot: list = list(run.trace_json or [])
    done_statuses = ("completed", "failed")
    initial_done = run.status in done_statuses

    async def event_stream():
        for entry in snapshot:
            yield f"data: {json.dumps({'type': 'trace', 'data': entry})}\n\n"
            await asyncio.sleep(0)
        if initial_done:
            yield f"data: {json.dumps({'type': '__done__', 'data': {}})}\n\n"
            return
        try:
            while True:
                evt = await asyncio.wait_for(queue.get(), timeout=90.0)
                if evt.get("type") == "__done__":
                    yield f"data: {json.dumps({'type': '__done__', 'data': {}})}\n\n"
                    return
                yield f"data: {json.dumps(evt)}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'timeout', 'data': {}})}\n\n"
        finally:
            subs = RUN_TRACE_SUBSCRIBERS.get(run_id)
            if subs and queue in subs:
                subs.remove(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
@app.get("/api/drafts/{run_id}")
def get_draft(
    run_id: str,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    _ensure_run_owned(session, run_id, user.id)
    draft = session.exec(select(Draft).where(Draft.run_id == run_id)).first()
    run = session.get(Run, run_id)
    if not draft:
        if run and run.status in ("queued", "running"):
            return {"run_id": run_id, "status": "pending_pipeline"}
        raise HTTPException(404, "Draft not found")
    verified = draft.verified_json or {}
    counts = draft.claims_summary_json or {}
    return {
        "run_id": draft.run_id,
        "content_md": draft.content_md,
        "content_html": draft.content_html,
        "disputed_callout_md": draft.disputed_callout_md,
        "claims_summary": counts,
        "verified": verified,
        "critique_score": draft.critic_score,
        "version": draft.version,
        "status": draft.status,
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
        "approved_at": draft.approved_at.isoformat() if draft.approved_at else None,
    }


@app.patch("/api/drafts/{run_id}")
def update_draft(
    run_id: str,
    req: DraftUpdate,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    _ensure_run_owned(session, run_id, user.id)
    draft = session.exec(select(Draft).where(Draft.run_id == run_id)).first()
    if not draft:
        raise HTTPException(404, "Draft not found")
    draft.content_md = req.content_md
    draft.updated_at = datetime.utcnow()
    if draft.status == "approved":
        draft.version += 1
        draft.status = "pending_review"
        draft.approved_at = None
    session.add(draft)
    session.commit()
    return {"run_id": run_id, "version": draft.version, "status": draft.status}


@app.post("/api/drafts/{run_id}/regenerate-section")
def regenerate_section_endpoint(
    run_id: str,
    req: RegenerateSectionRequest,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    """Re-run writer for ONE section using cached verified context — no search/scrape/verify"""
    _ensure_run_owned(session, run_id, user.id)
    draft = session.exec(select(Draft).where(Draft.run_id == run_id)).first()
    if not draft:
        raise HTTPException(404, "Draft not found")
    cached = dict(draft.cached_state_json or {})
    if not cached.get("sources"):
        raise HTTPException(
            409,
            "No cached source data available; section regen requires a completed pipeline run.",
        )
    rewritten_md = regenerate_section(
        cached_state=cached,
        section_name=req.section_name,
        user_instruction=req.user_instruction,
    )
    draft.updated_at = datetime.utcnow()
    session.add(draft)
    session.commit()
    return {
        "run_id": run_id,
        "section_name": req.section_name,
        "rewritten_markdown": rewritten_md,
        "note": "Reused cached verified context; did not re-run search/scrape/verify.",
    }


@app.post("/api/drafts/{run_id}/approve")
def approve_draft(
    run_id: str,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    _ensure_run_owned(session, run_id, user.id)
    draft = session.exec(select(Draft).where(Draft.run_id == run_id)).first()
    if not draft:
        raise HTTPException(404, "Draft not found")
    now = datetime.utcnow()
    draft.status = "approved"
    draft.approved_at = now
    draft.updated_at = now
    draft.delivery_status = "delivery_queued"
    session.add(draft)
    session.commit()
    return {
        "run_id": run_id,
        "status": "approved",
        "approved_at": now.isoformat(),
        "version": draft.version,
        "delivery_status": "delivery_queued (Stage 7 adds Resend/SendGrid)",
    }


# ---------------------------------------------------------------------------
# Watches
# ---------------------------------------------------------------------------
@app.post("/api/watches")
def create_watch(
    watch: WatchCreate,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    cron = watch.cron_schedule or derive_cron(watch.cadence)
    w = Watch(
        user_id=user.id,
        topic=watch.topic,
        profile_json=watch.profile,
        cron_schedule=cron,
        next_run_at=next_run_time(cron),
        active=watch.active,
    )
    session.add(w)
    session.commit()
    session.refresh(w)
    return {
        "id": w.id,
        "topic": w.topic,
        "profile": w.profile_json,
        "cron_schedule": w.cron_schedule,
        "cadence_human": cadence_for_humans(watch.cadence),
        "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
        "active": w.active,
        "created_at": w.created_at.isoformat(),
    }


@app.get("/api/watches")
def list_watches(
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> list[dict]:
    rows = session.exec(
        select(Watch).where(Watch.user_id == user.id).order_by(Watch.id.desc())
    ).all()
    return [
        {
            "id": w.id,
            "topic": w.topic,
            "profile": w.profile_json,
            "cron_schedule": w.cron_schedule,
            "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
            "active": w.active,
            "created_at": w.created_at.isoformat(),
        }
        for w in rows
    ]


@app.get("/api/watches/{watch_id}")
def get_watch(
    watch_id: int,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    w = session.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "Watch not found")
    return {
        "id": w.id,
        "topic": w.topic,
        "profile": w.profile_json,
        "cron_schedule": w.cron_schedule,
        "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
        "active": w.active,
        "created_at": w.created_at.isoformat(),
    }


@app.post("/api/watches/{watch_id}/run-now", status_code=202)
async def run_watch_now(
    watch_id: int,
    req: Optional[RunNowRequest] = None,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    w = session.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "Watch not found")
    topic = req.topic if req else w.topic
    profile = (req.user_profile if req else None) or dict(w.profile_json or {})
    run_id = uuid.uuid4().hex[:16]
    run = Run(
        id=run_id,
        watch_id=w.id,
        status="queued",
        topic_cache=topic,
        profile_cache_json=profile,
        trace_json=[],
    )
    session.add(run)
    session.commit()
    asyncio.create_task(_execute_pipeline(run_id, topic, profile, watch_id=w.id))
    return {"run_id": run_id, "watch_id": w.id, "status": "queued"}


@app.get("/api/watches/{watch_id}/history")
def watch_history(
    watch_id: int,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> list[dict]:
    w = session.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "Watch not found")
    rows = session.exec(
        select(Run)
        .where(Run.watch_id == w.id)
        .order_by(Run.created_at.desc())
    ).all()
    out = []
    for r in rows:
        d = session.exec(select(Draft).where(Draft.run_id == r.id)).first()
        out.append(
            {
                "run_id": r.id,
                "status": r.status,
                "topic": r.topic_cache,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "draft_status": d.status if d else None,
                "version": d.version if d else None,
                "approved_at": d.approved_at.isoformat() if d and d.approved_at else None,
                "critique_score": d.critic_score if d else None,
            }
        )
    return out


@app.get("/api/watches/{watch_id}/diff")
def watch_diff_versions(
    watch_id: int,
    a: str,
    b: str,
    user: User = Depends(current_user_opt),
    session: Session = Depends(get_session),
) -> dict:
    w = session.get(Watch, watch_id)
    if not w or w.user_id != user.id:
        raise HTTPException(404, "Watch not found")
    import difflib

    da = session.exec(select(Draft).where(Draft.run_id == a)).first()
    db = session.exec(select(Draft).where(Draft.run_id == b)).first()
    if not da or not db:
        raise HTTPException(404, "One or both run drafts not found")
    a_lines = da.content_md.splitlines(keepends=True)
    b_lines = db.content_md.splitlines(keepends=True)
    diff = list(difflib.unified_diff(a_lines, b_lines, fromfile=a, tofile=b))
    # simple text stats
    additions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removals = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {
        "watch_id": watch_id,
        "from_run_id": a,
        "to_run_id": b,
        "unified_diff": "".join(diff),
        "stats": {"additions": additions, "removals": removals},
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
