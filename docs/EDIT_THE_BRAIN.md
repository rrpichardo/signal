# Edit Signal Stream Without Touching Code

If you want to change how Signal Stream behaves, start with the live brain file:

- [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml)

This file is loaded on the next run.

You can also edit the same settings from the dashboard:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

Then open `http://127.0.0.1:8765` and click **Settings**.

## What `agent_brain.toml` Controls

### Agent Prompts

These sections control the role instructions:

- `[orchestrator]`
- `[scout]`
- `[analyst]`
- `[critic]`

Each one has a `prompt = """ ... """` block.

Plain-English edits are fine. Keep prompts specific, keep JSON-output requirements explicit, and avoid adding facts the system cannot verify from the article text.

### Behavior Switches

The `[behavior]` section controls product behavior:

```toml
[behavior]
scout_mode = "hybrid"
analyst_mode = "hybrid"
model_score_adjustment_limit = 20
analyst_review_limit = 40
analyst_review_batch_size = 1
analyst_full_review = true
executive_summary_limit = 12
summary_mode = "short_expanded"
visuals_mode = "image_icon"
entity_extraction = "hybrid"
enable_critic = true
max_critic_rounds = 1
critic_score_threshold = 70
```

Common edits:

- Raise or lower `analyst_review_limit` to change how many top candidates Groq reviews.
- Keep `analyst_review_batch_size = 1` if you want one article per Groq request.
- Raise or lower `executive_summary_limit` to change how many top signals feed memory and the executive-summary view.
- Lower `model_score_adjustment_limit` if you want Groq to stay closer to the Python score.
- Set `enable_critic = false` to skip the reflection pass.

Mode meanings:

- `code`: Python logic only
- `hybrid`: Python first, then model judgment where configured
- `model`: lean more heavily on Groq judgment

### Scoring Components

The `[scoring.components]` section defines the five base-score buckets:

```toml
[scoring.components]
priority_match = 25
company_match = 25
recency = 15
event_strength = 25
corroboration = 10
```

The values must sum to 100. If you change one, adjust another.

### Scoring Bands

These sections control how the code maps article features into component points:

- `[scoring.recency_bands]`
- `[scoring.event_strength_bands]`
- `[scoring.priority_match_bands]`
- `[scoring.company_match_bands]`
- `[scoring.corroboration_bands]`

Examples:

- Raise `within_7_days` if you want older-but-still-current stories to stay competitive.
- Lower `opinion_or_listicle` if those articles are showing up too often.
- Raise `watchlist_strategic_action` if company launches, acquisitions, and partnerships should dominate.
- Lower `same_source_repeated` if repeated coverage from one source should not boost a cluster.

### Critic / Reflection Loop

The Critic reviews the proposed digest before it ships.

```toml
[behavior]
enable_critic = true
max_critic_rounds = 1
critic_score_threshold = 70
```

When enabled, the Orchestrator asks Critic to score the proposed digest. If the score is below the threshold and revision rounds remain, Critic's notes go back into the Orchestrator's context and the run tries another pass.

To change what Critic looks for, edit the `[critic]` prompt block.

### Dashboard Display

The `[display]` section controls dashboard defaults:

```toml
[display]
page_size = 10
default_scope = "latest"
```

Use `default_scope = "latest"` for the most recent complete run, or `"all"` to show stored signals across runs.

## What Lives Somewhere Else

Use [configs/ai_tech.toml](/Users/ricopichardo/Claude/signal/configs/ai_tech.toml) for:

- source list
- source limits
- source kinds such as `rss`, `youtube`, and `html_scrape`
- priority groups, weights, and keywords
- Groq model name and timeout
- storage path
- delivery digest limit
- dashboard port and worker timeout

The dashboard Settings tab includes editors for the common settings, including scoring weights, priority groups, and top-N knobs. Direct TOML editing is still the fallback for anything the UI does not expose.

## What Happens After You Edit

Run Signal Stream again:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

Then open the dashboard:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

The next run uses your changes. Already-stored signals are not rewritten retroactively.

## Safe Editing Checklist

- Keep `[scoring.components]` summing to 100.
- Keep `analyst_review_batch_size = 1` unless you intentionally want batched Groq review.
- Keep `analyst_review_limit` at or below the number of articles you can afford to review.
- Keep prompts asking for strict JSON when the worker expects structured output.
- Export `GROQ_API_KEY` before live runs; Signal Stream does not auto-load `.env`.

You do not need to edit Python files just to change prompts, behavior switches, scoring bands, top-N review limits, Critic settings, or dashboard display preferences.
