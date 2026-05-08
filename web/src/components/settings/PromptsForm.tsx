import { Textarea } from "@/components/ui/textarea";
import { FieldRow } from "./FieldRow";
import type { BrainSettings } from "@/lib/types";

interface PromptsFormProps {
  settings: BrainSettings;
  onChange: (patch: Partial<BrainSettings>) => void;
}

// System prompts for each agent. These are the "personality knobs" users are
// most likely to customise — shown as large textareas with serif body font.
export function PromptsForm({ settings, onChange }: PromptsFormProps) {
  const prompts = (settings.prompts as Record<string, string> | undefined) ?? {};

  function patch(key: string, value: string) {
    onChange({ prompts: { ...prompts, [key]: value } });
  }

  return (
    <div className="space-y-6">
      <FieldRow id="prompt_orchestrator" label="Orchestrator prompt" help="Shapes how the Orchestrator decides what to do next.">
        <Textarea
          id="prompt_orchestrator"
          rows={8}
          value={prompts.orchestrator ?? ""}
          onChange={(e) => patch("orchestrator", e.target.value)}
          className="font-mono text-xs"
        />
      </FieldRow>

      <FieldRow id="prompt_scout" label="Scout prompt" help="Guides the Scout when filtering and summarising articles.">
        <Textarea
          id="prompt_scout"
          rows={8}
          value={prompts.scout ?? ""}
          onChange={(e) => patch("scout", e.target.value)}
          className="font-mono text-xs"
        />
      </FieldRow>

      <FieldRow id="prompt_analyst" label="Analyst prompt" help="Guides the Analyst when scoring and ranking signals.">
        <Textarea
          id="prompt_analyst"
          rows={8}
          value={prompts.analyst ?? ""}
          onChange={(e) => patch("analyst", e.target.value)}
          className="font-mono text-xs"
        />
      </FieldRow>

      <FieldRow id="prompt_critic" label="Critic prompt" help="Reviews the digest after the Analyst; flags weak signals. Only used when Critic is enabled in Agent settings.">
        <Textarea
          id="prompt_critic"
          rows={8}
          value={prompts.critic ?? ""}
          onChange={(e) => patch("critic", e.target.value)}
          className="font-mono text-xs"
        />
      </FieldRow>
    </div>
  );
}
