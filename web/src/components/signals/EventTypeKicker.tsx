import { kickerLabel } from "@/lib/format";

// Tracked-uppercase label that sits above the headline, like a section eyebrow
// in a printed paper ("PLATFORM SHIFT", "REGULATORY RISK").
export function EventTypeKicker({ type }: { type: string | null | undefined }) {
  return <div className="kicker">{kickerLabel(type)}</div>;
}
