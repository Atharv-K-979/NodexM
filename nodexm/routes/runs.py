from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .. import agents
from ..auth import get_current_user
from ..config import settings
from ..crypto import decrypt
from ..db import engine, get_session
from ..jobs import hub, runner
from ..llm import LLMClient, LLMError
from ..models import Claim, Draft, Run, Source, User, utcnow
from ..pipeline import _claims_block, render_html
from ..serializers import run_detail

router = APIRouter(prefix="/api/runs", tags=["runs"])
MAX_REWRITES_PER_RUN = 10


class DraftIn(BaseModel):
    content_md: str = Field(max_length=200_000)


class RewriteIn(BaseModel):
    heading: str = Field(min_length=1, max_length=300)
    instruction: str = Field(default="", max_length=1000)


def own_run(session: Session, run_id: str, user: User) -> Run:
    run = session.get(Run, run_id)
    if not run or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found.")
    return run


def own_draft(session: Session, run: Run) -> Draft:
    draft = session.exec(select(Draft).where(Draft.run_id == run.id)).first()
    if not draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer is not finished yet.")
    return draft


def _touch(draft: Draft) -> None:
    if draft.status == "approved":
        draft.version += 1
        draft.status = "pending_review"
        draft.approved_at = None
    draft.content_html = render_html(draft.content_md)
    draft.updated_at = utcnow()


@router.get("/{run_id}")
def get_run(run_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    return run_detail(session, own_run(session, run_id, user))


@router.get("/{run_id}/events")
async def events(run_id: str, user: User = Depends(get_current_user)) -> StreamingResponse:
    """Server-sent events: the trace so far, then live updates until the run ends."""
    with Session(engine) as session:
        run = own_run(session, run_id, user)
        snapshot, run_status = list(run.trace or []), run.status
    queue = hub.subscribe(run_id)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'status', 'data': {'status': run_status}})}\n\n"
            for entry in snapshot:
                yield f"data: {json.dumps({'type': 'trace', 'data': entry})}\n\n"
            if run_status not in ("queued", "running"):
                yield f"data: {json.dumps({'type': 'done', 'data': {}})}\n\n"
                return
            seen = {(e.get("ts"), e.get("node"), e.get("summary")) for e in snapshot}
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event["type"] == "trace":
                    d = event["data"]
                    key = (d.get("ts"), d.get("node"), d.get("summary"))
                    if key in seen:
                        continue
                    seen.add(key)
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "done":
                    return
        finally:
            hub.unsubscribe(run_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{run_id}/cancel")
def cancel(run_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    run = own_run(session, run_id, user)
    if run.status == "queued":
        run.status, run.error, run.finished_at = "cancelled", "Stopped by you.", utcnow()
        session.add(run)
        session.commit()
    elif run.status == "running":
        runner.cancel(run_id)
    return {"id": run.id, "status": run.status}


@router.patch("/{run_id}/draft")
def save_draft(run_id: str, body: DraftIn, user: User = Depends(get_current_user),
               session: Session = Depends(get_session)) -> dict:
    run = own_run(session, run_id, user)
    draft = own_draft(session, run)
    draft.content_md = body.content_md
    _touch(draft)
    session.add(draft)
    session.commit()
    return run_detail(session, run)["draft"]


def _find_section(md: str, heading: str) -> tuple[int, int, int] | None:
    lines = md.split("\n")
    target = heading.strip().lower()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m and m.group(2).strip().lower() == target:
            level = len(m.group(1))
            end = len(lines)
            for j in range(i + 1, len(lines)):
                m2 = re.match(r"^(#{1,6})\s+", lines[j])
                if m2 and len(m2.group(1)) <= level:
                    end = j
                    break
            return i, end, level
    return None


@router.post("/{run_id}/draft/rewrite-section")
def rewrite_section(run_id: str, body: RewriteIn, user: User = Depends(get_current_user),
                    session: Session = Depends(get_session)) -> dict:
    run = own_run(session, run_id, user)
    draft = own_draft(session, run)
    rewrites = int((run.result or {}).get("rewrites", 0))
    if rewrites >= MAX_REWRITES_PER_RUN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "This answer reached its limit of section rewrites.")
    if body.heading.strip().lower() == "sources":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The Sources section is generated automatically.")
    found = _find_section(draft.content_md, body.heading)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found. Save your edits first.")
    start, end, _ = found
    lines = draft.content_md.split("\n")
    section = "\n".join(lines[start:end]).strip()

    key = decrypt(user.gemini_key_enc) if run.used_own_keys else settings.gemini_api_key
    claims = session.exec(select(Claim).where(Claim.run_id == run.id).order_by(Claim.id)).all()
    sources = session.exec(select(Source).where(Source.run_id == run.id).order_by(Source.idx)).all()
    claims_block = _claims_block([{"key": c.key, "status": c.status, "confidence": c.confidence, "text": c.text,
                                   "source_indices": c.source_indices, "evidence": [], "note": c.note}
                                  for c in claims])
    used = {i for c in claims for i in c.source_indices}
    sources_block = "\n".join(f"[{s.idx + 1}] {s.title} | {s.domain} | credibility {s.credibility}"
                              for s in sources if s.idx in used)
    try:
        llm = LLMClient(key)
        new_section = agents.rewrite_section(llm, question=run.standalone_question or run.question,
                                             section_md=section, instruction=body.instruction,
                                             claims_block=claims_block, sources_block=sources_block)
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The model could not rewrite this section: {str(e)[:200]}")
    new_section = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", new_section.strip())
    heading_line = lines[start]
    if not new_section.startswith("#"):
        new_section = heading_line + "\n" + new_section
    draft.content_md = "\n".join(lines[:start] + new_section.split("\n") + [""] + lines[end:]).strip() + "\n"
    _touch(draft)
    run.result = {**(run.result or {}), "rewrites": rewrites + 1}
    run.cost_usd = round(run.cost_usd + llm.usage.cost(), 5)
    session.add_all([draft, run])
    session.commit()
    return run_detail(session, run)["draft"]


@router.post("/{run_id}/draft/approve")
def approve(run_id: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> dict:
    run = own_run(session, run_id, user)
    draft = own_draft(session, run)
    draft.status, draft.approved_at, draft.updated_at = "approved", utcnow(), utcnow()
    session.add(draft)
    session.commit()
    return run_detail(session, run)["draft"]


@router.get("/{run_id}/export")
def export(run_id: str, format: Literal["md", "html"] = "md", user: User = Depends(get_current_user),
           session: Session = Depends(get_session)):
    run = own_run(session, run_id, user)
    draft = own_draft(session, run)
    title = run.standalone_question or run.question
    if format == "html":
        html = (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
                "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}"
                "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}</style></head>"
                f"<body><h1>{title}</h1>{draft.content_html}</body></html>")
        return PlainTextResponse(html, media_type="text/html",
                                 headers={"Content-Disposition": f'attachment; filename="nodexm-{run.id}.html"'})
    return PlainTextResponse(f"# {title}\n\n{draft.content_md}", media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="nodexm-{run.id}.md"'})
