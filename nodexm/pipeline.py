"""The research graph.

router -> planner -> search -> read -> extract -> gap_review
   gap_review loops back to search (at most 2 extra rounds in Deep mode) or moves on to verify.
verify -> challenge_review
   challenge_review sends one devil's-advocate round back to search in Deep mode, then the
   new evidence is verified again before moving on to the analyst.
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
    confidence,
    find_duplicates,
    group_claims,
    independence_groups,
    lexical_vectors,
    locate_quote,
    numbers_in,
    pack_batches,
    parse_citation,
    source_origin,
    tokens,
)
from .llm import LLMClient, LLMError
from .reader import read
from .search import SearchContext, domain_of, normalize_url, run_searcher, search_available

log = logging.getLogger("nodexm.pipeline")


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
    verified_ids: list[str]
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
    if n == 1:
        return f"{n} {word}"
    if word.endswith(("s", "sh", "ch", "x")):
        return f"{n} {word}es"
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return f"{n} {word[:-1]}ies"
    return f"{n} {word}s"


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
            "vectors": {}, "verified_ids": [], "vector_method": "embedding"}


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
        given = hit.get("text") or ""
        if len(given) >= 2000:  # the search API already returned the page text (Tavily raw content)
            page, text = None, given
        else:
            page = read(hit["url"])
            text = page.text if page.ok else ""
            if len(given) >= 200 and len(text) < len(given):
                text = given  # abstracts and data series from the API
        if len(text) < 200:
            return None
        return {"url": hit["url"], "title": (hit.get("title") or (page.title if page else "") or hit["url"])[:300],
                "domain": domain_of(hit["url"]), "source_type": hit.get("source_type", "web"),
                "searcher": hit.get("searcher", "web"),
                "published": hit.get("published") or (page.published if page else None),
                "snippet": hit.get("snippet", ""), "text": text, "sub_question": hit.get("sub_q", ""),
                "phase": hit.get("phase", "research"), "extra": hit.get("extra") or {}}

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = [p for p in pool.map(one, candidates) if p]
    for p in pages:
        p["idx"] = len(sources)
        p["credibility"], p["credibility_reasons"] = score_source(
            p["url"], p["source_type"], p["published"], len(p["text"]), p.pop("extra"))
        p["duplicate_of"] = None
        p["origin"] = source_origin(p["text"], p["domain"])
        sources.append(p)
    ctx.emit("reader", "end", f"Read {len(pages)} of {len(candidates)}"
             + (f" ({len(candidates) - len(pages)} unreadable or blocked)" if len(pages) < len(candidates) else ""))

    dups = find_duplicates({s["idx"]: s["text"] for s in sources}, {s["idx"]: s["credibility"] for s in sources})
    for s in sources:
        s["duplicate_of"] = dups.get(s["idx"])
    kinds = Counter(r[0] for s in pages for r in s["credibility_reasons"][:1])
    wires = Counter(p["origin"] for p in pages if p.get("origin"))
    wire_note = (f"; {_plural(sum(wires.values()), 'page')} carry wire stories ("
                 + ", ".join(f"{n} {w}" for w, n in wires.most_common(3)) + ")") if wires else ""
    ctx.emit("credibility", "end",
             f"Scored {_plural(len(pages), 'source')}; {_plural(len(dups), 'copy')} of other sources set aside"
             + wire_note,
             {"top_reasons": dict(kinds.most_common(4)),
              "duplicates": {str(k): v for k, v in dups.items()}, "wire_origins": dict(wires)})
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
VERIFY_BATCH = 60


def _fallback_verdict(members: list[dict]) -> dict:
    """Used only if the verifier model fails: different number sets mean a dispute."""
    number_sets = {frozenset(numbers_in(m["text"])) for m in members if numbers_in(m["text"])}
    disputed = len(number_sets) > 1
    return {"canonical_claim": members[0]["text"], "status": "disputed" if disputed else "agreed",
            "note": "Automatic check: the sources give different figures." if disputed else "",
            "stances": {m["id"]: "supports" for m in members}}


def _claim_vectors(ctx: RunContext, state: State, claims: list[dict]) -> tuple[dict, str]:
    """Embeddings for every claim (kept for batching and the Watch ledger); word vectors as fallback."""
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
    return vectors, method


def verify_node(state: State, config) -> dict:
    """Group claims by the fact they state and decide agreed / disputed / single-source.

    The verifier model does the grouping: embeddings rate every claim on a narrow topic as
    near-identical, so they cannot tell "415 TWh in 2024" from "945 TWh by 2030". Embeddings
    are only used to split very large runs into batches and as the offline fallback.
    """
    ctx = _ctx(config)
    sources = {s["idx"]: s for s in state.get("sources", [])}
    claims = [c for c in state.get("claims", []) if sources[c["source_idx"]].get("duplicate_of") is None]
    if not claims:
        return {"clusters": []}
    claim_ids = sorted(c["id"] for c in claims)
    if claim_ids == state.get("verified_ids"):
        return {}  # nothing new since the last verification
    ctx.emit("verifier", "start", f"Grouping {_plural(len(claims), 'claim')} by fact and checking agreement")

    vectors, method = _claim_vectors(ctx, state, claims)
    lexical = method == "lexical"
    batches = [[claims[i] for i in b] for b in pack_batches([vectors[c["id"]] for c in claims], VERIFY_BATCH, lexical)]

    def run(batch: list[dict]):
        payload = [{"id": m["id"], "source": m["source_idx"] + 1, "text": m["text"]} for m in batch]
        try:
            return batch, agents.group_and_verify(ctx.llm, state["standalone"], payload).groups
        except LLMError as e:
            ctx.warn("verifier", f"Verifier unavailable, grouped claims automatically instead ({str(e)[:60]})")
            return batch, None

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, batches))

    grouped: list[tuple[list[dict], dict | None]] = []
    for batch, groups in results:
        if groups is None:
            for g in group_claims([m["text"] for m in batch], [vectors[m["id"]] for m in batch], lexical):
                members = [batch[i] for i in g]
                grouped.append((members, _fallback_verdict(members)))
            continue
        by_id, seen = {m["id"]: m for m in batch}, set()
        for fg in groups:
            members = [by_id[i] for i in dict.fromkeys(fg.statement_ids) if i in by_id and i not in seen]
            if not members:
                continue
            seen.update(m["id"] for m in members)
            against = set(fg.contradicting_ids)
            grouped.append((members, {"canonical_claim": fg.canonical_claim, "status": fg.status, "note": fg.note,
                                      "stances": {m["id"]: "contradicts" if m["id"] in against else "supports"
                                                  for m in members}}))
        grouped += [([m], None) for m in batch if m["id"] not in seen]  # anything the model left out

    clusters: list[dict] = []
    inflated = 0  # claims that looked agreed but rest on one original source
    for members, verdict in grouped:
        src_ids = sorted({m["source_idx"] for m in members})
        stances = (verdict or {}).get("stances", {})
        if verdict and len(src_ids) > 1:
            status, note = verdict["status"], verdict.get("note", "")
        else:
            status, note = "single_source", ""
        text = (verdict or {}).get("canonical_claim") or max(members, key=lambda m: m.get("match", 0))["text"]
        support = {m["source_idx"] for m in members if stances.get(m["id"], "supports") == "supports"}
        against = {m["source_idx"] for m in members if stances.get(m["id"]) == "contradicts"}
        if status == "disputed" and not against and len(src_ids) > 1:
            against = set(src_ids[1:])  # the model flagged a dispute without naming a side
            support = {src_ids[0]}

        # Independence: copies of one wire story, word-for-word repeats and sources citing each other
        # count once. "Agreed" needs at least two independent groups behind the claim.
        groups, reasons = independence_groups(members, sources)
        support_groups = [g for g in groups if set(g) & (support or set(src_ids))]
        against_groups = [g for g in groups if set(g) & against]
        if status == "agreed" and len(support_groups) < 2:
            status = "single_source"
            origin = next((sources[i].get("origin") for i in src_ids if sources[i].get("origin")), None)
            note = (f"Reported by {len(src_ids)} sites, but they trace back to one original source"
                    + (f" ({origin})" if origin else "") + ": " + "; ".join(reasons) + ".")
            inflated += 1

        def best(group_list):
            return [max(sources[i]["credibility"] for i in g) for g in group_list]

        conf = confidence(status, best(support_groups) or [max(sources[i]["credibility"] for i in src_ids)],
                          best(against_groups))
        clusters.append({
            "text": text, "status": status, "confidence": conf, "note": note, "source_indices": src_ids,
            "independence": {"independent": len(support_groups) or 1, "sites": len(src_ids),
                             "groups": [sorted(g) for g in groups], "reasons": reasons},
            "sub_questions": sorted({m["sub_question"] for m in members if m["sub_question"]}),
            "embedding": centroid([vectors[m["id"]] for m in members]),
            "evidence": [{"claim_id": m["id"], "source_idx": m["source_idx"], "statement": m["text"],
                          "quote": m["quote"], "start": m["start"], "end": m["end"],
                          "stance": stances.get(m["id"], "supports")} for m in members],
        })

    order = {"disputed": 0, "agreed": 1, "single_source": 2}
    clusters.sort(key=lambda c: (-len(c["source_indices"]), order[c["status"]], -c["confidence"]))
    for i, c in enumerate(clusters):
        c["key"] = f"C{i + 1}"
    counts = Counter(c["status"] for c in clusters)
    multi = sum(1 for c in clusters if len(c["source_indices"]) > 1)
    ctx.emit("verifier", "end",
             f"{_plural(len(clusters), 'distinct fact')} ({multi} reported by 2+ sources): {counts['agreed']} agreed, "
             f"{counts['disputed']} disputed, {counts['single_source']} single-source"
             + (f"; {_plural(inflated, 'claim')} looked agreed but rest on one original source" if inflated else ""),
             {**dict(counts), "method": method, "copy_inflated": inflated})
    return {"clusters": clusters, "vectors": vectors, "vector_method": method, "verified_ids": claim_ids}


# ---------------------------------------------------------------------------
# 8. Gap finder (works on raw claims, so it needs no verification per round)
# ---------------------------------------------------------------------------
def gap_review_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    rnd = state.get("round", 0)
    room = budget["max_sources"] - len(state.get("sources", []))
    if state.get("challenge_done") or rnd >= budget["gap_rounds"] or room <= 0:
        return {"next": "verify"}

    ctx.emit("gap_finder", "start", "Checking which sub-questions still lack evidence")
    sources = {s["idx"]: s for s in state.get("sources", [])}
    by_sq = defaultdict(list)
    for c in state.get("claims", []):
        if sources[c["source_idx"]].get("duplicate_of") is None:
            by_sq[c["sub_question"] or "(other)"].append(c["text"])
    planned = [sq["question"] for sq in state.get("plan", [])]
    coverage = [{"sub_question": q, "claims_found": len(by_sq[q]), "examples": by_sq[q][:4]}
                for q in planned + [q for q in by_sq if q not in planned]]
    already = {q.lower() for q in state.get("queries_run", [])}
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
    return {"next": "verify"}


def route_after_gaps(state: State) -> str:
    return state.get("next", "verify")


# ---------------------------------------------------------------------------
# 9. Devil's advocate (needs verified claims, runs once in Deep mode)
# ---------------------------------------------------------------------------
def challenge_review_node(state: State, config) -> dict:
    ctx = _ctx(config)
    budget = _budget(state)
    room = budget["max_sources"] - len(state.get("sources", []))
    if budget["challenge"] and not state.get("challenge_done") and room > 0:
        multi = [c for c in state.get("clusters", []) if c["status"] in ("agreed", "disputed")]
        targets = [f"{c['status'].upper()}: {c['text']}" for c in multi[:5]]
        if targets:
            ctx.emit("devils_advocate", "start", f"Testing {_plural(len(targets), 'key claim')} against counter-evidence")
            already = {q.lower() for q in state.get("queries_run", [])}
            try:
                out = agents.challenge(ctx.llm, state["standalone"], targets)
                queries = [{"query": q.query, "searcher": q.searcher, "sub_q": "Counter-evidence", "phase": "challenge"}
                           for q in out.queries if q.query.lower() not in already]
                if queries:
                    return {"queries": queries, "round": state.get("round", 0) + 1, "challenge_done": True,
                            "next": "search"}
            except LLMError as e:
                ctx.warn("devils_advocate", f"Devil's advocate unavailable ({str(e)[:80]})")
    return {"challenge_done": True, "next": "analyst"}


def route_after_challenge(state: State) -> str:
    return state.get("next", "analyst")


# ---------------------------------------------------------------------------
# 10. Quant analyst
# ---------------------------------------------------------------------------
def _period_key(period: str) -> str:
    years = sorted(set(re.findall(r"(?:19|20|21)\d{2}", period or "")))
    return ",".join(years) or (period or "").strip().lower()


def mark_ranges(figures: list[dict]) -> int:
    """Where sources give different values for the same measure, unit and period, attach the range.
    Returns how many measures have such a spread. Different periods are never compared."""
    groups = defaultdict(list)
    for f in figures:
        groups[(f["label"].strip().lower(), f["unit"].strip().lower(), _period_key(f["period"]))].append(f)
    spreads = 0
    for members in groups.values():
        values = [f["value"] for f in members]
        if len(values) > 1 and min(values) != max(values):
            spreads += 1
            for f in members:
                f["range"] = [min(values), max(values)]
    return spreads


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
    spreads = mark_ranges(figures)
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
    g.add_node("gap_review", gap_review_node)
    g.add_node("verify", verify_node)
    g.add_node("challenge_review", challenge_review_node)
    g.add_node("analyst", analyst_node)
    g.add_node("writer", writer_node)
    g.add_node("draft_review", draft_review_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "router")
    g.add_edge("router", "planner")
    g.add_edge("planner", "search")
    g.add_edge("search", "read")
    g.add_edge("read", "extract")
    g.add_edge("extract", "gap_review")
    g.add_conditional_edges("gap_review", route_after_gaps, {"search": "search", "verify": "verify"})
    g.add_edge("verify", "challenge_review")
    g.add_conditional_edges("challenge_review", route_after_challenge, {"search": "search", "analyst": "analyst"})
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
