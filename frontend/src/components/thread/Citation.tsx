import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ExternalLink } from "lucide-react";
import type { Run } from "../../types";
import { Meter } from "../ui";
import { SourceAvatar } from "./SourcesPanel";

/** One citation chip [n]; clicking opens the exact quotes taken from that source. */
export function CitationChip({ n, run }: { n: number; run: Run }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const exists = run.sources.some((s) => s.number === n);
  return (
    <>
      <button
        ref={btn}
        type="button"
        onClick={() => exists && setOpen((o) => !o)}
        aria-label={`Source ${n}`}
        className={`mx-[1px] inline-flex h-[18px] min-w-[18px] -translate-y-[1px] items-center justify-center rounded-[5px] px-1 align-middle font-mono text-[10.5px] font-medium transition-colors ${
          exists ? (open ? "bg-accent text-accent-ink" : "bg-raised text-muted hover:bg-accent/20 hover:text-accent") : "bg-disputed/15 text-disputed"
        }`}
      >
        {n}
      </button>
      {open && btn.current && createPortal(<SourcePopover n={n} run={run} anchor={btn.current} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

function SourcePopover({ n, run, anchor, onClose }: { n: number; run: Run; anchor: HTMLElement; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const source = run.sources.find((s) => s.number === n)!;
  const quotes = run.claims.flatMap((c) => c.evidence.filter((e) => e.source === n).map((e) => ({ ...e, claim: c })));

  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const width = Math.min(420, window.innerWidth - 24);
    const left = Math.min(Math.max(12, r.left - 24), window.innerWidth - width - 12);
    const below = r.bottom + 8;
    const height = ref.current?.offsetHeight ?? 300;
    const top = below + height > window.innerHeight - 12 ? Math.max(12, r.top - height - 8) : below;
    setPos({ top, left });
  }, [anchor]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onScroll = () => onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onScroll);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onScroll);
    };
  }, [anchor, onClose]);

  return (
    <div ref={ref} style={{ top: pos.top, left: pos.left, width: Math.min(420, window.innerWidth - 24) }} className="fixed z-50 max-h-[60vh] animate-rise overflow-y-auto rounded-xl border border-line bg-raised p-3.5 shadow-2xl shadow-black/60">
      <a href={source.url} target="_blank" rel="noreferrer" className="group flex items-start gap-2.5">
        <SourceAvatar domain={source.domain} />
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-sm font-medium leading-snug group-hover:text-accent">{source.title}</span>
          <span className="mt-0.5 flex items-center gap-1.5 text-xs text-faint">
            {source.domain} <ExternalLink className="h-3 w-3" />
          </span>
        </span>
      </a>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="chip-neutral">{source.type}</span>
        <span className="flex items-center gap-1.5">Credibility <Meter value={source.credibility} /> {Math.round(source.credibility * 100)}</span>
        {source.published && <span>{String(source.published).slice(0, 10)}</span>}
      </div>
      {source.credibility_reasons.length > 0 && <p className="mt-1.5 text-xs text-faint">{source.credibility_reasons.join(" · ")}</p>}

      <p className="mt-3.5 text-[11px] font-medium uppercase tracking-wider text-faint">
        {quotes.length ? `Quoted evidence (${quotes.length})` : "No quotes from this source were used"}
      </p>
      <ul className="mt-1.5 space-y-2.5">
        {quotes.slice(0, 6).map((q, i) => (
          <li key={i} className="rounded-lg border border-line/70 bg-card p-2.5 text-[13px] leading-relaxed">
            <p className="text-muted">
              <span className="text-faint">…{q.before.slice(-90)}</span>
              <mark className={`rounded px-0.5 text-fg ${q.stance === "contradicts" ? "bg-disputed/25" : "bg-accent/20"}`}>{q.quote}</mark>
              <span className="text-faint">{q.after.slice(0, 90)}…</span>
            </p>
            <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-faint">
              <span className={q.claim.status === "agreed" ? "text-agreed" : q.claim.status === "disputed" ? "text-disputed" : "text-single"}>●</span>
              {q.claim.key} · {q.stance === "contradicts" ? "contradicts" : "supports"} the claim
            </p>
          </li>
        ))}
      </ul>
      {!quotes.length && <p className="mt-1 text-[13px] text-muted">{source.snippet}</p>}
    </div>
  );
}
