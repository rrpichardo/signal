import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { relativeTime, shortTime, tryParse } from "@/lib/format";
import type { AgentRun, RunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

// Top card on the Activity page — shows the run goal, status chip, and timing.
export function RunHeaderCard({ run }: { run: AgentRun }) {
  const statusVariant =
    run.status === "completed" ? "low" :
    run.status === "running" ? "medium" :
    run.status === "failed" ? "critical" : "default";

  // Pull the failure reason out of summary_json so the user doesn't have to
  // grep logs to find out why a red badge happened. Defensive parse — we'd
  // rather show "failed" with no reason than crash the activity page.
  const summary = tryParse<RunSummary>(run.summary_json);
  const failureReason = run.status === "failed" ? summary?.reason : undefined;

  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex flex-col gap-1">
            <div className="kicker">Latest run</div>
            <p className="font-serif text-h3 font-semibold">{run.goal || "Agent run"}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* Pulse animation for running state gives instant liveness signal. */}
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                run.status === "running" && "bg-[hsl(var(--urgency-medium))] animate-pulse",
                run.status === "completed" && "bg-[hsl(var(--urgency-low))]",
                run.status === "failed" && "bg-destructive",
              )}
            />
            <Badge variant={statusVariant}>{run.status}</Badge>
          </div>
        </div>

        {failureReason && (
          <div className="mt-3 rounded-sm border border-destructive/40 bg-destructive/10 p-3 text-meta text-destructive">
            <span className="font-semibold">Failed:</span> {failureReason}
            {summary?.last_action && (
              <span className="text-destructive/80"> (during {summary.last_action})</span>
            )}
          </div>
        )}

        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-meta text-muted-foreground">
          <span>Started {relativeTime(run.started_at)} ({shortTime(run.started_at)})</span>
          {run.completed_at && (
            <span>Finished {relativeTime(run.completed_at)}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
