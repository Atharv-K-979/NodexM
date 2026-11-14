import { useState } from "react";
import { BadgeCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import { api } from "../../api";
import { STATUS_LABEL, statusChip, timeAgo } from "../../lib/format";
import { navigate } from "../../lib/route";
import type { AlertQuality, ChangeEvent, QuoteSnapshot } from "../../types";
import { useToast } from "../ui";

const KIND: Record<ChangeEvent["kind"], { label: string; edge: string }> = {
  status_changed: { label: "Status changed", edge: "border-l-disputed" },
  returned: { label: "Back again", edge: "border-l-accent" },
  new: { label: "New fact", edge: "border-l-agreed" },
  stale: { label: "No longer found", edge: "border-l-single" },
};

const CONFIRMATION: Record<string, string> = {
  new_source: "Confirmed by a new independent source",
  repeat: "Confirmed on consecutive runs",
  strong: "Backed by 2+ independent sources",
  stale: "Missing from 3 runs in a row",
};

function Quotes({ title, quotes }: { title: string; quotes: QuoteSnapshot[] }) {
  return (
    <div className="min-w-0 rounded-lg border border-line/70 p-3">
      <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-faint">{title}</p>
      {quotes.length === 0 ? (
        <p className="text-[12.5px] text-faint">Nothing recorded.</p>
      ) : (
        <ul className="space-y-2">
          {quotes.slice(0, 3).map((q, i) => (
            <li key={i} className="text-[12.5px] leading-relaxed">
              <p className={q.stance === "contradicts" ? "text-disputed/90" : "text-muted"}>“{q.quote}”</p>
              <p className="text-[11px] text-faint">
                {q.url ? <a href={q.url} target="_blank" rel="noreferrer" className="hover:text-accent">{q.domain}</a> : q.domain}
                {q.stance === "contradicts" ? " · contradicts" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Props {
  change: ChangeEvent;
  showWatch?: boolean;
  compact?: boolean;
  onFeedback?: (change: ChangeEvent, quality: AlertQuality) => void;
}

/** One confirmed Watch alert: what changed, why it was confirmed, the quotes before and after,
 *  and buttons to mark it as a real change or noise (that is how alert precision is measured). */
export default function ChangeCard({ change, showWatch, compact, onFeedback }: Props) {
  const toast = useToast();
  const [c, setC] = useState(change);
  const [busy, setBusy] = useState(false);
  // The first sentence of "repeat"/"strong" details only restates the confirmation badge.
  const detail = c.confirmation === "repeat" || c.confirmation === "strong" || c.confirmation === "stale"
    ? c.detail.split(/(?<=\.)\s+/).slice(1).join(" ")
    : c.detail;

  async function verdict(v: "real" | "noise") {
    setBusy(true);
    try {
      const next = c.feedback === v ? null : v;
      const r = await api<{ change: ChangeEvent; quality: AlertQuality }>(
        `/api/watches/${c.watch_id}/changes/${c.id}/feedback`, { body: { verdict: next } });
      setC({ ...c, feedback: r.change.feedback });
      onFeedback?.(r.change, r.quality);
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className={`rounded-xl border border-line/80 border-l-[3px] bg-card/50 p-4 ${KIND[c.kind].edge}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">{KIND[c.kind].label}</span>
        {c.from_status && <span className={statusChip(c.from_status)}>{STATUS_LABEL[c.from_status] ?? c.from_status}</span>}
        {c.from_status && <span className="text-faint">→</span>}
        {c.to_status && <span className={statusChip(c.to_status)}>{STATUS_LABEL[c.to_status] ?? c.to_status}</span>}
        <span className="ml-auto text-faint">{timeAgo(c.created_at)}</span>
      </div>
      {showWatch && c.watch_topic && (
        <button onClick={() => navigate(`/watches/${c.watch_id}`)} className="mt-1 text-xs text-accent hover:underline">
          {c.watch_topic}
        </button>
      )}
      <p className="mt-2 text-[14.5px] leading-relaxed">{c.claim}</p>
      {c.confirmation && (
        <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-agreed">
          <BadgeCheck className="h-3.5 w-3.5" /> {CONFIRMATION[c.confirmation] ?? c.confirmation}
        </p>
      )}
      {detail && !compact && <p className="mt-1 text-[13px] text-muted">{detail}</p>}
      {!compact && c.kind === "new" && c.after.length > 0 && (
        <div className="mt-3"><Quotes title="Evidence" quotes={c.after} /></div>
      )}
      {!compact && c.kind === "stale" && c.before.length > 0 && (
        <div className="mt-3"><Quotes title="Last seen" quotes={c.before} /></div>
      )}
      {!compact && (c.kind === "status_changed" || c.kind === "returned") && (
        <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
          <Quotes title="Before" quotes={c.before} />
          <Quotes title="Now" quotes={c.after} />
        </div>
      )}
      <div className="mt-3 flex items-center gap-2 text-xs">
        <span className="text-faint">Was this a real change?</span>
        <button disabled={busy} onClick={() => verdict("real")}
          className={`btn px-2 py-1 text-xs ${c.feedback === "real" ? "bg-agreed/15 text-agreed" : "btn-ghost"}`}>
          <ThumbsUp className="h-3.5 w-3.5" /> Real change
        </button>
        <button disabled={busy} onClick={() => verdict("noise")}
          className={`btn px-2 py-1 text-xs ${c.feedback === "noise" ? "bg-disputed/15 text-disputed" : "btn-ghost"}`}>
          <ThumbsDown className="h-3.5 w-3.5" /> Noise
        </button>
      </div>
    </article>
  );
}

export function QualityBadge({ quality }: { quality?: AlertQuality }) {
  if (!quality || !quality.alerts) return null;
  return (
    <span className="text-xs text-faint" title="Share of reviewed alerts you marked as a real change">
      {quality.precision == null
        ? `${quality.alerts} alert${quality.alerts === 1 ? "" : "s"}, none reviewed yet`
        : `Alert precision ${Math.round(quality.precision * 100)}% (${quality.real} of ${quality.reviewed} reviewed)`}
    </span>
  );
}
