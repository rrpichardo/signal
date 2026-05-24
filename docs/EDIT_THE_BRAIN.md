# Edit Signal Stream Without Touching Code

Two files control all live behavior. Start here:

| File | What it controls | When changes take effect |
|---|---|---|
| `configs/agent_brain.toml` | Prompts, behavior switches, V2 scoring weights and bands, display defaults | **Next agent run** (or next page load for display) |
| `configs/ai_tech.toml` | Groq model, timeouts, port, delivery limits, sources, priority groups, profile | **Restart required** (process reads this once at startup) |

You can edit either file directly, or use the dashboard Settings tab:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
# open http://127.0.0.1:8765 → Settings
```

The dashboard renders every editable knob with a badge showing when the change takes effect.

---

## `agent_brain.toml` — Live Knobs (next run)

### Prompts

Four role-instruction blocks:

```toml
[orchestrator]   # decides what to do each iteration
[scout]          # fetches and filters sources
[analyst]        # scores, clusters, sends articles to Groq
[critic]         # reviews the proposed digest before finalization
```

Each has a `prompt = """ ... """` multiline string. Keep JSON-output requirements explicit — the workers parse structured responses from Groq.

### Behavior Switches

```toml
[behavior]
scout_mode = "hybrid"                 # code | hybrid | model
analyst_mode = "hybrid"               # code | hybrid | model
relevance_policy = "strict"           # strict | inclusive
entity_extraction = "hybrid"          # code | hybrid | model

scout_note_enabled = true             # include scout notes in the digest header
analyst_review_limit = 40             # max articles sent to Groq for review
analyst_full_review = true            # send full article text (vs. summary only)
analyst_retry_max_attempts = 1        # Groq retries per article on parse failure
model_score_adjustment_limit = 20     # max points Groq can move the Python score
executive_summary_limit = 12          # top-N signals fed to memory + exec summary
executive_summary_min_score = 65      # minimum score to appear in exec summary
max_article_tokens_for_llm = 12000    # token cap per article sent to Groq

summary_mode = "short_expanded"       # short | short_expanded | full
visuals_mode = "image_icon"           # none | icon | image_icon | image

enable_critic = true                  # run the Critic reflection pass
max_critic_rounds = 1                 # max revision rounds before finalization
critic_score_threshold = 70           # Critic score below this triggers a revision
```

Mode meanings: `code` = Python only, `hybrid` = Python first then Groq judgment where configured, `model` = lean on Groq.

### V2 Scoring — Value Weights

The Python scorer assigns a base score up to 100. These six weights control how those 100 points are distributed. **They must sum to 20.**

```toml
[scoring.value_weights]
relevance_to_richard     = 4   # how directly this fits the configured profile
strategic_importance     = 4   # significance for strategy/competitive position
actionability            = 3   # can the reader act on this today?
credibility              = 3   # source quality and evidence quality
novelty                  = 3   # new information, not recycled coverage
time_sensitivity         = 3   # urgency — will this matter less in a week?
```

Each weight is a multiplier applied to a 0-5 subscale. A weight of 4 means that dimension can contribute up to 20 points; the full budget is 20 × 5 = 100.

### V2 Scoring — Trust Weights

Three trust-deficit weights penalize articles with weak evidence or questionable sourcing. **They must sum to 1.0.**

```toml
[scoring.trust_weights]
claim_support_deficit       = 0.4   # unsupported assertions
hype_or_manipulation_deficit = 0.3  # marketing language, sensationalism
source_credibility_deficit  = 0.3   # low-quality or anonymous sourcing
```

### V2 Scoring — Trust Penalty Scale

How aggressively trust deficits reduce the base score:

```toml
[scoring.trust_penalty]
scale = 0.25   # 0.0 = no penalty, 1.0 = full deficit applied
```

### V2 Scoring — Hard Caps

An article matching one of these patterns is capped at a fixed maximum score regardless of weights, preventing low-value content from outranking substantive articles.

```toml
[scoring.hard_caps]
generic_tutorial       = 30   # "How to use X" without new information
sponsored_or_pr        = 20   # press releases, sponsored posts
duplicate_or_rehash    = 35   # obvious rehash of a story already in the digest
pure_opinion_no_signal = 40   # opinion piece with no factual news hook
```

Set a cap higher to allow that content type to rank normally; lower to suppress it harder.

### V2 Scoring — Live Bands

These three sections control how the Python rubric converts article features to points. Each band value is a point award (not a multiplier).

**`[scoring.priority_match_bands]`** — how directly the article matches your configured priority groups (contributes to `relevance_to_richard`):

```toml
[scoring.priority_match_bands]
no_match                     = 0
weak_incidental              = 5
one_relevant_not_central     = 10
one_central_or_two_weak      = 15
one_central_with_support     = 20
direct_high_impact           = 25
```

**`[scoring.company_match_bands]`** — how centrally a watchlist company features (contributes to `strategic_importance`):

```toml
[scoring.company_match_bands]
no_match                      = 0
one_passing                   = 5
relevant_not_central          = 10
watchlist_central             = 15
watchlist_in_title_or_lede    = 20
watchlist_strategic_action    = 25
```

**`[scoring.event_strength_bands]`** — how strong the underlying event is (contributes to `strategic_importance`):

```toml
[scoring.event_strength_bands]
none                    = 0
opinion_or_listicle     = 5
useful_analysis         = 10
product_update_or_signal = 15
launch_funding_regulation = 20
major_platform_shift    = 25
```

Raise a band value to make that signal level score higher. Lower it to suppress it.

### Display Defaults

Takes effect on next page load (no restart or run needed):

```toml
[display]
page_size = 10           # articles per page in the digest view
default_scope = "latest" # latest | all
```

---

## `ai_tech.toml` — Runtime Knobs (restart required)

These are read once when a process starts. Saving via the dashboard Settings → Runtime tab writes only the changed lines; your `[[sources]]` and `[[priorities]]` arrays are left byte-for-byte intact.

```toml
[brain]
model = "meta-llama/llama-4-scout-17b-16e-instruct"  # Groq model name
timeout_seconds = 60                                  # per-request Groq timeout

[agent]
max_iterations = 30            # Orchestrator loop cap
worker_timeout_seconds = 2400  # max seconds a worker subprocess can run
dashboard_port = 8765          # port the dashboard listens on

[delivery]
digest_limit = 40              # max signals in the final digest
critical_threshold = 80        # score floor for "critical" badge
similarity_threshold = 0.75    # dedup threshold (higher = stricter)
```

---

## Advanced / Legacy Keys (edit `ai_tech.toml` directly)

These keys exist but are not exposed as controls in the Settings UI. Each is marked here with the reason.

| Key | Reason not a live control |
|---|---|
| `scoring.recency_bands` | Legacy V1. Only consumed by `_score_recency`, which `_base_score_card` no longer calls. Has no effect on V2 scores. |
| `scoring.corroboration_bands` | Legacy V1. Only consumed by `_score_corroboration`, which `_base_score_card` no longer calls. Has no effect on V2 scores. |
| `agent.require_brain` / `agent.allow_mock_brain` | Safety and offline-demo flags. Mock path is demo-only; changing these in production can break runs. |
| `agent.scout_mode` / `agent.analyst_mode` (in ai_tech.toml) | Fallback copies in ai_tech.toml. The live modes are the `[behavior]` keys in agent_brain.toml, editable above. |
| `profile.*` (name, organization, audience, mission, competitors, markets) | Identity and framing. Restart-required, low-churn. Edit directly in ai_tech.toml. May graduate to a Profile tab later. |
| `priorities[].name/description/weight/keywords` | Priority groups with large keyword arrays. Edit in ai_tech.toml until a dedicated Priorities editor exists. |
| `storage.path` / `delivery.output_dir` | Filesystem paths. Changing these repoints the database or output directory — risky without an explicit migration. |
| `sources[]*` | Every source field is editable via the **Sources** tab, not the scalar Settings UI. |

---

## Safe Editing Checklist

- `scoring.value_weights` must sum to **20** (not 100).
- `scoring.trust_weights` must sum to **1.0**.
- Keep `analyst_review_batch_size = 1` — the Groq prompt is tuned for one article per request.
- Keep prompts asking for strict JSON when the worker expects structured output.
- Export `GROQ_API_KEY` before live runs; Signal Stream does not auto-load `.env`.
- `recency_bands` and `corroboration_bands` have no effect on V2 scores. Don't tune them expecting results.

---

## What Happens After You Edit

**agent_brain.toml changes** — just rerun the agent:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

**ai_tech.toml changes** — restart the dashboard and/or agent:

```bash
# stop the dashboard (Ctrl-C), then:
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

Already-stored signals are not rewritten retroactively.
