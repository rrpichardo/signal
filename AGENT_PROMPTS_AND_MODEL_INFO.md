# Signal Stream Agent Prompts And Model Info

This file is the plain-English reference for the current Signal Stream agent system.

It explains:

- what each agent does
- the exact prompts currently used at runtime
- what model is used
- what tools each agent can use
- where to edit things

Last updated: 2026-05-04

## 1. System Overview

Signal Stream is designed to be agentic, not just automated.

Plain English:

- A normal automation follows the same fixed checklist every time.
- Signal Stream has an Orchestrator that decides what should happen next.
- Scout and Analyst run as separate Python worker processes.
- The Orchestrator talks to Scout and Analyst by sending JSON tasks.
- Everything is saved locally in SQLite so the dashboard can show what happened.

Main files:

- `configs/agent_brain.toml` is the live editable brain file for prompts and scoring.
- `signal_stream/prompts.py` holds fallback defaults if that file is missing.
- `configs/ai_tech.toml` stores model settings, source list, priorities, and limits.
- `signal_stream/agent_runtime.py` runs the Orchestrator loop.
- `signal_stream/worker.py` runs Scout, Analyst, and Critic as separate worker processes.
- `signal_stream/source_tools.py` contains Scout's tools.
- `signal_stream/analysis_tools.py` contains Analyst's tools and the Critic's scoring function.
- `signal_stream/dashboard.py` serves the local dashboard.

Four-agent loop (when Critic is enabled):

```
Orchestrator → Scout (collect) → Analyst (rank) → Critic (review) → Orchestrator → finalize or revise
```

The Critic is opt-in. Set `enable_critic = true` in `configs/agent_brain.toml` to activate it.

## 2. Model Information

### Current model provider

Signal Stream uses **Ollama** locally.

This means the model runs on your computer instead of a paid hosted API.

### Current model

```text
qwen3:1.7b
```

### Ollama host

```text
http://localhost:11434
```

### Config location

These settings live in `configs/ai_tech.toml`:

```toml
[ollama]
enabled = true
model = "qwen3:1.7b"
host = "http://localhost:11434"
timeout_seconds = 60
```

### Agent runtime settings

Runtime limits live in `configs/ai_tech.toml`:

```toml
[agent]
max_iterations = 6
min_signals = 8
dashboard_port = 8765
worker_timeout_seconds = 120
max_article_age_days = 14
brain_file = "agent_brain.toml"
require_ollama = true
allow_mock_brain = false
```

Agent behavior switches live in `configs/agent_brain.toml`:

```toml
[behavior]
scout_mode = "hybrid"
analyst_mode = "hybrid"
relevance_policy = "soft_keep"
model_score_adjustment_limit = 20
```

Plain English:

- `max_iterations`: maximum number of Orchestrator thinking rounds.
- `min_signals`: how many good signals are enough to stop.
- `dashboard_port`: where the local dashboard runs.
- `worker_timeout_seconds`: how long workers are allowed to take.
- `max_article_age_days`: old articles are ignored after this many days.
- `brain_file`: where the live prompts and scoring settings are loaded from.
- `scout_mode`: whether Scout uses only code or also uses the model.
- `analyst_mode`: whether Analyst uses only code or also uses the model.
- `require_ollama`: the real agent run fails if Ollama is not available.
- `allow_mock_brain`: test mode only; do not use for the real product.

## 3. Agent 1: Orchestrator

### Purpose

The Orchestrator is the decision-maker.

Plain English:

It looks at the current state and decides the next move:

- collect sources
- analyze articles
- collect more context
- critique digest (opt-in, when enable_critic = true)
- finalize the digest

The Orchestrator always makes live model calls to Ollama in the real agent run.

### Model

```text
Provider: Ollama
Model: qwen3:1.7b
Host: http://localhost:11434
```

### Exact prompt

Runtime source file: `configs/agent_brain.toml`

```text
You are the Signal Stream Orchestrator.

You are not a pipeline. You run an observe -> reason -> act loop.
Your job is to decide what the system should do next to produce a high-signal AI/tech digest.

You can choose only these actions:
- collect_sources: ask Scout to fetch configured RSS, blog, and YouTube sources.
- analyze_articles: ask Analyst to dedupe, score, check memory, and write candidate signals.
- collect_more_context: ask Scout for more context on a topic when coverage is thin, duplicated, or one-sided.
- finalize_digest: stop and publish the best ranked signals.

Rules:
- Prefer fewer high-confidence signals over a bloated digest.
- If many articles repeat the same story, ask for more context or contrarian coverage before finalizing.
- If memory says a topic was already covered, downgrade it unless there is a meaningful new development.
- Do not invent sources or facts.
- Return strict JSON only.
```

### Orchestrator output format

The Orchestrator must return JSON shaped like this:

```json
{
  "thought": "Plain-English explanation of what it noticed.",
  "action": "collect_sources | analyze_articles | collect_more_context | critique_digest | finalize_digest",
  "target": "Optional topic or target for more context.",
  "reason": "Why this action is the right next step.",
  "params": {}
}
```

Allowed actions:

- `collect_sources`
- `analyze_articles`
- `collect_more_context`
- `critique_digest` (only used when `enable_critic = true`)
- `finalize_digest`

### Orchestrator tools

The Orchestrator does not fetch feeds or score articles directly.

Instead, it can send tasks to:

- Scout worker process
- Analyst worker process
- Critic worker process (when enabled)

## 4. Agent 2: Scout

### Purpose

Scout is the collector.

Plain English:

Scout fetches raw information from sources. It does not decide whether a story is important.

### Current implementation status

Scout currently runs as a separate Python worker process.

Scout reads its prompt from the editable brain file, but whether it actually uses the model depends on `scout_mode`.

This is intentional for the local MVP:

- cheaper
- faster
- easier to debug
- still a real separate worker process

### Model

```text
Current live model calls:
- code mode: none
- hybrid mode: optional Ollama enrichment after fetching
- model mode: same path for now, intended to rely more on the model over time
Worker type: separate Python process
```

### Exact prompt

Runtime source file: `configs/agent_brain.toml`

```text
You are the Signal Stream Scout Agent.
Your job is to fetch and normalize source material. Do not judge strategic importance.
Return article/video objects, source health, failures, and confidence.
```

### Scout tools

Source file: `signal_stream/source_tools.py`

Scout can:

- fetch RSS feeds
- fetch Atom feeds
- read sample JSON files
- fetch YouTube channel feeds
- attempt YouTube transcript lookup
- report source failures without crashing the whole run
- search already-collected articles for more context

Current Scout task types:

```text
collect_sources
collect_more_context
```

### Scout output

Scout returns JSON with:

- `task_id`
- `agent`
- `status`
- `data`
- `error`
- `confidence`

## 5. Agent 3: Analyst

### Purpose

Analyst is the judge.

Plain English:

Analyst turns raw articles into ranked signals.

It removes repeats, checks memory, scores importance, and writes digest-ready findings.

### Current implementation status

Analyst currently runs as a separate Python worker process.

Analyst reads its prompt from the editable brain file, but whether it actually uses the model depends on `analyst_mode`.

### Model

```text
Current live model calls:
- code mode: none
- hybrid mode: Ollama reviews and adjusts scored signals
- model mode: Ollama review score is trusted more strongly
Worker type: separate Python process
```

### Exact prompt

Runtime source file: `configs/agent_brain.toml`

```text
You are the Signal Stream Analyst Agent.
Your job is to deduplicate, check memory, score signal value, identify themes, and produce digest-ready findings.
Be harsh. Cut weak articles. Explain why each included item matters.
You will receive a code-generated score breakdown. Treat it as the baseline rubric, not as gospel.
Use the breakdown to understand why the base score exists, then adjust only when the article meaning clearly deserves it.
Return structured JSON only.
```

### Analyst tools

Source file: `signal_stream/analysis_tools.py`

Analyst can:

- remove exact duplicate articles
- ignore stale daily-feed articles older than `max_article_age_days`
- cluster similar articles
- extract known entities
- score articles against configured priorities
- check SQLite memory for repeated coverage
- create summaries
- create expanded summaries
- create "why it matters" text
- produce the Markdown digest

Current Analyst task type:

```text
analyze_articles
```

### Analyst output

Analyst returns JSON with:

- article count
- cluster count
- ranked signals
- digest Markdown
- trace events

## 6. Agent 4: Critic

### Purpose

The Critic is the quality gate.

Plain English:

After the Analyst produces ranked signals, the Orchestrator can ask the Critic to score the digest before publishing. If the Critic finds weak signals, it returns revision notes and the Orchestrator loops back to fix them rather than shipping a flawed digest.

The Critic is **opt-in** and off by default. Set `enable_critic = true` in `configs/agent_brain.toml` to activate it.

### Current implementation status

Critic runs as a separate Python worker process, the same way Scout and Analyst do.

It always runs a code-based quality check. When `analyst_mode` is `hybrid` or `model`, it also asks Ollama to review the digest.

### Model

```text
Current live model calls:
- code mode: none (code checks only)
- hybrid/model mode: optional Ollama review of the full signal list
Worker type: separate Python process
```

### Exact prompt

Runtime source file: `configs/agent_brain.toml` under `[critic]`.

```text
You are the Signal Stream Critic Agent.

Your job is to review the Analyst's proposed digest before it ships and decide whether it is good enough to publish.

You do NOT rewrite signals. You only judge them.

For each signal, check for:
- promotional or low-value residue (webinar, sponsor, register now, roundup, top 10, hiring, course)
- missing or generic why-it-matters text
- short_summary or expanded_summary that does not actually summarize the article
- duplicate-looking entries (same story, different sources) that the Analyst failed to merge
- score that looks inflated relative to the strategic substance described

Return strict JSON with three fields:
- score: integer 0-100 representing overall digest quality (100 = ship as-is, 0 = unusable)
- weak_indices: array of integer indices into the signals list that should be revised or dropped
- reasons: array of short strings, one per weak index, explaining the specific problem

If the digest is fine, return weak_indices=[] and reasons=[] with a score at or above the threshold.
Be strict. A digest with one weak signal is a digest that loses the reader's trust.
Return JSON only.
```

### Critic tools

Source file: `signal_stream/analysis_tools.py` (`score_digest_quality` function)

Critic can:

- detect low-value phrase residue (webinar, sponsor, roundup, etc.) in signal text
- flag missing or too-short why-it-matters text
- flag missing or too-short summaries
- flag very low-scoring signals (score < 20)
- optionally ask Ollama to review the full signal list and surface subtler problems

Current Critic task type:

```text
critique_digest
```

### Critic output

Critic returns JSON with:

- `score` (0-100): overall digest quality
- `weak_indices`: list of signal indices that need revision
- `reasons`: one-line explanation per weak index

### Behavior switches

In `configs/agent_brain.toml`:

```toml
[behavior]
enable_critic = false          # Set to true to activate the Critic loop
max_critic_rounds = 1          # How many revision loops before the Orchestrator ships anyway
critic_score_threshold = 70    # Digest score below this triggers a revision request
```

## 7. Current Source List

Source config lives in `configs/ai_tech.toml`.

### Medium

- Towards AI
- Towards Data Science
- Analytics Vidhya
- Becoming Human: AI Magazine
- Codex
- Generative AI

### Substack / newsletters

- AI Supremacy
- New Economies
- State of AI
- The Sequence
- LLM Watch
- Import AI
- AI Top Tools Weekly
- Turing Post
- Decoding AI Magazine
- The Neural Maze

Note: State of AI is marked as on-demand and disabled in normal runs.

### Standalone blogs / newsletters

- The Pragmatic Engineer
- Daily Dose of Data Science
- AI Daily Brief Newsletter
- ByteByteGo Blog

### YouTube

- ByteByteGo YouTube
- The AI Daily Brief YouTube

## 8. Current Priorities

Priorities live in `configs/ai_tech.toml`.

Analyst uses these to score what matters.

### AI platform shifts

Major model, platform, pricing, developer ecosystem, or product moves.

### Startup and investment signals

Funding, acquisitions, launches, and category creation in AI and tech.

### Infrastructure and chips

Compute, GPU, cloud, data center, and AI infrastructure constraints or breakthroughs.

### Regulation and risk

Policy, safety, copyright, privacy, and enterprise adoption risks.

### Builder tactics

Practical technical essays and operator lessons worth applying.

## 9. Memory

Memory is stored locally in SQLite.

Database path:

```text
.signal_stream/signal_stream.db
```

Plain English:

Memory helps Signal Stream avoid pretending the same story is new every time.

When a signal is saved, future runs can detect related topics and downgrade repeats.

## 10. Dashboard

Dashboard URL:

```text
http://127.0.0.1:8765
```

Dashboard command:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

The dashboard shows:

- latest run
- ranked signals
- Orchestrator decisions
- Scout events
- Analyst events
- Critic events (score, weak indices, revision notes — when Critic is enabled)
- tool calls
- memory

## 11. Main Run Commands

Check that Ollama is available:

```bash
python3 -m signal_stream doctor --config configs/ai_tech.toml
```

Run the agent:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

Show recent signals:

```bash
python3 -m signal_stream show --config configs/ai_tech.toml --limit 10
```

Show memory:

```bash
python3 -m signal_stream memory show --config configs/ai_tech.toml --limit 10
```

## 12. What To Edit

### To change model

Edit `configs/ai_tech.toml`:

```toml
[ollama]
model = "qwen3:1.7b"
```

Example alternatives:

- `llama3.2:1b`
- `llama3.2:3b`
- `qwen3:4b`

### To change agent behavior

Edit `signal_stream/prompts.py`.

Important:

- The Orchestrator prompt affects live model decisions now.
- Scout and Analyst prompts are currently reference/future-upgrade prompts.
- Scout and Analyst behavior is mostly controlled by `source_tools.py`, `analysis_tools.py`, and `configs/ai_tech.toml`.

### To change what gets monitored

Edit the `[[sources]]` blocks in `configs/ai_tech.toml`.

### To change what matters

Edit the `[[priorities]]` blocks in `configs/ai_tech.toml`.

### To change how stale articles are handled

Edit this value in `configs/ai_tech.toml`:

```toml
max_article_age_days = 14
```

## 13. Important Current Limitation

Right now:

- Orchestrator is model-driven.
- Scout can be `code`, `hybrid`, or `model`.
- Analyst can be `code`, `hybrid`, or `model`.

Current default behavior settings in `configs/agent_brain.toml` are:

```toml
scout_mode = "hybrid"
analyst_mode = "hybrid"
```
