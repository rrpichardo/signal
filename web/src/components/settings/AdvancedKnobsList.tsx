import type { ManifestEntry } from "@/lib/types";

// Read-only list of every advanced-only config knob and WHY it isn't a simple
// control. Keeps the operator surface honest: nothing is silently hidden.
export function AdvancedKnobsList({ manifest }: { manifest: ManifestEntry[] }) {
  const advanced = manifest.filter((e) => e.exposure === "advanced");
  if (advanced.length === 0) return null;

  return (
    <div className="mt-10">
      <div className="kicker mb-1">Advanced-only knobs</div>
      <p className="mb-4 text-ui text-muted-foreground">
        These keys exist but aren't simple controls. Edit them directly in the config file
        (<code>ai_tech.toml</code> or the raw editor above). Each is here for a reason:
      </p>
      <div className="divide-y divide-border rounded-md border border-border">
        {advanced.map((e) => (
          <div key={e.id} className="flex flex-col gap-0.5 p-3">
            <code className="text-meta text-foreground">{e.id}</code>
            <span className="text-meta text-muted-foreground">{e.reason}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
