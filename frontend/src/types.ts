export type Mode = "auto" | "quick" | "deep";
export type ClaimStatus = "agreed" | "disputed" | "single_source";
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Preferences {
  default_mode: Mode;
  region: "Global" | "EU" | "US" | "UK" | "APAC";
  depth: "summary" | "analyst" | "technical";
}

export interface User {
  id: number;
  email: string;
  name: string;
  preferences: Preferences;
  keys: { gemini: string; tavily: string };
  avatar_url: string | null;
  sign_in: { password: boolean; google: boolean };
  created_at: string;
}

export interface Allowance {
  can_run: boolean;
  reason: string;
  allow_deep: boolean;
  force_deep: boolean;
  quick_left: number;
  deep_left: number;
  runs_today: number;
  daily_limit: number;
  own_keys: boolean;
  unlimited: boolean;
}

export interface TraceEntry {
  node: string;
  status: "start" | "end" | "warning" | "error";
  summary: string;
  extra?: Record<string, unknown>;
  ts: string;
}

export interface Source {
  number: number;
  url: string;
  title: string;
  domain: string;
  type: string;
  searcher: string;
  published: string | null;
  credibility: number;
  credibility_reasons: string[];
  snippet: string;
  duplicate_of: number | null;
  evidence_count: number;
}

export interface Evidence {
  source: number;
  statement: string;
  quote: string;
  stance: "supports" | "contradicts" | "unrelated";
  before: string;
  after: string;
}

export interface Claim {
  id: number;
  key: string;
  text: string;
  status: ClaimStatus;
  confidence: number;
  note: string;
  sources: number[];
  evidence: Evidence[];
}

export interface DraftInfo {
  content_md: string;
  version: number;
  status: "pending_review" | "approved";
  critic_score: number | null;
  audit_score: number | null;
  approved_at: string | null;
  updated_at: string | null;
}

export interface AuditIssue {
  sentence: string;
  citations: number[];
  problem: string;
}

export interface Run {
  id: string;
  question: string;
  standalone_question: string | null;
  mode: Mode;
  requested_mode: Mode;
  status: RunStatus;
  error: string | null;
  thread_id: string | null;
  watch_id: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  used_own_keys: boolean;
  trace: TraceEntry[];
  usage: {
    calls?: number;
    lite_tokens?: { in: number; out: number };
    flash_tokens?: { in: number; out: number };
    search_credits?: number;
    cost_usd?: number;
  };
  plan: { id: string; question: string; searchers: string[] }[];
  figures: { label: string; value: number; unit: string; period: string; status: ClaimStatus; range?: number[] }[];
  critique: { accuracy: number; completeness: number; clarity: number; overall: number; issues: string[] } | null;
  audit: { score?: number; checked?: number; supported?: number; issues?: AuditIssue[] };
  warnings: string[];
  rounds: number;
  revisions: number;
  sources: Source[];
  claims: Claim[];
  draft: DraftInfo | null;
}

export interface ThreadSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  run_count: number;
  last_status: RunStatus | null;
  watch_id: number | null;
}

export interface ThreadDetail extends ThreadSummary {
  runs: Run[];
}

export interface ChangeEvent {
  id: number;
  run_id: string;
  kind: "new" | "status_changed" | "stale" | "returned";
  from_status: string | null;
  to_status: string | null;
  claim: string;
  detail: string;
  created_at: string;
}

export interface Watch {
  id: number;
  topic: string;
  thread_id: string | null;
  cadence: string;
  cadence_label: string;
  mode: Mode;
  next_run_at: string | null;
  active: boolean;
  email_alerts: boolean;
  last_run_id: string | null;
  created_at: string;
  recent_changes: number;
  changes?: ChangeEvent[];
  ledger?: { id: number; text: string; status: string; confidence: number; updated_at: string }[];
  runs?: { id: string; status: RunStatus; created_at: string; mode: Mode; cost_usd: number }[];
}

export interface PublicConfig {
  free_quick_runs: number;
  free_deep_runs: number;
  server_llm: boolean;
  web_search: boolean;
  email_alerts: boolean;
  google_client_id: string | null;
}
