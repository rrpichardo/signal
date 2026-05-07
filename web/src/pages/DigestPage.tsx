import { useSignals, useLatestRun } from "@/lib/queries";
import { FeaturedSignal } from "@/components/signals/FeaturedSignal";
import { SignalListItem } from "@/components/signals/SignalListItem";
import { Skeleton } from "@/components/ui/skeleton";
import { relativeTime } from "@/lib/format";
import type { AgentRun } from "@/lib/types";
import { Link } from "react-router-dom";

// Digest page — the editorial home screen.
// Layout: kicker header → featured signal → numbered list of remaining signals.
export default function DigestPage() {
  const { data: signals, isLoading, error } = useSignals();
  const { data: run } = useLatestRun();

  const hasRun = run && "id" in run;
  const latestRun = hasRun ? (run as AgentRun) : null;

  const featured = signals?.[0];
  const rest = signals?.slice(1) ?? [];

  if (isLoading) {
    return (
      <div className="space-y-10">
        <div className="space-y-3">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-6 w-1/2" />
        </div>
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex gap-5 border-b border-border py-7">
            <Skeleton className="h-8 w-6 hidden sm:block" />
            <div className="flex-1 space-y-3">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-20 text-center">
        <p className="font-serif text-h3 text-foreground">Could not load signals</p>
        <p className="mt-2 text-ui text-muted-foreground">{String(error)}</p>
      </div>
    );
  }

  if (!signals?.length) {
    return (
      <div className="py-20 text-center">
        <p className="font-serif text-h3 text-foreground">No signals yet</p>
        <p className="mt-3 text-body text-muted-foreground max-w-md mx-auto">
          Run the agent to generate your first digest.
        </p>
        <pre className="mt-6 inline-block rounded-md border border-border bg-muted px-6 py-4 font-mono text-xs text-foreground/80">
          python3 -m signal_stream agent run
        </pre>
      </div>
    );
  }

  return (
    <div>
      {/* Section header: digest label + freshness. */}
      <div className="mb-10 flex items-baseline justify-between gap-4">
        <div className="kicker">Today's digest</div>
        {latestRun?.started_at && (
          <Link to="/activity" className="text-meta text-muted-foreground hover:text-foreground">
            Updated {relativeTime(latestRun.started_at)} · {signals.length} signals
          </Link>
        )}
      </div>

      {/* Featured story — full display-size treatment. */}
      {featured && <FeaturedSignal signal={featured} />}

      {/* Ranked list of remaining signals. */}
      {rest.length > 0 && (
        <div className="mt-12">
          <div className="kicker mb-0">More signals</div>
          {rest.map((signal, i) => (
            <SignalListItem key={signal.id} signal={signal} rank={i + 2} />
          ))}
        </div>
      )}
    </div>
  );
}
