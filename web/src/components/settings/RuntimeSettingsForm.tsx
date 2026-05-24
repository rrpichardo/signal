import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScalarGroupForm } from "./ScalarGroupForm";
import { useRuntimeSettings, useSaveRuntimeSettings } from "@/lib/queries";
import { pushToast } from "@/hooks/use-toast";
import type { ManifestEntry, RuntimeSettings } from "@/lib/types";

// Runtime knobs live in ai_tech.toml and are read once at process start, so they
// have their own save flow (separate endpoint) and a clear "restart required"
// message. The surgical backend writer preserves the sources/priorities arrays.
export function RuntimeSettingsForm({ manifest }: { manifest: ManifestEntry[] }) {
  const { data: runtime, isLoading } = useRuntimeSettings();
  const { mutate: save, isPending } = useSaveRuntimeSettings();
  const [draft, setDraft] = useState<Partial<RuntimeSettings> | null>(null);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!runtime) return <p className="text-body text-muted-foreground">Could not load runtime settings.</p>;

  const effective: RuntimeSettings = {
    brain: { ...runtime.brain, ...(draft?.brain ?? {}) },
    agent: { ...runtime.agent, ...(draft?.agent ?? {}) },
    delivery: { ...runtime.delivery, ...(draft?.delivery ?? {}) },
  };
  const dirty = draft !== null;

  function handleChange(patch: Record<string, unknown>) {
    // patch is { brain|agent|delivery: {...full section...} }
    setDraft((prev) => ({ ...prev, ...patch }));
  }

  function handleSave() {
    if (!draft) return;
    save(draft, {
      onSuccess: () => {
        setDraft(null);
        pushToast({
          title: "Runtime settings saved",
          description: "Restart the agent/dashboard for these to take effect.",
        });
      },
      onError: (err) => pushToast({ title: "Save failed", description: String(err), variant: "destructive" }),
    });
  }

  return (
    <div className="space-y-6">
      <div className="rounded-md border border-border bg-muted/40 p-3 text-ui text-muted-foreground">
        These knobs live in <code>ai_tech.toml</code> and are read when a process starts. Saving rewrites
        only the affected lines (your sources and priority groups are left untouched), but you must
        restart the agent or dashboard for changes to apply.
      </div>

      <ScalarGroupForm manifest={manifest} group="runtime" source={effective} onChange={handleChange} />

      <div className="flex justify-end gap-2">
        {dirty && (
          <Button variant="outline" size="sm" onClick={() => setDraft(null)} disabled={isPending}>
            Discard
          </Button>
        )}
        <Button variant="default" size="sm" onClick={handleSave} disabled={!dirty || isPending}>
          {isPending ? "Saving…" : "Save runtime settings"}
        </Button>
      </div>
    </div>
  );
}
