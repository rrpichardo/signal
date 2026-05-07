import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { FieldRow } from "./FieldRow";
import type { BrainSettings } from "@/lib/types";

interface ReaderSettingsFormProps {
  settings: BrainSettings;
  // onChange fires per-field so the parent can accumulate dirty state.
  onChange: (patch: Partial<BrainSettings>) => void;
}

// Reader-facing controls: what the digest shows and how summaries render.
// Values come from the `reader` section of the brain TOML.
export function ReaderSettingsForm({ settings, onChange }: ReaderSettingsFormProps) {
  const reader = (settings.reader as Record<string, string> | undefined) ?? {};
  const behavior = (settings.behavior as Record<string, unknown> | undefined) ?? {};

  function patchReader(key: string, value: unknown) {
    onChange({ reader: { ...reader, [key]: value } });
  }
  function patchBehavior(key: string, value: unknown) {
    onChange({ behavior: { ...behavior, [key]: value } });
  }

  return (
    <div className="space-y-6">
      <FieldRow id="summary_mode" label="Summary mode" help="Controls whether the expanded summary is shown on the digest.">
        <Select
          value={String(reader.summary_mode ?? "short_expanded")}
          onValueChange={(v) => patchReader("summary_mode", v)}
        >
          <SelectTrigger id="summary_mode"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="short_expanded">Short + expanded</SelectItem>
            <SelectItem value="short_only">Short only</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow id="visuals_mode" label="Visuals mode" help="Whether to show article images, icons only, or no visuals.">
        <Select
          value={String(reader.visuals_mode ?? "image_icon")}
          onValueChange={(v) => patchReader("visuals_mode", v)}
        >
          <SelectTrigger id="visuals_mode"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="image_icon">Image + icon fallback</SelectItem>
            <SelectItem value="icon_only">Icon only</SelectItem>
            <SelectItem value="none">None</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow id="scout_note_enabled" label="Scout note" help="Show the Scout agent's sourcing note on each signal.">
        <Switch
          id="scout_note_enabled"
          checked={String(behavior.scout_note_enabled) !== "false"}
          onCheckedChange={(v) => patchBehavior("scout_note_enabled", v)}
        />
      </FieldRow>

      <FieldRow id="entity_extraction" label="Entity extraction" help="How named entities are identified from article text.">
        <Select
          value={String(behavior.entity_extraction ?? "hybrid")}
          onValueChange={(v) => patchBehavior("entity_extraction", v)}
        >
          <SelectTrigger id="entity_extraction"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="hybrid">Hybrid (code + model)</SelectItem>
            <SelectItem value="model">Model only</SelectItem>
            <SelectItem value="known_list">Known list only</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>
    </div>
  );
}
