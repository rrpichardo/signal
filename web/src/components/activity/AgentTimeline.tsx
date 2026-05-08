import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { AgentEvent } from "@/lib/types";
import { shortTime, tryParse } from "@/lib/format";
import { cn } from "@/lib/utils";

// Agent name → left-rail dot colour. These are intentionally distinct so the
// eye can quickly scan which agent owns each event.
const AGENT_COLORS: Record<string, string> = {
  orchestrator: "bg-accent",
  scout: "bg-blue-400",
  analyst: "bg-emerald-400",
};

function agentColor(agent: string): string {
  return AGENT_COLORS[agent.toLowerCase()] ?? "bg-muted-foreground/40";
}

interface AgentTimelineProps {
  events: AgentEvent[];
}

// Vertical timeline — colour-coded left rail, one row per event. Clicking a
// row expands the raw payload in a monospace block so nothing is hidden.
export function AgentTimeline({ events }: AgentTimelineProps) {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!events.length) {
    return <p className="text-meta text-muted-foreground">No events recorded for this run.</p>;
  }

  return (
    <div className="relative flex flex-col">
      {/* Vertical connector line drawn behind all dots. */}
      <div className="absolute left-[7px] top-3 bottom-3 w-px bg-border" aria-hidden />

      {events.map((ev) => {
        // Explicit generic keeps payload as Record so JSX children are typed.
        const payload = tryParse<Record<string, unknown>>(ev.payload_json);
        const hasPayload = payload !== undefined && Object.keys(payload).length > 0;
        const isOpen = expanded === ev.id;
        // Error events get a destructive treatment so the failure point is
        // visually obvious when scanning a long timeline. Matches the red
        // status badge on the run header card so the eye links the two.
        const isError = ev.event_type === "error";

        return (
          <div
            key={ev.id}
            className={cn(
              "relative flex gap-4 pb-5 last:pb-0",
              isError && "rounded-sm border border-destructive/40 bg-destructive/10 p-3",
            )}
          >
            {/* Coloured dot anchored to the vertical connector line. */}
            <div className="relative mt-1 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
              <span
                className={cn(
                  "h-2.5 w-2.5 rounded-full",
                  isError ? "bg-destructive" : agentColor(ev.agent),
                )}
              />
            </div>

            <div className="flex min-w-0 flex-1 flex-col gap-1">
              {/* Row header — agent label, event type, time. */}
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-meta">
                <span
                  className={cn(
                    "font-semibold",
                    isError && "text-destructive",
                    !isError && ev.agent.toLowerCase() === "orchestrator" && "text-accent",
                    !isError && ev.agent.toLowerCase() === "scout" && "text-blue-400",
                    !isError && ev.agent.toLowerCase() === "analyst" && "text-emerald-400",
                  )}
                >
                  {ev.agent}
                </span>
                <span className={cn(isError ? "text-destructive font-semibold" : "text-muted-foreground")}>
                  {ev.event_type}
                </span>
                <span className="ml-auto font-mono text-muted-foreground/60">{shortTime(ev.created_at)}</span>
              </div>

              {/* Human-readable message — the most important part. */}
              <p className={cn("text-ui", isError ? "text-destructive" : "text-foreground")}>
                {ev.message}
              </p>

              {/* Expandable payload for debugging — hidden until clicked. */}
              {hasPayload && (
                <button
                  onClick={() => setExpanded(isOpen ? null : ev.id)}
                  className="mt-1 inline-flex items-center gap-1 text-meta text-muted-foreground hover:text-foreground"
                >
                  {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                  payload
                </button>
              )}
              {isOpen && hasPayload && (
                <pre className="mt-2 overflow-x-auto rounded-sm border border-border bg-muted p-3 font-mono text-xs text-foreground/80">
                  {JSON.stringify(payload, null, 2)}
                </pre>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
