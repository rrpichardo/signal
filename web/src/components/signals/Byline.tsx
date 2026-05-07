import { ExternalLink } from "lucide-react";
import { relativeTime } from "@/lib/format";

interface BylineProps {
  source: string;
  publishedAt?: string | null;
  url?: string;
}

// Source · time · external link. Used both on list items and detail headers.
export function Byline({ source, publishedAt, url }: BylineProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted-foreground">
      <span className="font-medium text-foreground">{source}</span>
      {publishedAt && <span aria-hidden>·</span>}
      {publishedAt && <span>{relativeTime(publishedAt)}</span>}
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
