"""Turn database rows into API responses."""
from __future__ import annotations

from collections import defaultdict

from sqlmodel import Session, select

from .cadence import label_for
from .crypto import decrypt, mask
from .models import ChangeEvent, Claim, Draft, Evidence, LedgerClaim, Run, Source, Thread, User, Watch, iso

CONTEXT_CHARS = 220


def user_out(user: User) -> dict:
    prefs = {"default_mode": "auto", "region": "Global", "depth": "analyst", **(user.preferences or {})}
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name or user.email.split("@")[0],
        "preferences": prefs,
        "avatar_url": user.avatar_url,
        "sign_in": {"password": bool(user.password_hash), "google": bool(user.google_sub)},
        "keys": {"gemini": mask(decrypt(user.gemini_key_enc)), "tavily": mask(decrypt(user.tavily_key_enc))},
        "created_at": iso(user.created_at),
    }


def run_summary(run: Run) -> dict:
    return {
        "id": run.id,
        "question": run.question,
        "mode": run.mode or run.requested_mode,
        "status": run.status,
        "created_at": iso(run.created_at),
        "finished_at": iso(run.finished_at),
        "cost_usd": run.cost_usd,
    }


def run_detail(session: Session, run: Run) -> dict:
    sources = session.exec(select(Source).where(Source.run_id == run.id).order_by(Source.idx)).all()
    claims = session.exec(select(Claim).where(Claim.run_id == run.id).order_by(Claim.id)).all()
    evidence = session.exec(select(Evidence).where(Evidence.run_id == run.id)).all()
    draft = session.exec(select(Draft).where(Draft.run_id == run.id)).first()
    text_by_idx = {s.idx: s.text for s in sources}

    ev_by_claim = defaultdict(list)
    quotes_by_source = defaultdict(int)
    for e in evidence:
        text = text_by_idx.get(e.source_idx, "")
        before = text[max(0, e.char_start - CONTEXT_CHARS):e.char_start] if e.char_start >= 0 else ""
        after = text[e.char_end:e.char_end + CONTEXT_CHARS] if e.char_end >= 0 else ""
        ev_by_claim[e.claim_id].append({
            "source": e.source_idx + 1, "statement": e.statement, "quote": e.quote, "stance": e.stance,
            "before": before, "after": after,
        })
        quotes_by_source[e.source_idx] += 1

    result = dict(run.result or {})
    return {
        **run_summary(run),
        "thread_id": run.thread_id,
        "watch_id": run.watch_id,
        "standalone_question": run.standalone_question,
        "requested_mode": run.requested_mode,
        "error": run.error,
        "started_at": iso(run.started_at),
        "trace": run.trace or [],
        "usage": run.usage or {},
        "used_own_keys": run.used_own_keys,
        "plan": result.get("plan") or [],
        "figures": result.get("figures") or [],
        "critique": result.get("critique"),
        "audit": result.get("audit") or {},
        "warnings": result.get("warnings") or [],
        "rounds": result.get("rounds", 0),
        "revisions": result.get("revisions", 0),
        "sources": [{
            "number": s.idx + 1, "url": s.url, "title": s.title, "domain": s.domain, "type": s.source_type,
            "searcher": s.searcher, "published": s.published_at, "credibility": s.credibility,
            "credibility_reasons": s.credibility_reasons, "snippet": s.snippet[:400],
            "duplicate_of": (s.duplicate_of + 1) if s.duplicate_of is not None else None,
            "origin": s.origin,
            "evidence_count": quotes_by_source.get(s.idx, 0),
        } for s in sources],
        "claims": [{
            "id": c.id, "key": c.key, "text": c.text, "status": c.status, "confidence": c.confidence,
            "note": c.note, "sources": [i + 1 for i in c.source_indices], "evidence": ev_by_claim.get(c.id, []),
            "independence": {
                "independent": (c.independence or {}).get("independent", len(c.source_indices)),
                "sites": (c.independence or {}).get("sites", len(c.source_indices)),
                "groups": [[i + 1 for i in g] for g in (c.independence or {}).get("groups", [])],
                "reasons": (c.independence or {}).get("reasons", []),
            },
        } for c in claims],
        "draft": {
            "content_md": draft.content_md, "version": draft.version, "status": draft.status,
            "critic_score": draft.critic_score, "audit_score": draft.audit_score,
            "approved_at": iso(draft.approved_at), "updated_at": iso(draft.updated_at),
        } if draft else None,
    }


def thread_out(session: Session, thread: Thread, detail: bool = False) -> dict:
    runs = session.exec(select(Run).where(Run.thread_id == thread.id).order_by(Run.created_at)).all()
    watch = session.exec(select(Watch).where(Watch.thread_id == thread.id)).first()
    out = {
        "id": thread.id,
        "title": thread.title,
        "created_at": iso(thread.created_at),
        "updated_at": iso(thread.updated_at),
        "run_count": len(runs),
        "last_status": runs[-1].status if runs else None,
        "watch_id": watch.id if watch else None,
    }
    if detail:
        out["runs"] = [run_detail(session, r) for r in runs]
    return out


def watch_out(session: Session, watch: Watch, detail: bool = False) -> dict:
    out = {
        "id": watch.id,
        "topic": watch.topic,
        "thread_id": watch.thread_id,
        "cadence": watch.cadence,
        "cadence_label": label_for(watch.cadence),
        "mode": (watch.profile or {}).get("mode", "auto"),
        "next_run_at": iso(watch.next_run_at),
        "active": watch.active,
        "email_alerts": watch.email_alerts,
        "last_run_id": watch.last_run_id,
        "created_at": iso(watch.created_at),
    }
    events = session.exec(select(ChangeEvent).where(ChangeEvent.watch_id == watch.id)
                          .order_by(ChangeEvent.created_at.desc()).limit(200 if detail else 5)).all()
    out["recent_changes"] = len(events)
    if detail:
        ledger = session.exec(select(LedgerClaim).where(LedgerClaim.watch_id == watch.id)
                              .order_by(LedgerClaim.updated_at.desc())).all()
        runs = session.exec(select(Run).where(Run.watch_id == watch.id).order_by(Run.created_at.desc())).all()
        out["changes"] = [{
            "id": e.id, "run_id": e.run_id, "kind": e.kind, "from_status": e.from_status, "to_status": e.to_status,
            "claim": e.claim_text, "detail": e.detail, "created_at": iso(e.created_at),
        } for e in events]
        out["ledger"] = [{
            "id": c.id, "text": c.text, "status": c.status, "confidence": c.confidence,
            "first_seen_run": c.first_seen_run, "last_seen_run": c.last_seen_run, "updated_at": iso(c.updated_at),
        } for c in ledger]
        out["runs"] = [run_summary(r) for r in runs]
    return out
