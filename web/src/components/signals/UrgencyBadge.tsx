import { Badge } from "@/components/ui/badge";
import type { Urgency } from "@/lib/types";

// Urgency badge — uses the urgency-* color tokens defined in index.css so the
// same component reads correctly in both light and dark themes.
export function UrgencyBadge({ urgency }: { urgency: Urgency | string }) {
  // Map any string value defensively; SQLite rows can theoretically carry
  // values outside the canonical set, so we fall back to "low" styling.
  const variant = (["critical", "high", "medium", "low"] as const).includes(urgency as Urgency)
    ? (urgency as Urgency)
    : "low";

  return <Badge variant={variant}>{urgency}</Badge>;
}
