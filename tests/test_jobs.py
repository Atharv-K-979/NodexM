import pytest
from sqlmodel import Session, select

from nodexm import jobs, pipeline
from nodexm.auth import hash_password
from nodexm.config import settings
from nodexm.db import engine
from nodexm.models import ChangeEvent, Claim, Draft, Evidence, LedgerClaim, Run, Source, User, Watch
from nodexm.quota import allowance

from . import fakes


@pytest.fixture(autouse=True)
def server_key(monkeypatch):
    object.__setattr__(settings, "gemini_api_key", "server-key")
    monkeypatch.setattr(pipeline, "run_searcher", fakes.fake_search)
    monkeypatch.setattr(pipeline, "read", fakes.fake_read)
    monkeypatch.setattr(pipeline, "search_available", lambda key: True)
    monkeypatch.setattr(jobs, "LLMClient", lambda key, usage: _llm(usage))
    yield
    object.__setattr__(settings, "gemini_api_key", "")


def _llm(usage):
    llm = fakes.FakeLLM()
    llm.usage = usage
    return llm


_n = 0


def make_user(session) -> User:
    global _n
    _n += 1
    user = User(email=f"user{_n}@test.dev", password_hash=hash_password("secret123"))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def run_now(session, user, **kw) -> Run:
    run = jobs.create_run(session, user, question="What do SMEs pay under the AI Act?", **kw)
    run.status = "running"
    session.add(run)
    session.commit()
    jobs.execute_run(run.id)
    session.expire_all()
    return session.get(Run, run.id)


def test_run_is_persisted_with_evidence():
    with Session(engine) as s:
        user = make_user(s)
        run = run_now(s, user, mode="quick")
        assert run.status == "completed", run.error
        assert run.mode == "quick" and run.cost_usd > 0 and run.trace
        assert s.exec(select(Draft).where(Draft.run_id == run.id)).one().content_md.startswith("## Summary")
        assert len(s.exec(select(Source).where(Source.run_id == run.id)).all()) == 4
        claims = s.exec(select(Claim).where(Claim.run_id == run.id)).all()
        assert any(c.status == "disputed" for c in claims)
        assert s.exec(select(Evidence).where(Evidence.run_id == run.id)).all()


def test_free_quota_counts_only_completed_runs():
    with Session(engine) as s:
        user = make_user(s)
        a = allowance(s, user)
        assert a.can_run and a.quick_left == 2 and a.deep_left == 1

        failed = Run(id=jobs.new_id(), user_id=user.id, question="x", status="failed", mode="quick")
        s.add(failed)
        s.commit()
        assert allowance(s, user).quick_left == 2

        run_now(s, user, mode="quick")
        run_now(s, user, mode="quick")
        a = allowance(s, user)
        assert a.quick_left == 0 and a.deep_left == 1 and a.force_deep
    # a third run the same day hits the daily limit
    with Session(engine) as s:
        assert "a day" in allowance(s, s.get(User, user.id)).reason


def test_refused_when_free_runs_are_used_up():
    with Session(engine) as s:
        user = make_user(s)
        for mode in ("quick", "quick", "deep"):
            s.add(Run(id=jobs.new_id(), user_id=user.id, question="x", status="completed", mode=mode))
        s.commit()
        with pytest.raises(jobs.RunRefused, match="used your free runs"):
            jobs.create_run(s, user, question="again")


def test_watch_ledger_detects_status_change(monkeypatch):
    object.__setattr__(settings, "admin_emails", ["watcher@test.dev"])
    try:
        with Session(engine) as s:
            user = User(email="watcher@test.dev", password_hash="x")
            s.add(user)
            s.commit()
            watch = Watch(user_id=user.id, topic="AI Act SME costs", profile={"mode": "quick"}, cadence="manual")
            s.add(watch)
            s.commit()

            first = run_now(s, user, mode="quick", watch=watch)
            assert first.status == "completed", first.error
            assert s.exec(select(LedgerClaim).where(LedgerClaim.watch_id == watch.id)).all()
            assert not s.exec(select(ChangeEvent).where(ChangeEvent.watch_id == watch.id)).all()  # baseline

            # Second run: the blog with the conflicting figure is gone, so the dispute resolves.
            original = fakes.fake_search
            monkeypatch.setattr(pipeline, "run_searcher",
                                lambda n, q, c: [h for h in original(n, q, c) if "medium.com" not in h.url])
            second = run_now(s, user, mode="quick", watch=watch, thread=s.get(jobs.Thread, first.thread_id))
            assert second.status == "completed", second.error
            events = s.exec(select(ChangeEvent).where(ChangeEvent.run_id == second.id)).all()
            assert any(e.kind == "status_changed" and e.from_status == "disputed" and e.to_status == "agreed"
                       for e in events), [(e.kind, e.from_status, e.to_status) for e in events]
            assert any(t["node"] == "change_detector" for t in second.trace)
    finally:
        object.__setattr__(settings, "admin_emails", [])
