import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn helper: merges class names while resolving Tailwind conflicts.
// Lets components accept overrideable className without pile-ups like "p-4 p-2".
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
