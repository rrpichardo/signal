import { Link } from "react-router-dom";
import type { Signal } from "@/lib/types";
import { UrgencyBadge } from "./UrgencyBadge";
import { ScorePill } from "./ScorePill";

interface RelatedSignalsRailProps {
  // Signals with the same event_type, excluding the current one.
  signals: Signal[];
}

// Bottom rail of the detail page — up to 3 related signals so readers have a
// natural next path without returning to the full digest list.
export function RelatedSignalsRail({ signals }: RelatedSignalsRailProps) {
  if (!signals.length) return null;

  return (
    <section>
      <div className="kicker mb-5">Related signals</div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {signals.slice(0, 3).map((s) => (
          <Link
            key={s.id}
            to={`/signal/${s.id}`}
            className="flex flex-col gap-2 rounded-md border border-border p-4 transition-colors hover:bg-muted/30"
          >
            <div className="flex items-center gap-2">
              <UrgencyBadge urgency={s.urgency} />
              <ScorePill score={s.score} />
            </div>
            <p className="font-serif text-ui font-semibold text-foreground line-clamp-3">{s.title}</p>
            <p className="text-meta text-muted-foreground">{s.source}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
