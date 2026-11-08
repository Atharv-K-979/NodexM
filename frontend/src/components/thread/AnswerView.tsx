import { Children, isValidElement, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, BadgeCheck, Check, Copy, Download, FileCheck2, Gavel, PencilLine, RefreshCw, X } from "lucide-react";
import { api, download } from "../../api";
import { splitSections, type MarkdownSection } from "../../lib/markdownSections";
import type { DraftInfo, Run } from "../../types";
import { Menu, MenuItem, Spinner, useToast } from "../ui";
import { CitationChip } from "./Citation";

const CITE = /\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\](?!\()/g;

function expand(group: string): number[] {
  const out: number[] = [];
  for (const part of group.split(/\s*,\s*/)) {
    const m = part.match(/^(\d+)\s*[–-]\s*(\d+)$/);
    if (m) for (let i = +m[1]; i <= +m[2] && i - +m[1] < 20; i++) out.push(i);
    else if (/^\d+$/.test(part)) out.push(+part);
  }
  return out;
}

/** Turn [1, 3] into markdown links the renderer swaps for citation chips. */
function linkCitations(md: string): string {
  return md.replace(CITE, (_, g: string) => `[${g}](#cite-${expand(g).join("-")})`);
}

export function answerBody(md: string): string {
  return md.split(/\n#{1,3}\s*Sources\s*\n/)[0].trim();
}

function textOf(children: ReactNode): string {
  return Children.toArray(children)
    .map((c) => (typeof c === "string" ? c : isValidElement(c) ? textOf((c.props as { children?: ReactNode }).children) : ""))
    .join("");
}

function Markdown({ md, run }: { md: string; run: Run }) {
  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("#cite-")) {
          const nums = href.slice(6).split("-").map(Number).filter(Boolean);
          return <span className="whitespace-nowrap">{nums.map((n) => <CitationChip key={n} n={n} run={run} />)}</span>;
        }
        return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
      },
      strong({ children }) {
        return /^disputed\b/i.test(textOf(children).trim()) ? <strong className="disputed-mark">{children}</strong> : <strong>{children}</strong>;
      },
    }),
    [run],
  );
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {linkCitations(md)}
    </ReactMarkdown>
  );
}

interface Props {
  run: Run;
  onDraft: (d: DraftInfo) => void;
  onShowClaims: (filter: "disputed" | "all") => void;
}

export default function AnswerView({ run, onDraft, onShowClaims }: Props) {
  const toast = useToast();
  const draft = run.draft!;
  const body = answerBody(draft.content_md);
  const sections = useMemo(() => splitSections(body), [body]);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [rewriting, setRewriting] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");

  const counts = run.claims.reduce<Record<string, number>>((a, c) => ({ ...a, [c.status]: (a[c.status] ?? 0) + 1 }), {});
  const audit = run.audit ?? {};

  async function act<T>(label: string, fn: () => Promise<T>, done?: string) {
    setBusy(label);
    try {
      const out = await fn();
      if (done) toast({ kind: "success", text: done });
      return out;
    } catch (e) {
      toast({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    act("save", async () => {
      onDraft(await api<DraftInfo>(`/api/runs/${run.id}/draft`, { method: "PATCH", body: { content_md: editText } }));
      setEditing(false);
    }, "Saved");

  const approve = () => act("approve", async () => onDraft(await api<DraftInfo>(`/api/runs/${run.id}/draft/approve`, { body: {} })), "Approved");

  const rewrite = (section: MarkdownSection) =>
    act("rewrite", async () => {
      onDraft(await api<DraftInfo>(`/api/runs/${run.id}/draft/rewrite-section`, { body: { heading: section.heading, instruction } }));
      setRewriting(null);
      setInstruction("");
    }, `Rewrote “${section.heading}”`);

  return (
    <div>
      {run.claims.length > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-2 text-xs">
          <button onClick={() => onShowClaims("all")} className="chip-agreed hover:brightness-125">{counts.agreed ?? 0} agreed</button>
          <button onClick={() => onShowClaims("disputed")} className="chip-disputed hover:brightness-125">{counts.disputed ?? 0} disputed</button>
          <button onClick={() => onShowClaims("all")} className="chip-single hover:brightness-125">{counts.single_source ?? 0} single source</button>
          <span className="text-faint">across {run.sources.filter((s) => s.duplicate_of == null).length} independent sources</span>
        </div>
      )}

      {run.warnings.length > 0 && (
        <details className="mb-4 rounded-lg border border-single/30 bg-single/5 px-3 py-2 text-[13px] text-single">
          <summary className="cursor-pointer">{run.warnings.length} note{run.warnings.length > 1 ? "s" : ""} from the agents</summary>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-single/90">{run.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}

      {editing ? (
        <div>
          <textarea value={editText} onChange={(e) => setEditText(e.target.value)} spellCheck={false} aria-label="Edit answer markdown" className="input h-[60vh] font-mono text-[13px] leading-relaxed" />
          <div className="mt-3 flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => setEditing(false)}><X className="h-4 w-4" /> Cancel</button>
            <button className="btn-primary" onClick={save} disabled={busy === "save"}>{busy === "save" ? <Spinner /> : <Check className="h-4 w-4" />} Save</button>
          </div>
        </div>
      ) : (
        <article className="answer">
          {sections.map((sec, i) => (
            <section key={`${sec.heading}-${i}`} className="group/sec relative">
              {sec.heading && draft.status !== "approved" && (
                <button
                  onClick={() => setRewriting(rewriting === sec.heading ? null : sec.heading)}
                  className="absolute right-0 top-0 z-10 hidden items-center gap-1 rounded-md border border-line bg-card px-2 py-1 text-[11px] font-medium text-muted hover:text-fg group-hover/sec:inline-flex sm:top-0.5"
                >
                  <RefreshCw className="h-3 w-3" /> Rewrite
                </button>
              )}
              {rewriting === sec.heading && (
                <div className="not-prose mb-3 mt-1 flex animate-rise flex-col gap-2 rounded-xl border border-accent/30 bg-accent/5 p-2.5 sm:flex-row">
                  <input autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} onKeyDown={(e) => e.key === "Enter" && rewrite(sec)} placeholder="How should this section change? (optional)" className="input flex-1 bg-bg" />
                  <button className="btn-primary" onClick={() => rewrite(sec)} disabled={busy === "rewrite"}>{busy === "rewrite" ? <Spinner /> : "Rewrite section"}</button>
                </div>
              )}
              <Markdown md={sec.full} run={run} />
            </section>
          ))}
        </article>
      )}

      {!editing && (
        <div className="mt-7 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-line/70 pt-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            {audit.checked ? (
              <span className="flex items-center gap-1.5" title="Each cited sentence is checked against the source it cites">
                <FileCheck2 className={`h-3.5 w-3.5 ${(audit.score ?? 0) >= 0.85 ? "text-agreed" : "text-single"}`} />
                {audit.supported}/{audit.checked} citations verified
              </span>
            ) : null}
            {run.critique && (
              <span className="flex items-center gap-1.5"><Gavel className="h-3.5 w-3.5" /> Critic {run.critique.overall}/10</span>
            )}
            {draft.status === "approved" && <span className="flex items-center gap-1.5 text-agreed"><BadgeCheck className="h-3.5 w-3.5" /> Approved · v{draft.version}</span>}
          </div>
          <div className="ml-auto flex items-center gap-1">
            <button className="btn-ghost px-2 py-1.5 text-xs" onClick={() => navigator.clipboard.writeText(body).then(() => toast({ kind: "success", text: "Copied" }), () => toast({ kind: "error", text: "Copy failed" }))}>
              <Copy className="h-3.5 w-3.5" /> Copy
            </button>
            <button className="btn-ghost px-2 py-1.5 text-xs" onClick={() => { setEditText(draft.content_md); setEditing(true); }}>
              <PencilLine className="h-3.5 w-3.5" /> Edit
            </button>
            <Menu trigger={(toggle) => <button className="btn-ghost px-2 py-1.5 text-xs" onClick={toggle}><Download className="h-3.5 w-3.5" /> Export</button>}>
              {(close) => (
                <>
                  <MenuItem onClick={() => { close(); act("export", () => download(`/api/runs/${run.id}/export?format=md`, `nodexm-${run.id}.md`)); }}>Markdown (.md)</MenuItem>
                  <MenuItem onClick={() => { close(); act("export", () => download(`/api/runs/${run.id}/export?format=html`, `nodexm-${run.id}.html`)); }}>Web page (.html)</MenuItem>
                </>
              )}
            </Menu>
            {draft.status !== "approved" && (
              <button className="btn-outline ml-1 px-2.5 py-1.5 text-xs" onClick={approve} disabled={busy === "approve"}>
                {busy === "approve" ? <Spinner className="h-3 w-3" /> : <BadgeCheck className="h-3.5 w-3.5" />} Approve
              </button>
            )}
          </div>
        </div>
      )}

      {!editing && (audit.issues?.length ?? 0) > 0 && (
        <details className="mt-3 text-[13px]">
          <summary className="flex cursor-pointer items-center gap-1.5 text-xs text-single"><AlertTriangle className="h-3.5 w-3.5" /> {audit.issues!.length} sentence{audit.issues!.length > 1 ? "s" : ""} flagged by the citation auditor</summary>
          <ul className="mt-2 space-y-2">
            {audit.issues!.map((i, k) => (
              <li key={k} className="rounded-lg border border-line/70 bg-card p-2.5">
                <p className="text-xs font-medium text-single">{i.problem}</p>
                <p className="mt-0.5 text-muted">{i.sentence}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
