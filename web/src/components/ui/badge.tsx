import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

// Badge variants. The "urgency-*" variants intentionally use color tokens
// rather than fixed values so they shift with the active theme.
const badgeVariants = cva(
  "inline-flex items-center rounded-sm px-2 py-0.5 text-kicker font-semibold uppercase tracking-wider",
  {
    variants: {
      variant: {
        default: "bg-muted text-foreground",
        outline: "border border-border text-muted-foreground",
        critical: "bg-[hsl(var(--urgency-critical)/0.15)] text-[hsl(var(--urgency-critical))]",
        high: "bg-[hsl(var(--urgency-high)/0.15)] text-[hsl(var(--urgency-high))]",
        medium: "bg-[hsl(var(--urgency-medium)/0.15)] text-[hsl(var(--urgency-medium))]",
        low: "bg-[hsl(var(--urgency-low)/0.15)] text-[hsl(var(--urgency-low))]",
        accent: "bg-accent/10 text-accent",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
