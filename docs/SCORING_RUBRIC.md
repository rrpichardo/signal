# Signal Stream Scoring Rubric

This document explains how Signal Stream scores candidate stories before Groq reviews the top results.

The short version:

- Python computes the base score with one function: `_base_score_card()`.
- The base score has five components and a maximum of 100 points.
- Priority group weights are multipliers inside the priority-match bucket, not bonus points added after the fact.
- Groq reviews only the top candidates and can adjust the Python score within the configured limit.

## Where The Settings Live

- Score component weights and bands: [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml)
- Priority groups, weights, and keywords: [configs/ai_tech.toml](/Users/ricopichardo/Claude/signal/configs/ai_tech.toml)
- Scoring implementation: [signal_stream/analysis_tools.py](/Users/ricopichardo/Claude/signal/signal_stream/analysis_tools.py)

## The Five Components

The component weights must sum to 100.

| Component | Max | Plain-English question |
|---|---:|---|
| Priority match | 25 | Does this story match the configured high-signal themes? |
| Company match | 25 | Is a watchlist company central to the story? |
| Recency | 15 | How fresh is the story? |
| Event strength | 25 | Is this a concrete event or just weak/opinion/listicle content? |
| Corroboration | 10 | Are independent sources covering the same story? |

There are no separate repeat or low-value penalties in the final score. Repeats are removed before scoring when possible, and low-value content is handled inside the event-strength component.

## Priority Match: 25 Points

Priority matching starts with keyword hits against the seven priority groups in `configs/ai_tech.toml`.

The group weight multiplies hit intensity inside this 25-point bucket:

```text
priority intensity = priority weight * raw keyword hit count
```

That means a high-weight group reaches stronger bands with fewer hits, but the priority component still cannot exceed 25 points.

Bands:

| Band | Points |
|---|---:|
| no_match | 0 |
| weak_incidental | 5 |
| one_relevant_not_central | 10 |
| one_central_or_two_weak | 15 |
| one_central_with_support | 20 |
| direct_high_impact | 25 |

## Company Match: 25 Points

Company match looks at the profile watchlist in `configs/ai_tech.toml`, currently the `competitors` array.

It rewards stories where a watchlist company is prominent, especially in the title or lede, and where the company is doing something strategic such as launching, acquiring, partnering, raising, or releasing.

Bands:

| Band | Points |
|---|---:|
| no_match | 0 |
| one_passing | 5 |
| relevant_not_central | 10 |
| watchlist_central | 15 |
| watchlist_in_title_or_lede | 20 |
| watchlist_strategic_action | 25 |

## Recency: 15 Points

Recency uses the article publication timestamp when available.

Bands:

| Band | Points |
|---|---:|
| within_1_day | 15 |
| within_3_days | 12 |
| within_7_days | 9 |
| older | 6 |
| unknown | 7 |

The cursor handles freshness at the fetch stage. Recency scoring is only a ranking signal among the articles that remain.

## Event Strength: 25 Points

Event strength asks what kind of thing happened.

Examples of stronger events:

- model or product launches
- pricing or API access changes
- funding, acquisitions, or IPO movement
- regulation, lawsuits, or platform risk
- infrastructure, chips, inference, and cloud capacity changes

Examples of weaker events:

- opinion-only commentary
- generic listicles
- promotional webinars
- sponsored or hiring-style posts

Bands:

| Band | Points |
|---|---:|
| none | 0 |
| opinion_or_listicle | 5 |
| useful_analysis | 10 |
| product_update_or_signal | 15 |
| launch_funding_regulation | 20 |
| major_platform_shift | 25 |

## Corroboration: 10 Points

Corroboration checks whether related articles cluster around the same story and whether those articles come from independent sources.

Bands:

| Band | Points |
|---|---:|
| single | 0 |
| same_source_repeated | 3 |
| two_independent | 5 |
| three_or_more_independent | 8 |
| broad_cross_type | 10 |

## Priority Groups

These groups define what "high signal" means for the default AI/tech digest.

### Frontier AI Product And Model Launches

Weight: `2.8`

Major model releases, capability jumps, frontier lab launches, and pricing/access changes.

Keywords:

```text
Anthropic, Claude, Claude Code, Claude Desktop, Claude for Enterprise, Artifacts,
Projects, MCP, model context protocol, computer use, tool use, OpenAI, ChatGPT,
GPT, Gemini, DeepMind, Llama, Meta AI, Grok, xAI, Mistral, frontier model,
model release, new model, reasoning model, multimodal, voice mode,
image generation, video generation, API, SDK, pricing, enterprise plan,
developer platform, feature launch, product update
```

### Agents And Developer Workflows

Weight: `2.3`

Agent frameworks, tool use, developer platforms, IDE integrations, and dev workflows.

Keywords:

```text
agents, agentic, AI agent, coding agent, Claude Code, Codex, Cursor, Devin,
Replit, Windsurf, IDE, CLI, developer tools, tool calling, function calling,
browser use, computer use, workflow automation, orchestration, multi-agent,
evals, evaluation, observability, prompt engineering, RAG, retrieval,
embeddings, fine-tuning
```

### Enterprise AI Adoption And Monetization

Weight: `1.8`

Enterprise rollouts, Fortune 500 customers, productivity case studies, and vertical AI deployments.

Keywords:

```text
enterprise, customer, deployment, pilot, procurement, ROI, productivity,
integration, partnership, Microsoft 365, Google Workspace, Salesforce,
ServiceNow, Databricks, Snowflake, Oracle, AWS, Azure, Google Cloud, pricing,
subscription, seats, revenue, ARR, go-to-market, compliance, security review
```

### AI Infrastructure, Chips, And Inference

Weight: `1.8`

GPUs, accelerators, datacenters, inference cost, compute supply, and training runs.

Keywords:

```text
NVIDIA, AMD, Intel, GPU, TPU, chip, accelerator, compute, inference, training,
data center, cloud, AWS, Azure, Google Cloud, CoreWeave, Lambda, latency,
throughput, HBM, CUDA, networking, capacity, power, energy, capex,
inference cost, model serving
```

### AI Startups, Funding, And Category Creation

Weight: `1.6`

Seed/Series/IPO/M&A, new entrants, valuations, and founder moves in AI.

Keywords:

```text
startup, funding, seed, Series A, Series B, Series C, venture, valuation, YC,
acquisition, acquires, merger, IPO, spinout, launch, category, market map,
open source startup, AI app, AI infrastructure startup, AI tooling, revenue, ARR
```

### AI Regulation, Safety, Copyright, And Platform Risk

Weight: `1.7`

Policy, EU AI Act, safety research, alignment incidents, lawsuits, and copyright.

Keywords:

```text
regulation, regulatory, policy, EU AI Act, copyright, lawsuit, privacy,
security, safety, compliance, antitrust, export controls, data protection,
model misuse, deepfake, watermarking, governance, liability, content licensing,
training data, fair use
```

### High-Signal Builder Tactics

Weight: `1.2`

Practical engineering essays, eval methodologies, RAG patterns, and postmortems.

Keywords:

```text
architecture, engineering, case study, postmortem, benchmark, eval, evaluation,
RAG, retrieval, vector database, embeddings, fine-tuning, prompt, observability,
monitoring, workflow, automation, latency, cost optimization, production AI
```

## What Groq Does After The Base Score

The Python score is the baseline. Groq reviews the top `analyst_review_limit` candidates, currently 40, with `analyst_review_batch_size = 1`.

For each reviewed article, Groq sees:

- the Python score
- the score breakdown
- the event type
- matched priorities
- entities
- source notes
- the article text, preferably from the full article page

Groq can:

- write or improve the card summary
- write or improve the expanded summary
- add discovered entities
- adjust the score up or down by at most `model_score_adjustment_limit`, currently 20

The final score remains clamped to `0..100`.

## Mental Model

```text
Python rubric = consistent, inspectable base ranking
Groq review   = judgment pass over the top candidates
Dashboard     = explanation layer showing the score breakdown and summaries
```

If a score looks wrong, inspect the signal detail page first. It shows the component breakdown that explains where the points came from.
