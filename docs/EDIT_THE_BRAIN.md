# Edit Signal Stream Without Touching Code

If you want to change how Signal Stream behaves, start here:

- [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml)

This file is loaded automatically on the next run.

You can also edit the same settings from the dashboard:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

Then open `http://127.0.0.1:8765` and click **Settings**.

## What you can safely change

### 1. Agent prompts

These sections control how each agent thinks:

- `[orchestrator]`
- `[scout]`
- `[analyst]`

Each one has a `prompt = """ ... """` block.

You can rewrite those instructions in plain English.

### 2. Scoring system

These sections control the base Analyst rubric:

- `[scoring.freshness]`
- `[scoring.max_points]`
- `[scoring.event_strength]`
- `[scoring]` for `low_value_phrases`

Example:

- raise `priority_match` if you want priority themes to matter more
- lower `corroboration` if you do not want repeated coverage to boost a story as much
- add phrases to `low_value_phrases` if you want more kinds of fluff penalized

### 3. Behavior switches

The `[behavior]` section controls the easiest product decisions:

- `scout_mode`: whether Scout uses code only, hybrid, or model help
- `analyst_mode`: whether Analyst uses code only, hybrid, or model help
- `relevance_policy`: `soft_keep` keeps borderline items for Analyst review
- `scout_note_enabled`: turns internal Scout notes on/off
- `model_score_adjustment_limit`: how far the model can move the base score
- `summary_mode`: short only or short plus expanded summary
- `visuals_mode`: article image, icon fallback, or no visuals
- `repeat_penalty_strength`: how strongly memory lowers repeated stories
- `entity_extraction`: known names plus model-discovered names, or stricter modes

## What happens after you edit it

Run Signal Stream again:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

Then open the dashboard:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

## Important note

You do **not** need to edit Python files just to change:

- prompts
- behavior switches
- scoring weights
- low-value phrases
- event-strength scores

Those are now controlled by the brain file and the dashboard Settings page.
