import { Slider } from "@/components/ui/slider";
import { FieldRow } from "./FieldRow";
import type { BrainSettings } from "@/lib/types";

interface ScoringSettingsFormProps {
  settings: BrainSettings;
  onChange: (patch: Partial<BrainSettings>) => void;
}

const COMPONENT_KEYS = [
  { key: "priority_match", label: "Priority match", max: 25 },
  { key: "company_match", label: "Company match", max: 25 },
  { key: "recency", label: "Recency", max: 15 },
  { key: "event_strength", label: "Event strength", max: 25 },
  { key: "corroboration", label: "Corroboration", max: 10 },
] as const;

// Scoring controls: 5-component weights (must sum to 100) and top-N knobs.
export function ScoringSettingsForm({ settings, onChange }: ScoringSettingsFormProps) {
  const scoring = (settings.scoring as Record<string, unknown> | undefined) ?? {};
  const components = (scoring.components as Record<string, number> | undefined) ?? {
    priority_match: 25,
    company_match: 25,
    recency: 15,
    event_strength: 25,
    corroboration: 10,
  };
  const behavior = (settings.behavior as Record<string, unknown> | undefined) ?? {};

  const currentSum = COMPONENT_KEYS.reduce((acc, { key }) => acc + Number(components[key] ?? 0), 0);
  const sumOk = currentSum === 100;

  function patchComponent(key: string, value: number) {
    onChange({
      scoring: {
        ...scoring,
        components: { ...components, [key]: value },
      },
    });
  }

  function patchBehavior(key: string, value: unknown) {
    onChange({ behavior: { ...behavior, [key]: value } });
  }

  return (
    <div className="space-y-8">
      {/* Component weights — must sum to 100. Backend validates this on save. */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <div className="kicker">Component weights</div>
          <span className={sumOk ? "text-meta text-muted-foreground" : "text-meta font-semibold text-destructive"}>
            Sum: {currentSum} / 100{!sumOk && " — must equal 100"}
          </span>
        </div>
        <p className="mb-4 text-ui text-muted-foreground">
          Each component contributes a maximum number of points to the 100-point score.
        </p>
        <div className="space-y-6">
          {COMPONENT_KEYS.map(({ key, label, max }) => {
            const val = Number(components[key] ?? 0);
            return (
              <FieldRow
                key={key}
                id={`component_${key}`}
                label={`${label} — ${val} pts (max ${max})`}
                help=""
              >
                <Slider
                  id={`component_${key}`}
                  min={0}
                  max={max}
                  step={1}
                  value={[val]}
                  onValueChange={([v]) => patchComponent(key, v)}
                />
              </FieldRow>
            );
          })}
        </div>
      </div>

      {/* Top-N knobs */}
      <div>
        <div className="kicker mb-4">Top-N limits</div>
        <div className="space-y-6">
          <FieldRow
            id="analyst_review_limit"
            label={`Articles sent to Groq — ${Number(behavior.analyst_review_limit ?? 40)}`}
            help="Top-N articles by Python score that get a full Groq review."
          >
            <Slider
              id="analyst_review_limit"
              min={1}
              max={100}
              step={1}
              value={[Number(behavior.analyst_review_limit ?? 40)]}
              onValueChange={([v]) => patchBehavior("analyst_review_limit", v)}
            />
          </FieldRow>

          <FieldRow
            id="analyst_review_batch_size"
            label={`Groq batch size — ${Number(behavior.analyst_review_batch_size ?? 1)}`}
            help="Articles per Groq request. 1 = most reliable; higher = faster but less focused."
          >
            <Slider
              id="analyst_review_batch_size"
              min={1}
              max={10}
              step={1}
              value={[Number(behavior.analyst_review_batch_size ?? 1)]}
              onValueChange={([v]) => patchBehavior("analyst_review_batch_size", v)}
            />
          </FieldRow>

          <FieldRow
            id="executive_summary_limit"
            label={`Executive summary size — ${Number(behavior.executive_summary_limit ?? 12)}`}
            help="Number of top signals shown in the digest executive summary block."
          >
            <Slider
              id="executive_summary_limit"
              min={1}
              max={40}
              step={1}
              value={[Number(behavior.executive_summary_limit ?? 12)]}
              onValueChange={([v]) => patchBehavior("executive_summary_limit", v)}
            />
          </FieldRow>
        </div>
      </div>
    </div>
  );
}
