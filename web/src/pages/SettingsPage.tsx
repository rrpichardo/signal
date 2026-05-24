import { useState } from "react";
import { useBrain, useManifest, useSaveSettings } from "@/lib/queries";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScalarGroupForm } from "@/components/settings/ScalarGroupForm";
import { ScoringTablesForm } from "@/components/settings/ScoringTablesForm";
import { RuntimeSettingsForm } from "@/components/settings/RuntimeSettingsForm";
import { AdvancedKnobsList } from "@/components/settings/AdvancedKnobsList";
import { PromptsForm } from "@/components/settings/PromptsForm";
import { AdvancedBrainEditor } from "@/components/settings/AdvancedBrainEditor";
import { SaveBar } from "@/components/settings/SaveBar";
import { Skeleton } from "@/components/ui/skeleton";
import { pushToast } from "@/hooks/use-toast";
import type { BrainSettings } from "@/lib/types";

// Settings page — every live config knob, driven by the backend manifest.
// Brain-file tabs (Reader/Agent/Scoring/Display/Prompts) share the sticky
// SaveBar (changes apply next run / next page load). The Runtime tab edits
// ai_tech.toml via its own save flow (restart required). Advanced has the raw
// TOML editor plus the read-only list of advanced-only knobs.
export default function SettingsPage() {
  const { data: brain, isLoading } = useBrain();
  const { data: manifestResp, isLoading: manifestLoading } = useManifest();
  const { mutate: saveSettings, isPending } = useSaveSettings();

  const [draft, setDraft] = useState<Partial<BrainSettings> | null>(null);
  const dirty = draft !== null;

  function handleChange(patch: Record<string, unknown>) {
    setDraft((prev) => ({ ...prev, ...patch }));
  }

  function handleSave() {
    if (!draft || !brain) return;
    saveSettings({ ...brain, ...draft }, {
      onSuccess: () => {
        setDraft(null);
        pushToast({ title: "Settings saved", description: "Changes take effect on the next agent run." });
      },
      onError: (err) => pushToast({ title: "Save failed", description: String(err), variant: "destructive" }),
    });
  }

  if (isLoading || manifestLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!brain || !manifestResp) {
    return <p className="py-20 text-center text-body text-muted-foreground">Could not load settings.</p>;
  }

  const manifest = manifestResp.manifest;
  const effective: BrainSettings = { ...brain, ...(draft ?? {}) };

  return (
    <div className="pb-24">
      <div className="mb-8">
        <h1 className="font-serif text-h1 font-semibold">Settings</h1>
        <p className="mt-2 text-ui text-muted-foreground">
          Every config knob. Each field shows when its change takes effect.
        </p>
      </div>

      <Tabs defaultValue="reader">
        <TabsList>
          <TabsTrigger value="reader">Reader</TabsTrigger>
          <TabsTrigger value="agent">Agent</TabsTrigger>
          <TabsTrigger value="scoring">Scoring</TabsTrigger>
          <TabsTrigger value="display">Display</TabsTrigger>
          <TabsTrigger value="runtime">Runtime</TabsTrigger>
          <TabsTrigger value="prompts">Prompts</TabsTrigger>
          <TabsTrigger value="advanced">Advanced</TabsTrigger>
        </TabsList>

        <TabsContent value="reader">
          <ScalarGroupForm manifest={manifest} group="reader" source={effective} onChange={handleChange} />
        </TabsContent>

        <TabsContent value="agent">
          <ScalarGroupForm manifest={manifest} group="agent" source={effective} onChange={handleChange} />
        </TabsContent>

        <TabsContent value="scoring">
          {/* Top-N scalars first, then the V2 weight/cap/band tables. No V1 controls. */}
          <ScalarGroupForm manifest={manifest} group="scoring" source={effective} onChange={handleChange} />
          <div className="mt-10">
            <ScoringTablesForm manifest={manifest} source={effective} onChange={handleChange} />
          </div>
        </TabsContent>

        <TabsContent value="display">
          <ScalarGroupForm manifest={manifest} group="display" source={effective} onChange={handleChange} />
        </TabsContent>

        {/* Runtime tab owns its own save flow (ai_tech.toml, restart required). */}
        <TabsContent value="runtime">
          <RuntimeSettingsForm manifest={manifest} />
        </TabsContent>

        <TabsContent value="prompts">
          <PromptsForm settings={effective} onChange={handleChange} />
        </TabsContent>

        <TabsContent value="advanced">
          <AdvancedBrainEditor raw={brain.raw ?? ""} />
          <AdvancedKnobsList manifest={manifest} />
        </TabsContent>
      </Tabs>

      {/* Sticky save bar for the brain-file tabs (not Runtime, which saves itself). */}
      <SaveBar dirty={dirty} saving={isPending} onSave={handleSave} onDiscard={() => setDraft(null)} />
    </div>
  );
}
