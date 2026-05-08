import { Link } from "react-router-dom";
import type { Signal } from "@/lib/types";
import { UrgencyBadge } from "./UrgencyBadge";
import { EventTypeKicker } from "./EventTypeKicker";
import { ScorePill } from "./ScorePill";
import { Byline } from "./Byline";
import { SignalHoverCard } from "./SignalHoverCard";

// Pick the visible blurb on the card. Prefers the model-written short_summary
// (curated 2-3 sentences explaining why the article is worth reading); falls
// back to the raw article lede clipped to ~200 chars when the analyst didn't
// produce one. Empty string when neither is available so the dek is hidden.
function pickBlurb(signal: Signal): string {
  const short = (signal.short_summary ?? "").trim();
  if (short) return short;
  const summary = (signal.summary ?? "").trim();
  if (!summary) return "";
  return summary.length > 200 ? `${summary.slice(0, 200).trimEnd()}…` : summary;
}

// Pick the hover-preview content. Prefers expanded_summary (multi-paragraph
// outline), falls back to the full summary so the hover always has something
// useful to show on signals where expanded_summary wasn't generated.
function pickHoverContent(signal: Signal): string {
  return (signal.expanded_summary || signal.summary || "").trim();
}

// Secondary list item — h3 headline, 2-line clamped dek, optional 64px thumb.
// Separated by hairline only; no card chrome so the feed reads like a paper.
export function SignalListItem({ signal, rank }: { signal: Signal; rank: number }) {
  const blurb = pickBlurb(signal);
  const hoverContent = pickHoverContent(signal);

  return (
    <article className="flex items-start gap-5 border-b border-border py-7 transition-colors hover:bg-muted/20">
      {/* Rank numeral — visually anchors hierarchy in the list without heavy chrome. */}
      <span className="hidden shrink-0 pt-1 font-serif text-h2 font-semibold text-muted-foreground/30 sm:block">
        {rank}
      </span>

      {/* Primary content column. Title + blurb wrapped in HoverCard so hovering
          anywhere in that region opens the expanded preview. */}
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex items-center gap-3">
          <EventTypeKicker type={signal.event_type} />
          <UrgencyBadge urgency={signal.urgency} />
        </div>

        <SignalHoverCard content={hoverContent} className="block">
          <Link
            to={`/signal/${signal.id}`}
            className="mb-2 block font-serif text-h3 font-semibold text-foreground hover:text-accent transition-colors"
          >
            {signal.title}
          </Link>

          {blurb && (
            <p className="mb-3 text-body text-muted-foreground line-clamp-3">{blurb}</p>
          )}
        </SignalHoverCard>

        <div className="flex flex-wrap items-center gap-3">
          {/* Pass url so the Source link appears next to the date in the byline.
              Previously omitted on list items, leaving readers without a quick
              way to jump to the article. */}
          <Byline source={signal.source} publishedAt={signal.published_at} url={signal.url} />
          <ScorePill score={signal.score} />
        </div>
      </div>

      {/* Thumbnail — 64x64 right-aligned image if present. Hidden on mobile to
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
