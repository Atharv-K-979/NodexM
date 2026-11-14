import { useEffect, useRef, useState } from "react";
import { AlertCircle, BookOpen, Layers, RotateCcw, ShieldCheck, Sparkles } from "lucide-react";
import { api, streamEvents } from "../../api";
import { money, timeAgo } from "../../lib/format";
import type { DraftInfo, Mode, Run, TraceEntry } from "../../types";
import { useToast } from "../ui";
import AnswerView from "./AnswerView";
import FactsView from "./FactsView";
import SourcesPanel, { SourceStrip } from "./SourcesPanel";
import TracePanel, { ProgressPanel } from "./TracePanel";

type Tab = "facts" | "answer" | "sources" | "steps";

interface Props {
  initial: Run;
  onFinished: () => void;
  onRetry: (question: string, mode: Mode) => void;
}

export default function RunBlock({ initial, onFinished, onRetry }: Props) {
  const toast = useToast();
  const [run, setRun] = useState<Run>(initial);
  const [trace, setTrace] = useState<TraceEntry[]>(initial.trace);
  const [tab, setTab] = useState<Tab>("facts");
  const active = run.status === "queued" || run.status === "running";
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    if (!active) return;
    const ctrl = new AbortController();
    let stopped = false;

    async function follow() {
      for (let attempt = 0; attempt < 30 && !stopped; attempt++) {
        try {
          await streamEvents(`/api/runs/${run.id}/events`, (e) => {
            if (e.type === "trace") {
              setTrace((prev) => (prev.some((p) => p.ts === e.data.ts && p.summary === e.data.summary) ? prev : [...prev, e.data]));
            } else if (e.type === "status") {
              setRun((r) => ({ ...r, status: e.data.status, error: e.data.error ?? r.error, started_at: r.started_at ?? new Date().toISOString() }));
            }
          }, ctrl.signal);
          break;
        } catch {
          if (stopped) return;
          await new Promise((r) => setTimeout(r, 2000));
        }
      }
      if (stopped) return;
      const fresh = await api<Run>(`/api/runs/${run.id}`);
      setRun(fresh);
      setTrace(fresh.trace);
      finishedRef.current();
    }
    follow().catch(() => {});
    return () => {
      stopped = true;
      ctrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.id, active]);

  async function cancel() {
    try {
      await api(`/api/runs/${run.id}/cancel`, { body: {} });
      toast({ kind: "info", text: "Stopping…" });
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    }
  }

  const setDraft = (d: DraftInfo) => setRun((r) => ({ ...r, draft: d }));
  const tabs: { key: Tab; label: string; icon: typeof Sparkles; count?: number }[] = [
    { key: "facts", label: "Facts", icon: ShieldCheck, count: run.claims.length },
    { key: "answer", label: "Answer", icon: Sparkles },
    { key: "sources", label: "Sources", icon: BookOpen, count: run.sources.length },
    { key: "steps", label: "Steps", icon: Layers },
  ];

  return (
    <article className="animate-rise">
      <h1 className="font-serif text-[26px] font-semibold leading-tight tracking-tight text-balance sm:text-[30px]">{run.question}</h1>
      {run.standalone_question && run.standalone_question.toLowerCase() !== run.question.toLowerCase() && (
        <p className="mt-1.5 text-sm text-faint">Researched as: {run.standalone_question}</p>
      )}

      <div className="mt-4 flex items-end justify-between gap-3 border-b border-line">
        <nav className="-mb-px flex gap-1 overflow-x-auto" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 pb-2.5 pt-1 text-sm font-medium transition-colors ${tab === t.key ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg"}`}
            >
              <t.icon className="h-4 w-4" />
              {t.label}
              {t.count ? <span className="rounded bg-raised px-1.5 font-mono text-[10.5px] text-muted">{t.count}</span> : null}
            </button>
          ))}
        </nav>
        <p className="hidden shrink-0 pb-2.5 text-xs text-faint sm:block">
          {run.mode !== "auto" && <span className="mr-2 rounded bg-raised px-1.5 py-0.5 font-mono uppercase text-muted">{run.mode}</span>}
          {timeAgo(run.created_at)}
          {run.status === "completed" && ` · ${money(run.cost_usd)}${run.used_own_keys ? " on your key" : ""}`}
        </p>
      </div>

      <div className="pt-5">
        {active && (tab === "facts" || tab === "answer") && <ProgressPanel run={run} trace={trace} onCancel={cancel} />}

        {!active && (run.status === "failed" || run.status === "cancelled") && (tab === "facts" || tab === "answer") && (
          <div className="rounded-xl border border-disputed/30 bg-disputed/5 p-4">
            <p className="flex items-center gap-2 text-sm font-medium text-disputed">
              <AlertCircle className="h-4 w-4" /> {run.status === "cancelled" ? "Stopped" : "This research run failed"}
            </p>
            {run.error && <p className="mt-1 text-sm text-muted">{run.error}</p>}
            <button className="btn-outline mt-3 text-xs" onClick={() => onRetry(run.question, run.requested_mode)}>
              <RotateCcw className="h-3.5 w-3.5" /> Try again
            </button>
          </div>
        )}

        {tab === "answer" && run.status === "completed" && run.draft && (
          <div className="space-y-6">
            <SourceStrip run={run} onMore={() => setTab("sources")} />
            <AnswerView run={run} onDraft={setDraft} onShowClaims={() => setTab("facts")} />
          </div>
        )}
        {tab === "sources" && <SourcesPanel run={run} />}
        {tab === "facts" && run.status === "completed" && <FactsView run={run} onReadAnswer={() => setTab("answer")} />}
        {tab === "steps" && <TracePanel run={{ ...run, trace }} />}
      </div>
    </article>
  );
}
