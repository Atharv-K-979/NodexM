import { useEffect, useState } from "react";
import { AlertTriangle, Check, Square } from "lucide-react";
import { agentOf, STAGES } from "../../lib/agents";
import { compact, duration, money } from "../../lib/format";
import type { Run, TraceEntry } from "../../types";
import { Spinner } from "../ui";

export function ProgressPanel({ run, trace, onCancel }: { run: Run; trace: TraceEntry[]; onCancel: () => void }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const seen = new Set(trace.map((t) => t.node));
  const lastNode = [...trace].reverse().find((t) => t.node !== "system")?.node;
  const activeIdx = STAGES.findIndex((s) => lastNode && s.nodes.includes(lastNode));
  const recent = trace.filter((t) => t.status !== "start").slice(-3).reverse();

  return (
    <div className="rounded-2xl border border-line bg-card/60 p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm font-medium">
          <Spinner className="h-3.5 w-3.5 text-accent" />
          {run.status === "queued" ? "Waiting for a free worker…" : `Researching${run.mode && run.mode !== "auto" ? ` · ${run.mode}` : ""}`}
          <span className="font-mono text-xs font-normal text-faint">{duration(run.started_at ?? run.created_at, null)}</span>
        </p>
        <button onClick={onCancel} className="btn-ghost px-2 py-1 text-xs">
          <Square className="h-3 w-3" /> Stop
        </button>
      </div>
      <ol className="mt-3.5 grid gap-1.5 sm:grid-cols-2">
        {STAGES.map((s, i) => {
          const done = activeIdx > i || (s.nodes.some((n) => seen.has(n)) && activeIdx > i);
          const active = activeIdx === i;
          return (
            <li key={s.key} className={`flex items-center gap-2 text-[13px] ${active ? "text-fg" : done ? "text-muted" : "text-faint"}`}>
              <span className={`flex h-4 w-4 items-center justify-center rounded-full ${done ? "bg-agreed/20 text-agreed" : active ? "text-accent" : "border border-line"}`}>
                {done ? <Check className="h-3 w-3" strokeWidth={3} /> : active ? <Spinner className="h-3 w-3" /> : null}
              </span>
              {s.label}
            </li>
          );
        })}
      </ol>
      {recent.length > 0 && (
        <ul className="mt-3.5 space-y-1 border-t border-line/70 pt-3">
          {recent.map((t, i) => {
            const A = agentOf(t.node);
            return (
              <li key={`${t.ts}-${i}`} className={`flex animate-rise items-start gap-2 text-[12.5px] ${i === 0 ? "text-muted" : "text-faint"}`}>
                <A.icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span><span className="font-medium">{A.label}:</span> {t.summary}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default function TracePanel({ run }: { run: Run }) {
  const u = run.usage ?? {};
  const tokens = (u.lite_tokens?.in ?? 0) + (u.lite_tokens?.out ?? 0) + (u.flash_tokens?.in ?? 0) + (u.flash_tokens?.out ?? 0);
  const entries = run.trace.filter((t) => t.status !== "start");
  return (
    <div>
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-5">
        {[
          ["Time", duration(run.started_at, run.finished_at)],
          ["Model calls", String(u.calls ?? 0)],
          ["Tokens", compact(tokens)],
          ["Searches", String(u.search_credits ?? 0)],
          ["Cost", money(run.cost_usd)],
        ].map(([k, v]) => (
          <div key={k} className="bg-card px-3 py-2.5">
            <dt className="text-[11px] uppercase tracking-wider text-faint">{k}</dt>
            <dd className="mt-0.5 font-mono text-sm tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {run.plan.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-medium uppercase tracking-wider text-faint">Research plan</p>
          <ol className="mt-1.5 space-y-1 text-[13.5px] text-muted">
            {run.plan.map((p) => (
              <li key={p.id}><span className="font-mono text-xs text-faint">{p.id}</span> {p.question} <span className="text-xs text-faint">· {p.searchers.join(", ")}</span></li>
            ))}
          </ol>
        </div>
      )}
      <ol className="relative mt-5 space-y-0 border-l border-line pl-5">
        {entries.map((t, i) => {
          const A = agentOf(t.node);
          const warn = t.status === "warning" || t.status === "error";
          return (
            <li key={`${t.ts}-${i}`} className="relative pb-3.5">
              <span className={`absolute -left-[29px] flex h-[17px] w-[17px] items-center justify-center rounded-full border bg-bg ${warn ? "border-single/60 text-single" : "border-line text-muted"}`}>
                {warn ? <AlertTriangle className="h-2.5 w-2.5" /> : <A.icon className="h-2.5 w-2.5" />}
              </span>
              <p className="text-[13.5px]">
                <span className="font-medium">{A.label}</span>
                <span className="ml-2 font-mono text-[11px] text-faint">{new Date(t.ts).toLocaleTimeString()}</span>
              </p>
              <p className={`text-[13px] ${warn ? "text-single" : "text-muted"}`}>{t.summary}</p>
              {t.extra && Object.keys(t.extra).length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[11px] text-faint hover:text-muted">details</summary>
                  <pre className="mt-1 max-h-60 overflow-auto rounded-lg border border-line bg-card p-2 font-mono text-[11px] text-muted">{JSON.stringify(t.extra, null, 2)}</pre>
                </details>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
