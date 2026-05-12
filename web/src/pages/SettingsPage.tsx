import { useState } from "react";
import { useBrain, useSaveSettings } from "@/lib/queries";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ReaderSettingsForm } from "@/components/settings/ReaderSettingsForm";
import { AgentSettingsForm } from "@/components/settings/AgentSettingsForm";
import { ScoringSettingsForm } from "@/components/settings/ScoringSettingsForm";
import { PromptsForm } from "@/components/settings/PromptsForm";
import { AdvancedBrainEditor } from "@/components/settings/AdvancedBrainEditor";
import { SaveBar } from "@/components/settings/SaveBar";
import { Skeleton } from "@/components/ui/skeleton";
import { pushToast } from "@/hooks/use-toast";
import type { BrainSettings } from "@/lib/types";

// Settings page — tabbed form over the brain TOML, with a sticky SaveBar for
// any unsaved changes across the Reader, Agent, and Prompts tabs.
// The Advanced tab has its own save flow with an AlertDialog confirmation.
export default function SettingsPage() {
  const { data: brain, isLoading } = useBrain();
  const { mutate: saveSettings, isPending } = useSaveSettings();

  // Local draft accumulates patches from each sub-form without hitting the backend.
  const [draft, setDraft] = useState<Partial<BrainSettings> | null>(null);
  const dirty = draft !== null;

  function handleChange(patch: Partial<BrainSettings>) {
    setDraft((prev) => ({ ...prev, ...patch }));
  }

  function handleSave() {
    if (!draft || !brain) return;
    const merged = { ...brain, ...draft };
    saveSettings(merged, {
      onSuccess: () => {
        setDraft(null);
        pushToast({ title: "Settings saved", description: "Changes take effect on the next agent run." });
      },
      onError: (err) => pushToast({ title: "Save failed", description: String(err), variant: "destructive" }),
    });
  }

  function handleDiscard() {
    setDraft(null);
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!brain) {
    return (
      <p className="py-20 text-center text-body text-muted-foreground">Could not load settings.</p>
    );
  }

  // Merged view: what the form renders is the saved state + any un-saved patches.
  const effective: BrainSettings = { ...brain, ...(draft ?? {}) };

  return (
    <div className="pb-24">
      <div className="mb-8">
        <h1 className="font-serif text-h1 font-semibold">Settings</h1>
        <p className="mt-2 text-ui text-muted-foreground">
          Changes apply on the next agent run.
        </p>
      </div>

      <Tabs defaultValue="reader">
        <TabsList>
          <TabsTrigger value="reader">Reader</TabsTrigger>
          <TabsTrigger value="agent">Agent</TabsTrigger>
          <TabsTrigger value="scoring">Scoring</TabsTrigger>
          <TabsTrigger value="prompts">Prompts</TabsTrigger>
          <TabsTrigger value="advanced">Advanced</TabsTrigger>
        </TabsList>

        <TabsContent value="reader">
          <ReaderSettingsForm settings={effective} onChange={handleChange} />
        </TabsContent>

        <TabsContent value="agent">
          <AgentSettingsForm settings={effective} onChange={handleChange} />
        </TabsContent>

        {/* Scoring tab: component weights + top-N knobs. Sum-of-weights must equal 100. */}
        <TabsContent value="scoring">
          <ScoringSettingsForm settings={effective} onChange={handleChange} />
        </TabsContent>

        <TabsContent value="prompts">
          <PromptsForm settings={effective} onChange={handleChange} />
        </TabsContent>

        {/* Advanced tab bypasses the shared SaveBar — it has its own AlertDialog guard. */}
        <TabsContent value="advanced">
          <AdvancedBrainEditor raw={brain.raw ?? ""} />
        </TabsContent>
      </Tabs>

      {/* Sticky save bar appears when Reader/Agent/Prompts tabs have unsaved changes. */}
      <SaveBar dirty={dirty} saving={isPending} onSave={handleSave} onDiscard={handleDiscard} />
    </div>
  );
}
