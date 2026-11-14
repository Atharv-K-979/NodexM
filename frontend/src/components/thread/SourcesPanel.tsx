import { Copy, ExternalLink } from "lucide-react";
import type { Run, Source } from "../../types";
import { Meter } from "../ui";

const HUES = [210, 160, 30, 280, 340, 190, 100, 50];

export function SourceAvatar({ domain, size = 20 }: { domain: string; size?: number }) {
  const hue = HUES[[...domain].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, background: `hsl(${hue} 35% 22%)`, color: `hsl(${hue} 70% 75%)` }}
      className="inline-flex shrink-0 items-center justify-center rounded-md font-mono text-[10px] font-semibold uppercase"
    >
      {domain.replace(/^www\./, "").slice(0, 1)}
    </span>
  );
}

export function SourceStrip({ run, onMore }: { run: Run; onMore: () => void }) {
  const used = run.sources.filter((s) => s.evidence_count > 0 && s.duplicate_of == null);
  const list = (used.length ? used : run.sources).slice(0, 4);
  const others = run.sources.filter((s) => !list.includes(s));
  if (!run.sources.length) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      {list.map((s) => (
        <a key={s.number} href={s.url} target="_blank" rel="noreferrer" className="group flex min-h-[78px] flex-col justify-between rounded-xl border border-line/70 bg-card/60 p-2.5 transition hover:border-line hover:bg-card">
          <span className="line-clamp-2 text-[12.5px] leading-snug text-muted group-hover:text-fg">{s.title}</span>
          <span className="mt-2 flex items-center gap-1.5 text-[11px] text-faint">
            <SourceAvatar domain={s.domain} size={16} />
            <span className="truncate">{s.domain}</span>
            <span className="ml-auto font-mono">{s.number}</span>
          </span>
        </a>
      ))}
      {others.length > 0 && (
        <button onClick={onMore} className="flex min-h-[78px] flex-col justify-between rounded-xl border border-line/70 bg-card/60 p-2.5 text-left transition hover:border-line hover:bg-card">
          <span className="flex -space-x-1">
            {others.slice(0, 4).map((s) => <SourceAvatar key={s.number} domain={s.domain} size={18} />)}
          </span>
          <span className="text-[12.5px] text-muted">View {others.length} more</span>
        </button>
      )}
    </div>
  );
}

function SourceRow({ s }: { s: Source }) {
  const tone = s.credibility >= 0.8 ? "agreed" : s.credibility >= 0.55 ? "accent" : "single";
  return (
    <li className="flex gap-3 py-3.5">
      <span className="w-6 shrink-0 pt-0.5 text-right font-mono text-xs text-faint">{s.number}</span>
      <div className="min-w-0 flex-1">
        <a href={s.url} target="_blank" rel="noreferrer" className="group inline-flex max-w-full items-start gap-2">
          <SourceAvatar domain={s.domain} />
          <span className="text-[14px] font-medium leading-snug group-hover:text-accent">{s.title}</span>
          <ExternalLink className="mt-1 h-3 w-3 shrink-0 text-faint opacity-0 group-hover:opacity-100" />
        </a>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-faint">
          <span>{s.domain}</span>
          <span className="chip-neutral">{s.type}</span>
          {s.published && <span>{String(s.published).slice(0, 10)}</span>}
          <span className="flex items-center gap-1.5" title={s.credibility_reasons.join(" · ")}>
            <Meter value={s.credibility} tone={tone as "agreed" | "accent" | "single"} /> {Math.round(s.credibility * 100)}
          </span>
          {s.evidence_count > 0 && <span>{s.evidence_count} quote{s.evidence_count === 1 ? "" : "s"} used</span>}
          {s.duplicate_of != null && (
            <span className="flex items-center gap-1 text-single" title="Copies another source, so it does not count as independent agreement">
              <Copy className="h-3 w-3" /> copy of [{s.duplicate_of}]
            </span>
          )}
          {s.origin && s.origin.toLowerCase() !== s.domain.split(".")[0] && (
            <span className="text-single" title="Republishes a wire story; it counts once with every other copy of that story">
              via {s.origin}
            </span>
          )}
        </div>
        {s.credibility_reasons.length > 0 && <p className="mt-1 text-xs text-faint/80">{s.credibility_reasons.join(" · ")}</p>}
      </div>
    </li>
  );
}

export default function SourcesPanel({ run }: { run: Run }) {
  if (!run.sources.length) return <p className="py-10 text-center text-sm text-muted">No sources yet.</p>;
  const byType = run.sources.reduce<Record<string, number>>((acc, s) => ({ ...acc, [s.type]: (acc[s.type] ?? 0) + 1 }), {});
  return (
    <div>
      <p className="text-sm text-muted">
        {run.sources.length} sources read in full ·{" "}
        {Object.entries(byType).map(([t, n]) => `${n} ${t}`).join(", ")}
      </p>
      <ul className="mt-2 divide-y divide-line/60">{run.sources.map((s) => <SourceRow key={s.number} s={s} />)}</ul>
    </div>
  );
}
