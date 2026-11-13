"""Claim ledger and change detection for Watches (agent 14, plain code).

Each Watch keeps one LedgerClaim per distinct fact. After every run, the run's
verified claims are matched to the ledger by embedding (or wording) similarity.
New facts, status flips and facts that stop appearing become ChangeEvents.
"""
from __future__ import annotations

import html

from sqlmodel import Session, select

from .evidence import LEXICAL_DIM, cosine, lexical_vectors, periods_compatible
from .models import ChangeEvent, Claim, LedgerClaim, Run, Watch, utcnow

# Gemini embeddings put different facts on one topic at ~0.94, paraphrases at ~0.98.
EMBED_MATCH = 0.96
LEXICAL_MATCH = 0.5
STALE_AFTER_MISSES = 2


def _similarity(a_text: str, a_vec, b_text: str, b_vec) -> tuple[float, float]:
    """Compare the centroids of the underlying statements when both sides used the same vector kind,
    so a claim still matches after its summary sentence changes (e.g. agreed -> disputed)."""
    if periods_compatible(a_text, b_text) is False:
        return 0.0, 1.0  # different years: never the same fact
    if a_vec and b_vec and len(a_vec) == len(b_vec):
        return cosine(a_vec, b_vec), (LEXICAL_MATCH if len(a_vec) == LEXICAL_DIM else EMBED_MATCH)
    va, vb = lexical_vectors([a_text, b_text])
    return cosine(va, vb), LEXICAL_MATCH


def update_ledger(session: Session, watch: Watch, run: Run, claims: list[Claim]) -> list[ChangeEvent]:
    ledger = list(session.exec(select(LedgerClaim).where(LedgerClaim.watch_id == watch.id)).all())
    baseline = not ledger
    events: list[ChangeEvent] = []

    # Best-first greedy matching so two claims never map to the same ledger entry.
    pairs = []
    for ci, c in enumerate(claims):
        for li, lc in enumerate(ledger):
            sim, threshold = _similarity(c.text, c.embedding, lc.text, lc.embedding)
            if sim >= threshold:
                pairs.append((sim, ci, li))
    pairs.sort(reverse=True)
    claim_match: dict[int, int] = {}
    used_ledger: set[int] = set()
    for _, ci, li in pairs:
        if ci not in claim_match and li not in used_ledger:
            claim_match[ci] = li
            used_ledger.add(li)

    now = utcnow()
    for ci, c in enumerate(claims):
        if ci in claim_match:
            lc = ledger[claim_match[ci]]
            if lc.status != c.status:
                kind = "returned" if lc.status == "stale" else "status_changed"
                events.append(ChangeEvent(watch_id=watch.id, run_id=run.id, ledger_claim_id=lc.id, kind=kind,
                                          from_status=lc.status, to_status=c.status, claim_text=c.text,
                                          detail=c.note or ""))
            lc.status, lc.confidence, lc.last_seen_run, lc.misses, lc.updated_at = c.status, c.confidence, run.id, 0, now
            lc.embedding = lc.embedding or c.embedding
            session.add(lc)
            c.ledger_claim_id = lc.id
        else:
            lc = LedgerClaim(watch_id=watch.id, text=c.text, status=c.status, confidence=c.confidence,
                             embedding=c.embedding, first_seen_run=run.id, last_seen_run=run.id)
            session.add(lc)
            session.flush()
            c.ledger_claim_id = lc.id
            if not baseline:
                events.append(ChangeEvent(watch_id=watch.id, run_id=run.id, ledger_claim_id=lc.id, kind="new",
                                          to_status=c.status, claim_text=c.text))
        session.add(c)

    for li, lc in enumerate(ledger):
        if li in used_ledger or lc.status == "stale":
            continue
        lc.misses += 1
        if lc.misses >= STALE_AFTER_MISSES:
            events.append(ChangeEvent(watch_id=watch.id, run_id=run.id, ledger_claim_id=lc.id, kind="stale",
                                      from_status=lc.status, to_status="stale", claim_text=lc.text,
                                      detail=f"Not found in the last {lc.misses} runs"))
            lc.status = "stale"
        lc.updated_at = now
        session.add(lc)

    for e in events:
        session.add(e)
    return events


LABEL = {"agreed": "agreed", "disputed": "disputed", "single_source": "single source", "stale": "stale"}


def describe(e: ChangeEvent) -> str:
    if e.kind == "new":
        return f"New ({LABEL.get(e.to_status, e.to_status)})"
    if e.kind == "stale":
        return "No longer found"
    if e.kind == "returned":
        return f"Back ({LABEL.get(e.to_status, e.to_status)})"
    return f"{LABEL.get(e.from_status, e.from_status)} → {LABEL.get(e.to_status, e.to_status)}"


def change_brief_html(watch: Watch, events: list[ChangeEvent], link: str) -> str:
    order = {"status_changed": 0, "returned": 1, "new": 2, "stale": 3}
    rows = "".join(
        f"<li style='margin:0 0 12px'><b>{html.escape(describe(e))}</b><br>{html.escape(e.claim_text)}"
        + (f"<br><span style='color:#666'>{html.escape(e.detail)}</span>" if e.detail else "") + "</li>"
        for e in sorted(events, key=lambda e: order.get(e.kind, 9))
    )
    return (f"<div style='font-family:system-ui,sans-serif;max-width:640px'>"
            f"<h2 style='margin:0 0 4px'>What changed: {html.escape(watch.topic)}</h2>"
            f"<p style='color:#666;margin:0 0 16px'>{len(events)} change(s) since the last run</p>"
            f"<ul style='padding-left:18px'>{rows}</ul>"
            f"<p><a href='{html.escape(link)}'>Open the full report</a></p></div>")
