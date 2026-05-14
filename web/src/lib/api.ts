// Thin typed fetch layer. Every call goes through one function so retries,
// error shape, and base URL are all set in one place. Vite's dev proxy makes
// /api/* hit the Python backend on port 8765 without CORS configuration.

import type {
  AgentEvent,
  AgentRun,
  BrainSettings,
  DisplaySettings,
  ExecutiveBriefingResponse,
  MemoryItem,
  Signal,
  SignalsResponse,
  ToolCall,
} from "./types";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    // Try to surface the JSON error body the dashboard returns on 4xx; fall
    // back to the status text so callers always get a useful message.
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body?.error === "string" ? body.error : JSON.stringify(body);
    } catch {
      detail = res.statusText;
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

export const api = {
  latestRun: () => http<AgentRun | Record<string, never>>("/api/run/latest"),
  events: () => http<AgentEvent[]>("/api/events"),
  toolCalls: () => http<ToolCall[]>("/api/tool-calls"),
  // Paged signals endpoint. Defaults match the backend (latest scope, page 1).
  // The page_size param is omitted from the URL when not provided so the server
  // can apply the user's default from /api/display-settings.
  signals: (params?: { scope?: "latest" | "all"; page?: number; page_size?: number }) => {
    const qs = new URLSearchParams();
    if (params?.scope) qs.set("scope", params.scope);
    if (params?.page) qs.set("page", String(params.page));
    if (params?.page_size) qs.set("page_size", String(params.page_size));
    const query = qs.toString();
    return http<SignalsResponse>(`/api/signals${query ? `?${query}` : ""}`);
  },
  // Editor-generated briefing for the latest complete run. Null briefing when no Editor has run.
  executiveBriefing: () => http<ExecutiveBriefingResponse>("/api/executive-briefing"),
  // Single signal detail with full score_breakdown included.
  signalById: (id: string) => http<Signal>(`/api/signals/${encodeURIComponent(id)}`),
  memory: () => http<MemoryItem[]>("/api/memory"),
  settings: () => http<BrainSettings>("/api/settings"),
  brain: () => http<{ raw: string }>("/api/brain"),
  // Display preferences (page_size, default_scope) — read by DigestPage on mount.
  displaySettings: () => http<DisplaySettings>("/api/display-settings"),
  saveDisplaySettings: (payload: Partial<DisplaySettings>) =>
    http<{ status: string; display: DisplaySettings }>("/api/display-settings", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startRun: () =>
    http<{ status: "started" | "already_running" }>("/api/run", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  saveSettings: (payload: Partial<BrainSettings>) =>
    http<{ status: string; settings: BrainSettings }>("/api/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveBrain: (raw: string) =>
    http<{ status: string; brain: BrainSettings }>("/api/brain", {
      method: "POST",
      body: JSON.stringify({ raw }),
    }),
};
