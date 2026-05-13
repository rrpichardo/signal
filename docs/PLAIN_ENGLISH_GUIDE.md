# Signal Stream Plain-English Guide

This guide explains the codebase without assuming you know the internals.

## The Big Idea

Signal Stream is not a fixed checklist. It acts like a research assistant:

1. Look at what it already knows.
2. Decide what information it needs next.
3. Ask helper agents to gather or analyze information.
4. Review the results.
5. Publish a digest when the signal is strong enough.

That loop is what makes it agentic.

## The Agents

### Orchestrator

File: `signal_stream/agent_runtime.py`

The Orchestrator is the manager. It decides the next move in the run.

It can choose to:

- collect sources
- analyze articles
- collect more context
- ask Critic to review the digest
- finalize the digest

The Orchestrator uses Groq for its decision loop unless demo/offline mock mode is explicitly enabled.

### Scout

Files: `signal_stream/worker.py`, `signal_stream/source_tools.py`

Scout is the collector. It fetches source material from RSS, Atom, YouTube, sample JSON, report stubs, and archive-style `html_scrape` sources.

Scout does not decide what is strategically important. It brings back normalized articles, source health, and basic source notes.

### Analyst

Files: `signal_stream/worker.py`, `signal_stream/analysis_tools.py`

Analyst is the judge. It removes repeats, clusters related articles, scores stories, fetches full pages for top candidates, and turns raw articles into useful digest signals.

### Critic

Files: `signal_stream/worker.py`, `signal_stream/analysis_tools.py`

Critic is optional. When enabled, it reviews the Analyst's proposed digest before the Orchestrator publishes it. If the digest is weak and a revision round remains, the Orchestrator gets those notes and tries again.

## The Run Lifecycle

Plain English version:

1. Signal Stream finds the most recent complete run.
2. Scout fetches articles newer than that run, with a 6-hour overlap so late-arriving feed items are not missed.
3. Analyst drops articles already saved by previous complete runs.
4. Analyst clusters the remaining articles and extracts entities.
5. Analyst scores each candidate with the Python rubric.
6. Analyst fetches full article pages for the top 40 candidates.
7. Analyst sends those top 40 to Groq one article at a time.
8. The digest keeps up to 40 ranked signals.
9. The run is saved atomically so a half-finished run cannot poison the seen set.
10. The top 12 feed the executive-summary view and future repeat detection.

## What "Seen" Means

File: `signal_stream/storage.py`

An article counts as seen only after a run finishes successfully with:

```text
agent_runs.status = "complete"
```

Failed runs use `status = "failed"`. Runs that hit the max-iteration limit without finalizing use `status = "interrupted"`. Neither of those statuses advances the cursor.

The cursor is the start time of the latest complete run. Scout subtracts 6 hours from it before fetching, then Analyst removes anything already saved in the `articles` table. This gives the system overlap without duplicate digest items.

## How Scoring Works

File: `signal_stream/analysis_tools.py`

Python gives every candidate a base score out of 100. There is one base-score function: `_base_score_card()`.

The five components are:

- **Priority match, 25 points:** Does the article match the configured priority groups?
- **Company match, 25 points:** Is a watchlist company central to the story?
- **Recency, 15 points:** How fresh is the article?
- **Event strength, 25 points:** Is this a launch, funding round, regulation, platform shift, useful analysis, or weaker content?
- **Corroboration, 10 points:** Are multiple independent sources covering the same story?

The priority groups and their keyword weights live in `configs/ai_tech.toml`. The component bands live in `configs/agent_brain.toml`.

## How Groq Decides

Files: `signal_stream/llm.py`, `signal_stream/analysis_tools.py`

Groq is used for judgment, not for the first score from scratch.

The Analyst sends only the top candidates to Groq:

- `analyst_review_limit = 40`
- `analyst_review_batch_size = 1`
- `model_score_adjustment_limit = 20`

That means Groq reviews at most 40 articles per run, one article per request. It sees the Python score and breakdown, the article text, the matched priorities, entities, and source notes. It can rewrite summaries and move the score up or down within the configured adjustment limit.

New summaries fold the strategic implication into `short_summary` instead of writing a separate field for it.

## Where Article Text Comes From

File: `signal_stream/source_tools.py`

Every article starts with source-provided text:

- RSS or Atom title/body fields
- YouTube feed metadata
- sample JSON body text
- `html_scrape` archive article pages

For the top 40 candidates, Analyst tries to fetch the full article page before sending the article to Groq. The full-page fetch uses stdlib HTML parsing, removes obvious page chrome such as scripts, headers, footers, navigation, and sidebars, then keeps the best article-like text block.

If extraction fails or produces less than 200 characters, Signal Stream keeps the original source body instead.

Images come from RSS media tags, enclosures, `og:image` metadata, or an existing source `image_url`. If no image exists, the dashboard uses an icon key based on event type, company, or topic.

## Memory

File: `signal_stream/storage.py`

Memory is a local SQLite database. It stores runs, articles, signals, dashboard activity events, tool calls, feedback, and memory items.

Database file:

```text
.signal_stream/signal_stream.db
```

The memory system helps Signal Stream avoid treating the same story as fresh every run.

## Dashboard

Files: `signal_stream/dashboard.py`, `web/src/`

The dashboard is a local website. It shows:

- latest digest cards
- executive-summary top signals
- signal detail pages with expanded summaries and score breakdowns
- Orchestrator decisions and worker events
- source health and tool calls
- memory items
- settings for scoring, priorities, behavior, and top-N limits

Run it with:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

Then open:

```text
http://127.0.0.1:8765
```

## Main Config

File: `configs/ai_tech.toml`

This file controls:

- profile and audience
- source list and source limits
- priority groups, weights, and keywords
- storage path
- delivery limit
- Groq model name and per-call timeout
- dashboard port and worker timeout

## Brain File

File: `configs/agent_brain.toml`

This is the live editable behavior source. It controls:

- prompts
- behavior modes
- scoring component bands
- top-N review settings
- Critic settings
- dashboard display preferences

You can edit it directly or use the dashboard Settings tab.

## Main Command

Run the agent:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

Check setup:

```bash
python3 -m signal_stream doctor --config configs/ai_tech.toml
```

## Important Note

Live runs require `GROQ_API_KEY` in the environment. Signal Stream does not auto-load `.env`; export the variable yourself or source your local env file before running.
