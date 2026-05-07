import { ExternalLink } from "lucide-react";
import { relativeTime } from "@/lib/format";
import type { MemoryItem } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

// Individual memory item card. Kept minimal — topic chip, title, summary, time.
export function MemoryCard({ item }: { item: MemoryItem }) {
  return (
    <article className="border-b border-border py-5">
      <div className="mb-2 flex items-center gap-2">
        <Badge variant="outline" className="normal-case text-meta">{item.topic.replace(/_/g, " ")}</Badge>
        <span className="text-meta text-muted-foreground">{relativeTime(item.created_at)}</span>
      </div>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="font-serif text-ui font-semibold text-foreground">{item.title}</p>
          {item.summary && (
            <p className="mt-1 text-meta text-muted-foreground line-clamp-2">{item.summary}</p>
          )}
        </div>
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-0.5 shrink-0 text-muted-foreground hover:text-accent"
            aria-label="Source"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
        )}
      </div>
    </article>
  );
}
