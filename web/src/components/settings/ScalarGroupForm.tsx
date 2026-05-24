import { ManifestField, isScalarControl } from "./ManifestField";
import { getByPath, patchByPath } from "./paths";
import type { ManifestEntry } from "@/lib/types";

// Renders every editable scalar control for one manifest group, driven entirely
// by the backend manifest. `source` is the object the dotted ids resolve against
// (BrainSettings for brain groups, RuntimeSettings for the runtime group).
interface Props {
  manifest: ManifestEntry[];
  group: ManifestEntry["group"];
  source: unknown;
  onChange: (patch: Record<string, unknown>) => void;
}

export function ScalarGroupForm({ manifest, group, source, onChange }: Props) {
  const entries = manifest.filter(
    (e) => e.group === group && e.exposure === "editable" && isScalarControl(e.control),
  );

  return (
    <div className="space-y-6">
      {entries.map((entry) => (
        <ManifestField
          key={entry.id}
          entry={entry}
          value={getByPath(source, entry.id)}
          onChange={(value) => onChange(patchByPath(source, entry.id, value))}
        />
      ))}
    </div>
  );
}
