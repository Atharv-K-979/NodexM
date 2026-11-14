"""Claim ledger and change detection for Watches (agent 14, plain code).

Each Watch keeps one LedgerClaim per distinct fact. After every run, the run's verified claims
are matched to the ledger and changes are recorded as ChangeEvents.

AI research answers vary between identical runs, so a change is only alerted once it is
confirmed:
- by a NEW independent source that backs the change (for example a new outlet contradicting
  a fact that used to be agreed), or
- by showing up on two runs in a row.
Unconfirmed changes wait on the ledger claim (pending_status). A brand-new claim backed by a
single source is a "candidate" until it is seen again. A fact is only called stale after it has
been missing from three runs in a row. Every alert carries the before and after quotes.
"""
from __future__ import annotations

import html

from sqlmodel import Session, select

from .evidence import LEXICAL_DIM, cosine, lexical_vectors, periods_compatible
from .models import ChangeEvent, Claim, LedgerClaim, Run, Watch, utcnow

# Gemini embeddings put different facts on one topic at ~0.94, paraphrases at ~0.98.
EMBED_MATCH = 0.96
LEXICAL_MATCH = 0.5
STALE_AFTER_MISSES = 3
CANDIDATE_DROP_AFTER_MISSES = 2
CONFIRM_RUNS = 2  # an unconfirmed change must appear this many runs in a row


def _similarity(a_text: str, a_vec, b_text: str, b_vec) -> tuple[float, float]:
    """Compare the centroids of the underlying statements when both sides used the same vector kind,
    so a claim still matches after its summary sentence changes (e.g. agreed -> disputed)."""
    if periods_compatible(a_text, b_text) is False:
        return 0.0, 1.0  # different years: never the same fact
    if a_vec and b_vec and len(a_vec) == len(b_vec):
        return cosine(a_vec, b_vec), (LEXICAL_MATCH if len(a_vec) == LEXICAL_DIM else EMBED_MATCH)
    va, vb = lexical_vectors([a_text, b_text])
    return cosine(va, vb), LEXICAL_MATCH


def _quote_keys(evidence: list | None) -> set[tuple[str, str]]:
    return {(e.get("domain", ""), " ".join(str(e.get("quote", "")).lower().split())[:160])
            for e in evidence or [] if e.get("quote")}


def _keys(snapshot: dict, stance: str | None = None) -> set[str]:
    keys = snapshot.get("keys") or {}
    if stance:
        return set(keys.get(stance, []))
    return {k for values in keys.values() for k in values}


LABEL = {"agreed": "agreed", "disputed": "disputed", "single_source": "single source", "stale": "stale"}


def update_ledger(session: Session, watch: Watch, run: Run, claims: list[Claim],
                  snapshots: dict[int, dict] | None = None) -> tuple[list[ChangeEvent], dict]:
    """Update the Watch's ledger with this run's claims. Returns (confirmed events, stats).

    snapshots maps Claim.id -> {"evidence": [...quotes...], "keys": {"supports": [...], "contradicts": [...]}}
    where keys identify independent sources (a wire name or a site).
    """
    snapshots = snapshots or {}
    ledger = [lc for lc in session.exec(select(LedgerClaim).where(LedgerClaim.watch_id == watch.id)).all()
              if lc.status != "dropped"]
    baseline = not ledger
    events: list[ChangeEvent] = []
    stats = {"confirmed": 0, "pending": 0, "candidates": 0}

    # Best-first greedy matching. Sharing an exact quote from the same site is a certain match.
    pairs = []
    for ci, c in enumerate(claims):
        c_quotes = _quote_keys(snapshots.get(c.id, {}).get("evidence"))
        for li, lc in enumerate(ledger):
            if c_quotes and c_quotes & _quote_keys(lc.evidence):
                pairs.append((2.0, ci, li))
                continue
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

    def event(lc: LedgerClaim, kind: str, to_status: str, text: str, confirmation: str, detail: str,
              before: list | None, after: list | None, from_status: str | None = None) -> None:
        events.append(ChangeEvent(watch_id=watch.id, run_id=run.id, ledger_claim_id=lc.id, kind=kind,
                                  from_status=from_status, to_status=to_status, claim_text=text,
                                  confirmation=confirmation, detail=detail,
                                  evidence_before=before, evidence_after=after))

    now = utcnow()
    for ci, c in enumerate(claims):
        snap = snapshots.get(c.id, {})
        after = snap.get("evidence") or []
        if ci in claim_match:
            lc = ledger[claim_match[ci]]
            known = set(lc.source_keys or [])
            before = lc.evidence
            waiting = False
            if not lc.confirmed:
                # A candidate seen again: it is real.
                lc.confirmed = True
                event(lc, "new", c.status, c.text, "repeat",
                      "Confirmed: it showed up again on this run.", None, after)
            elif c.status != lc.status:
                relevant_stance = "contradicts" if c.status == "disputed" else "supports"
                new_sources = sorted(_keys(snap, relevant_stance) - known)
                kind = "returned" if lc.status == "stale" else "status_changed"
                if new_sources:
                    confirmation = "new_source"
                    detail = f"Confirmed by a new independent source: {', '.join(new_sources[:3])}."
                elif lc.pending_status == c.status and lc.pending_runs + 1 >= CONFIRM_RUNS:
                    confirmation = "repeat"
                    detail = f"Confirmed: the change showed up on {lc.pending_runs + 1} runs in a row."
                else:
                    confirmation = ""
                if confirmation:
                    if c.note:
                        detail += " " + c.note
                    event(lc, kind, c.status, c.text, confirmation, detail, before, after, from_status=lc.status)
                    lc.status, lc.pending_status, lc.pending_runs = c.status, None, 0
                else:
                    lc.pending_runs = lc.pending_runs + 1 if lc.pending_status == c.status else 1
                    lc.pending_status = c.status
                    stats["pending"] += 1
                    waiting = True
            else:
                lc.pending_status, lc.pending_runs = None, 0
            lc.text = c.text if lc.status == c.status else lc.text
            lc.confidence, lc.last_seen_run, lc.misses, lc.updated_at = c.confidence, run.id, 0, now
            if not waiting:  # keep the quotes of the last confirmed state as the "before" side
                lc.evidence = after or lc.evidence
            lc.source_keys = sorted(known | _keys(snap))
            lc.embedding = lc.embedding or c.embedding
            session.add(lc)
            c.ledger_claim_id = lc.id
        else:
            strong = c.status in ("agreed", "disputed")  # two or more independent sources already
            lc = LedgerClaim(watch_id=watch.id, text=c.text, status=c.status, confidence=c.confidence,
                             embedding=c.embedding, first_seen_run=run.id, last_seen_run=run.id,
                             confirmed=baseline or strong, evidence=after, source_keys=sorted(_keys(snap)))
            session.add(lc)
            session.flush()
            c.ledger_claim_id = lc.id
            if not baseline:
                if strong:
                    n = (c.independence or {}).get("independent", 2)
                    event(lc, "new", c.status, c.text, "strong",
                          f"Backed by {n} independent sources the first time it was seen.", None, after)
                else:
                    stats["candidates"] += 1
        session.add(c)

    for li, lc in enumerate(ledger):
        if li in used_ledger or lc.status == "stale":
            continue
        lc.misses += 1
        if not lc.confirmed and lc.misses >= CANDIDATE_DROP_AFTER_MISSES:
            lc.status = "dropped"  # a one-off that never came back; never alerted
        elif lc.confirmed and lc.misses >= STALE_AFTER_MISSES:
            event(lc, "stale", "stale", lc.text, "stale", f"Not found in the last {lc.misses} runs.",
                  lc.evidence, None, from_status=lc.status)
            lc.status, lc.pending_status, lc.pending_runs = "stale", None, 0
        lc.updated_at = now
        session.add(lc)

    for e in events:
        session.add(e)
    stats["confirmed"] = len(events)
    return events, stats


def describe(e: ChangeEvent) -> str:
    if e.kind == "new":
        return f"New ({LABEL.get(e.to_status, e.to_status)})"
    if e.kind == "stale":
        return "No longer found"
    if e.kind == "returned":
        return f"Back ({LABEL.get(e.to_status, e.to_status)})"
    return f"{LABEL.get(e.from_status, e.from_status)} → {LABEL.get(e.to_status, e.to_status)}"


def _quotes_html(title: str, evidence: list | None) -> str:
    if not evidence:
        return ""
    items = "".join(
        f"<li style='margin:0 0 4px'>“{html.escape(str(q.get('quote', ''))[:220])}” "
        f"<span style='color:#888'>— {html.escape(str(q.get('domain', '')))}"
        + (" (contradicts)" if q.get("stance") == "contradicts" else "") + "</span></li>"
        for q in evidence[:3]
    )
    return f"<div style='margin:6px 0 0'><b style='color:#555'>{title}</b><ul style='margin:4px 0;padding-left:18px'>{items}</ul></div>"


def change_brief_html(watch: Watch, events: list[ChangeEvent], link: str) -> str:
    order = {"status_changed": 0, "returned": 1, "new": 2, "stale": 3}
    rows = "".join(
        f"<li style='margin:0 0 16px'><b>{html.escape(describe(e))}</b><br>{html.escape(e.claim_text)}"
        + (f"<br><span style='color:#666'>{html.escape(e.detail)}</span>" if e.detail else "")
        + _quotes_html("Before", e.evidence_before) + _quotes_html("Now", e.evidence_after)
        + "</li>"
        for e in sorted(events, key=lambda e: order.get(e.kind, 9))
    )
    return (f"<div style='font-family:system-ui,sans-serif;max-width:640px'>"
            f"<h2 style='margin:0 0 4px'>What changed: {html.escape(watch.topic)}</h2>"
            f"<p style='color:#666;margin:0 0 16px'>{len(events)} confirmed change(s) since the last run</p>"
            f"<ul style='padding-left:18px'>{rows}</ul>"
            f"<p><a href='{html.escape(link)}'>Open the full report</a></p></div>")
