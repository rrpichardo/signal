import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";
import { TimingBadge } from "./TimingBadge";
import type { ManifestEntry } from "@/lib/types";

// Reusable form row: label (with optional effect-timing badge) + help text,
// control below. Shared by all Settings sub-forms so the layout is consistent.
export function FieldRow({
  id,
  label,
  help,
  timing,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  timing?: ManifestEntry["timing"];
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        {timing && <TimingBadge timing={timing} />}
      </div>
      {children}
      {help && <p className="text-meta text-muted-foreground">{help}</p>}
    </div>
  );
}
