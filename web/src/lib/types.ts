// TypeScript mirrors of the Python wire formats from signal_stream/dashboard.py.
// These are intentionally permissive (lots of optional fields) because the Python
// side returns dicts straight from SQLite rows — fields can be null, missing, or
// JSON strings depending on the endpoint.

export type Urgency = "critical" | "high" | "medium" | "low";

// Score breakdown rows are pre-parsed in storage.list_signals (json.loads).
export interface ScoreBreakdownItem {
  name?: string;
  points?: number;
  reason?: string;
  [key: string]: unknown;
}

export interface Signal {
  id: string;
  title: string;
  score: number;
  urgency: Urgency;
  event_type: string;
  source: string;
  published_at: string | null;
  summary: string;
  short_summary: string;
  expanded_summary: string;
  why_it_matters?: string | null;
  url: string;
  score_breakdown: ScoreBreakdownItem[];
  // Entities is a free-form bag keyed by entity type (e.g. "company", "person").
  entities: Record<string, string[] | string>;
  image_url: string;
  icon_key: string;
  scout_note: string;
  relevance_label: string;
  created_at: string;
}

// Agent run header — what /api/run/latest returns. summary_json arrives as a
// raw JSON string; we parse it lazily where it's displayed.
export interface AgentRun {
  id: string;
  goal: string;
  status: "running" | "completed" | "failed" | string;
  started_at: string;
  completed_at: string | null;
  summary_json: string | null;
}

// Each timeline event from /api/events. payload_json is a raw JSON string.
export interface AgentEvent {
  id: number;
  run_id: string;
  agent: string;
  event_type: string;
  message: string;
  payload_json: string;
  created_at: string;
}

// Tool call rows from /api/tool-calls. input/output arrive as JSON strings.
export interface ToolCall {
  id: string;
  run_id: string;
  agent: string;
  tool: string;
  status: string;
  input_json: string;
  output_json: string;
  error: string | null;
  confidence: number | null;
  created_at: string;
}

export interface MemoryItem {
  id: string;
  topic: string;
  title: string;
  url: string;
  summary: string;
  signal_id: string;
  created_at: string;
}

// /api/settings returns the parsed brain TOML plus a "raw" string copy.
// Shape matches load_brain_file() — defensive typing because brains evolve.
export interface BrainSettings {
  raw: string;
  behavior?: Record<string, unknown>;
  prompts?: { orchestrator?: string; scout?: string; analyst?: string };
  scoring?: Record<string, unknown>;
  reader?: Record<string, unknown>;
  [key: string]: unknown;
}
