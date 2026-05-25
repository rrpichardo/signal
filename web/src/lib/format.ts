// Display formatters. Centralized so date/score/kicker formatting stays
// consistent across pages and is easy to tweak in one place.

import { format, formatDistanceToNow } from "date-fns";

// Defensive date parser — handles BOTH ISO 8601 (Python's utc_now_iso() output,
// e.g. "2026-05-04T04:20:15Z") AND RFC 2822 (RSS-feed published_at, e.g.
// "Sun, 03 May 2026 16:01:01 GMT"). Earlier versions used date-fns parseISO
// which silently returned Invalid Date on RFC 2822 strings, so card dates
// rendered as "Invalid Date". `new Date(str)` parses both formats natively.
function safeDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
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

// "May 3, 2026" — used on signal cards next to the source name.
// Returns empty string when the date is missing or unparseable so callers can
// hide the date span entirely instead of rendering "Invalid Date".
export function signalDate(value: string | null | undefined): string {
  const d = safeDate(value);
  if (!d) return "";
  return format(d, "MMM d, yyyy");
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

// Strip common Markdown syntax to plain text for short previews (hover cards)
// where rendering full markdown is overkill and raw marks like ** or | would
// leak. Not a parser — it just removes the marks.
export function stripMarkdown(value: string | null | undefined): string {
  if (!value) return "";
  let text = value;
  text = text.replace(/```[a-zA-Z0-9]*\n?/g, "");        // fenced code markers
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");  // images → alt text
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");   // links → link text
  text = text.replace(/^[\s|:-]*\|[\s|:-]*$/gm, "");     // table separator rows
  text = text.replace(/\|/g, " ");                        // remaining table pipes
  text = text.replace(/^\s{0,3}(#{1,6}|>)\s*/gm, "");    // heading / quote markers
  text = text.replace(/^\s*([-*+]|\d+\.)\s+/gm, "");     // list markers
  text = text.replace(/(\*\*|__|\*|_|~~|`)/g, "");        // emphasis / inline code
  text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n");
  return text.trim();
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
