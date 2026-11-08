"""The research graph.

router -> planner -> search -> read -> extract -> verify -> research_review
   research_review loops back to search (gap finder, at most 2 rounds; then one
   devil's-advocate round in deep mode) or moves on to the analyst.
analyst -> writer -> draft_review -> (writer again, at most N revisions) -> finalize

Progress is reported through ctx.emit as it happens, so the UI trace is live
even while a node is still working.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

import markdown as md_lib
from langgraph.graph import END, START, StateGraph

from . import agents
from .credibility import score_source
from .evidence import (
    CITE,
    audit_citations,
    centroid,
    cluster,
    confidence,
    find_duplicates,
    lexical_vectors,
    locate_quote,
    numbers_in,
    parse_citation,
    tokens,
)
from .llm import LLMClient, LLMError
from .reader import read
from .search import SearchContext, domain_of, normalize_url, run_searcher, search_available

log = logging.getLogger("nodexm.pipeline")

EMBED_THRESHOLD = 0.84
LEXICAL_THRESHOLD = 0.45

BUDGETS: dict[str, dict[str, Any]] = {
    "quick": dict(max_queries=5, max_sources=8, first_round=8, later_round=0, gap_rounds=0, challenge=False,
                  revisions=1, claims_per_source=4, text_chars=5000, batch=4, figures=False),
    "deep": dict(max_queries=10, max_sources=24, first_round=14, later_round=6, gap_rounds=2, challenge=True,
                 revisions=2, claims_per_source=6, text_chars=7000, batch=3, figures=True),
}


class RunCancelled(Exception):
    pass


@dataclass
class RunContext:
    run_id: str
    llm: LLMClient
    search: SearchContext
    emit: Callable[..., None] = lambda *a, **k: None
    cancelled: Callable[[], bool] = lambda: False
    warnings: list[str] = field(default_factory=list)

    def warn(self, node: str, message: str) -> None:
        self.warnings.append(message)
        self.emit(node, "warning", message)


class State(TypedDict, total=False):
    question: str
    context: str
    profile: dict
    requested_mode: str
    allow_deep: bool
    mode: str
    standalone: str
    plan: list[dict]
    queries: list[dict]
    queries_run: list[str]
    candidates: list[dict]
    sources: list[dict]
    extracted_upto: int
    claims: list[dict]
    vectors: dict
    vector_method: str
    verdict_cache: dict
    clusters: list[dict]
    round: int
    challenge_done: bool
    next: str
    figures: list[dict]
    report: str
    revision: int
    feedback: str
    critique: dict | None
    audit: dict
    report_md: str
    report_html: str


def _ctx(config) -> RunContext:
    ctx: RunContext = config["configurable"]["ctx"]
    if ctx.cancelled():
        raise RunCancelled()
    return ctx


def _budget(state: State) -> dict:
    return BUDGETS[state.get("mode") or "quick"]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------
# 1. Router
# ---------------------------------------------------------------------------
def router_node(state: State, config) -> dict:
    ctx = _ctx(config)
    ctx.emit("router", "start", "Reading the question")
    question, context = state["question"], state.get("context", "")
    requested, allow_deep = state.get("requested_mode", "auto"), state.get("allow_deep", True)
    if requested == "deep" and not allow_deep:
        ctx.warn("router", "No Deep runs left, so this runs in Quick mode.")
        requested = "quick"

    mode, standalone, reason = (requested if requested in ("quick", "deep") else "quick"), question, "Chosen by you"
    if requested == "auto" or context:
        try:
            out = agents.route(ctx.llm, question, context, allow_deep)
            standalone = out.standalone_question.strip() or question
            if requested == "auto":
                mode, reason = out.mode, out.reason
        except LLMError as e:
            ctx.warn("router", f"Router unavailable, using Quick mode ({str(e)[:80]})")
    ctx.emit("router", "end", f"{mode.title()} mode. {reason}", {"mode": mode, "question": standalone})
    return {"mode": mode, "standalone": standalone, "round": 0, "revision": 0, "challenge_done": False,
            "sources": [], "claims": [], "clusters": [], "queries_run": [], "extracted_upto": 0,
            "vectors": {}, "verdict_cache": {}, "vector_method": "embedding"}


# ---------------------------------------------------------------------------
# 2. Planner
# ---------------------------------------------------------------------------
def planner_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    question = state["standalone"]
    ctx.emit("planner", "start", "Breaking the question into sub-questions")
    try:
        sub_questions = [sq.model_dump() for sq in agents.plan(ctx.llm, question, state["mode"], state.get("profile", {})).sub_questions]
    except LLMError as e:
        ctx.warn("planner", f"Planner unavailable, searching the question directly ({str(e)[:80]})")
        sub_questions = []
    if not sub_questions:
        sub_questions = [{"question": question, "searchers": ["web", "academic"] if state["mode"] == "deep" else ["web"]}]
    plan = [{"id": f"Q{i + 1}", **sq} for i, sq in enumerate(sub_questions)]
    queries = [{"query": sq["question"], "searcher": s, "sub_q": sq["question"], "phase": "research"}
               for sq in plan for s in sq["searchers"]][: budget["max_queries"]]
    ctx.emit("planner", "end", f"{_plural(len(plan), 'sub-question')}, {_plural(len(queries), 'search')}",
             {"sub_questions": plan})
    return {"plan": plan, "queries": queries}


# ---------------------------------------------------------------------------
# 3. Specialist searchers
# ---------------------------------------------------------------------------
def search_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    queries = state.get("queries") or []
    phase = queries[0]["phase"] if queries else "research"
    node = "devils_advocate" if phase == "challenge" else "searcher"
    ctx.emit(node, "start", f"Running {_plural(len(queries), 'search')}",
             {"queries": [f"{q['searcher']}: {q['query']}" for q in queries]})
    if not search_available(ctx.search.tavily_key) and not state.get("queries_run"):
        ctx.warn("searcher", "Web search is off: add TAVILY_API_KEY or SEARXNG_URL. Using free academic, "
                             "regulatory and news APIs only.")

    def one(q: dict):
        return q, run_searcher(q["searcher"], q["query"], ctx.search)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, queries))

    known = {normalize_url(s["url"]) for s in state.get("sources", [])}
    per_query, counts = [], Counter()
    for q, hits in results:
        fresh = []
        for h in hits:
            n = normalize_url(h.url)
            if n in known:
                continue
            known.add(n)
            d = h.to_dict()
            d.update(sub_q=q["sub_q"], phase=q["phase"])
            fresh.append(d)
        counts[q["searcher"]] += len(hits)
        per_query.append(fresh)

    # Round-robin across queries so one sub-question cannot take every slot.
    limit = budget["first_round"] if state.get("round", 0) == 0 else budget["later_round"]
    if phase == "challenge":
        limit = 5
    limit = max(0, min(limit, budget["max_sources"] - len(state.get("sources", []))))
    candidates: list[dict] = []
    while len(candidates) < limit and any(per_query):
        for bucket in per_query:
            if bucket and len(candidates) < limit:
                candidates.append(bucket.pop(0))

    summary = ", ".join(f"{k} {v}" for k, v in counts.items()) or "no results"
    ctx.emit(node, "end", f"Found {sum(counts.values())} results ({summary}); reading {len(candidates)}",
             {"by_searcher": dict(counts)})
    return {"candidates": candidates, "queries": [],
            "queries_run": state.get("queries_run", []) + [q["query"] for q in queries]}


# ---------------------------------------------------------------------------
# 4a. Reader + 5. Credibility scorer
# ---------------------------------------------------------------------------
def read_node(state: State, config) -> dict:
    ctx = _ctx(config)
    candidates = state.get("candidates") or []
    sources = list(state.get("sources", []))
    if not candidates:
        return {"candidates": []}
    ctx.emit("reader", "start", f"Reading {_plural(len(candidates), 'source')} in full")

    def one(hit: dict) -> dict | None:
        page = read(hit["url"])
        text = page.text if page.ok else ""
        if len(hit.get("text") or "") >= 200 and len(text) < len(hit["text"]):
            text = hit["text"]  # abstracts and data series from the API
        if len(text) < 200:
            return None
        return {"url": hit["url"], "title": (hit.get("title") or page.title or hit["url"])[:300],
                "domain": domain_of(hit["url"]), "source_type": hit.get("source_type", "web"),
                "searcher": hit.get("searcher", "web"), "published": hit.get("published") or page.published,
                "snippet": hit.get("snippet", ""), "text": text, "sub_question": hit.get("sub_q", ""),
                "phase": hit.get("phase", "research"), "extra": hit.get("extra") or {}}

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = [p for p in pool.map(one, candidates) if p]
    for p in pages:
        p["idx"] = len(sources)
        p["credibility"], p["credibility_reasons"] = score_source(
            p["url"], p["source_type"], p["published"], len(p["text"]), p.pop("extra"))
        p["duplicate_of"] = None
        sources.append(p)
    ctx.emit("reader", "end", f"Read {len(pages)} of {len(candidates)}"
             + (f" ({len(candidates) - len(pages)} unreadable or blocked)" if len(pages) < len(candidates) else ""))

    dups = find_duplicates({s["idx"]: s["text"] for s in sources}, {s["idx"]: s["credibility"] for s in sources})
    for s in sources:
        s["duplicate_of"] = dups.get(s["idx"])
    kinds = Counter(r[0] for s in pages for r in s["credibility_reasons"][:1])
    ctx.emit("credibility", "end",
             f"Scored {_plural(len(pages), 'source')}; {_plural(len(dups), 'copy')} of other sources set aside",
             {"top_reasons": dict(kinds.most_common(4)),
              "duplicates": {str(k): v for k, v in dups.items()}})
    return {"sources": sources, "candidates": []}


# ---------------------------------------------------------------------------
# 4b. Extractor
# ---------------------------------------------------------------------------
def focus_text(text: str, query: str, limit: int) -> str:
    """Keep the paragraphs most relevant to the query, in their original order."""
    if len(text) <= limit:
        return text
    paras = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    if len(paras) < 3:
        return text[:limit]
    q = set(tokens(query))
    scored = sorted(range(len(paras)), key=lambda i: (-(len(q & set(tokens(paras[i])))
                                                        + (0.5 if numbers_in(paras[i]) else 0)), i))
    keep, total = {0}, len(paras[0])
    for i in scored:
        if total + len(paras[i]) > limit:
            continue
        keep.add(i)
        total += len(paras[i])
    return "\n".join(paras[i] for i in sorted(keep))[:limit]


def extract_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    sources = state.get("sources", [])
    start = state.get("extracted_upto", 0)
    todo = [s for s in sources[start:] if s.get("duplicate_of") is None]
    if not todo:
        return {"extracted_upto": len(sources)}
    ctx.emit("extractor", "start", f"Extracting claims with exact quotes from {_plural(len(todo), 'source')}")
    question = state["standalone"]
    batches = [todo[i:i + budget["batch"]] for i in range(0, len(todo), budget["batch"])]

    def run(batch: list[dict]):
        payload = [{"number": s["idx"] + 1, "title": s["title"],
                    "text": focus_text(s["text"], f"{question} {s['sub_question']}", budget["text_chars"])}
                   for s in batch]
        try:
            return agents.extract(ctx.llm, question, payload, budget["claims_per_source"]).sources
        except LLMError as e:
            ctx.warn("extractor", f"Could not extract from {_plural(len(batch), 'source')}: {str(e)[:80]}")
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [sc for out in pool.map(run, batches) for sc in out]

    by_idx = {s["idx"]: s for s in todo}
    claims = list(state.get("claims", []))
    kept = dropped = 0
    for sc in results:
        src = by_idx.get(sc.source_number - 1)
        if not src:
            continue
        for k, c in enumerate(sc.claims[: budget["claims_per_source"]]):
            begin, end, score = locate_quote(src["text"], c.quote)
            if begin < 0 or not c.claim.strip():
                dropped += 1
                continue
            claims.append({"id": f"s{src['idx']}c{k}", "source_idx": src["idx"], "text": c.claim.strip(),
                           "quote": src["text"][begin:end], "start": begin, "end": end, "match": score,
                           "kind": c.kind, "sub_question": src["sub_question"]})
            kept += 1
    ctx.emit("extractor", "end",
             f"Kept {_plural(kept, 'claim')} with quotes found in the source"
             + (f"; dropped {dropped} whose quote was not in the text" if dropped else ""),
             {"kept": kept, "dropped": dropped})
    return {"claims": claims, "extracted_upto": len(sources)}


# ---------------------------------------------------------------------------
# 6. Clusterer + 7. Verifier
# ---------------------------------------------------------------------------
def _fallback_verdict(members: list[dict]) -> dict:
    """Used only if the verifier model fails: different number sets mean a dispute."""
    number_sets = {frozenset(numbers_in(m["text"])) for m in members if numbers_in(m["text"])}
    disputed = len(number_sets) > 1
    return {"canonical_claim": members[0]["text"], "status": "disputed" if disputed else "agreed",
            "note": "Automatic check: the sources give different figures." if disputed else "",
            "stances": {m["id"]: "supports" for m in members}}


def verify_node(state: State, config) -> dict:
    ctx = _ctx(config)
    sources = {s["idx"]: s for s in state.get("sources", [])}
    claims = [c for c in state.get("claims", []) if sources[c["source_idx"]].get("duplicate_of") is None]
    if not claims:
        return {"clusters": []}
    ctx.emit("clusterer", "start", f"Grouping {_plural(len(claims), 'claim')} that state the same fact")

    vectors = dict(state.get("vectors", {}))
    method = state.get("vector_method", "embedding")
    missing = [c for c in claims if c["id"] not in vectors]
    if missing and method == "embedding":
        emb = ctx.llm.embed([c["text"] for c in missing])
        if emb is None:
            method, vectors = "lexical", {}
            ctx.warn("clusterer", "Embeddings unavailable, matching claims by wording instead")
        else:
            vectors.update({c["id"]: v for c, v in zip(missing, emb)})
    if method == "lexical":
        missing = [c for c in claims if c["id"] not in vectors]
        vectors.update({c["id"]: v for c, v in zip(missing, lexical_vectors([c["text"] for c in missing]))})

    groups = cluster([vectors[c["id"]] for c in claims],
                     EMBED_THRESHOLD if method == "embedding" else LEXICAL_THRESHOLD)
    groups = [[claims[i] for i in g] for g in groups]
    multi = [g for g in groups if len({m["source_idx"] for m in g}) > 1]
    ctx.emit("clusterer", "end", f"{_plural(len(groups), 'distinct claim')}, {len(multi)} reported by 2+ sources",
             {"method": method})

    # Verify multi-source clusters, reusing verdicts from earlier rounds when membership is unchanged.
    cache = dict(state.get("verdict_cache", {}))
    pending = [(f"K{i}", g) for i, g in enumerate(multi) if "|".join(sorted(m["id"] for m in g)) not in cache]
    if pending:
        ctx.emit("verifier", "start", f"Checking {_plural(len(pending), 'cluster')} for agreement or conflict")

    def run(batch):
        payload = [{"cluster_id": cid, "statements": [
            {"statement_id": m["id"], "source": m["source_idx"] + 1, "text": m["text"], "quote": m["quote"][:400]}
            for m in g[:8]]} for cid, g in batch]
        try:
            out = agents.verify(ctx.llm, state["standalone"], payload)
            return {v.cluster_id: {"canonical_claim": v.canonical_claim, "status": v.status, "note": v.note,
                                   "stances": {s.statement_id: s.stance for s in v.stances}} for v in out.verdicts}
        except LLMError as e:
            ctx.warn("verifier", f"Verifier unavailable for {_plural(len(batch), 'cluster')}, used a number check "
                                 f"instead ({str(e)[:60]})")
            return {}

    batches = [pending[i:i + 8] for i in range(0, len(pending), 8)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        verdicts = {k: v for out in pool.map(run, batches) for k, v in out.items()}
    for cid, g in pending:
        cache["|".join(sorted(m["id"] for m in g))] = verdicts.get(cid) or _fallback_verdict(g)

    clusters: list[dict] = []

    def add(members: list[dict], verdict: dict | None):
        src_ids = sorted({m["source_idx"] for m in members})
        stances = (verdict or {}).get("stances", {})
        if verdict and len(src_ids) > 1:
            status = verdict["status"]
            text = verdict["canonical_claim"] or members[0]["text"]
            note = verdict.get("note", "")
        else:
            status, text, note = "single_source", max(members, key=lambda m: m.get("match", 0))["text"], ""
        support = {m["source_idx"] for m in members if stances.get(m["id"], "supports") == "supports"}
        against = {m["source_idx"] for m in members if stances.get(m["id"]) == "contradicts"}
        conf = confidence(status, [sources[i]["credibility"] for i in support or set(src_ids)],
                          [sources[i]["credibility"] for i in against])
        clusters.append({
            "text": text, "status": status, "confidence": conf, "note": note, "source_indices": src_ids,
            "sub_questions": sorted({m["sub_question"] for m in members if m["sub_question"]}),
            "embedding": centroid([vectors[m["id"]] for m in members]) if method == "embedding" else None,
            "evidence": [{"claim_id": m["id"], "source_idx": m["source_idx"], "statement": m["text"],
                          "quote": m["quote"], "start": m["start"], "end": m["end"],
                          "stance": stances.get(m["id"], "supports")} for m in members],
        })

    for g in groups:
        if len({m["source_idx"] for m in g}) == 1:
            add(g, None)
            continue
        verdict = cache["|".join(sorted(m["id"] for m in g))]
        unrelated = [m for m in g if verdict["stances"].get(m["id"]) == "unrelated"]
        related = [m for m in g if m not in unrelated]
        if related:
            add(related, verdict)
        for m in unrelated:
            add([m], None)

    order = {"disputed": 0, "agreed": 1, "single_source": 2}
    clusters.sort(key=lambda c: (-len(c["source_indices"]), order[c["status"]], -c["confidence"]))
    for i, c in enumerate(clusters):
        c["key"] = f"C{i + 1}"
    counts = Counter(c["status"] for c in clusters)
    ctx.emit("verifier", "end",
             f"{counts['agreed']} agreed, {counts['disputed']} disputed, {counts['single_source']} single-source",
             dict(counts))
    return {"clusters": clusters, "vectors": vectors, "vector_method": method, "verdict_cache": cache}


# ---------------------------------------------------------------------------
# 8. Gap finder + 9. Devil's advocate
# ---------------------------------------------------------------------------
def research_review_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    rnd = state.get("round", 0)
    room = budget["max_sources"] - len(state.get("sources", []))
    already = {q.lower() for q in state.get("queries_run", [])}

    if rnd < budget["gap_rounds"] and room > 0:
        ctx.emit("gap_finder", "start", "Checking which sub-questions still lack evidence")
        claims_by_sq = defaultdict(list)
        for c in state.get("clusters", []):
            for sq in c["sub_questions"] or [""]:
                claims_by_sq[sq].append(f"[{c['status']}] {c['text']}")
        coverage = [{"sub_question": sq["question"], "verified_claims": len(claims_by_sq[sq["question"]]),
                     "examples": claims_by_sq[sq["question"]][:4]} for sq in state.get("plan", [])]
        try:
            gaps = agents.find_gaps(ctx.llm, state["standalone"], coverage)
            queries = [{"query": q.query, "searcher": q.searcher, "sub_q": q.query, "phase": "research"}
                       for q in gaps.queries if q.query.lower() not in already]
        except LLMError as e:
            ctx.warn("gap_finder", f"Gap finder unavailable ({str(e)[:80]})")
            gaps, queries = None, []
        if gaps and not gaps.sufficient and queries:
            ctx.emit("gap_finder", "end", f"Searching again: {_plural(len(queries), 'follow-up query')}",
                     {"queries": [q["query"] for q in queries], "reasons": [q.reason for q in gaps.queries]})
            return {"queries": queries, "round": rnd + 1, "next": "search"}
        ctx.emit("gap_finder", "end", "Coverage is sufficient")

    if budget["challenge"] and not state.get("challenge_done") and room > 0:
        multi = [c for c in state.get("clusters", []) if c["status"] in ("agreed", "disputed")]
        targets = [f"{c['status'].upper()}: {c['text']}" for c in multi[:5]]
        if targets:
            ctx.emit("devils_advocate", "start", f"Testing {_plural(len(targets), 'key claim')} against counter-evidence")
            try:
                out = agents.challenge(ctx.llm, state["standalone"], targets)
                queries = [{"query": q.query, "searcher": q.searcher, "sub_q": "Counter-evidence", "phase": "challenge"}
                           for q in out.queries if q.query.lower() not in already]
                if queries:
                    return {"queries": queries, "round": rnd + 1, "challenge_done": True, "next": "search"}
            except LLMError as e:
                ctx.warn("devils_advocate", f"Devil's advocate unavailable ({str(e)[:80]})")
    return {"challenge_done": True, "next": "analyst"}


def route_after_research(state: State) -> str:
    return state.get("next", "analyst")


# ---------------------------------------------------------------------------
# 10. Quant analyst
# ---------------------------------------------------------------------------
def analyst_node(state: State, config) -> dict:
    ctx = _ctx(config)
    numeric = [c for c in state.get("clusters", []) if numbers_in(c["text"])][:25]
    if not _budget(state)["figures"] or len(numeric) < 2:
        return {"figures": []}
    ctx.emit("quant_analyst", "start", f"Normalising figures from {_plural(len(numeric), 'claim')}")
    payload = [{"cluster_id": c["key"], "claim": c["text"], "status": c["status"]} for c in numeric]
    try:
        figures = [f.model_dump() for f in agents.figures(ctx.llm, state["standalone"], payload).figures]
    except LLMError as e:
        ctx.warn("quant_analyst", f"Quant analyst unavailable ({str(e)[:80]})")
        return {"figures": []}
    by_key = {c["key"]: c for c in state["clusters"]}
    figures = [f for f in figures if f["cluster_id"] in by_key]
    for f in figures:
        c = by_key[f["cluster_id"]]
        f["status"], f["source_indices"] = c["status"], c["source_indices"]
    groups = defaultdict(list)
    for f in figures:
        groups[(f["label"].lower(), f["unit"].lower())].append(f["value"])
    for f in figures:
        values = groups[(f["label"].lower(), f["unit"].lower())]
        if len(values) > 1 and min(values) != max(values):
            f["range"] = [min(values), max(values)]
    spreads = sum(1 for v in groups.values() if len(v) > 1 and min(v) != max(v))
    ctx.emit("quant_analyst", "end", f"{_plural(len(figures), 'figure')}"
             + (f"; {spreads} measured differently by different sources" if spreads else ""))
    return {"figures": figures}


# ---------------------------------------------------------------------------
# 11. Writer
# ---------------------------------------------------------------------------
def _sources_block(state: State) -> str:
    used = {i for c in state.get("clusters", []) for i in c["source_indices"]}
    lines = []
    for s in state.get("sources", []):
        if s["idx"] in used:
            lines.append(f"[{s['idx'] + 1}] {s['title']} | {s['domain']} | {s['source_type']} | "
                         f"credibility {s['credibility']} | {s.get('published') or 'date unknown'}")
    return "\n".join(lines)


def _claims_block(clusters: list[dict], limit: int = 45) -> str:
    lines = []
    for c in clusters[:limit]:
        cites = ", ".join(str(i + 1) for i in c["source_indices"])
        lines.append(f"{c['key']} [{c['status']}, confidence {c['confidence']}] {c['text']} (sources: {cites})")
        if c["status"] == "disputed":
            for e in c["evidence"]:
                lines.append(f"    source {e['source_idx'] + 1} ({e['stance']}): \"{e['quote'][:220]}\"")
            if c.get("note"):
                lines.append(f"    note: {c['note']}")
    return "\n".join(lines)


def _figures_block(figures: list[dict]) -> str:
    return "\n".join(
        f"- {f['label']}: {f['value']:g} {f['unit']} ({f['period'] or 'period unknown'}) "
        f"[{f['status']}] sources {', '.join(str(i + 1) for i in f['source_indices'])}" for f in figures)


def writer_node(state: State, config) -> dict:
    ctx = _ctx(config)
    clusters = state.get("clusters", [])
    rev = state.get("revision", 0)
    if not clusters:
        ctx.warn("writer", "No verifiable evidence was found, so there is nothing reliable to report.")
        report = ("## Answer\nI could not find enough readable, relevant sources to give a verified answer. "
                  "Try rephrasing the question, adding a region or timeframe, or using Deep mode.")
        return {"report": report}
    ctx.emit("writer", "start", "Writing the answer" if rev == 0 else f"Revising the answer (revision {rev})")
    try:
        report = agents.write(ctx.llm, question=state["standalone"], mode=state["mode"],
                              profile=state.get("profile", {}), claims_block=_claims_block(clusters),
                              sources_block=_sources_block(state), figures_block=_figures_block(state.get("figures", [])),
                              feedback=state.get("feedback", ""))
    except LLMError as e:
        if state.get("report"):
            ctx.warn("writer", f"Revision failed, keeping the previous draft ({str(e)[:80]})")
            return {}
        raise
    report = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", report.strip())
    report = re.split(r"\n#{1,3}\s*Sources\s*\n", report)[0].strip()
    ctx.emit("writer", "end", f"Draft ready, {len(report.split())} words")
    return {"report": report}


# ---------------------------------------------------------------------------
# 12. Critic + 13. Citation auditor
# ---------------------------------------------------------------------------
def draft_review_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    report, clusters = state.get("report", ""), state.get("clusters", [])
    if not clusters:
        return {"next": "finalize", "audit": {"score": 0, "checked": 0, "supported": 0, "issues": []}}

    texts = {s["idx"]: s["text"] for s in state.get("sources", [])}
    quotes = defaultdict(list)
    for c in clusters:
        for e in c["evidence"]:
            quotes[e["source_idx"]].append(e["quote"])
    audit = audit_citations(report, texts, quotes)
    ctx.emit("citation_auditor", "end",
             f"{audit['supported']} of {audit['checked']} cited sentences supported by their sources",
             {"score": audit["score"], "issues": audit["issues"][:8]})

    critique = None
    try:
        critique = agents.critique(ctx.llm, state["standalone"], report, _claims_block(clusters, 30)).model_dump()
        ctx.emit("critic", "end", f"Score {critique['overall']}/10", critique)
    except LLMError as e:
        ctx.warn("critic", f"Critic unavailable, relying on the citation audit ({str(e)[:80]})")

    rev = state.get("revision", 0)
    weak_audit = audit["checked"] >= 3 and audit["score"] < 0.8
    weak_critic = critique is not None and critique["overall"] < 7
    if rev < budget["revisions"] and (weak_audit or weak_critic):
        feedback = []
        if critique:
            feedback += [f"- {i}" for i in critique["issues"]]
        feedback += [f"- {i['problem']}: \"{i['sentence'][:160]}\"" for i in audit["issues"][:6]]
        ctx.emit("critic", "end", "Sending back for revision", {"feedback": feedback})
        return {"critique": critique, "audit": audit, "revision": rev + 1, "feedback": "\n".join(feedback),
                "next": "writer"}
    return {"critique": critique, "audit": audit, "next": "finalize"}


def route_after_draft(state: State) -> str:
    return state.get("next", "finalize")


# ---------------------------------------------------------------------------
# Finalize (code, not an LLM)
# ---------------------------------------------------------------------------
def build_sources_section(report: str, sources: list[dict]) -> str:
    cited = sorted({n for g in CITE.findall(report) for n in parse_citation(g)})
    by_number = {s["idx"] + 1: s for s in sources}
    lines = [f"- **[{n}]** [{by_number[n]['title']}]({by_number[n]['url']}) · {by_number[n]['domain']}"
             for n in cited if n in by_number]
    return "\n\n## Sources\n\n" + "\n".join(lines) if lines else ""


def render_html(report_md: str) -> str:
    return md_lib.markdown(report_md, extensions=["tables", "fenced_code", "sane_lists"])


def finalize_node(state: State, config) -> dict:
    ctx = _ctx(config)
    report = state.get("report", "")
    full = report + build_sources_section(report, state.get("sources", []))
    ctx.emit("finalize", "end", "Answer ready")
    return {"report_md": full, "report_html": render_html(full)}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(State)
    g.add_node("router", router_node)
    g.add_node("planner", planner_node)
    g.add_node("search", search_node)
    g.add_node("read", read_node)
    g.add_node("extract", extract_node)
    g.add_node("verify", verify_node)
    g.add_node("research_review", research_review_node)
    g.add_node("analyst", analyst_node)
    g.add_node("writer", writer_node)
    g.add_node("draft_review", draft_review_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "router")
    g.add_edge("router", "planner")
    g.add_edge("planner", "search")
    g.add_edge("search", "read")
    g.add_edge("read", "extract")
    g.add_edge("extract", "verify")
    g.add_edge("verify", "research_review")
    g.add_conditional_edges("research_review", route_after_research, {"search": "search", "analyst": "analyst"})
    g.add_edge("analyst", "writer")
    g.add_edge("writer", "draft_review")
    g.add_conditional_edges("draft_review", route_after_draft, {"writer": "writer", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def run_pipeline(ctx: RunContext, *, question: str, context: str = "", profile: dict | None = None,
                 requested_mode: str = "auto", allow_deep: bool = True) -> dict:
    """Run the full graph synchronously and return a JSON-serialisable result."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    final: State = _GRAPH.invoke(
        {"question": question, "context": context, "profile": profile or {}, "requested_mode": requested_mode,
         "allow_deep": allow_deep},
        config={"configurable": {"ctx": ctx}, "recursion_limit": 80},
    )
    sources = [{k: v for k, v in s.items() if k != "phase"} for s in final.get("sources", [])]
    return {
        "mode": final.get("mode"),
        "standalone": final.get("standalone"),
        "plan": final.get("plan", []),
        "rounds": final.get("round", 0),
        "queries_run": final.get("queries_run", []),
        "sources": sources,
        "clusters": final.get("clusters", []),
        "figures": final.get("figures", []),
        "report_md": final.get("report_md", ""),
        "report_html": final.get("report_html", ""),
        "critique": final.get("critique"),
        "audit": final.get("audit", {}),
        "revisions": final.get("revision", 0),
        "warnings": list(ctx.warnings),
    }
