import { scoreLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

// Compact score chip. Background fades to accent intensity as the score climbs,
// giving the eye a quick read of magnitude without a separate progress bar.
export function ScorePill({ score, className }: { score: number; className?: string }) {
  // Map 0–100 → 0.06–0.20 alpha so the highest scores still read as accent
  // rather than a full saturated chip (which would compete with the title).
  const alpha = Math.min(0.2, 0.06 + (score / 100) * 0.14).toFixed(2);

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-2 py-0.5 text-meta font-mono font-medium text-foreground",
        className,
      )}
      style={{ background: `hsl(var(--accent) / ${alpha})` }}
    >
      {scoreLabel(score)}
    </span>
  );
}
