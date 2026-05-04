# Signal Stream Scoring Rubric

This file explains how the Analyst's **base score** works before the local model
reviews it.

Plain English:

- The **code** gives each story a starting score using visible clues.
- The **model** can then review that score and adjust it when the meaning of the
  story deserves a correction.

That means the score is not pure "AI vibes" and it is not pure keyword math
either. It starts with a readable rubric, then gets a model review.

## Why the code can score at all

The code does **not** understand content the way an LLM does.

Instead, it uses signals that often correlate with importance:

- how fresh the story is
- whether it matches your configured priorities
- whether it involves major known players
- what kind of event it appears to be
- whether multiple articles are covering the same event
- whether the story looks repetitive or low-value

This is why we call it a **base rubric**. It is a structured first pass, not
the final word.

## Current base rubric

The base score adds positive points and subtracts penalties.

### Positive components

1. **Freshness**: `0 to 20`
   - Newer stories score higher.
   - Unknown publication dates get a neutral score.

2. **Priority match**: `0 to 25`
   - Stories earn points when they match your configured priority themes.
   - Example themes: AI platform shifts, infrastructure and chips, regulation.

3. **Major-player involvement**: `0 to 15`
   - Stories involving known important companies or organizations score higher.
   - Examples: OpenAI, Anthropic, NVIDIA, Google.

4. **Event strength**: `0 to 18`
   - The system gives more points to strong event types like platform shifts,
     competitor moves, regulation, and infrastructure changes.

5. **Corroboration**: `0 to 10`
   - If multiple articles cluster around the same event, the story gets a boost.
   - This is a proxy for "this is getting attention."

### Negative components

6. **Repeat penalty**: `0 to -20`
   - If memory says we recently covered the same topic, points are removed.

7. **Low-value content penalty**: `0 to -15`
   - Promotional, roundup, sponsored, or obviously low-signal content gets
     penalized.

## Formula

```text
base score =
  freshness
  + priority match
  + major-player involvement
  + event strength
  + corroboration
  - repeat penalty
  - low-value penalty
```

The result is clamped to `0 to 100`.

## What the model does after that

When Analyst mode is `hybrid` or `model`, the local model sees:

- the current score
- the score breakdown
- the event type
- matched priorities
- entities
- the Scout triage note
- the article text
- the draft short summary, expanded summary, and why-it-matters text

Then it can:

- keep the score close to the base rubric
- raise the score if the story is more strategically important than the code saw
- lower the score if the story is hype, fluff, or misleadingly keyword-heavy
- rewrite the short and expanded summaries
- rewrite the why-it-matters text
- add newly discovered entities

## Mental model

- **Code** = consistent checklist
- **Model** = reviewer with judgment

In hybrid mode, the model score adjustment is capped by
`model_score_adjustment_limit` in [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml).

That is the current scoring design.
