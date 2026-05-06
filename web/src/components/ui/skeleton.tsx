import { cn } from "@/lib/utils";

// Skeleton shimmer used while React Query is loading the first batch of data.
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />;
}
