import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { FieldRow } from "./FieldRow";
import type { BrainSettings } from "@/lib/types";

interface AgentSettingsFormProps {
  settings: BrainSettings;
  onChange: (patch: Partial<BrainSettings>) => void;
}

// Agent behaviour controls: scout/analyst modes, scoring policies, and the
// model_score_adjustment_limit slider.
export function AgentSettingsForm({ settings, onChange }: AgentSettingsFormProps) {
  const behavior = (settings.behavior as Record<string, unknown> | undefined) ?? {};

  function patch(key: string, value: unknown) {
    onChange({ behavior: { ...behavior, [key]: value } });
  }

  const adjustLimit = Number(behavior.model_score_adjustment_limit ?? 20);

  return (
    <div className="space-y-6">
      <FieldRow id="scout_mode" label="Scout mode" help="How the Scout agent fetches and filters articles.">
        <Select value={String(behavior.scout_mode ?? "hybrid")} onValueChange={(v) => patch("scout_mode", v)}>
          <SelectTrigger id="scout_mode"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="code">Code (fast, deterministic)</SelectItem>
            <SelectItem value="hybrid">Hybrid</SelectItem>
            <SelectItem value="model">Model (slower, more nuanced)</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow id="analyst_mode" label="Analyst mode" help="How the Analyst agent scores and clusters signals.">
        <Select value={String(behavior.analyst_mode ?? "hybrid")} onValueChange={(v) => patch("analyst_mode", v)}>
          <SelectTrigger id="analyst_mode"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="code">Code</SelectItem>
            <SelectItem value="hybrid">Hybrid</SelectItem>
            <SelectItem value="model">Model</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow id="relevance_policy" label="Relevance policy" help="What to do with articles below the relevance threshold.">
        <Select value={String(behavior.relevance_policy ?? "soft_keep")} onValueChange={(v) => patch("relevance_policy", v)}>
          <SelectTrigger id="relevance_policy"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="soft_keep">Soft keep (downweight, keep in list)</SelectItem>
            <SelectItem value="hard_drop">Hard drop (remove from digest)</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow id="repeat_penalty_strength" label="Repeat penalty" help="How aggressively to downweight stories you've seen before.">
        <Select value={String(behavior.repeat_penalty_strength ?? "medium")} onValueChange={(v) => patch("repeat_penalty_strength", v)}>
          <SelectTrigger id="repeat_penalty_strength"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="light">Light</SelectItem>
            <SelectItem value="medium">Medium</SelectItem>
            <SelectItem value="strong">Strong</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow
        id="model_score_adjustment_limit"
        label={`Model score adjustment limit — ${adjustLimit} pts`}
        help="Maximum number of points the model can add or subtract from a signal's score."
      >
        <Slider
          id="model_score_adjustment_limit"
          min={0}
          max={100}
          step={5}
          value={[adjustLimit]}
          onValueChange={([v]) => patch("model_score_adjustment_limit", v)}
        />
      </FieldRow>

      <FieldRow id="enable_critic" label="Critic (reflection loop)" help="Critic reviews the Analyst's digest, scores it, and requests revisions until quality clears the threshold.">
        <Select value={String(behavior.enable_critic === true)} onValueChange={(v) => patch("enable_critic", v === "true")}>
          <SelectTrigger id="enable_critic"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="false">Disabled (skip Critic)</SelectItem>
            <SelectItem value="true">Enabled</SelectItem>
          </SelectContent>
        </Select>
      </FieldRow>

      <FieldRow
        id="max_critic_rounds"
        label={`Max critic rounds — ${Number(behavior.max_critic_rounds ?? 1)}`}
        help="How many revision loops before the Critic approves anyway."
      >
        <Slider
          id="max_critic_rounds"
          min={0}
          max={5}
          step={1}
          value={[Number(behavior.max_critic_rounds ?? 1)]}
          onValueChange={([v]) => patch("max_critic_rounds", v)}
        />
      </FieldRow>

      <FieldRow
        id="critic_score_threshold"
        label={`Critic score threshold — ${Number(behavior.critic_score_threshold ?? 70)}`}
        help="Digests scoring below this trigger a revision request (0–100)."
      >
        <Slider
          id="critic_score_threshold"
          min={0}
          max={100}
          step={5}
          value={[Number(behavior.critic_score_threshold ?? 70)]}
          onValueChange={([v]) => patch("critic_score_threshold", v)}
        />
      </FieldRow>
    </div>
  );
}
