import { useLatestRun, useEvents, useToolCalls, useStartRun } from "@/lib/queries";
import { RunHeaderCard } from "@/components/activity/RunHeaderCard";
import { AgentTimeline } from "@/components/activity/AgentTimeline";
import { ToolCallList } from "@/components/activity/ToolCallList";
import { Skeleton } from "@/components/ui/skeleton";
import { Loader2, Play } from "lucide-react";
import type { AgentEvent, AgentRun } from "@/lib/types";
import { tryParse } from "@/lib/format";

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
          <div className="kicker mb-3">Agent timeline</div>
          {/* Current-stage indicator — only visible when a run is in progress. */}
          {isRunning && events.length > 0 && (
            <CurrentStageChip events={events} />
          )}
          <div className={isRunning && events.length > 0 ? "mt-4" : "mt-6"}>
            {eventsLoading ? (
              <div className="space-y-4">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            ) : (
              <AgentTimeline events={events} />
            )}
          </div>
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

// Stage labels for the current-stage chip. These match the event_type values
// emitted by the Python backend during a run.
const STAGE_LABELS: Record<string, string> = {
  collecting: "Collecting sources",
  filtering: "Filtering articles",
  clustering: "Clustering topics",
  scoring: "Scoring signals",
  fetching_full_articles: "Fetching article pages",
  groq_reviewing: "Reviewing with Groq",
  writing_digest: "Writing digest",
  complete: "Complete",
  failed: "Failed",
};

// Shows the latest event's stage + any batch progress in the payload.
function CurrentStageChip({ events }: { events: AgentEvent[] }) {
  const latest = events.at(-1);
  if (!latest) return null;
  const label = STAGE_LABELS[latest.event_type] ?? latest.event_type;
  // Batch-progress is stored in payload as {progress: "12/40"} — surface inline.
  const payload = tryParse<Record<string, unknown>>(latest.payload_json);
  const progress = typeof payload?.progress === "string" ? payload.progress : null;
  return (
    <div className="flex items-center gap-2 rounded-sm border border-accent/40 bg-accent/5 px-3 py-1.5 text-meta">
      <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
      <span className="font-medium text-accent">{label}</span>
      {progress && <span className="text-muted-foreground">{progress}</span>}
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
