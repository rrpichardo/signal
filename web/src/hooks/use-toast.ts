// Tiny toast hook — keeps a single global queue and exposes push/clear helpers.
// Simpler than the full shadcn boilerplate; we only need success/error toasts
// for save actions in Settings.

import { useSyncExternalStore } from "react";

type ToastVariant = "default" | "destructive";

interface ToastEntry {
  id: number;
  title: string;
  description?: string;
  variant?: ToastVariant;
}

let counter = 0;
let toasts: ToastEntry[] = [];
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((l) => l());
}

export function pushToast(entry: Omit<ToastEntry, "id">) {
  const id = ++counter;
  toasts = [...toasts, { ...entry, id }];
  notify();
  // Auto-dismiss after 4 seconds; matches Radix's default behavior.
  window.setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id);
    notify();
  }, 4000);
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  notify();
}

function subscribe(callback: () => void) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function useToasts(): ToastEntry[] {
  return useSyncExternalStore(subscribe, () => toasts, () => toasts);
}
