import { useState } from "react";
import type { Claim, ClaimStatus, Run } from "../../types";
import { STATUS_LABEL, statusChip } from "../../lib/format";
import { Meter } from "../ui";
import { CitationChip } from "./Citation";

type Filter = "all" | ClaimStatus;

export default function ClaimsPanel({ run, initial = "all" }: { run: Run; initial?: Filter }) {
  const [filter, setFilter] = useState<Filter>(initial);
  const counts = run.claims.reduce<Record<string, number>>((a, c) => ({ ...a, [c.status]: (a[c.status] ?? 0) + 1 }), {});
  const shown = filter === "all" ? run.claims : run.claims.filter((c) => c.status === filter);
  const filters: { value: Filter; label: string }[] = [
    { value: "all", label: `All ${run.claims.length}` },
    { value: "disputed", label: `Disputed ${counts.disputed ?? 0}` },
    { value: "agreed", label: `Agreed ${counts.agreed ?? 0}` },
    { value: "single_source", label: `Single source ${counts.single_source ?? 0}` },
  ];

  if (!run.claims.length) return <p className="py-10 text-center text-sm text-muted">No verified claims yet.</p>;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {filters.map((f) => (
          <button key={f.value} onClick={() => setFilter(f.value)} className={`rounded-full border px-3 py-1 text-xs font-medium transition ${filter === f.value ? "border-accent/50 bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}>
            {f.label}
          </button>
        ))}
      </div>
      {run.figures.length > 0 && filter === "all" && <Figures run={run} />}
      <ul className="mt-4 space-y-3">{shown.map((c) => <ClaimCard key={c.id} claim={c} run={run} />)}</ul>
    </div>
  );
}

function ClaimCard({ claim, run }: { claim: Claim; run: Run }) {
  const [open, setOpen] = useState(claim.status === "disputed");
  const tone = claim.status === "agreed" ? "agreed" : claim.status === "disputed" ? "disputed" : "single";
  return (
    <li className="rounded-xl border border-line/80 bg-card/50 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={statusChip(claim.status)}>{STATUS_LABEL[claim.status]}</span>
        <span className="flex items-center gap-1.5 text-xs text-faint" title="Confidence combines how many independent sources agree and how credible they are">
          <Meter value={claim.confidence} tone={tone} /> {Math.round(claim.confidence * 100)}% confidence
        </span>
        <span className="ml-auto font-mono text-[11px] text-faint">{claim.key}</span>
      </div>
      <p className="mt-2 text-[14.5px] leading-relaxed">
        {claim.text} {claim.sources.map((n) => <CitationChip key={n} n={n} run={run} />)}
      </p>
      {claim.note && <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{claim.note}</p>}
      <button onClick={() => setOpen((o) => !o)} className="mt-2 text-xs font-medium text-accent hover:underline">
        {open ? "Hide" : "Show"} evidence ({claim.evidence.length})
      </button>
      {open && (
        <ul className="mt-2 space-y-2">
          {claim.evidence.map((e, i) => (
            <li key={i} className={`border-l-2 pl-3 text-[13px] leading-relaxed ${e.stance === "contradicts" ? "border-disputed/60" : "border-agreed/50"}`}>
              <p className="text-muted">
                “{e.quote}” <CitationChip n={e.source} run={run} />
              </p>
              <p className="mt-0.5 text-[11px] text-faint">
                {e.stance === "contradicts" ? "Contradicts" : "Supports"} · {run.sources.find((s) => s.number === e.source)?.domain}
              </p>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Figures({ run }: { run: Run }) {
  return (
    <div className="mt-4 overflow-x-auto rounded-xl border border-line/80">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-card text-left font-mono text-[11px] uppercase tracking-wide text-faint">
            <th className="px-3 py-2 font-medium">Key figure</th>
            <th className="px-3 py-2 text-right font-medium">Value</th>
            <th className="px-3 py-2 font-medium">Period</th>
            <th className="px-3 py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {run.figures.map((f, i) => (
            <tr key={i} className="border-t border-line/60">
              <td className="px-3 py-2">{f.label}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                {f.value.toLocaleString()} {f.unit}
                {f.range && <span className="block text-[11px] text-single">range {f.range[0].toLocaleString()}–{f.range[1].toLocaleString()}</span>}
              </td>
              <td className="px-3 py-2 text-muted">{f.period || "–"}</td>
              <td className="px-3 py-2"><span className={statusChip(f.status)}>{STATUS_LABEL[f.status]}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
