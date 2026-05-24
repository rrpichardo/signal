import { Slider } from "@/components/ui/slider";
import { TimingBadge } from "./TimingBadge";
import { getByPath, patchByPath } from "./paths";
import type { ManifestEntry } from "@/lib/types";

// V2 scoring editor: value weights (sum to 20), trust weights (sum to 1.0),
// trust penalty scale, hard caps, and the three live band tables. Every section
// and its range come from the manifest; subkeys come from the loaded scoring
// data. There are NO V1 "components" controls here — the engine doesn't use them.
interface Props {
  manifest: ManifestEntry[];
  source: unknown; // full BrainSettings
  onChange: (patch: Record<string, unknown>) => void;
}

const TABLE_CONTROLS = new Set<ManifestEntry["control"]>(["weights", "caps", "bands", "scale"]);

function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ScoringTablesForm({ manifest, source, onChange }: Props) {
  const sections = manifest.filter(
    (e) => e.group === "scoring" && e.exposure === "editable" && TABLE_CONTROLS.has(e.control),
  );

  return (
    <div className="space-y-10">
      {sections.map((entry) => {
        const sectionData = (getByPath(source, entry.id) as Record<string, number> | undefined) ?? {};
        const keys = Object.keys(sectionData);
        const sum = keys.reduce((acc, k) => acc + Number(sectionData[k] ?? 0), 0);

        let sumNote: { text: string; ok: boolean } | null = null;
        if (entry.validation === "sum_to_20") sumNote = { text: `Sum: ${sum} / 20`, ok: Math.abs(sum - 20) < 0.001 };
        if (entry.validation === "sum_to_1") sumNote = { text: `Sum: ${sum.toFixed(2)} / 1.00`, ok: Math.abs(sum - 1) < 0.001 };

        return (
          <div key={entry.id}>
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="kicker">{entry.label}</div>
              <div className="flex items-center gap-2">
                {sumNote && (
                  <span className={sumNote.ok ? "text-meta text-muted-foreground" : "text-meta font-semibold text-destructive"}>
                    {sumNote.text}
                    {!sumNote.ok && " — adjust to match"}
                  </span>
                )}
                <TimingBadge timing={entry.timing} />
              </div>
            </div>
            <p className="mb-4 text-ui text-muted-foreground">{entry.help}</p>
            <div className="space-y-6">
              {keys.map((key) => {
                const val = Number(sectionData[key] ?? 0);
                const fieldId = `${entry.id}.${key}`.replace(/[^a-zA-Z0-9]/g, "_");
                return (
                  <div key={key} className="flex flex-col gap-1.5">
                    <label htmlFor={fieldId} className="text-ui">
                      {humanize(key)} — {val}
                    </label>
                    <Slider
                      id={fieldId}
                      min={entry.min ?? 0}
                      max={entry.max ?? 100}
                      step={entry.step ?? 1}
                      value={[val]}
                      onValueChange={([v]) => onChange(patchByPath(source, `${entry.id}.${key}`, v))}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
