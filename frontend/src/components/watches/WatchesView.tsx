import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, BellRing, Eye, GitCompare, Loader2, Pause, Play, Plus, Trash2, Zap } from "lucide-react";
import { api } from "../../api";
import { useApp } from "../../appContext";
import { dateTime, STATUS_LABEL, statusChip, timeAgo } from "../../lib/format";
import { navigate } from "../../lib/route";
import { useSession } from "../../session";
import type { AlertQuality, ChangeEvent, Mode, Watch } from "../../types";
import ChangeCard, { QualityBadge } from "./ChangeCard";
import { Modal, Segmented, Spinner, useToast } from "../ui";

const CADENCES = [
  { value: "daily", label: "Daily" },
  { value: "workdays", label: "Weekdays" },
  { value: "weekly", label: "Weekly" },
  { value: "biweekly", label: "Twice a month" },
  { value: "monthly", label: "Monthly" },
  { value: "manual", label: "Manual only" },
];

export function WatchesView() {
  const [watches, setWatches] = useState<Watch[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [feed, setFeed] = useState<{ changes: ChangeEvent[]; quality: AlertQuality } | null>(null);
  const load = useCallback(() => {
    api<Watch[]>("/api/watches").then(setWatches).catch(() => setWatches([]));
    api<{ changes: ChangeEvent[]; quality: AlertQuality }>("/api/watches/changes?limit=10").then(setFeed).catch(() => {});
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-10 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-[30px] font-semibold tracking-tight">Watches</h1>
          <p className="mt-1 max-w-xl text-[15px] text-muted">
            A Watch re-runs a topic on a schedule and compares every claim with the last run. You get a short brief of
            what changed instead of a whole new report.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New watch</button>
      </div>

      {feed && feed.changes.length > 0 && (
        <section className="mt-8">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-[11px] font-medium uppercase tracking-wider text-faint">Recent changes across your watches</h2>
            <QualityBadge quality={feed.quality} />
          </div>
          <div className="space-y-2.5">
            {feed.changes.map((c) => (
              <ChangeCard key={c.id} change={c} showWatch compact onFeedback={(_, q) => setFeed((f) => (f ? { ...f, quality: q } : f))} />
            ))}
          </div>
        </section>
      )}

      {watches === null ? (
        <div className="flex justify-center py-20 text-faint"><Loader2 className="h-5 w-5 animate-spin" /></div>
      ) : watches.length === 0 ? (
        <div className="mt-10 rounded-2xl border border-dashed border-line px-6 py-14 text-center">
          <Eye className="mx-auto h-6 w-6 text-faint" />
          <p className="mt-3 font-medium">No watches yet</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-muted">Create one here, or open any thread and choose “Watch topic”.</p>
        </div>
      ) : (
        <ul className="mt-8 divide-y divide-line/70 rounded-2xl border border-line bg-card/40">
          {watches.map((w) => (
            <li key={w.id}>
              <button onClick={() => navigate(`/watches/${w.id}`)} className="flex w-full items-center gap-4 px-4 py-4 text-left hover:bg-card">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${w.active ? "bg-accent/10 text-accent" : "bg-raised text-faint"}`}>
                  {w.active ? <Eye className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{w.topic}</span>
                  <span className="mt-0.5 block text-xs text-faint">
                    {w.cadence_label}
                    {w.active && w.next_run_at ? ` · next run ${dateTime(w.next_run_at)}` : !w.active ? " · paused" : ""}
                  </span>
                </span>
                {w.recent_changes > 0 && <span className="chip-neutral"><GitCompare className="h-3 w-3" /> {w.recent_changes} changes</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <NewWatchModal open={creating} onClose={() => setCreating(false)} onCreated={(w) => navigate(`/watches/${w.id}`)} />
    </div>
  );
}

function NewWatchModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (w: Watch) => void }) {
  const toast = useToast();
  const { config } = useSession();
  const [topic, setTopic] = useState("");
  const [cadence, setCadence] = useState("weekly");
  const [mode, setMode] = useState<Mode>("auto");
  const [email, setEmail] = useState(true);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      onCreated(await api<Watch>("/api/watches", { body: { topic, cadence, mode, email_alerts: email } }));
      onClose();
    } catch (err) {
      toast({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New watch">
      <form onSubmit={submit} className="space-y-5 p-5">
        <div>
          <label className="label" htmlFor="topic">Topic or question</label>
          <textarea id="topic" required minLength={3} rows={3} className="input resize-none" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. EU AI Act compliance costs for SMEs" />
        </div>
        <div>
          <label className="label" htmlFor="cadence">How often</label>
          <select id="cadence" className="input" value={cadence} onChange={(e) => setCadence(e.target.value)}>
            {CADENCES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        </div>
        <div>
          <span className="label">Depth</span>
          <Segmented<Mode> value={mode} onChange={setMode} options={[{ value: "auto", label: "Auto" }, { value: "quick", label: "Quick" }, { value: "deep", label: "Deep" }]} />
          <p className="mt-1.5 text-xs text-faint">Each scheduled run uses one of your runs.</p>
        </div>
        <label className="flex items-center gap-2.5 text-sm">
          <input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} className="h-4 w-4 accent-[rgb(var(--accent))]" />
          Email me when something changes
          {config && !config.email_alerts && <span className="text-xs text-faint">(email is not set up on this server)</span>}
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={busy}>{busy ? <Spinner /> : "Create watch"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function WatchView({ id }: { id: number }) {
  const [watch, setWatch] = useState<Watch | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const toast = useToast();
  const { refreshThreads } = useApp();
  const { refresh } = useSession();

  const load = useCallback(async () => {
    try {
      setWatch(await api<Watch>(`/api/watches/${id}`));
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
      navigate("/watches");
    }
  }, [id, toast]);
  useEffect(() => {
    load();
  }, [load]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  if (!watch) return <div className="flex justify-center py-32 text-faint"><Loader2 className="h-5 w-5 animate-spin" /></div>;
  const running = watch.runs?.some((r) => r.status === "queued" || r.status === "running");

  return (
    <div className="mx-auto w-full max-w-[860px] px-4 py-8 sm:px-6">
      <button onClick={() => navigate("/watches")} className="btn-ghost -ml-2 px-2 py-1 text-xs"><ArrowLeft className="h-3.5 w-3.5" /> Watches</button>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-serif text-[26px] font-semibold leading-tight tracking-tight">{watch.topic}</h1>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span>{watch.cadence_label}</span>
            {watch.active ? watch.next_run_at && <span>Next run {dateTime(watch.next_run_at)}</span> : <span className="text-single">Paused</span>}
            {watch.email_alerts && <span className="flex items-center gap-1"><BellRing className="h-3.5 w-3.5" /> Email alerts</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={!!busy || running} onClick={() => act("run", async () => {
            await api(`/api/watches/${watch.id}/run`, { body: {} });
            toast({ kind: "success", text: "Run started. Changes appear here when it finishes." });
            await Promise.all([load(), refreshThreads(), refresh()]);
          })}>
            {busy === "run" || running ? <Spinner /> : <Zap className="h-4 w-4" />} {running ? "Running…" : "Run now"}
          </button>
          <button className="btn-outline" disabled={!!busy} onClick={() => act("pause", async () => setWatch(await api<Watch>(`/api/watches/${watch.id}`, { method: "PATCH", body: { active: !watch.active } })))}>
            {watch.active ? <><Pause className="h-4 w-4" /> Pause</> : <><Play className="h-4 w-4" /> Resume</>}
          </button>
          {watch.thread_id && <button className="btn-outline" onClick={() => navigate(`/t/${watch.thread_id}`)}>Open thread</button>}
          <button className="btn-ghost text-disputed hover:text-disputed" disabled={!!busy} aria-label="Delete watch" onClick={() => act("delete", async () => {
            await api(`/api/watches/${watch.id}`, { method: "DELETE" });
            await refreshThreads();
            navigate("/watches");
          })}>
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      <section className="mt-10">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[11px] font-medium uppercase tracking-wider text-faint">What changed</h2>
          <QualityBadge quality={watch.quality} />
        </div>
        {!watch.changes?.length ? (
          <p className="mt-3 rounded-xl border border-dashed border-line px-4 py-6 text-sm text-muted">
            {watch.runs?.length
              ? "No confirmed changes yet. The first run sets the baseline; a change is only alerted once a new independent source backs it or it shows up on two runs in a row."
              : "No runs yet. Run it now to set the baseline."}
          </p>
        ) : (
          <div className="mt-3 space-y-2.5">
            {watch.changes.map((e) => (
              <ChangeCard key={e.id} change={e} onFeedback={(_, q) => setWatch((w) => (w ? { ...w, quality: q } : w))} />
            ))}
          </div>
        )}
      </section>

      <section className="mt-10">
        <h2 className="text-[11px] font-medium uppercase tracking-wider text-faint">Claim ledger · {watch.ledger?.length ?? 0} tracked claims</h2>
        {watch.ledger?.length ? (
          <ul className="mt-3 divide-y divide-line/60 rounded-xl border border-line/80">
            {watch.ledger.map((c) => (
              <li key={c.id} className="flex items-start gap-3 px-3.5 py-3 text-[13.5px]">
                <span className={`${statusChip(c.status)} mt-0.5 shrink-0`}>{STATUS_LABEL[c.status] ?? c.status}</span>
                <span className="flex-1 leading-relaxed text-muted">
                  {c.text}
                  {!c.confirmed && <span className="mt-1 block text-[11.5px] text-single">New, waiting for a second sighting before it is alerted</span>}
                  {c.pending_status && (
                    <span className="mt-1 block text-[11.5px] text-single">
                      Possible change to {STATUS_LABEL[c.pending_status] ?? c.pending_status}, waiting for confirmation
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-right font-mono text-[11px] text-faint">
                  {Math.round(c.confidence * 100)}%
                  <span className="block">{c.sources} src</span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-faint">Claims appear here after the first run.</p>
        )}
      </section>

      {!!watch.runs?.length && (
        <section className="mt-10">
          <h2 className="text-[11px] font-medium uppercase tracking-wider text-faint">Runs</h2>
          <ul className="mt-3 space-y-1 text-sm">
            {watch.runs.map((r) => (
              <li key={r.id} className="flex items-center gap-3 text-muted">
                <span className="w-36 text-faint">{dateTime(r.created_at)}</span>
                <span className="font-mono text-xs uppercase">{r.mode}</span>
                <span className={r.status === "completed" ? "text-agreed" : r.status === "failed" ? "text-disputed" : ""}>{r.status}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
