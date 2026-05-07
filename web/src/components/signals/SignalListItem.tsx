import { Link } from "react-router-dom";
import type { Signal } from "@/lib/types";
import { UrgencyBadge } from "./UrgencyBadge";
import { EventTypeKicker } from "./EventTypeKicker";
import { ScorePill } from "./ScorePill";
import { Byline } from "./Byline";

// Secondary list item — h3 headline, 2-line clamped dek, optional 64px thumb.
// Separated by hairline only; no card chrome so the feed reads like a paper.
export function SignalListItem({ signal, rank }: { signal: Signal; rank: number }) {
  return (
    <article className="flex items-start gap-5 border-b border-border py-7 transition-colors hover:bg-muted/20">
      {/* Rank numeral — visually anchors hierarchy in the list without heavy chrome. */}
      <span className="hidden shrink-0 pt-1 font-serif text-h2 font-semibold text-muted-foreground/30 sm:block">
        {rank}
      </span>

      {/* Primary content column */}
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex items-center gap-3">
          <EventTypeKicker type={signal.event_type} />
          <UrgencyBadge urgency={signal.urgency} />
        </div>

        <Link
          to={`/signal/${signal.id}`}
          className="mb-2 block font-serif text-h3 font-semibold text-foreground hover:text-accent transition-colors"
        >
          {signal.title}
        </Link>

        {signal.short_summary && (
          <p className="mb-3 text-body text-muted-foreground line-clamp-2">{signal.short_summary}</p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Byline source={signal.source} publishedAt={signal.published_at} />
          <ScorePill score={signal.score} />
        </div>
      </div>

      {/* Thumbnail — 64×64 right-aligned image if present. Hidden on mobile to
          keep the layout breathable on small screens. */}
      {signal.image_url && (
        <div className="hidden shrink-0 sm:block">
          <img
            src={signal.image_url}
            alt=""
            loading="lazy"
            className="h-16 w-16 rounded-sm object-cover border border-border"
          />
        </div>
      )}
    </article>
  );
}
