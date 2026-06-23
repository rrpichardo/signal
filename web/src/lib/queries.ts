// React Query hooks per endpoint. Live screens (Digest, Activity) opt in to a
// 5-second refetch interval so the dashboard reflects the agent's progress
// without users having to reload.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

const LIVE_INTERVAL = 5_000;

export function useLatestRun() {
  return useQuery({ queryKey: ["run/latest"], queryFn: api.latestRun, refetchInterval: LIVE_INTERVAL });
}

// Paged signals query. Live polling is enabled only on the first page of the
// `latest` scope so deep archive pages don't flicker under the user while they
// read. Older pages stay stable; the user refreshes manually if they want
// updates from a new run.
export function useSignals(params?: { scope?: "latest" | "all"; page?: number }) {
  const scope = params?.scope ?? "latest";
  const page = params?.page ?? 1;
  const isLive = scope === "latest" && page === 1;
  return useQuery({
    queryKey: ["signals", scope, page],
    queryFn: () => api.signals({ scope, page }),
    refetchInterval: isLive ? LIVE_INTERVAL : false,
  });
}

// Detail lookup: prefer the dedicated /api/signals/<id> endpoint which includes
// score_breakdown. Falls back gracefully when the signal is not found.
export function useSignal(id: string | undefined) {
  return useQuery({
    queryKey: ["signals", "detail", id],
    queryFn: () => (id ? api.signalById(id) : Promise.resolve(undefined)),
    enabled: Boolean(id),
  });
}

// Editor-generated briefing for the latest complete run.
// Falls back to null briefing when no Editor has run (status="skipped").
export function useExecutiveBriefing() {
  return useQuery({
    queryKey: ["executive-briefing"],
    queryFn: api.executiveBriefing,
    refetchInterval: LIVE_INTERVAL,
  });
}

// Display preferences (page_size, default_scope) editable in Settings.
// Cached without polling — these only change when the user saves.
export function useDisplaySettings() {
  return useQuery({ queryKey: ["display-settings"], queryFn: api.displaySettings });
}

export function useSaveDisplaySettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveDisplaySettings,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["display-settings"] });
      // Force a re-fetch of any signals query keys since page_size may have changed.
      qc.invalidateQueries({ queryKey: ["signals"] });
    },
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startRun,
    onSuccess: () => {
      // Kick live queries immediately so the activity tab shows the new run.
      qc.invalidateQueries({ queryKey: ["run/latest"] });
      qc.invalidateQueries({ queryKey: ["events"] });
      qc.invalidateQueries({ queryKey: ["tool-calls"] });
    },
  });
}

export function useEvents() {
  return useQuery({ queryKey: ["events"], queryFn: api.events, refetchInterval: LIVE_INTERVAL });
}

export function useToolCalls() {
  return useQuery({ queryKey: ["tool-calls"], queryFn: api.toolCalls, refetchInterval: LIVE_INTERVAL });
}

export function useMemory() {
  return useQuery({ queryKey: ["memory"], queryFn: api.memory });
}

export function useBrain() {
  return useQuery({ queryKey: ["settings"], queryFn: api.settings });
}

// The settings manifest rarely changes; cache it for the session.
export function useManifest() {
  return useQuery({ queryKey: ["settings-manifest"], queryFn: api.settingsManifest, staleTime: Infinity });
}

// Restart-required runtime knobs (ai_tech.toml). No polling — only changes on save.
export function useRuntimeSettings() {
  return useQuery({ queryKey: ["runtime-settings"], queryFn: api.runtimeSettings });
}

export function useSaveRuntimeSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveRuntimeSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runtime-settings"] }),
  });
}

// Save mutations invalidate the matching read so the form re-syncs with the
// freshly persisted settings the moment the request resolves.
export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export function useSaveBrain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.saveBrain,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export function useSources() {
  return useQuery({ queryKey: ["sources"], queryFn: api.sources });
}

export function useTestSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.testSource(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useTestAllSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.testAllSources,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useToggleSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.toggleSource(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useRemoveSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.removeSource(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useAddSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (source: Parameters<typeof api.addSource>[0]) =>
      api.addSource(source),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
}
