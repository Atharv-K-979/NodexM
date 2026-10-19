export interface TraceEntry {
  node: string;
  status: "start" | "end" | "error";
  summary: string;
  extra?: Record<string, unknown>;
  ts: string;
}

export interface VerifiedClaims {
  agreed?: Array<{ claim: string; supporting_source_indices?: number[] }>;
  disputed?: Array<{
    claim: string;
    conflicting_source_indices?: number[];
    note?: string;
  }>;
  single_source?: Array<{ claim: string; source_index?: number }>;
}

export interface Draft {
  run_id: string;
  content_md: string;
  content_html: string;
  disputed_callout_md: string;
  claims_summary: {
    agreed_count?: number;
    disputed_count?: number;
    single_source_count?: number;
  };
  verified: VerifiedClaims;
  critique_score?: number;
  version: number;
  status:
    | "pending_pipeline"
    | "pending_review"
    | "approved";
  created_at?: string;
  updated_at?: string;
  approved_at?: string;
}

export interface RunInfo {
  id: string;
  topic: string;
  user_profile?: Record<string, unknown>;
  status: "queued" | "running" | "completed" | "failed";
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  trace_count: number;
  error?: string;
}
