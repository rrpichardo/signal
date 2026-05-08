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

// Detail lookup: fetch a wide window (all-time, page 1, large page size) and
// filter client-side. Avoids a per-id backend endpoint while still resolving
// links to older signals that wouldn't appear on the latest-run page.
export function useSignal(id: string | undefined) {
  const query = useQuery({
    queryKey: ["signals", "detail-lookup"],
    queryFn: () => api.signals({ scope: "all", page: 1, page_size: 100 }),
  });
  return { ...query, data: query.data?.items.find((s) => s.id === id) };
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
