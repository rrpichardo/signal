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

// Richer per-signal artifact written by the Analyst (Phase 2+).
// All fields optional — old signals return analyst_artifact: null.
export interface AnalystArtifact {
  mechanism?: string;
  key_actors?: Array<{ name: string; role: string }>;
  affected_parties?: string[];
  evidence_excerpts?: Array<{ quote: string; source_offset?: number }>;
  confidence?: "low" | "medium" | "high";
  confidence_reason?: string;
  model_confidence?: "low" | "medium" | "high";
  critic_flags?: string[];
  _meta?: {
    was_truncated: boolean;
    chars_total: number;
    chars_sent: number;
    extraction_quality?: "good" | "partial" | "poor";
    refresh_source?: string;
  };
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
  // Omitted by the list endpoint (/api/signals) for performance; present on /api/signals/<id>.
  score_breakdown?: ScoreBreakdownItem[];
  // Entities is a free-form bag keyed by entity type (e.g. "company", "person").
  entities: Record<string, string[] | string>;
  image_url: string;
  icon_key: string;
  scout_note: string;
  relevance_label: string;
  created_at: string;
  // Present on /api/signals/<id> for runs that have Phase 2+ artifacts; null otherwise.
  analyst_artifact?: AnalystArtifact | null;
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

// Parsed shape of summary_json. The Python side writes:
//   - {"reason": "...", "last_action": "..."} on failure
//   - {"reason": "stale: no events for 300s"} when the sweeper marks it stale
//   - {"articles": N, "signals": N, "output_path": "..."} on success
// All fields optional because old rows may be partial or empty.
export interface RunSummary {
  reason?: string;
  last_action?: string;
  articles?: number;
  signals?: number;
  output_path?: string;
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

// Paged digest list response from /api/signals?scope=...&page=...&page_size=...
// `run` is null when scope=all, or when the database has no completed runs yet.
export interface SignalsRunInfo {
  id: number;
  started_at: string;
  completed_at: string;
  signal_count: number;
}

export interface SignalsResponse {
  items: Signal[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  scope: "latest" | "all";
  run: SignalsRunInfo | null;
}

// User-editable display preferences from /api/display-settings.
// Backed by the [display] block in agent_brain.toml.
export interface DisplaySettings {
  page_size: number;
  default_scope: "latest" | "all";
}

// /api/settings returns the parsed brain TOML plus a "raw" string copy.
// Shape matches load_brain_file() — defensive typing because brains evolve.
export interface BrainSettings {
  raw: string;
  behavior?: Record<string, unknown>;
  prompts?: { orchestrator?: string; scout?: string; analyst?: string; critic?: string; editor?: string };
  scoring?: Record<string, unknown>;
  reader?: Record<string, unknown>;
  [key: string]: unknown;
}

// One entry from /api/settings/manifest — the source of truth describing a
// config knob. Mirrors signal_stream/settings_manifest.py.
export interface ManifestEntry {
  id: string; // dotted path, e.g. "behavior.scout_mode"
  file: "brain" | "runtime";
  group: "reader" | "agent" | "scoring" | "display" | "runtime" | "prompts" | "sources" | "advanced";
  label: string;
  help: string;
  control:
    | "select"
    | "slider"
    | "switch"
    | "number"
    | "text"
    | "textarea"
    | "weights"
    | "caps"
    | "bands"
    | "scale"
    | "list"
    | "external";
  timing: "next_run" | "next_page" | "restart";
  exposure: "editable" | "advanced";
  reason?: string;
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  validation?: "sum_to_20" | "sum_to_1";
}

// Restart-required runtime knobs from /api/runtime-settings (ai_tech.toml),
// grouped by TOML section.
export interface RuntimeSettings {
  brain: Record<string, unknown>;
  agent: Record<string, unknown>;
  delivery: Record<string, unknown>;
}

// Executive briefing written by the Editor worker (Phase 3+).
// Schema v2: structured paragraphs with subheads + bullets, plus key_takeaways
// / insights / summary as first-class fields. Backend normalizes legacy
// briefings on read so the UI only ever sees v2.

export interface ExecutiveBriefingParagraph {
  heading: string;
  body: string;
  bullets: string[];
  signal_ids: string[];
}

export interface ExecutiveBriefingTheme {
  label: string;
  summary: string;
  signal_ids: string[];
}

export interface ExecutiveBriefing {
  schema_version?: number;
  headline: string;
  summary: string;
  key_takeaways: string[];
  insights: string[];
  briefing_paragraphs: ExecutiveBriefingParagraph[];
  key_themes: ExecutiveBriefingTheme[];
  cross_signal_narrative?: string;
  watch_items: string[];
  source_signal_ids: string[];
  input_artifact_count: number;
  artifact_coverage: { with_artifact: number; missing: number; thin: number };
  any_artifact_truncated: boolean;
  generated_at: string;
}

// /api/executive-briefing response shape.
export interface ExecutiveBriefingResponse {
  briefing: ExecutiveBriefing | null;
  briefing_status: "pending" | "generated" | "partial" | "failed" | "skipped";
  generated_at: string | null;
  source_signal_ids: string[];
  run_id: string | null;
  stale: boolean;
  stale_from_run_id: string | null;
  stale_run_started_at: string | null;
}

export interface SourceHealth {
  source_id: string;
  status: "ok" | "error" | "paywall" | "empty" | "skipped" | null;
  checked_at: string | null;
  article_count: number;
  paywall_detected: boolean;
  error_msg: string;
  confidence: number;
}

export interface Source {
  id: string;
  name: string;
  kind: "rss" | "atom" | "youtube" | "html_scrape" | "sample" | "report";
  group: string;
  url: string | null;
  path: string | null;
  channel_id: string | null;
  limit: number;
  enabled: boolean;
  on_demand: boolean;
  // "toml" = static config, "manual" = added via UI, "discovered" = Phase 2
  origin: "toml" | "manual" | "discovered";
  health: SourceHealth | null;
}

export interface SourceTestResult {
  source_id: string;
  source_name: string;
  status: SourceHealth["status"];
  checked_at: string;
  article_count: number;
  paywall_detected: boolean;
  error_msg: string;
  confidence: number;
}

export interface TestAllResult {
  results: SourceTestResult[];
}
