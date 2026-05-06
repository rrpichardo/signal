// Thin typed fetch layer. Every call goes through one function so retries,
// error shape, and base URL are all set in one place. Vite's dev proxy makes
// /api/* hit the Python backend on port 8765 without CORS configuration.

import type { AgentEvent, AgentRun, BrainSettings, MemoryItem, Signal, ToolCall } from "./types";

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
  signals: () => http<Signal[]>("/api/signals"),
  memory: () => http<MemoryItem[]>("/api/memory"),
  settings: () => http<BrainSettings>("/api/settings"),
  brain: () => http<{ raw: string }>("/api/brain"),
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
