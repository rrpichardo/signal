# Signal Stream

Signal Stream is an on-demand AI/tech intelligence agent that produces a ranked digest of high-signal developments across frontier AI, infrastructure, startups, enterprise adoption, builder tactics, and regulation. It runs locally, stores memory in SQLite, and uses Groq as the hosted brain for agent decisions and article review.

Originated as a Design for AI class project at Tepper. Conceptually linked to a broader idea called SignalIQ.

## Stack

- **Language:** Python 3.11+ (stdlib only for the backend; no new Python deps)
- **Agent brain:** Groq cloud API, model `meta-llama/llama-4-scout-17b-16e-instruct`
- **Auth:** `GROQ_API_KEY` environment variable; `.env` is not auto-loaded
- **Memory:** SQLite at `../.signal_stream/signal_stream.db`
- **Frontend:** React + Vite + shadcn/ui (`web/`) served by the Python dashboard, with a legacy inline fallback
- **Deployment:** Local runtime and local dashboard. Hosted API support is complete through Groq; no remote scheduler or delivery service is configured.

## Architecture Overview

The active run lifecycle is:

```text
last complete run cursor
  -> fetch configured sources with 6-hour overlap
  -> drop already-seen articles
  -> cluster and extract entities
  -> score with the 5-component Python rubric
  -> fetch full pages for the top 40
  -> send the top 40 to Groq one article per request
  -> keep the ranked digest up to 40 signals
  -> atomically persist articles, signals, run status, and dashboard events
  -> use the top 12 for the executive-summary view and memory
```

Four roles cooperate during the run:

- **Orchestrator** — the decision-maker. Runs an observe/reason/act loop and decides whether to collect sources, analyze articles, critique, or finalize.
- **Scout** — separate worker process. Fetches RSS, Atom, YouTube, sample JSON, report stubs, and `html_scrape` archive sources. It applies the run cursor and reports source health.
- **Analyst** — separate worker process. Deduplicates, removes already-seen articles, clusters, scores with the Python rubric, fetches full article pages for the top candidates, and asks Groq to review one article at a time.
- **Critic** — separate worker process, optional via `enable_critic` in `agent_brain.toml`. Reviews the proposed digest and can trigger one revision round before finalization.

Agents communicate through explicit JSON inputs and outputs. Keep the worker boundary clean; the design is ready for more parallelism later.

## Persistence Model

- `agent_runs.status = "complete"` is the only status that advances the cursor.
- Failed runs are marked `"failed"`.
- Runs that exhaust max iterations without finalizing are marked `"interrupted"`.
- `save_run_atomic()` writes fetched articles, generated signals, the legacy run row, and the `agent_runs.status = "complete"` update in one transaction.
- Articles are treated as "seen" only after a complete run persists them.
- The 6-hour cursor overlap intentionally over-fetches; `storage.is_article_seen()` removes duplicates before scoring.

## Scoring And Review

Python owns the base score. `_base_score_card()` is the single score source and returns a 100-point breakdown:

- priority match: 25
- company match: 25
- recency: 15
- event strength: 25
- corroboration: 10

Groq reviews only the highest-scoring candidates, capped by `analyst_review_limit` in `configs/agent_brain.toml` and set to 40 by default. The review batch size is 1, so each article gets its own request. Groq can adjust the Python score by up to `model_score_adjustment_limit` points, currently 20.

## Known Gaps

- Full-page extraction is stdlib-only and best-effort. Some pages may include navigation, footer, or ad text.
- Raw fetched articles that are filtered out before becoming signals are stored but not yet exposed as a rejected-items dashboard view.
- There is no scheduler, email, or Slack delivery wired up yet.
- Old stored signals may still have legacy fields, but new prompts fold strategic implication into `short_summary`.

## Key Files

| File | Purpose |
|---|---|
| `configs/agent_brain.toml` | Live-editable prompts, behavior switches, scoring bands, top-N settings, and display preferences. The dashboard Settings tab edits this file. |
| `configs/ai_tech.toml` | Profile, sources, priority groups, storage path, delivery settings, Groq model config, and agent runtime settings. |
| `signal_stream/llm.py` | Groq `BrainClient`, JSON chat calls, rate-limit retry, and required-field validation support. |
| `signal_stream/agent_runtime.py` | Orchestrator run loop, worker lifecycle, activity events, finalization, and atomic persistence path. |
| `signal_stream/worker.py` | Scout, Analyst, and Critic worker process entry points. |
| `signal_stream/source_tools.py` | Source fetching, cursor filtering, `html_scrape`, full-page extraction, and image extraction. |
| `signal_stream/analysis_tools.py` | Analyst scoring, clustering handoff, top-40 page fetch, Groq review, summaries, icons, and Critic helpers. |
| `signal_stream/agents.py` | Lightweight Scout/Cluster/Entity agent helpers. |
| `signal_stream/storage.py` | SQLite schema, seen checks, run cursor, atomic save, dashboard reads, and activity logs. |
| `signal_stream/dashboard.py` | Python dashboard server plus legacy inline UI fallback. |
| `web/src/pages/` | React dashboard pages for digest, detail, activity, memory, and settings. |
| `docs/PLAIN_ENGLISH_GUIDE.md` | No-jargon codebase walkthrough. |
| `docs/EDIT_THE_BRAIN.md` | Operator guide for editing prompts, scoring, top-N knobs, and settings. |
| `docs/SCORING_RUBRIC.md` | Plain-English scoring rubric and priority-group explanation. |

## Setup

```bash
# Backend has zero pip dependencies. Export the key before running.
export GROQ_API_KEY=<your-key>

# Build the frontend once.
cd web && npm install && npm run build && cd ..
```

## Commands

```bash
# Run the agent
python3 -m signal_stream agent run --config configs/ai_tech.toml

# Start the dashboard (visit http://127.0.0.1:8765)
python3 -m signal_stream dashboard --config configs/ai_tech.toml

# Check system health
python3 -m signal_stream doctor --config configs/ai_tech.toml

# View recent memory
python3 -m signal_stream memory show --config configs/ai_tech.toml

# Show stored signals
python3 -m signal_stream show --config configs/ai_tech.toml --limit 10
```

## Adding A New Source

Edit `configs/ai_tech.toml`. Add a `[[sources]]` block:

```toml
[[sources]]
name = "Source Name"
kind = "rss"          # rss | atom | youtube | html_scrape | sample | report
group = "substack"    # medium | substack | newsletter_blog | youtube | on_demand | sample
url = "https://example.com/feed"
limit = 8
enabled = true
```

For YouTube, use `kind = "youtube"` and add `channel_id`. For archive-style sites, use `kind = "html_scrape"` with `article_link_pattern`, a substring or regex that identifies article links.

## Behavior Modes

Set in `configs/agent_brain.toml` or via the dashboard Settings tab:

- `code` — Python logic only
- `hybrid` — Python first, then model judgment where configured
- `model` — lean more heavily on Groq for judgment

## Constraints And Gotchas

- `GROQ_API_KEY` must be exported before `agent run`. The app does not auto-load `.env`.
- Backend code must stay stdlib-only unless the no-deps rule is explicitly changed.
- The dashboard uses a PID file to keep one instance active.
- Edit prompts and behavior in `configs/agent_brain.toml`; Python prompt fallbacks are not the operator-facing source of truth.
- When adding an agent capability, update the Python dashboard API, React UI, and TypeScript types together.
- Offline smoke test: enable `Signal Stream AI Sample Wire` in `configs/ai_tech.toml`, disable live sources, and use `allow_mock_brain = true` only for demo/offline flows.
