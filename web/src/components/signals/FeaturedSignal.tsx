import { Link } from "react-router-dom";
import type { Signal } from "@/lib/types";
import { UrgencyBadge } from "./UrgencyBadge";
import { EventTypeKicker } from "./EventTypeKicker";
import { ScorePill } from "./ScorePill";
import { Byline } from "./Byline";

// The lead story. Display-size serif headline, full dek, optional 16:9 hero.
// No card chrome — just a hairline top divider and generous vertical breathing.
export function FeaturedSignal({ signal }: { signal: Signal }) {
  return (
    <article className="hairline pt-10">
      <div className="grid gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,5fr)] md:items-start">
        {/* Eyebrow column — kicker above urgency badge, score below. Stacks on
            mobile so the headline always leads. */}
        <div className="flex flex-col gap-3">
          <EventTypeKicker type={signal.event_type} />
          <UrgencyBadge urgency={signal.urgency} />
          <ScorePill score={signal.score} className="self-start" />
        </div>

        <div className="flex flex-col gap-5">
          <Link
            to={`/signal/${signal.id}`}
            className="font-serif text-display font-semibold leading-tight tracking-tight text-foreground hover:text-accent transition-colors"
          >
            {signal.title}
          </Link>

          {/* Dek: short_summary is curated by the agent specifically for this slot. */}
          {signal.short_summary && (
            <p className="font-serif text-dek text-foreground/85">{signal.short_summary}</p>
          )}

          <Byline source={signal.source} publishedAt={signal.published_at} url={signal.url} />

          {/* Hero image lives below the byline so the headline doesn't compete
              with imagery. We aspect-ratio the wrapper rather than the img so a
              missing image_url collapses cleanly. */}
          {signal.image_url && (
            <div className="overflow-hidden rounded-md border border-border bg-muted">
              <img
                src={signal.image_url}
                alt=""
                loading="lazy"
                className="aspect-[16/9] w-full object-cover"
              />
            </div>
          )}

          <Link
            to={`/signal/${signal.id}`}
            className="text-ui font-medium text-accent hover:underline underline-offset-4"
          >
            Read the full briefing →
          </Link>
        </div>
      </div>
    </article>
  );
}
