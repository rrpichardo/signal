// Display formatters. Centralized so date/score/kicker formatting stays
// consistent across pages and is easy to tweak in one place.

import { format, formatDistanceToNow, parseISO } from "date-fns";

// Defensive ISO parser — backend timestamps come from Python's utc_now_iso(),
// which is always ISO 8601, but we guard against missing/null values.
function safeDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  try {
    const d = parseISO(value);
    return Number.isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

export function relativeTime(value: string | null | undefined): string {
  const d = safeDate(value);
  if (!d) return "";
  return formatDistanceToNow(d, { addSuffix: true });
}

export function longDate(value: string | null | undefined): string {
  const d = safeDate(value);
  if (!d) return "";
  // "Sunday, May 4 2026" — the masthead date format.
  return format(d, "EEEE, MMMM d yyyy");
}

export function shortTime(value: string | null | undefined): string {
  const d = safeDate(value);
  if (!d) return "";
  return format(d, "HH:mm:ss");
}

// Convert "platform_shift" → "PLATFORM SHIFT" for editorial kickers.
export function kickerLabel(eventType: string | null | undefined): string {
  if (!eventType) return "SIGNAL";
  return eventType.replace(/_/g, " ").toUpperCase();
}

// Score label used on the featured card and detail header.
export function scoreLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return "—";
  return `${Math.round(score)}/100`;
}

// Try parsing a stringified JSON payload; return undefined on failure so callers
// can decide whether to show a raw string or hide the section.
export function tryParse<T = unknown>(raw: string | null | undefined): T | undefined {
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return undefined;
  }
}
