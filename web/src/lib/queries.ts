// React Query hooks per endpoint. Live screens (Digest, Activity) opt in to a
// 5-second refetch interval so the dashboard reflects the agent's progress
// without users having to reload.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

const LIVE_INTERVAL = 5_000;

export function useLatestRun() {
  return useQuery({ queryKey: ["run/latest"], queryFn: api.latestRun, refetchInterval: LIVE_INTERVAL });
}

export function useSignals() {
  return useQuery({ queryKey: ["signals"], queryFn: api.signals, refetchInterval: LIVE_INTERVAL });
}

// Detail view filters the cached list rather than hitting a per-id endpoint
// (the backend exposes only the bulk list today). This keeps detail navigation
// instant and avoids an extra round trip.
export function useSignal(id: string | undefined) {
  const query = useSignals();
  return { ...query, data: query.data?.find((s) => s.id === id) };
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
