import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { FieldRow } from "./FieldRow";
import type { ManifestEntry } from "@/lib/types";

// Renders ONE scalar config control straight from its manifest entry, so the
// label, help, options, range, and effect-timing badge all come from the single
// source of truth (the backend manifest). Non-scalar controls (weights/caps/
// bands/textarea/external/list) are handled by their own components.
const SCALAR_CONTROLS = new Set(["select", "switch", "slider", "number", "text"]);

export function isScalarControl(control: ManifestEntry["control"]): boolean {
  return SCALAR_CONTROLS.has(control);
}

interface Props {
  entry: ManifestEntry;
  value: unknown;
  onChange: (value: unknown) => void;
}

export function ManifestField({ entry, value, onChange }: Props) {
  const id = entry.id.replace(/[^a-zA-Z0-9]/g, "_");

  if (entry.control === "select") {
    return (
      <FieldRow id={id} label={entry.label} help={entry.help} timing={entry.timing}>
        <Select value={String(value ?? entry.options?.[0] ?? "")} onValueChange={(v) => onChange(v)}>
          <SelectTrigger id={id}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(entry.options ?? []).map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FieldRow>
    );
  }

  if (entry.control === "switch") {
    return (
      <FieldRow id={id} label={entry.label} help={entry.help} timing={entry.timing}>
        <Switch id={id} checked={value === true} onCheckedChange={(v) => onChange(Boolean(v))} />
      </FieldRow>
    );
  }

  if (entry.control === "slider") {
    const num = Number(value ?? entry.min ?? 0);
    return (
      <FieldRow id={id} label={`${entry.label} — ${num}`} help={entry.help} timing={entry.timing}>
        <Slider
          id={id}
          min={entry.min ?? 0}
          max={entry.max ?? 100}
          step={entry.step ?? 1}
          value={[num]}
          onValueChange={([v]) => onChange(v)}
        />
      </FieldRow>
    );
  }

  if (entry.control === "number") {
    return (
      <FieldRow id={id} label={entry.label} help={entry.help} timing={entry.timing}>
        <Input
          id={id}
          type="number"
          min={entry.min}
          max={entry.max}
          step={entry.step}
          value={value === undefined || value === null ? "" : Number(value)}
          onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        />
      </FieldRow>
    );
  }

  // text
  return (
    <FieldRow id={id} label={entry.label} help={entry.help} timing={entry.timing}>
      <Input id={id} type="text" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
    </FieldRow>
  );
}
