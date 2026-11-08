import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, Loader2 } from "lucide-react";
import { api, ApiError } from "../../api";
import { useApp } from "../../appContext";
import { navigate } from "../../lib/route";
import { useSession } from "../../session";
import type { Mode, ThreadDetail, Watch } from "../../types";
import Composer from "../Composer";
import { useAsk } from "../HomeView";
import { useToast } from "../ui";
import RunBlock from "./RunBlock";

export default function ThreadView({ id }: { id: string }) {
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { refreshThreads } = useApp();
  const { refresh } = useSession();
  const { ask, busy } = useAsk();
  const toast = useToast();
  const bottom = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);

  const load = useCallback(async () => {
    try {
      setThread(await api<ThreadDetail>(`/api/threads/${id}`));
      setError(null);
    } catch (e) {
      setError((e as ApiError).status === 404 ? "This thread does not exist or was deleted." : (e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    setThread(null);
    lastCount.current = 0;
    load();
  }, [load]);

  useEffect(() => {
    const n = thread?.runs.length ?? 0;
    if (n > lastCount.current && lastCount.current > 0) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    lastCount.current = n;
  }, [thread?.runs.length]);

  async function followUp(question: string, mode: Mode) {
    const ok = await ask(question, mode, id);
    if (ok) await load();
    return ok;
  }

  async function watchTopic() {
    if (!thread) return;
    if (thread.watch_id) return navigate(`/watches/${thread.watch_id}`);
    const first = thread.runs[0];
    try {
      const w = await api<Watch>("/api/watches", {
        body: { topic: first?.standalone_question || first?.question || thread.title, cadence: "weekly", mode: first?.mode === "deep" ? "deep" : "auto", thread_id: thread.id },
      });
      toast({ kind: "success", text: "Watching this topic. You'll be told when a claim changes." });
      await Promise.all([load(), refreshThreads()]);
      navigate(`/watches/${w.id}`);
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    }
  }

  if (error) return <p className="px-6 py-24 text-center text-muted">{error}</p>;
  if (!thread)
    return (
      <div className="flex justify-center py-32 text-faint">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );

  const anyActive = thread.runs.some((r) => r.status === "queued" || r.status === "running");

  return (
    <div className="flex min-h-full flex-col">
      <header className="z-20 flex items-center lg:sticky lg:top-0 justify-between gap-3 border-b border-line/60 bg-bg/85 px-4 py-2.5 backdrop-blur sm:px-8">
        <p className="min-w-0 truncate text-sm text-muted">{thread.title}</p>
        <button onClick={watchTopic} className={`btn shrink-0 whitespace-nowrap px-2.5 py-1.5 text-xs ${thread.watch_id ? "bg-accent/10 text-accent" : "btn-outline"}`}>
          <Eye className="h-3.5 w-3.5" /> {thread.watch_id ? "Watching" : "Watch topic"}
        </button>
      </header>

      <div className="mx-auto w-full max-w-[780px] flex-1 space-y-16 px-4 pb-40 pt-8 sm:px-6">
        {thread.runs.map((r) => (
          <RunBlock
            key={r.id}
            initial={r}
            onFinished={() => { refreshThreads(); refresh(); }}
            onRetry={(q, m) => followUp(q, m)}
          />
        ))}
        <div ref={bottom} />
      </div>

      <div className="sticky bottom-0 z-10 bg-gradient-to-t from-bg via-bg/95 to-transparent px-4 pb-4 pt-8 sm:px-6">
        <div className="mx-auto max-w-[780px]">
          <Composer busy={busy || anyActive} placeholder={anyActive ? "Wait for the current answer to finish…" : "Ask a follow-up…"} onSubmit={followUp} />
        </div>
      </div>
    </div>
  );
}
