import { Link } from "react-router-dom";
import { useLatestRun, useSignals } from "@/lib/queries";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

// Compact run status surfaced in the masthead. Reads two queries that are
// already cached for the Digest page, so this is essentially free to render.
export function RunStatusPill() {
  const { data: run } = useLatestRun();
  const { data: signals } = useSignals();

  // No run yet → show a quiet "no runs" state instead of an empty pill.
  const hasRun = run && "id" in run;
  const status = hasRun ? (run as { status: string }).status : "idle";
  const startedAt = hasRun ? (run as { started_at: string }).started_at : null;
  const signalCount = signals?.length ?? 0;

  return (
    <Link
      to="/activity"
      className="group inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-meta text-muted-foreground transition-colors hover:text-foreground"
    >
      {/* Status dot — pulses while running so the masthead always shows liveness. */}
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          status === "running" && "bg-accent animate-pulse",
          status === "completed" && "bg-[hsl(var(--urgency-low))]",
          status === "failed" && "bg-destructive",
          status === "idle" && "bg-muted-foreground/40",
        )}
      />
      <span className="font-medium text-foreground">{signalCount} signals</span>
      {startedAt && <span aria-hidden>·</span>}
      {startedAt && <span>{relativeTime(startedAt)}</span>}
    </Link>
  );
}
