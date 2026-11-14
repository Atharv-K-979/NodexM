import { useState } from "react";
import { ArrowRight, Copy, Layers, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { STATUS_LABEL, statusChip } from "../../lib/format";
import type { Claim, Evidence, Run } from "../../types";
import { Meter } from "../ui";
import { CitationChip } from "./Citation";
import { Figures } from "./ClaimsPanel";

/** The claims-first view: what was checked, what holds up, what is contested, and why. */
export default function FactsView({ run, onReadAnswer }: { run: Run; onReadAnswer: () => void }) {
  const disputed = run.claims.filter((c) => c.status === "disputed");
  const agreed = run.claims.filter((c) => c.status === "agreed");
  const single = run.claims.filter((c) => c.status === "single_source");
  const inflated = run.claims.filter((c) => c.independence && c.independence.sites > c.independence.independent);
  const usable = run.sources.filter((s) => s.duplicate_of == null);
  const independent = new Set(usable.map((s) => s.origin || s.domain)).size;
  const copies = run.sources.length - usable.length;

  if (!run.claims.length) return <p className="py-10 text-center text-sm text-muted">No verified facts yet.</p>;

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4">
        <Stat label="Facts checked" value={run.claims.length} />
        <Stat label="Disputed" value={disputed.length} tone="text-disputed" />
        <Stat label="Agreed" value={agreed.length} tone="text-agreed" />
        <Stat label="Single source" value={single.length} tone="text-single" />
      </div>
      <p className="-mt-5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-faint">
        <span className="flex items-center gap-1.5"><Layers className="h-3.5 w-3.5" />{run.sources.length} sources read → {independent} independent</span>
        {copies > 0 && <span className="flex items-center gap-1.5"><Copy className="h-3.5 w-3.5" />{copies} cop{copies === 1 ? "y" : "ies"} of other pages set aside</span>}
      </p>

      {inflated.length > 0 && (
        <div className="rounded-xl border border-single/30 bg-single/5 px-4 py-3 text-[13.5px] leading-relaxed">
          <p className="font-medium text-single">Copies would have inflated the agreement</p>
          <p className="mt-0.5 text-muted">
            {inflated.length} fact{inflated.length === 1 ? " was" : "s were"} reported by several sites that trace back to fewer original sources.
            Other tools would count each site; NodexM counts each original source once.
          </p>
        </div>
      )}

      {disputed.length > 0 && (
        <Section icon={ShieldAlert} title="Where sources disagree" tone="text-disputed">
          {disputed.map((c) => <DisputedCard key={c.id} claim={c} run={run} />)}
        </Section>
      )}
      {agreed.length > 0 && (
        <Section icon={ShieldCheck} title="Confirmed by independent sources" tone="text-agreed">
          {agreed.map((c) => <FactRow key={c.id} claim={c} run={run} />)}
        </Section>
      )}
      {single.length > 0 && (
        <Section icon={ShieldQuestion} title="Resting on one original source" tone="text-single">
          {single.map((c) => <FactRow key={c.id} claim={c} run={run} />)}
        </Section>
      )}
      {run.figures.length > 0 && (
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-faint">Key figures</h3>
          <Figures run={run} />
        </div>
      )}

      {run.draft && (
        <button onClick={onReadAnswer} className="btn-outline w-full justify-between py-3">
          <span>Read the written answer built from these facts</span>
          <ArrowRight className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

function Stat({ label, value, tone = "text-fg" }: { label: string; value: number; tone?: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <p className="text-[11px] uppercase tracking-wider text-faint">{label}</p>
      <p className={`mt-0.5 font-serif text-2xl font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

function Section({ icon: Icon, title, tone, children }: { icon: typeof ShieldCheck; title: string; tone: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className={`mb-2.5 flex items-center gap-2 text-sm font-semibold ${tone}`}>
        <Icon className="h-4 w-4" /> {title}
      </h3>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

function independenceLabel(c: Claim): string {
  const { independent, sites } = c.independence ?? { independent: c.sources.length, sites: c.sources.length };
  if (sites > independent) return `${sites} sites → ${independent} independent source${independent === 1 ? "" : "s"}`;
  return `${independent} independent source${independent === 1 ? "" : "s"}`;
}

function Quote({ e, run }: { e: Evidence; run: Run }) {
  const s = run.sources.find((x) => x.number === e.source);
  return (
    <li className="text-[13px] leading-relaxed">
      <p className="text-muted">“{e.quote}” <CitationChip n={e.source} run={run} /></p>
      <p className="text-[11px] text-faint">{s?.domain}{s?.origin ? ` · via ${s.origin}` : ""}</p>
    </li>
  );
}

function DisputedCard({ claim, run }: { claim: Claim; run: Run }) {
  const pro = claim.evidence.filter((e) => e.stance !== "contradicts");
  const con = claim.evidence.filter((e) => e.stance === "contradicts");
  return (
    <article className="rounded-xl border border-disputed/25 bg-card/50 p-4">
      <p className="text-[14.5px] font-medium leading-relaxed">{claim.text}</p>
      {claim.note && <p className="mt-1 text-[13px] text-muted">{claim.note}</p>}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line/70 p-3">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-agreed">One side</p>
          <ul className="space-y-2">{pro.map((e, i) => <Quote key={i} e={e} run={run} />)}</ul>
        </div>
        <div className="rounded-lg border border-line/70 p-3">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-disputed">The other side</p>
          <ul className="space-y-2">{con.map((e, i) => <Quote key={i} e={e} run={run} />)}</ul>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-faint">{independenceLabel(claim)} · {Math.round(claim.confidence * 100)}% confidence</p>
    </article>
  );
}

function FactRow({ claim, run }: { claim: Claim; run: Run }) {
  const [open, setOpen] = useState(false);
  const tone = claim.status === "agreed" ? "agreed" : "single";
  const inflated = claim.independence && claim.independence.sites > claim.independence.independent;
  return (
    <article className="rounded-xl border border-line/80 bg-card/40 px-4 py-3">
      <p className="text-[14px] leading-relaxed">
        {claim.text} {claim.sources.map((n) => <CitationChip key={n} n={n} run={run} />)}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
        <span className={statusChip(claim.status)}>{STATUS_LABEL[claim.status]}</span>
        <span className="flex items-center gap-1.5"><Meter value={claim.confidence} tone={tone} />{Math.round(claim.confidence * 100)}%</span>
        <span className={inflated ? "text-single" : ""}>{independenceLabel(claim)}</span>
        <button onClick={() => setOpen((o) => !o)} className="font-medium text-accent hover:underline">
          {open ? "Hide" : "Show"} quotes ({claim.evidence.length})
        </button>
      </div>
      {inflated && claim.note && <p className="mt-1.5 text-[12.5px] text-muted">{claim.note}</p>}
      {open && <ul className="mt-2.5 space-y-2 border-l-2 border-line pl-3">{claim.evidence.map((e, i) => <Quote key={i} e={e} run={run} />)}</ul>}
    </article>
  );
}
