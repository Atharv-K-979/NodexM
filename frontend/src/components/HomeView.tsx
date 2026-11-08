import { useState } from "react";
import { ArrowUpRight, Landmark, LineChart, Microscope, Newspaper } from "lucide-react";
import { api, ApiError } from "../api";
import { useApp } from "../appContext";
import { navigate } from "../lib/route";
import { useSession } from "../session";
import type { Mode } from "../types";
import Composer from "./Composer";
import { Logo, useToast } from "./ui";

const EXAMPLES = [
  { icon: Landmark, text: "What will the EU AI Act cost SMEs compared with large companies?" },
  { icon: Microscope, text: "Does intermittent fasting improve long-term weight loss compared with calorie restriction?" },
  { icon: LineChart, text: "How fast did global data centre electricity demand grow in the last two years?" },
  { icon: Newspaper, text: "What changed in US export controls on advanced AI chips this year?" },
];

export function useAsk() {
  const toast = useToast();
  const { refresh } = useSession();
  const { refreshThreads, openSettings } = useApp();
  const [busy, setBusy] = useState(false);

  async function ask(question: string, mode: Mode, threadId?: string): Promise<boolean> {
    setBusy(true);
    try {
      if (threadId) {
        await api(`/api/threads/${threadId}/runs`, { body: { question, mode } });
      } else {
        const r = await api<{ thread_id: string }>("/api/threads", { body: { question, mode } });
        navigate(`/t/${r.thread_id}`);
      }
      await Promise.all([refreshThreads(), refresh()]);
      return true;
    } catch (e) {
      const err = e as ApiError;
      toast({
        kind: "error",
        text: err.message,
        action: err.status === 402 ? { label: "Add your own API key", run: () => openSettings("keys") } : undefined,
      });
      return false;
    } finally {
      setBusy(false);
    }
  }

  return { ask, busy };
}

export default function HomeView() {
  const { ask, busy } = useAsk();
  const { allowance, config, user } = useSession();

  const firstName = (user?.name || "").split(" ")[0];

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-[720px] animate-rise">
        <div className="mb-8 flex flex-col items-center text-center">
          <Logo size={44} />
          <h1 className="mt-5 font-serif text-[34px] font-semibold tracking-tight sm:text-[40px]">
            {firstName ? `What should we verify, ${firstName}?` : "What should we verify?"}
          </h1>
          <p className="mt-2 text-[15px] text-muted">Every claim is checked across sources and labelled agreed, disputed or single-source.</p>
        </div>

        <Composer large autoFocus busy={busy} placeholder="Ask a research question…" onSubmit={(q, m) => ask(q, m)} />

        <div className="mt-5 grid gap-2 sm:grid-cols-2">
          {EXAMPLES.map(({ icon: Icon, text }) => (
            <button
              key={text}
              disabled={busy}
              onClick={() => ask(text, "auto")}
              className="group flex items-start gap-3 rounded-xl border border-line/70 bg-card/40 px-3.5 py-3 text-left text-[13.5px] text-muted transition hover:border-line hover:bg-card hover:text-fg"
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-faint group-hover:text-accent" />
              <span className="flex-1">{text}</span>
              <ArrowUpRight className="h-4 w-4 shrink-0 opacity-0 transition group-hover:opacity-60" />
            </button>
          ))}
        </div>

        {allowance && (
          <p className="mt-8 text-center text-xs text-faint">
            {allowance.unlimited
              ? allowance.own_keys ? "Running on your own API key." : "Admin account."
              : allowance.quick_left + allowance.deep_left > 0
                ? `${allowance.quick_left} Quick and ${allowance.deep_left} Deep free runs left.`
                : "Free runs used. Add your own Gemini key in Settings to continue."}
            {config && !config.web_search && " Web search is off on this server; academic, regulatory and news APIs are still used."}
          </p>
        )}
      </div>
    </div>
  );
}
