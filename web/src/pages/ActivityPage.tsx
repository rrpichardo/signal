import { useLatestRun, useEvents, useToolCalls, useStartRun } from "@/lib/queries";
import { RunHeaderCard } from "@/components/activity/RunHeaderCard";
import { AgentTimeline } from "@/components/activity/AgentTimeline";
import { ToolCallList } from "@/components/activity/ToolCallList";
import { Skeleton } from "@/components/ui/skeleton";
import { Loader2, Play } from "lucide-react";
import type { AgentRun } from "@/lib/types";

// Activity page — run header, decision timeline, tool call table.
// All three queries use the 5s refetchInterval set in queries.ts so this page
// is always live during an active agent run.
export default function ActivityPage() {
  const { data: run, isLoading: runLoading } = useLatestRun();
  const { data: events = [], isLoading: eventsLoading } = useEvents();
  const { data: toolCalls = [], isLoading: callsLoading } = useToolCalls();
  const startRun = useStartRun();

  const hasRun = run && "id" in run;
  const isRunning = !!(hasRun && (run as AgentRun).status === "running");
  const isPending = (startRun.isPending ?? false) || isRunning;

  if (runLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-28 w-full rounded-lg" />
        <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (!hasRun) {
    return (
      <div className="py-20 text-center">
        <p className="font-serif text-h3 text-foreground">No runs yet</p>
        <p className="mt-2 text-body text-muted-foreground">
          Start a run to see agent activity here.
        </p>
        <div className="mt-6">
          <RunButton isPending={isPending} onClick={() => startRun.mutate()} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <RunHeaderCard run={run as AgentRun} />
        <RunButton isPending={isPending} onClick={() => startRun.mutate()} />
      </div>

      {/* Two-column on large screens; stacked on mobile. */}
      <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
        {/* Timeline: decision log for the Orchestrator, Scout, and Analyst. */}
        <section>
          <div className="kicker mb-6">Agent timeline</div>
          {eventsLoading ? (
            <div className="space-y-4">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : (
            <AgentTimeline events={events} />
          )}
        </section>

        {/* Tool calls: compact table with JSON sheet on row click. */}
        <section>
          <div className="kicker mb-6">Tool calls</div>
          {callsLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : (
            <ToolCallList calls={toolCalls} />
          )}
        </section>
      </div>
    </div>
  );
}

function RunButton({ isPending, onClick }: { isPending: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isPending}
      className="inline-flex shrink-0 items-center gap-2 rounded-md border border-accent bg-accent px-4 py-2 text-ui font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {isPending ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Play className="h-3.5 w-3.5" />
      )}
      {isPending ? "Running…" : "Start run"}
    </button>
  );
}
