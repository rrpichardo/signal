import { ExternalLink } from "lucide-react";
import { relativeTime, signalDate } from "@/lib/format";

interface BylineProps {
  source: string;
  publishedAt?: string | null;
  url?: string;
}

// Source · absolute-date · external link. Used both on list items and detail headers.
// The absolute date is primary; relative time appears in a hover tooltip
// (so a reader instantly sees "May 3, 2026" but can still get "5 days ago" on hover).
// signalDate returns "" when the date is missing or unparseable, in which case
// the date span is hidden entirely instead of rendering "Invalid Date".
export function Byline({ source, publishedAt, url }: BylineProps) {
  const dateLabel = signalDate(publishedAt);
  const tooltip = relativeTime(publishedAt);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
      <span className="font-medium text-foreground">{source}</span>
      {dateLabel && <span aria-hidden>·</span>}
      {dateLabel && <span title={tooltip || undefined}>{dateLabel}</span>}
      {url && (
        <>
          <span aria-hidden>·</span>
          {/* External links open in a new tab; rel hardens against tab-jacking. */}
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-accent hover:underline"
          >
            Source
            <ExternalLink className="h-3 w-3" />
          </a>
        </>
      )}
    </div>
  );
}
