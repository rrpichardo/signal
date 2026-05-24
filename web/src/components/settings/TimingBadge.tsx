import { Badge } from "@/components/ui/badge";
import type { ManifestEntry } from "@/lib/types";

// Small badge telling the operator when a change takes effect.
const TIMING_META: Record<ManifestEntry["timing"], { label: string; variant: "low" | "medium" | "high" }> = {
  next_run: { label: "next run", variant: "low" },
  next_page: { label: "next page load", variant: "medium" },
  restart: { label: "restart required", variant: "high" },
};

export function TimingBadge({ timing }: { timing: ManifestEntry["timing"] }) {
  const meta = TIMING_META[timing];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}
