import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";

// Reusable form row: label + help text above, control below.
// Shared by all Settings sub-forms so the layout is consistent.
export function FieldRow({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && <p className="text-meta text-muted-foreground">{help}</p>}
    </div>
  );
}
