import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Globe2 } from "lucide-react";
import { useSession } from "../session";
import type { Mode } from "../types";
import { Segmented, Spinner } from "./ui";

interface Props {
  onSubmit: (question: string, mode: Mode) => Promise<boolean | void>;
  placeholder?: string;
  autoFocus?: boolean;
  large?: boolean;
  busy?: boolean;
}

export default function Composer({ onSubmit, placeholder = "Ask anything…", autoFocus, large, busy }: Props) {
  const { user, allowance } = useSession();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>(user?.preferences.default_mode ?? "auto");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const deepDisabled = allowance ? !allowance.allow_deep : false;
  const effectiveMode: Mode = deepDisabled && mode === "deep" ? "auto" : mode;

  async function submit() {
    const q = text.trim();
    if (q.length < 3 || busy) return;
    const ok = await onSubmit(q, effectiveMode);
    if (ok !== false) setText("");
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className={`rounded-2xl border border-line bg-card shadow-xl shadow-black/20 transition-colors focus-within:border-accent/50 ${large ? "p-3.5" : "p-2.5"}`}>
      <textarea
        ref={ref}
        rows={large ? 2 : 1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={placeholder}
        aria-label="Question"
        className={`block w-full resize-none bg-transparent px-1.5 text-fg placeholder:text-faint focus:outline-none ${large ? "text-[16.5px] leading-7" : "text-[15px] leading-6"}`}
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Segmented<Mode>
            value={effectiveMode}
            onChange={setMode}
            size={large ? "sm" : "xs"}
            options={[
              { value: "auto", label: "Auto", hint: "Let the router pick Quick or Deep" },
              { value: "quick", label: "Quick", hint: "About a minute: ~8 sources, one pass" },
              { value: "deep", label: "Deep", hint: deepDisabled ? "No Deep runs left" : "3–6 minutes: up to 24 sources, gap-filling and counter-evidence", disabled: deepDisabled },
            ]}
          />
          <span className="hidden items-center gap-1 text-xs text-faint sm:flex" title="Change in Settings → Preferences">
            <Globe2 className="h-3.5 w-3.5" />
            {user?.preferences.region ?? "Global"}
          </span>
        </div>
        <button
          onClick={submit}
          disabled={busy || text.trim().length < 3}
          aria-label="Ask"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-accent-ink transition hover:bg-accent/90 disabled:bg-raised disabled:text-faint"
        >
          {busy ? <Spinner /> : <ArrowUp className="h-[18px] w-[18px]" strokeWidth={2.5} />}
        </button>
      </div>
    </div>
  );
}
