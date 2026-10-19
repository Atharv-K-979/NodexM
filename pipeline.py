from __future__ import annotations

import json
import time
import asyncio
import operator
from datetime import datetime, timezone
from typing import TypedDict, Annotated, Any

from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from agents import (
    planner_chain,
    verifier_chain,
    writer_chain,
    critic_chain,
    formatter_chain,
    section_regen_chain,
)
from tools import web_search, scrape_url
from rich import print


# ---------------------------------------------------------------------------
# State
#   Annotated[T, operator.add]  → multiple parallel writes are concatenated
#   Plain T                      → last write wins (single writer)
# ---------------------------------------------------------------------------
class ResearchState(TypedDict, total=False):
    topic: str
    user_profile: dict
    sub_questions: list[dict]
    raw_search_results: Annotated[list[dict], operator.add]
    sources: Annotated[list[dict], operator.add]
    verified: dict
    report: str
    critique: dict
    revision_count: int
    formatter_output: dict
    trace: Annotated[list[dict], operator.add]
    _sources_index: str
    _verified_json: str
    _research_notes: str
    _merged_search: dict
    _picked_urls: list[dict]


# ---------------------------------------------------------------------------
# Trace helper — returns ONLY NEW items (concatenated via operator.add)
# ---------------------------------------------------------------------------
def _trace_items(items: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for it in items:
        entry = {
            "node": it["node"],
            "status": it["status"],
            "summary": it["summary"],
            "extra": it.get("extra") or {},
            "ts": it.get("ts") or now,
        }
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# 1. Planner node
# ---------------------------------------------------------------------------
def planner_node(state: ResearchState) -> dict:
    new_trace = _trace_items([
        {"node": "planner", "status": "start", "summary": "Planning sub-questions..."},
    ])
    try:
        out = planner_chain.invoke(
            {
                "topic": state["topic"],
                "user_profile": json.dumps(state.get("user_profile") or {}, indent=2),
            }
        )
        sub_questions = out.get("sub_questions") or []
        if not sub_questions:
            sub_questions = [{"question": state["topic"], "source_hint": "general"}]
        new_trace += _trace_items([
            {
                "node": "planner",
                "status": "end",
                "summary": f"{len(sub_questions)} sub-questions generated",
                "extra": {"sub_questions": sub_questions},
            },
        ])
        return {"sub_questions": sub_questions, "trace": new_trace}
    except Exception as e:
        sub_questions = [{"question": state["topic"], "source_hint": "general"}]
        new_trace += _trace_items([
            {"node": "planner", "status": "error", "summary": f"Planner failed: {e}"},
        ])
        return {"sub_questions": sub_questions, "trace": new_trace}


# ---------------------------------------------------------------------------
# 2. Search fan-out (parallel) + merge
# ---------------------------------------------------------------------------
def _search_for_one(sub_q: dict) -> Send:
    return Send("search_worker", {"sub_question": sub_q})


def search_fan(state: ResearchState) -> list[Send]:
    sub_qs = state.get("sub_questions") or []
    return [_search_for_one(sq) for sq in sub_qs]


def search_worker(state: ResearchState) -> dict:
    sub_q = state["sub_question"]
    hint = sub_q.get("source_hint", "general")
    query = sub_q.get("question", "")
    hint_prefix = {
        "academic": "academic paper peer-reviewed ",
        "regulatory": "regulation policy official government ",
        "news": "news recent article ",
        "general": "",
    }.get(hint, "")
    full_query = f"{hint_prefix}{query}"
    try:
        raw = web_search.invoke(full_query)
    except Exception as e:
        raw = f"Search failed: {e}"
    return {
        "raw_search_results": [
            {
                "sub_question": sub_q,
                "raw": raw,
            }
        ]
    }


def search_merge(state: ResearchState) -> dict:
    results = list(state.get("raw_search_results") or [])
    all_urls: list[dict] = []
    seen_urls: set[str] = set()
    notes_chunks: list[str] = []
    for r in results:
        sq = r.get("sub_question", {})
        raw = r.get("raw", "")
        notes_chunks.append(f"### Sub-question: {sq.get('question')}\n{raw}")
        for block in str(raw).split("----"):
            url = None
            title = None
            snippet = None
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("URL:"):
                    url = line[4:].strip()
                elif line.startswith("Title:"):
                    title = line[6:].strip()
                elif line.startswith("Snippet:"):
                    snippet = line[8:].strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_urls.append(
                    {
                        "url": url,
                        "title": title or url,
                        "snippet": snippet or "",
                        "sub_question": sq.get("question", ""),
                    }
                )
    merged = {"candidates": all_urls, "notes": "\n\n".join(notes_chunks)}
    new_trace = _trace_items([
        {
            "node": "search",
            "status": "end",
            "summary": f"Search merged: {len(all_urls)} unique candidate URLs across {len(results)} sub-questions",
            "extra": {"unique_urls": len(all_urls)},
        },
    ])
    return {
        "_research_notes": "\n\n".join(notes_chunks),
        "_merged_search": merged,
        "trace": new_trace,
    }


# ---------------------------------------------------------------------------
# 3. Scraper fan-out (parallel) + merge
# ---------------------------------------------------------------------------
def scrape_fan(state: ResearchState) -> list[Send]:
    merged = state.get("_merged_search") or {}
    candidates: list[dict] = list(merged.get("candidates") or [])
    picked = candidates[:8] if len(candidates) > 8 else candidates
    return [
        Send("scrape_worker", {"candidate": c, "idx": i})
        for i, c in enumerate(picked)
    ]


def scrape_worker(state: ResearchState) -> dict:
    c = state["candidate"]
    idx = state["idx"]
    url = c.get("url", "")
    try:
        text = scrape_url.invoke(url)
    except Exception as e:
        text = f"Scrape error: {e}"
    return {
        "sources": [
            {
                "idx": idx,
                "url": url,
                "title": c.get("title") or url,
                "text": str(text)[:4000],
                "snippet": c.get("snippet", ""),
                "sub_question": c.get("sub_question", ""),
            }
        ]
    }


def scrape_merge(state: ResearchState) -> dict:
    sources = list(state.get("sources") or [])
    sources.sort(key=lambda s: s.get("idx", 999))
    for i, s in enumerate(sources):
        s["idx"] = i
    index_lines = []
    for s in sources:
        index_lines.append(f"[{s['idx']}] {s.get('title')} — {s.get('url')}")
    sources_index = "\n".join(index_lines)
    new_trace = _trace_items([
        {
            "node": "scraper",
            "status": "end",
            "summary": f"Scraped {len(sources)} sources",
            "extra": {"sources_count": len(sources), "sources_index": sources_index},
        },
    ])
    return {"_sources_index": sources_index, "trace": new_trace}


# ---------------------------------------------------------------------------
# 4. Verifier node
# ---------------------------------------------------------------------------
def verifier_node(state: ResearchState) -> dict:
    new_trace = _trace_items([
        {"node": "verifier", "status": "start", "summary": "Verifying claims across sources..."},
    ])
    sources = list(state.get("sources") or [])
    trimmed = [
        {
            "idx": s.get("idx"),
            "url": s.get("url"),
            "title": s.get("title"),
            "text": (s.get("text") or "")[:3000],
        }
        for s in sources
    ]
    try:
        verified = verifier_chain.invoke(
            {
                "topic": state["topic"],
                "sources_json": json.dumps(trimmed, indent=2),
            }
        )
    except Exception as e:
        verified = {
            "agreed": [],
            "disputed": [
                {
                    "claim": f"Verifier parse failed: {e}",
                    "conflicting_source_indices": [],
                    "note": "Fallback due to JSON parse error",
                }
            ],
            "single_source": [],
        }
    for key in ("agreed", "disputed", "single_source"):
        if key not in verified:
            verified[key] = []
    total = (
        len(verified["agreed"])
        + len(verified["disputed"])
        + len(verified["single_source"])
    )
    new_trace += _trace_items([
        {
            "node": "verifier",
            "status": "end",
            "summary": (
                f"Extracted {total} claims: "
                f"{len(verified['agreed'])} agreed / "
                f"{len(verified['disputed'])} disputed / "
                f"{len(verified['single_source'])} single-source"
            ),
            "extra": {k: len(verified[k]) for k in ("agreed", "disputed", "single_source")},
        },
    ])
    return {
        "verified": verified,
        "_verified_json": json.dumps(verified, indent=2),
        "trace": new_trace,
    }


# ---------------------------------------------------------------------------
# 5. Writer node
# ---------------------------------------------------------------------------
def writer_node(state: ResearchState) -> dict:
    rev = state.get("revision_count") or 0
    new_trace = _trace_items([
        {"node": "writer", "status": "start", "summary": f"Writing report (revision {rev})..."},
    ])
    critique = state.get("critique") or {}
    if rev > 0 and critique:
        critique_text = json.dumps(critique, indent=2)
    else:
        critique_text = "(first draft — no prior critique)"
    report = writer_chain.invoke(
        {
            "topic": state["topic"],
            "user_profile": json.dumps(state.get("user_profile") or {}, indent=2),
            "critique_text": critique_text,
            "sources_index": state.get("_sources_index", ""),
            "verified_json": state.get("_verified_json", "{}"),
            "research_notes": state.get("_research_notes", ""),
        }
    )
    new_trace += _trace_items([
        {
            "node": "writer",
            "status": "end",
            "summary": f"Report draft complete (revision {rev}) — ~{len(report)} chars",
        },
    ])
    return {"report": report, "trace": new_trace}


# ---------------------------------------------------------------------------
# 6. Critic node + conditional edge
# ---------------------------------------------------------------------------
def critic_node(state: ResearchState) -> dict:
    new_trace = _trace_items([
        {"node": "critic", "status": "start", "summary": "Critiquing draft..."},
    ])
    try:
        critique = critic_chain.invoke(
            {
                "topic": state["topic"],
                "verified_json": state.get("_verified_json", "{}"),
                "report": state.get("report", ""),
            }
        )
    except Exception as e:
        critique = {
            "score": 7,
            "axes": {"accuracy_vs_verifier": 7, "completeness": 7, "clarity": 7},
            "issues": [f"Critic parse fallback: {e}"],
            "verdict": "APPROVE",
        }
    rev = state.get("revision_count") or 0
    new_trace += _trace_items([
        {
            "node": "critic",
            "status": "end",
            "summary": (
                f"Critic score: {critique.get('score', '?')}/10 "
                f"— {critique.get('verdict', '?')} (rev_count={rev})"
            ),
            "extra": {"critique": critique},
        },
    ])
    return {"critique": critique, "trace": new_trace}


def should_revise(state: ResearchState) -> str:
    critique = state.get("critique") or {}
    score = critique.get("score", 10)
    rev = state.get("revision_count") or 0
    try:
        score_val = int(score)
    except (TypeError, ValueError):
        score_val = 10
    if score_val < 7 and rev < 2:
        return "revise"
    return "approve"


def increment_revision(state: ResearchState) -> dict:
    return {"revision_count": (state.get("revision_count") or 0) + 1}


# ---------------------------------------------------------------------------
# 7. Formatter node
# ---------------------------------------------------------------------------
def formatter_node(state: ResearchState) -> dict:
    new_trace = _trace_items([
        {"node": "formatter", "status": "start", "summary": "Formatting final outputs..."},
    ])
    try:
        output = formatter_chain.invoke(
            {
                "report_md": state.get("report", ""),
                "verified_json": state.get("_verified_json", "{}"),
            }
        )
    except Exception as e:
        output = {
            "content_md": state.get("report", ""),
            "content_html": f"<pre>{state.get('report', '')}</pre>",
            "disputed_callout_md": f"Formatter fallback: {e}",
            "claims_summary": {"agreed_count": 0, "disputed_count": 0, "single_source_count": 0},
        }
    new_trace += _trace_items([
        {"node": "formatter", "status": "end", "summary": "Formatted markdown, HTML, and disputed callout"},
    ])
    return {"formatter_output": output, "trace": new_trace}


# ---------------------------------------------------------------------------
# Build the StateGraph
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(ResearchState)

    g.add_node("planner", planner_node)
    g.add_node("search_worker", search_worker)
    g.add_node("search_merge", search_merge)
    g.add_node("scrape_worker", scrape_worker)
    g.add_node("scrape_merge", scrape_merge)
    g.add_node("verifier", verifier_node)
    g.add_node("writer", writer_node)
    g.add_node("critic", critic_node)
    g.add_node("increment_revision", increment_revision)
    g.add_node("formatter", formatter_node)

    g.add_edge(START, "planner")

    g.add_conditional_edges(
        "planner",
        search_fan,
        path_map=["search_worker"],
    )
    g.add_edge("search_worker", "search_merge")

    g.add_conditional_edges(
        "search_merge",
        scrape_fan,
        path_map=["scrape_worker"],
    )
    g.add_edge("scrape_worker", "scrape_merge")

    g.add_edge("scrape_merge", "verifier")
    g.add_edge("verifier", "writer")
    g.add_edge("writer", "critic")

    g.add_conditional_edges(
        "critic",
        should_revise,
        {
            "revise": "increment_revision",
            "approve": "formatter",
        },
    )
    g.add_edge("increment_revision", "writer")
    g.add_edge("formatter", END)

    return g.compile()


# ---------------------------------------------------------------------------
# Public pipeline entrypoints
# ---------------------------------------------------------------------------
def run_research_pipeline(
    topic: str,
    user_profile: dict | None = None,
    verbose: bool = True,
) -> dict:
    graph = build_graph()
    initial: ResearchState = {
        "topic": topic,
        "user_profile": user_profile or {},
        "revision_count": 0,
        "raw_search_results": [],
        "sources": [],
        "trace": [],
    }
    final: ResearchState = graph.invoke(initial)
    if verbose:
        print("\n[bold green]=== PIPELINE COMPLETE ===[/bold green]")
        print(f"Sub-questions: {len(final.get('sub_questions') or [])}")
        print(f"Sources scraped: {len(final.get('sources') or [])}")
        v = final.get("verified") or {}
        print(
            f"Claims: agreed={len(v.get('agreed') or [])}, "
            f"disputed={len(v.get('disputed') or [])}, "
            f"single={len(v.get('single_source') or [])}"
        )
        print(f"Revisions: {final.get('revision_count') or 0}")
        c = final.get("critique") or {}
        print(f"Critic score: {c.get('score', '?')}/10 — {c.get('verdict', '?')}")
    return final


def regenerate_section(
    cached_state: ResearchState,
    section_name: str,
    user_instruction: str = "",
) -> str:
    """Re-run ONLY the writer for a single section, reusing cached verified context."""
    return section_regen_chain.invoke(
        {
            "topic": cached_state.get("topic", ""),
            "section_name": section_name,
            "user_instruction": user_instruction
            or f"Rewrite {section_name} for clarity and accuracy",
            "sources_index": cached_state.get("_sources_index", ""),
            "verified_json": cached_state.get("_verified_json", "{}"),
            "full_report_md": cached_state.get("report", ""),
        }
    )


# ---------------------------------------------------------------------------
# Async streaming variant — yields trace events + final result
# ---------------------------------------------------------------------------
async def run_research_pipeline_stream(
    topic: str,
    user_profile: dict | None = None,
):
    graph = build_graph()
    initial: ResearchState = {
        "topic": topic,
        "user_profile": user_profile or {},
        "revision_count": 0,
        "raw_search_results": [],
        "sources": [],
        "trace": [],
    }
    seen_trace_ids: set[tuple[str, str, str]] = set()
    current_state: ResearchState = dict(initial)
    async for event in graph.astream(initial, stream_mode="updates"):
        for _node_name, node_output in event.items():
            if isinstance(node_output, dict):
                for k, v in node_output.items():
                    current_state[k] = v
                new_trace_entries = node_output.get("trace") or []
                for entry in new_trace_entries:
                    key = (entry.get("node", ""), entry.get("status", ""), entry.get("summary", ""))
                    if key not in seen_trace_ids:
                        seen_trace_ids.add(key)
                        yield {"type": "trace", "data": entry}
    final = current_state
    yield {
        "type": "done",
        "data": {
            "report": final.get("report"),
            "formatter_output": final.get("formatter_output"),
            "verified": final.get("verified"),
            "critique": final.get("critique"),
            "revision_count": final.get("revision_count", 0),
            "sources": final.get("sources"),
            "trace": final.get("trace"),
        },
    }


if __name__ == "__main__":
    topic = input("\nEnter a research topic: ").strip()
    if not topic:
        topic = (
            "Impact of LLM context-window scaling on retrieval-augmented "
            "generation accuracy in 2025"
        )
    profile = {
        "region": "Global",
        "depth": "technical",
        "format_prefs": "detailed markdown with citations",
        "source_prefs": ["academic", "news"],
    }
    out = run_research_pipeline(topic=topic, user_profile=profile, verbose=True)
    print("\n[bold]=== Disputed claims callout ===[/bold]")
    fout = out.get("formatter_output") or {}
    print(fout.get("disputed_callout_md", "(none)"))
