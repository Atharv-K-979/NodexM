"""Run NodexM on a question set and measure copy inflation, verification and cost.

Usage:
  python -m bench.run_benchmark [--mode quick|deep] [--limit N] [--questions bench/questions.json] [--out docx/benchmark]

Uses the API keys in .env and costs real money (about $0.03 per Quick and $0.14 per Deep question).

Metrics per question:
- sources read, copies set aside, pages that republish a wire story
- claims by status (agreed / disputed / single source)
- "site-count agreed": claims that two or more *sites* support, which a tool counting sites would
  present as corroborated
- "inflated": site-count agreed claims that NodexM found rest on ONE original source
- copy inflation ratio: supporting sites / independent supporting sources, over site-count agreed claims
- citation support: cited sentences the citation auditor found supported by their sources
- time and estimated cost
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from nodexm.config import settings
from nodexm.db import init_db
from nodexm.llm import LLMClient, Usage
from nodexm.pipeline import RunContext, run_pipeline
from nodexm.search import SearchContext

ROOT = Path(__file__).resolve().parent.parent


def claim_metrics(cluster: dict) -> dict:
    supporting_sites = len({e["source_idx"] for e in cluster["evidence"] if e["stance"] == "supports"})
    ind = cluster.get("independence") or {}
    return {
        "status": cluster["status"],
        "sites": ind.get("sites", len(cluster["source_indices"])),
        "supporting_sites": supporting_sites,
        "supporting_independent": ind.get("supporting", ind.get("independent", supporting_sites)),
        "reasons": ind.get("reasons", []),
        "text": cluster["text"],
    }


def run_one(question: str, mode: str, n: int) -> dict:
    usage = Usage()
    llm = LLMClient(settings.gemini_api_key, usage)
    warnings: list[str] = []
    ctx = RunContext(run_id=f"bench-{n}", llm=llm,
                     search=SearchContext(usage=usage, tavily_key=settings.tavily_api_key, region="Global"),
                     emit=lambda node, status, summary, extra=None: warnings.append(summary) if status == "warning" else None)
    started = time.time()
    try:
        result = run_pipeline(ctx, question=question, requested_mode=mode, allow_deep=True)
    except Exception as e:  # keep going; a failed question is reported, not hidden
        return {"question": question, "error": f"{type(e).__name__}: {str(e)[:300]}", "seconds": round(time.time() - started, 1),
                "cost_usd": usage.cost()}
    claims = [claim_metrics(c) for c in result["clusters"]]
    site_agreed = [c for c in claims if c["supporting_sites"] >= 2 and c["status"] != "disputed"]
    inflated = [c for c in site_agreed if c["supporting_independent"] < 2]
    audit = result.get("audit") or {}
    return {
        "question": question,
        "mode": result["mode"],
        "seconds": round(time.time() - started, 1),
        "cost_usd": usage.cost(),
        "sources": len(result["sources"]),
        "copies_set_aside": sum(1 for s in result["sources"] if s.get("duplicate_of") is not None),
        "wire_pages": sum(1 for s in result["sources"] if s.get("origin")),
        "claims": len(claims),
        "agreed": sum(c["status"] == "agreed" for c in claims),
        "disputed": sum(c["status"] == "disputed" for c in claims),
        "single_source": sum(c["status"] == "single_source" for c in claims),
        "site_count_agreed": len(site_agreed),
        "inflated": len(inflated),
        "supporting_sites": sum(c["supporting_sites"] for c in site_agreed),
        "supporting_independent": sum(c["supporting_independent"] for c in site_agreed),
        "citations_checked": audit.get("checked", 0),
        "citations_supported": audit.get("supported", 0),
        "critic": (result.get("critique") or {}).get("overall"),
        "inflated_examples": [{"claim": c["text"], "sites": c["supporting_sites"], "why": c["reasons"]} for c in inflated[:3]],
        "warnings": warnings[:5],
    }


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    total = lambda k: sum(r[k] for r in ok)
    site_agreed, inflated = total("site_count_agreed"), total("inflated")
    checked = total("citations_checked")
    return {
        "questions": len(rows), "completed": len(ok), "failed": len(rows) - len(ok),
        "claims": total("claims"), "agreed": total("agreed"), "disputed": total("disputed"),
        "single_source": total("single_source"),
        "site_count_agreed": site_agreed, "inflated": inflated,
        "inflated_share": round(inflated / site_agreed, 3) if site_agreed else None,
        "copy_inflation_ratio": round(total("supporting_sites") / total("supporting_independent"), 2)
        if total("supporting_independent") else None,
        "wire_pages": total("wire_pages"), "copies_set_aside": total("copies_set_aside"), "sources": total("sources"),
        "citation_support": round(total("citations_supported") / checked, 3) if checked else None,
        "citations_checked": checked,
        "avg_seconds": round(sum(r["seconds"] for r in ok) / len(ok), 1) if ok else None,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }


def to_markdown(meta: dict, summary: dict, rows: list[dict]) -> str:
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    lines = [
        "# NodexM copy-inflation benchmark",
        "",
        f"Run at {meta['run_at']} · mode {meta['mode']} · {summary['completed']} of {summary['questions']} questions completed"
        f" · total cost ${summary['total_cost_usd']}.",
        "",
        "## Headline numbers",
        "",
        f"- Claims checked: **{summary['claims']}** ({summary['agreed']} agreed, {summary['disputed']} disputed, "
        f"{summary['single_source']} single source)",
        f"- Claims that two or more sites support (what a site-counting tool would call corroborated): "
        f"**{summary['site_count_agreed']}**",
        f"- Of those, resting on only one original source: **{summary['inflated']}** ({pct(summary['inflated_share'])})",
        f"- Copy inflation ratio (supporting sites per independent source): **{summary['copy_inflation_ratio']}**",
        f"- Pages republishing a wire story: {summary['wire_pages']} of {summary['sources']}; "
        f"near-identical copies set aside: {summary['copies_set_aside']}",
        f"- Cited sentences supported by their sources (citation auditor): **{pct(summary['citation_support'])}** "
        f"of {summary['citations_checked']}",
        f"- Average time per question: {summary['avg_seconds']} s",
        "",
        "## Per question",
        "",
        "| Question | Claims | Site-count agreed | Inflated | Citations supported | Cost | Time |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['question']} | failed: {r['error'][:80]} | | | | ${r['cost_usd']} | {r['seconds']} s |")
            continue
        cites = f"{r['citations_supported']}/{r['citations_checked']}" if r["citations_checked"] else "–"
        lines.append(f"| {r['question']} | {r['claims']} | {r['site_count_agreed']} | {r['inflated']} | {cites} | "
                     f"${r['cost_usd']} | {r['seconds']} s |")
    examples = [(r["question"], e) for r in rows if "error" not in r for e in r["inflated_examples"]]
    if examples:
        lines += ["", "## Examples of inflated agreement", ""]
        for q, e in examples[:10]:
            lines.append(f"- *{q}* — \"{e['claim']}\": {e['sites']} sites, one original source ({'; '.join(e['why'])})")
    lines += [
        "",
        "## Method and limits",
        "",
        "- Independence is NodexM's own judgement (wire datelines and attributions, near-identical quotes, sources citing each "
        "other, sources crediting the same organisation). It misses copies that hide their origin, so the inflation figures are "
        "a lower bound, and it has not yet been checked against human labels on these live runs.",
        "- The citation auditor is automated; automated checkers agree with human reviewers only partly.",
        "- Results vary between runs and over time as search results change. Small question sets give noisy percentages.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure copy inflation and verification on live questions.")
    parser.add_argument("--mode", choices=["quick", "deep"], default="quick")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions (0 = all)")
    parser.add_argument("--questions", default=str(ROOT / "bench" / "questions.json"))
    parser.add_argument("--out", default=str(ROOT / "docx" / "benchmark"))
    args = parser.parse_args()
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set in .env")
    init_db()  # the search and page cache live in the database
    questions = json.loads(Path(args.questions).read_text())["questions"]
    if args.limit:
        questions = questions[: args.limit]
    rows = []
    for n, q in enumerate(questions, 1):
        print(f"[{n}/{len(questions)}] {q}", flush=True)
        rows.append(run_one(q, args.mode, n))
        r = rows[-1]
        print("   " + (f"failed: {r['error']}" if "error" in r else
                       f"{r['claims']} claims, {r['site_count_agreed']} site-count agreed, {r['inflated']} inflated, "
                       f"{r['seconds']} s, ${r['cost_usd']}"), flush=True)
    meta = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": args.mode}
    summary = summarise(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (out / f"benchmark-{args.mode}-{stamp}.json").write_text(json.dumps({**meta, "summary": summary, "rows": rows}, indent=2))
    report = to_markdown(meta, summary, rows)
    (out / f"benchmark-{args.mode}-{stamp}.md").write_text(report)
    print("\n" + report)
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
