import {
  BookOpen,
  Calculator,
  CheckCircle2,
  Crosshair,
  FileCheck2,
  Gavel,
  GitCompare,
  ListTree,
  Network,
  PenLine,
  Quote,
  Route,
  Scale,
  Search,
  Server,
  ShieldCheck,
  Swords,
} from "lucide-react";

export const AGENTS: Record<string, { label: string; icon: typeof Search }> = {
  router: { label: "Router", icon: Route },
  planner: { label: "Planner", icon: ListTree },
  searcher: { label: "Specialist searchers", icon: Search },
  reader: { label: "Reader", icon: BookOpen },
  credibility: { label: "Credibility scorer", icon: ShieldCheck },
  extractor: { label: "Extractor", icon: Quote },
  clusterer: { label: "Claim clusterer", icon: Network },
  verifier: { label: "Verifier", icon: Scale },
  gap_finder: { label: "Gap finder", icon: Crosshair },
  devils_advocate: { label: "Devil's advocate", icon: Swords },
  quant_analyst: { label: "Quant analyst", icon: Calculator },
  writer: { label: "Writer", icon: PenLine },
  critic: { label: "Critic", icon: Gavel },
  citation_auditor: { label: "Citation auditor", icon: FileCheck2 },
  change_detector: { label: "Change detector", icon: GitCompare },
  finalize: { label: "Done", icon: CheckCircle2 },
  system: { label: "System", icon: Server },
};

/** Coarse stages shown while a run is in progress. */
export const STAGES: { key: string; label: string; nodes: string[] }[] = [
  { key: "plan", label: "Understanding and planning", nodes: ["router", "planner"] },
  { key: "search", label: "Searching specialist sources", nodes: ["searcher", "devils_advocate"] },
  { key: "read", label: "Reading and scoring sources", nodes: ["reader", "credibility"] },
  { key: "extract", label: "Extracting evidence with quotes", nodes: ["extractor"] },
  { key: "verify", label: "Verifying claims across sources", nodes: ["clusterer", "verifier", "gap_finder", "quant_analyst"] },
  { key: "write", label: "Writing the answer", nodes: ["writer"] },
  { key: "review", label: "Critic and citation audit", nodes: ["critic", "citation_auditor"] },
];

export function agentOf(node: string) {
  return AGENTS[node] ?? { label: node, icon: Server };
}
