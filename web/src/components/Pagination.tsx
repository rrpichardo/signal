import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface PaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  className?: string;
}

// Numbered pagination — shows up to 7 buttons in a stable layout: prev arrow,
// first page, ellipsis if there's a gap, the current ±1 window, ellipsis if
// there's another gap, last page, next arrow. Hides itself when there's only
// one page.
//
// Window logic deliberately stays simple — we don't animate or virtualize.
// For 100+ pages the user can still page sequentially; jumping by hand is rare.
export function Pagination({ page, totalPages, onPageChange, className }: PaginationProps) {
  if (totalPages <= 1) return null;

  // Build the list of page numbers + sentinel ellipsis values to render.
  // We use -1 / -2 as sentinel keys so React can distinguish the two ellipses.
  const items: number[] = [];
  const window = 1; // pages on each side of the current page
  const start = Math.max(2, page - window);
  const end = Math.min(totalPages - 1, page + window);

  items.push(1);
  if (start > 2) items.push(-1); // left ellipsis
  for (let p = start; p <= end; p++) items.push(p);
  if (end < totalPages - 1) items.push(-2); // right ellipsis
  if (totalPages > 1) items.push(totalPages);

  const goPrev = () => onPageChange(Math.max(1, page - 1));
  const goNext = () => onPageChange(Math.min(totalPages, page + 1));

  // Common button classes — keep tight so the row reads as a coherent control
  // strip rather than a scatter of differently-styled chips.
  const btn =
    "inline-flex h-9 min-w-9 items-center justify-center rounded-sm border border-border bg-background px-3 text-ui transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40";
  const active =
    "border-accent bg-accent text-accent-foreground hover:bg-accent";

  return (
    <nav
      className={cn("flex items-center justify-center gap-1.5 py-8", className)}
      aria-label="Pagination"
    >
      <button
        type="button"
        onClick={goPrev}
        disabled={page <= 1}
        className={btn}
        aria-label="Previous page"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      {items.map((it) =>
        it < 0 ? (
          <span
            key={`ellipsis${it}`}
            className="inline-flex h-9 min-w-9 items-center justify-center text-muted-foreground"
            aria-hidden
          >
            …
          </span>
        ) : (
          <button
            key={it}
            type="button"
            onClick={() => onPageChange(it)}
            aria-current={it === page ? "page" : undefined}
            className={cn(btn, it === page && active)}
          >
            {it}
          </button>
        ),
      )}

      <button
        type="button"
        onClick={goNext}
        disabled={page >= totalPages}
        className={btn}
        aria-label="Next page"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </nav>
  );
}
