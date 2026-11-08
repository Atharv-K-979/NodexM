"""Background execution.

Runs are queued in the database, so a restart does not lose them: runs that
were in progress are put back in the queue on startup. A dispatcher executes
up to MAX_CONCURRENT_RUNS at a time in worker threads, and a scheduler queues
Watch runs when they are due. Live trace events go to SSE subscribers through
the EventHub.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import timedelta

from sqlmodel import Session, select

from . import cache
from .cadence import next_run_time
from .config import settings
from .crypto import decrypt
from .db import engine
from .ledger import change_brief_html, update_ledger
from .llm import LLMClient, LLMError, Usage
from .models import Claim, Draft, Evidence, Run, Source, Thread, User, Watch, now_iso, utcnow
from .notify import send_email
from .pipeline import RunCancelled, RunContext, run_pipeline
from .quota import allowance
from .search import SearchContext

log = logging.getLogger("nodexm.jobs")


# ---------------------------------------------------------------------------
# Live events
# ---------------------------------------------------------------------------
class EventHub:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(run_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subs.pop(run_id, None)

    def publish(self, run_id: str, event: dict) -> None:
        """Safe to call from worker threads."""
        with self._lock:
            subs = list(self._subs.get(run_id, ()))
        loop = self.loop
        if not subs or loop is None or loop.is_closed():
            return
        for q in subs:
            try:
                loop.call_soon_threadsafe(_put, q, event)
            except RuntimeError:  # loop closed during shutdown
                return


def _put(q: asyncio.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        pass


hub = EventHub()
_cancelled: set[str] = set()


# ---------------------------------------------------------------------------
# Creating runs
# ---------------------------------------------------------------------------
class RunRefused(Exception):
    pass


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def create_run(session: Session, user: User, *, question: str, mode: str = "auto", profile: dict | None = None,
               thread: Thread | None = None, watch: Watch | None = None) -> Run:
    """Check the allowance and queue a run. Raises RunRefused with a user-facing reason."""
    allow = allowance(session, user)
    if not allow.can_run:
        raise RunRefused(allow.reason)
    if mode not in ("auto", "quick", "deep"):
        mode = "auto"
    if allow.force_deep:
        mode = "deep"
    if thread is None:
        thread = Thread(id=new_id(), user_id=user.id, title=question.strip()[:120] or "New thread")
    thread.updated_at = utcnow()
    session.add(thread)
    session.flush()  # the run references the thread
    prefs = dict(user.preferences or {})
    run = Run(
        id=new_id(), user_id=user.id, thread_id=thread.id, watch_id=watch.id if watch else None,
        question=question.strip(), requested_mode=mode, max_mode="deep" if allow.allow_deep else "quick",
        profile={"region": prefs.get("region", "Global"), "depth": prefs.get("depth", "analyst"), **(profile or {})},
        used_own_keys=allow.own_keys,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    runner.wake()
    return run


# ---------------------------------------------------------------------------
# Executing a run (worker thread)
# ---------------------------------------------------------------------------
def _thread_context(session: Session, run: Run) -> str:
    if not run.thread_id:
        return ""
    prev = session.exec(select(Run).where(Run.thread_id == run.thread_id, Run.status == "completed",
                                          Run.created_at < run.created_at)
                        .order_by(Run.created_at.desc()).limit(2)).all()
    parts = []
    for r in reversed(prev):
        draft = session.exec(select(Draft).where(Draft.run_id == r.id)).first()
        answer = (draft.content_md if draft else "").split("## Sources")[0][:1200]
        parts.append(f"Q: {r.standalone_question or r.question}\nA: {answer}")
    return "\n\n".join(parts)


def _persist_result(session: Session, run: Run, result: dict) -> list[Claim]:
    for s in result["sources"]:
        session.add(Source(run_id=run.id, idx=s["idx"], url=s["url"], title=s["title"][:600], domain=s["domain"],
                           source_type=s["source_type"], searcher=s["searcher"],
                           published_at=(str(s.get("published"))[:32] if s.get("published") else None),
                           credibility=s["credibility"], credibility_reasons=s["credibility_reasons"],
                           duplicate_of=s.get("duplicate_of"), snippet=s.get("snippet", "")[:2000], text=s["text"],
                           sub_question=(s.get("sub_question") or "")[:1000]))
    claims = []
    for c in result["clusters"]:
        row = Claim(run_id=run.id, key=c["key"], text=c["text"], status=c["status"], confidence=c["confidence"],
                    note=c.get("note", ""), source_indices=c["source_indices"], embedding=c.get("embedding"))
        session.add(row)
        session.flush()
        for e in c["evidence"]:
            session.add(Evidence(claim_id=row.id, run_id=run.id, source_idx=e["source_idx"], statement=e["statement"],
                                 quote=e["quote"], char_start=e["start"], char_end=e["end"], stance=e["stance"]))
        claims.append(row)
    critique, audit = result.get("critique") or {}, result.get("audit") or {}
    session.add(Draft(run_id=run.id, content_md=result["report_md"], content_html=result["report_html"],
                      critic_score=critique.get("overall"), audit_score=audit.get("score")))
    run.mode = result["mode"]
    run.standalone_question = result.get("standalone")
    run.result = {k: result.get(k) for k in ("plan", "figures", "critique", "audit", "warnings", "rounds",
                                             "queries_run", "revisions")}
    return claims


def execute_run(run_id: str) -> None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if not run or run.status != "running":
            return
        user = session.get(User, run.user_id)
        context = _thread_context(session, run)
        if run.used_own_keys:
            gemini_key, tavily_key = decrypt(user.gemini_key_enc), decrypt(user.tavily_key_enc)
        else:
            gemini_key, tavily_key = settings.gemini_api_key, settings.tavily_api_key
        question, profile, requested, allow_deep = run.question, dict(run.profile), run.requested_mode, run.max_mode == "deep"

    trace: list[dict] = []
    trace_lock = threading.Lock()

    def emit(node: str, status: str, summary: str, extra: dict | None = None) -> None:
        entry = {"node": node, "status": status, "summary": summary, "extra": extra or {},
                 "ts": now_iso()}
        with trace_lock:
            trace.append(entry)
            snapshot = list(trace)
        hub.publish(run_id, {"type": "trace", "data": entry})
        try:
            with Session(engine) as s:
                r = s.get(Run, run_id)
                if r:
                    r.trace = snapshot
                    s.add(r)
                    s.commit()
        except Exception as e:  # trace persistence must not kill the run
            log.debug("trace write failed: %s", e)

    usage = Usage()
    status, error = "completed", None
    try:
        llm = LLMClient(gemini_key, usage)
        ctx = RunContext(run_id=run_id, llm=llm, search=SearchContext(usage=usage, tavily_key=tavily_key,
                                                                      region=profile.get("region", "Global")),
                         emit=emit, cancelled=lambda: run_id in _cancelled)
        result = run_pipeline(ctx, question=question, context=context, profile=profile, requested_mode=requested,
                              allow_deep=allow_deep)
        email = None
        with Session(engine) as session:
            run = session.get(Run, run_id)
            claims = _persist_result(session, run, result)
            if run.watch_id:
                watch = session.get(Watch, run.watch_id)
                events = update_ledger(session, watch, run, claims)
                watch.last_run_id = run.id
                session.add(watch)
                kinds = sorted({e.kind for e in events})
                user = session.get(User, run.user_id)
                if events and watch.email_alerts:
                    link = f"{settings.app_url}/#/t/{run.thread_id}"
                    email = (user.email, f"NodexM: {len(events)} change(s) in “{watch.topic[:60]}”",
                             change_brief_html(watch, events, link))
                change_summary = (f"{len(events)} change(s) since the last run" if events
                                  else "No changes since the last run")
            session.add(run)
            session.commit()
            watch_id = run.watch_id
        # Emit only after commit: emit() writes the trace in its own session.
        if watch_id:
            emit("change_detector", "end", change_summary, {"kinds": kinds})
            if email and send_email(*email):
                emit("change_detector", "end", f"Emailed the change brief to {email[0]}")
    except RunCancelled:
        status, error = "cancelled", "Stopped by you."
    except LLMError as e:
        status, error = "failed", f"The language model could not be reached: {str(e)[:300]}"
    except Exception as e:  # noqa: BLE001
        log.exception("run %s failed", run_id)
        status, error = "failed", f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        _cancelled.discard(run_id)
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status, run.error = status, error
                run.finished_at = utcnow()
                run.usage = usage.as_dict()
                run.cost_usd = usage.cost()
                run.trace = list(trace)
                if run.thread_id:
                    thread = session.get(Thread, run.thread_id)
                    if thread:
                        thread.updated_at = utcnow()
                        session.add(thread)
                session.add(run)
                session.commit()
        hub.publish(run_id, {"type": "status", "data": {"status": status, "error": error}})
        hub.publish(run_id, {"type": "done", "data": {}})


# ---------------------------------------------------------------------------
# Dispatcher and scheduler
# ---------------------------------------------------------------------------
class JobRunner:
    def __init__(self):
        self._wake: asyncio.Event | None = None
        self._active: set[str] = set()
        self._tasks: list[asyncio.Task] = []

    def wake(self) -> None:
        loop = hub.loop
        if self._wake is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass

    def cancel(self, run_id: str) -> None:
        _cancelled.add(run_id)

    async def start(self) -> None:
        hub.loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        with Session(engine) as s:
            for r in s.exec(select(Run).where(Run.status == "running")).all():
                r.status = "queued"
                r.trace = list(r.trace or []) + [{"node": "system", "status": "warning",
                                                  "summary": "Server restarted, run queued again", "extra": {},
                                                  "ts": now_iso()}]
                s.add(r)
            s.commit()
        cache.purge_expired()
        self._tasks = [asyncio.create_task(self._dispatch()), asyncio.create_task(self._schedule())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self._wake = None
        hub.loop = None

    async def _dispatch(self) -> None:
        while True:
            try:
                slots = settings.max_concurrent_runs - len(self._active)
                if slots > 0:
                    with Session(engine) as s:
                        queued = s.exec(select(Run).where(Run.status == "queued").order_by(Run.created_at)
                                        .limit(slots)).all()
                        for r in queued:
                            r.status, r.started_at = "running", utcnow()
                            s.add(r)
                        s.commit()
                        ids = [r.id for r in queued]
                    for run_id in ids:
                        self._active.add(run_id)
                        hub.publish(run_id, {"type": "status", "data": {"status": "running"}})
                        asyncio.create_task(self._execute(run_id))
            except Exception:
                log.exception("dispatcher error")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def _execute(self, run_id: str) -> None:
        try:
            await asyncio.to_thread(execute_run, run_id)
        finally:
            self._active.discard(run_id)
            self.wake()

    async def _schedule(self) -> None:
        while True:
            try:
                await asyncio.to_thread(queue_due_watches)
            except Exception:
                log.exception("scheduler error")
            await asyncio.sleep(60)


def queue_due_watches() -> int:
    queued = 0
    with Session(engine) as s:
        due = s.exec(select(Watch).where(Watch.active == True, Watch.next_run_at != None,  # noqa: E711,E712
                                         Watch.next_run_at <= utcnow())).all()
        for w in due:
            w.next_run_at = next_run_time(w.cron) or (utcnow() + timedelta(days=7))
            s.add(w)
            s.commit()
            user = s.get(User, w.user_id)
            thread = s.get(Thread, w.thread_id) if w.thread_id else None
            try:
                run = create_run(s, user, question=w.topic, mode=w.profile.get("mode", "auto"), profile=w.profile,
                                 thread=thread, watch=w)
                if not w.thread_id:
                    w.thread_id = run.thread_id
                    s.add(w)
                    s.commit()
                queued += 1
            except RunRefused as e:
                log.info("watch %s skipped: %s", w.id, e)
    return queued


runner = JobRunner()
