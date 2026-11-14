"""The Watch ledger only alerts on confirmed changes and keeps before/after quotes."""
import itertools

from sqlmodel import Session, select

from nodexm.db import engine
from nodexm.ledger import update_ledger
from nodexm.models import Claim, LedgerClaim, Run, User, Watch

_ids = itertools.count(1)

PRICE = "Acme's Pro plan costs $49 per month in 2026."
FUNDING = "Acme raised $50 million in a Series B round in 2026."


def new_watch(s: Session) -> Watch:
    n = next(_ids)
    user = User(email=f"ledger{n}@test.dev", password_hash="x")
    s.add(user)
    s.commit()
    watch = Watch(user_id=user.id, topic="Acme pricing", cadence="manual")
    s.add(watch)
    s.commit()
    return watch


def run(s: Session, watch: Watch, specs: list[dict]):
    """specs: [{text, status, quotes: [(domain, quote, stance)], independent}]"""
    r = Run(id=f"r{next(_ids)}", user_id=watch.user_id, watch_id=watch.id, question="q", status="completed")
    s.add(r)
    s.flush()
    claims, snaps = [], {}
    for sp in specs:
        c = Claim(run_id=r.id, key="C", text=sp["text"], status=sp["status"], confidence=0.7,
                  source_indices=list(range(len(sp["quotes"]))), independence={"independent": sp.get("independent", 1)})
        s.add(c)
        s.flush()
        claims.append(c)
        snaps[c.id] = {
            "evidence": [{"domain": d, "quote": q, "stance": st} for d, q, st in sp["quotes"]],
            "keys": {"supports": [d for d, _, st in sp["quotes"] if st == "supports"],
                     "contradicts": [d for d, _, st in sp["quotes"] if st == "contradicts"]},
        }
    events, stats = update_ledger(s, watch, r, claims, snaps)
    s.commit()
    return events, stats


def price(status="agreed", extra=()):
    quotes = [("acme.com", "The Pro plan is $49 per month.", "supports"),
              ("techreview.example", "Acme charges $49 a month for Pro.", "supports"), *extra]
    return {"text": PRICE, "status": status, "quotes": quotes, "independent": 2}


def ledger_of(s, watch):
    return s.exec(select(LedgerClaim).where(LedgerClaim.watch_id == watch.id)).all()


def test_first_run_is_a_silent_baseline():
    with Session(engine) as s:
        w = new_watch(s)
        events, _ = run(s, w, [price(), {"text": FUNDING, "status": "single_source",
                                         "quotes": [("news.example", "Acme raised $50 million.", "supports")]}])
        assert events == []
        assert all(lc.confirmed for lc in ledger_of(s, w))


def test_a_new_independent_source_confirms_a_change_immediately():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        contradiction = ("acme-pricing-update.example", "Acme raised the Pro plan to $59 per month.", "contradicts")
        events, _ = run(s, w, [price("disputed", extra=[contradiction])])
        assert len(events) == 1
        e = events[0]
        assert (e.kind, e.from_status, e.to_status, e.confirmation) == ("status_changed", "agreed", "disputed", "new_source")
        assert "acme-pricing-update.example" in e.detail
        assert {q["stance"] for q in e.evidence_before} == {"supports"}
        assert any(q["stance"] == "contradicts" for q in e.evidence_after)


def test_a_one_off_change_is_not_alerted_and_a_repeat_is():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        events, stats = run(s, w, [price("single_source")])  # same sources, just a noisier run
        assert events == [] and stats["pending"] == 1
        events, _ = run(s, w, [price("agreed")])  # back to normal: the pending change is dropped
        assert events == []
        assert ledger_of(s, w)[0].pending_status is None
        run(s, w, [price("single_source")])
        events, _ = run(s, w, [price("single_source")])  # seen twice in a row: confirmed
        assert [(e.from_status, e.to_status, e.confirmation) for e in events] == [("agreed", "single_source", "repeat")]


def test_new_single_source_claims_wait_for_a_second_sighting():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        funding = {"text": FUNDING, "status": "single_source", "quotes": [("news.example", "Acme raised $50 million.", "supports")]}
        events, stats = run(s, w, [price(), funding])
        assert events == [] and stats["candidates"] == 1
        events, _ = run(s, w, [price(), funding])
        assert [(e.kind, e.confirmation) for e in events] == [("new", "repeat")]


def test_new_claims_backed_by_two_independent_sources_alert_at_once():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        funding = {"text": FUNDING, "status": "agreed", "independent": 2,
                   "quotes": [("news.example", "Acme raised $50 million.", "supports"),
                              ("wire.example", "Acme closed a $50 million Series B.", "supports")]}
        events, _ = run(s, w, [price(), funding])
        assert [(e.kind, e.confirmation) for e in events] == [("new", "strong")]
        assert "2 independent sources" in events[0].detail


def test_candidates_that_never_return_are_dropped_silently():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        run(s, w, [price(), {"text": FUNDING, "status": "single_source",
                             "quotes": [("news.example", "Acme raised $50 million.", "supports")]}])
        for _ in range(2):
            events, _ = run(s, w, [price()])
            assert events == []
        assert any(lc.status == "dropped" for lc in ledger_of(s, w))


def test_stale_needs_three_missed_runs():
    with Session(engine) as s:
        w = new_watch(s)
        funding = {"text": FUNDING, "status": "single_source", "quotes": [("news.example", "Acme raised $50 million.", "supports")]}
        run(s, w, [price(), funding])
        for _ in range(2):
            events, _ = run(s, w, [price()])
            assert events == []
        events, _ = run(s, w, [price()])
        assert [(e.kind, e.to_status) for e in events] == [("stale", "stale")]
        assert events[0].evidence_before[0]["domain"] == "news.example"


def test_claims_match_by_exact_quote_even_when_reworded():
    with Session(engine) as s:
        w = new_watch(s)
        run(s, w, [price()])
        reworded = {**price(), "text": "Pricing for Acme's top tier is unchanged at forty-nine dollars monthly."}
        events, stats = run(s, w, [reworded])
        assert events == [] and stats["candidates"] == 0
        assert len(ledger_of(s, w)) == 1
