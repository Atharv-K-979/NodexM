import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Draft } from "../types";
import {
  splitSections,
  replaceSectionBody,
  type MarkdownSection,
} from "../lib/markdownSections";

interface Props {
  runId: string | null;
}

type ViewMode = "preview" | "edit";

export default function DraftPanel({ runId }: Props) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editMd, setEditMd] = useState<string>("");
  const [view, setView] = useState<ViewMode>("preview");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [regenSection, setRegenSection] = useState<string | null>(null);

  const editingRef = useRef(false);

  useEffect(() => {
    if (!runId) {
      setDraft(null);
      setEditMd("");
      setView("preview");
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const r = await fetch(`/api/drafts/${runId}`);
        if (!r.ok && r.status !== 404) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as Draft;
        if (!cancelled) {
          if (data && data.status !== "pending_pipeline") {
            if (!editingRef.current) {
              setDraft(data);
              setEditMd(data.content_md ?? "");
            } else {
              setDraft(data);
            }
          } else {
            setDraft(data);
          }
          setError(null);
          if (
            data &&
            (data.status === "pending_review" || data.status === "approved")
          ) {
            return;
          }
        }
      } catch (e: unknown) {
        if (!cancelled) setError(String(e));
      }
      timer = setTimeout(poll, 2000);
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  const sections = useMemo<MarkdownSection[]>(() => {
    return view === "preview" && draft && draft.status !== "pending_pipeline"
      ? splitSections(draft.content_md)
      : [];
  }, [draft, view]);

  if (!runId) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-dashed border-slate-300 p-8 text-center text-slate-500 text-sm">
        No draft yet — run a research topic on the left.
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-rose-200 p-6 text-rose-600 text-sm">
        Could not load draft: {error}
      </div>
    );
  }

  if (!draft || draft.status === "pending_pipeline") {
    return (
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-8 text-center text-slate-500 text-sm">
        <div className="inline-block w-5 h-5 border-2 border-brand-500 border-t-transparent rounded-full animate-spin mb-2" />
        <p>Generating draft… the agents are researching, verifying, and writing.</p>
      </div>
    );
  }

  const counts = draft.claims_summary || {};
  const disputed =
    (draft.verified?.disputed?.length ?? 0) > 0 ||
    (counts.disputed_count ?? 0) > 0;

  async function handleSave() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const r = await fetch(`/api/drafts/${runId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content_md: editMd }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const updated = (await r.json()) as Draft;
      setDraft((prev) =>
        prev
          ? { ...prev, content_md: editMd, version: updated.version }
          : updated
      );
      setSaveMsg(`Saved · v${updated.version}`);
      editingRef.current = false;
      setView("preview");
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 3000);
    }
  }

  async function handleRegen(section: MarkdownSection, instruction?: string) {
    const current = draft;
    if (!current) return;
    setRegenSection(section.heading);
    try {
      const r = await fetch(`/api/drafts/${runId}/regenerate-section`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section_name: section.heading,
          user_instruction:
            instruction ??
            "Rewrite this section with the same citations, clearer structure, and keep disputed claims explicitly flagged.",
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { rewritten_markdown: string; note: string };
      const replaced = replaceSectionBody(
        current.content_md,
        section,
        data.rewritten_markdown
      );
      setDraft({ ...current, content_md: replaced });
      setEditMd(replaced);
      editingRef.current = false;
      setSaveMsg(`Regenerated “${section.heading}” · ${data.note}`);
      setTimeout(() => setSaveMsg(null), 4500);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setRegenSection(null);
    }
  }

  async function handleApprove() {
    const current = draft;
    if (!current) return;
    setApproving(true);
    try {
      const r = await fetch(`/api/drafts/${runId}/approve`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as {
        status: string;
        approved_at?: string;
        delivery_status?: string;
      };
      setDraft({
        ...current,
        status: "approved",
        approved_at: data.approved_at ?? new Date().toISOString(),
      });
      setSaveMsg(
        `Approved · ${data.delivery_status ?? "delivery queued"}. Full delivery runs in Stage 7.`
      );
      setTimeout(() => setSaveMsg(null), 5000);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setApproving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h3 className="font-semibold text-slate-800">Draft review</h3>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {draft.critique_score !== undefined && (
              <span className="px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 font-medium">
                Critic: {draft.critique_score}/10
              </span>
            )}
            <span
              className={`px-2 py-1 rounded-full font-medium border ${
                draft.status === "approved"
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : "bg-amber-50 text-amber-700 border-amber-200"
              }`}
            >
              {draft.status === "approved" ? "Approved" : "Pending review"}
            </span>
            <span className="text-slate-500">v{draft.version}</span>
            {saveMsg && (
              <span className="text-emerald-700 font-medium px-2 py-1 rounded-full bg-emerald-50 border border-emerald-200">
                {saveMsg}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          <div className="inline-flex rounded-lg border border-slate-200 text-xs font-medium overflow-hidden">
            <button
              className={`px-3 py-1.5 ${
                view === "preview"
                  ? "bg-brand-600 text-white"
                  : "bg-white text-slate-600 hover:bg-slate-50"
              }`}
              onClick={() => setView("preview")}
              disabled={draft.status === "approved"}
            >
              Preview
            </button>
            <button
              className={`px-3 py-1.5 ${
                view === "edit"
                  ? "bg-brand-600 text-white"
                  : "bg-white text-slate-600 hover:bg-slate-50"
              }`}
              onClick={() => {
                setView("edit");
                setEditMd(draft.content_md);
                editingRef.current = true;
              }}
              disabled={draft.status === "approved"}
            >
              Edit markdown
            </button>
          </div>
          <div className="flex-1" />
          {view === "edit" && (
            <button
              onClick={handleSave}
              disabled={saving || draft.status === "approved"}
              className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save edits"}
            </button>
          )}
          <button
            onClick={handleApprove}
            disabled={approving || draft.status === "approved"}
            className="text-xs font-semibold px-4 py-1.5 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {approving
              ? "Approving…"
              : draft.status === "approved"
              ? "Approved"
              : "✓ Approve & deliver"}
          </button>
        </div>

        {disputed && draft.disputed_callout_md && (
          <div className="mb-4 rounded-lg border-l-4 border-rose-500 bg-rose-50 p-4">
            <h4 className="text-sm font-semibold text-rose-800 mb-1.5 flex items-center gap-1.5">
              ⚠️ Sources disagree on {counts.disputed_count ?? "some"} claims
            </h4>
            <div className="text-sm text-rose-900 prose prose-sm prose-rose max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {draft.disputed_callout_md}
              </ReactMarkdown>
            </div>
          </div>
        )}

        <div className="grid grid-cols-3 gap-3 text-xs mb-4">
          <div className="bg-emerald-50 border border-emerald-100 rounded p-2.5">
            <div className="text-emerald-900 font-semibold">
              {counts.agreed_count ?? draft.verified?.agreed?.length ?? 0}
            </div>
            <div className="text-emerald-700">agreed claims</div>
          </div>
          <div className="bg-rose-50 border border-rose-100 rounded p-2.5">
            <div className="text-rose-900 font-semibold">
              {counts.disputed_count ?? draft.verified?.disputed?.length ?? 0}
            </div>
            <div className="text-rose-700">disputed claims</div>
          </div>
          <div className="bg-amber-50 border border-amber-100 rounded p-2.5">
            <div className="text-amber-900 font-semibold">
              {counts.single_source_count ??
                draft.verified?.single_source?.length ??
                0}
            </div>
            <div className="text-amber-700">single-source</div>
          </div>
        </div>

        {view === "edit" ? (
          <textarea
            value={editMd}
            onChange={(e) => {
              setEditMd(e.target.value);
              editingRef.current = true;
            }}
            spellCheck={false}
            className="w-full h-[60vh] font-mono text-[12.5px] leading-relaxed rounded-lg border border-slate-300 p-3 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        ) : sections.length > 1 ? (
          <div className="space-y-6">
            {sections.map((sec, i) => (
              <section
                key={`${sec.heading}-${i}`}
                id={`sec-${i}`}
                className="relative group"
              >
                <div className="absolute -top-1 right-0 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                  <button
                    onClick={() => handleRegen(sec)}
                    disabled={!!regenSection || draft.status === "approved"}
                    className="text-[11px] font-semibold px-2.5 py-1 rounded-md bg-white border border-slate-200 text-slate-600 hover:border-brand-500 hover:text-brand-700 shadow-sm disabled:opacity-50"
                  >
                    {regenSection === sec.heading
                      ? "Regenerating…"
                      : "↻ Regenerate section"}
                  </button>
                </div>
                <article className="prose prose-sm max-w-none text-slate-700">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {sec.full}
                  </ReactMarkdown>
                </article>
              </section>
            ))}
          </div>
        ) : (
          <article className="prose prose-sm max-w-none text-slate-700">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {draft.content_md}
            </ReactMarkdown>
          </article>
        )}
      </div>
    </div>
  );
}
