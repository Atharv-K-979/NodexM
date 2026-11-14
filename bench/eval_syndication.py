"""Score NodexM's source-independence detection on the labeled syndication test set.

Usage:  python -m bench.eval_syndication [--cases bench/syndication_cases.json] [--out docx/benchmark]

For every case the sources behind one claim are grouped exactly as the research pipeline does
(duplicate pages + wire origin + near-identical quotes + one source citing another), then compared
with the hand-labeled true groups. Two scores are reported:
- case accuracy: the predicted number of independent sources equals the true number
- pairwise precision / recall: over every pair of sources in a case, "same original source"
  predicted vs true. Precision = merges that were right; recall = true merges that were found.
"""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from nodexm.evidence import find_duplicates, independence_groups, source_origin

ROOT = Path(__file__).resolve().parent.parent


def predict(case: dict) -> list[list[int]]:
    sources = {}
    for i, s in enumerate(case["sources"]):
        sources[i] = {"domain": s["domain"], "text": s["text"], "origin": source_origin(s["text"], s["domain"]),
                      "credibility": 0.7}
    members = []
    for i, quote in enumerate(case["quotes"]):
        start = sources[i]["text"].find(quote)
        if start < 0:
            raise ValueError(f"{case['id']}: quote {i} is not in its source text")
        members.append({"source_idx": i, "quote": quote, "start": start, "end": start + len(quote)})
    groups, _ = independence_groups(members, sources)
    # Pages that copy another page are merged into it, as the pipeline does before verification.
    dups = find_duplicates({i: s["text"] for i, s in sources.items()}, {i: 0.7 for i in sources})
    owner = {i: next(k for k, g in enumerate(groups) if i in g) for i in sources}
    for copy, original in dups.items():
        a, b = owner[copy], owner[original]
        if a != b:
            groups[b].extend(groups[a])
            groups[a] = []
            owner = {i: next(k for k, g in enumerate(groups) if i in g) for i in sources}
    return [sorted(g) for g in groups if g]


def same_group(groups: list[list[int]]) -> dict[tuple[int, int], bool]:
    where = {i: k for k, g in enumerate(groups) for i in g}
    return {(a, b): where[a] == where[b] for a, b in itertools.combinations(sorted(where), 2)}


def evaluate(path: Path) -> dict:
    data = json.loads(path.read_text())
    rows, tp, fp, fn, tn = [], 0, 0, 0, 0
    for case in data["cases"]:
        predicted = predict(case)
        truth = case["truth_groups"]
        p_pairs, t_pairs = same_group(predicted), same_group(truth)
        for pair, actual in t_pairs.items():
            guess = p_pairs[pair]
            tp += guess and actual
            fp += guess and not actual
            fn += actual and not guess
            tn += not guess and not actual
        rows.append({
            "id": case["id"], "description": case["description"], "sites": len(case["sources"]),
            "true_independent": len(truth), "predicted_independent": len(predicted),
            "correct": len(truth) == len(predicted) and sorted(map(sorted, truth)) == sorted(predicted),
            "predicted_groups": predicted, "truth_groups": truth,
        })
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "test_set": data["name"], "version": data["version"],
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": len(rows), "cases_correct": sum(r["correct"] for r in rows),
        "pairwise": {"true_merges_found": tp, "wrong_merges": fp, "missed_merges": fn, "correct_separations": tn,
                     "precision": precision, "recall": recall},
        "rows": rows,
    }


def to_markdown(result: dict) -> str:
    pw = result["pairwise"]
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    lines = [
        f"# {result['test_set']} v{result['version']} — results",
        "",
        f"Run at {result['run_at']}. Synthetic, hand-labeled cases; see bench/syndication_cases.json.",
        "",
        f"- Cases where the number of independent sources was exactly right: **{result['cases_correct']} of {result['cases']}**",
        f"- Pairwise precision (merges that were right): **{pct(pw['precision'])}**",
        f"- Pairwise recall (true merges found): **{pct(pw['recall'])}**",
        "",
        "| Case | Sites | True independent | Predicted | Correct |",
        "|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        lines.append(f"| {r['id']} | {r['sites']} | {r['true_independent']} | {r['predicted_independent']} | "
                     f"{'yes' if r['correct'] else 'no'} |")
    lines += ["", "Cases marked HARD in the test set describe known limits of the current method."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", default=str(ROOT / "bench" / "syndication_cases.json"))
    parser.add_argument("--out", default=str(ROOT / "docx" / "benchmark"))
    args = parser.parse_args()
    result = evaluate(Path(args.cases))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "syndication_results.json").write_text(json.dumps(result, indent=2))
    (out / "syndication_results.md").write_text(to_markdown(result))
    print(to_markdown(result))
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
