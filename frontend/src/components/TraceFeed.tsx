import type { TraceEntry } from "../types";

const NODE_ICON: Record<string, string> = {
  planner: "🗺️",
  search: "🔎",
  scraper: "🌐",
  verifier: "⚖️",
  writer: "✍️",
  critic: "🧐",
  formatter: "🎨",
};

const STATUS_BADGE: Record<string, string> = {
  start: "bg-sky-50 text-sky-700 border-sky-200",
  end: "bg-emerald-50 text-emerald-700 border-emerald-200",
  error: "bg-rose-50 text-rose-700 border-rose-200",
};

interface Props {
  trace: TraceEntry[];
  inProgress: boolean;
}

export default function TraceFeed({ trace, inProgress }: Props) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-200 flex items-center justify-between bg-slate-50">
        <h3 className="font-semibold text-slate-800 flex items-center gap-2">
          <span>🧵 Live research trace</span>
        </h3>
        {inProgress && (
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-brand-700 bg-brand-50 border border-brand-100 px-2 py-1 rounded-full">
            <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse"></span>
            running
          </span>
        )}
      </div>
      {trace.length === 0 ? (
        <div className="p-8 text-center text-slate-500 text-sm">
          Submit a topic to see the agent pipeline trace here.
        </div>
      ) : (
        <ol className="divide-y divide-slate-100 max-h-[420px] overflow-y-auto">
          {trace.map((t, i) => (
            <li
              key={`${t.ts}-${i}`}
              className="px-5 py-3 trace-enter"
            >
              <div className="flex items-start gap-3">
                <span className="text-lg leading-6 mt-0.5">
                  {NODE_ICON[t.node] ?? "⚙️"}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-slate-800 capitalize">
                      {t.node}
                    </span>
                    <span
                      className={`text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded border ${
                        STATUS_BADGE[t.status] ?? STATUS_BADGE.start
                      }`}
                    >
                      {t.status}
                    </span>
                    <span className="text-[11px] text-slate-400 ml-auto tabular-nums">
                      {new Date(t.ts).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-sm text-slate-600 mt-0.5">{t.summary}</p>
                  {t.extra && Object.keys(t.extra).length > 0 && (
                    <details className="mt-1.5">
                      <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-700">
                        details
                      </summary>
                      <pre className="mt-1.5 text-[11px] bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto text-slate-600">
                        {JSON.stringify(t.extra, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
