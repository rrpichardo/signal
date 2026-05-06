import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "@/lib/utils";

// Form label — use the kicker style so it visually pairs with the rest of the
// editorial UI rather than reading as a generic SaaS form.
export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn("text-kicker uppercase tracking-wider font-semibold text-muted-foreground", className)}
    {...props}
  />
));
Label.displayName = LabelPrimitive.Root.displayName;
