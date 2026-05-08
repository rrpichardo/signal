import * as React from "react";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cn } from "@/lib/utils";

interface SignalHoverCardProps {
  // The multi-paragraph article outline. Falls back to summary when
  // expanded_summary isn't populated. Empty/falsy values disable the hover.
  content: string | null | undefined;
  // Wrapping element that triggers the hover. Anything visible on the card.
  children: React.ReactNode;
  // Tailwind classes forwarded to the trigger wrapper for layout convenience.
  className?: string;
}

// Hover preview that shows a 2-3 paragraph outline of the article when the
// reader hovers over the card, so they can triage without leaving the digest.
//
// Built on Radix Tooltip (already in deps) instead of HoverCard (not installed).
// The 250ms open delay prevents flicker as a reader scrolls the list. Content
// is split on blank lines into paragraphs; if no paragraph breaks exist we
// render the whole string as one block.
export function SignalHoverCard({ content, children, className }: SignalHoverCardProps) {
  const text = (content ?? "").trim();
  // Skip the hover entirely when there's nothing useful to show — avoids
  // empty popovers on signals where neither expanded_summary nor summary
  // ended up populated by the analyst.
  if (!text) {
    return <div className={className}>{children}</div>;
  }

  // Split on blank lines into paragraphs. Cap at 4 so the popover stays compact.
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .slice(0, 4);

  return (
    <TooltipPrimitive.Provider delayDuration={250}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>
          <div className={cn("cursor-default", className)}>{children}</div>
        </TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            side="top"
            align="start"
            sideOffset={8}
            collisionPadding={16}
            className={cn(
              "z-50 max-w-[480px] rounded-md border border-border bg-popover px-4 py-3",
              "text-sm leading-relaxed text-popover-foreground shadow-lg",
              "data-[state=delayed-open]:animate-in data-[state=closed]:animate-out",
              "data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0",
            )}
          >
            <div className="space-y-2">
              {paragraphs.map((p, idx) => (
                <p key={idx} className="text-muted-foreground">
                  {p}
                </p>
              ))}
            </div>
            <TooltipPrimitive.Arrow className="fill-popover" />
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}
