import { useCallback, useEffect, useState } from "react";
import TopicForm, { TopicFormValues } from "./components/TopicForm";
import TraceFeed from "./components/TraceFeed";
import DraftPanel from "./components/DraftPanel";
import type { TraceEntry, RunInfo } from "./types";

export default function App() {
  const [runId, setRunId] = useState<string | null>(null);
  const [runInfo, setRunInfo] = useState<RunInfo | null>(null);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setRunId(null);
    setRunInfo(null);
    setTrace([]);
  };

  const runNow = useCallback(async (values: TopicFormValues) => {
    reset();
    setSubmitting(true);
    try {
      const r = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: values.topic,
          user_profile: { region: values.region, depth: values.depth },
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { run_id: string };
      setRunId(data.run_id);
      pollRunAndStream(data.run_id);
    } finally {
      setSubmitting(false);
    }
  }, []);

  function pollRunAndStream(id: string) {
    const es = new EventSource(`/api/runs/${id}/trace`);
    es.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        if (parsed.type === "trace" && parsed.data) {
          setTrace((prev) => {
            const last = prev[prev.length - 1];
            const new_ = parsed.data as TraceEntry;
            if (
              last &&
              last.ts === new_.ts &&
              last.node === new_.node &&
              last.summary === new_.summary
            )
              return prev;
            return [...prev, new_];
          });
        } else if (parsed.type === "__done__") {
          es.close();
          fetch(`/api/runs/${id}`)
            .then((r) => r.json())
            .then((d) => setRunInfo(d as RunInfo))
            .catch(() => {});
        } else if (parsed.type === "error") {
          es.close();
        }
      } catch {
        /* malformed event */
      }
    };
    es.onerror = () => {
      es.close();
    };
  }

  useEffect(() => {
    return () => {};
  }, []);

  const inProgress =
    !!runId &&
    (!runInfo ||
      (runInfo.status !== "completed" && runInfo.status !== "failed"));

  return (
    <div className="min-h-full">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-violet-600 flex items-center justify-center text-white font-bold">
              N
            </div>
            <div>
              <h1 className="font-bold text-slate-900 leading-tight">
                NodexM v2
              </h1>
              <p className="text-xs text-slate-500 leading-tight">
                Multi-agent research with claim verification & draft review
              </p>
            </div>
          </div>
          {runId && (
            <div className="text-xs text-slate-600 font-mono">
              run{" "}
              <span className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-800">
                {runId}
              </span>
              {runInfo?.status && (
                <span className="ml-2 text-slate-500">
                  · {runInfo.status}
                </span>
              )}
            </div>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-5 space-y-6">
            <TopicForm onSubmit={runNow} loading={submitting} />
            <TraceFeed trace={trace} inProgress={inProgress} />
          </div>
          <div className="lg:col-span-7">
            <DraftPanel runId={runId} />
          </div>
        </div>
      </main>
    </div>
  );
}
