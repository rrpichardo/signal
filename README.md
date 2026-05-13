# Signal Stream

Signal Stream is an on-demand AI/tech intelligence agent. An Orchestrator decides what to do next, starts separate Scout and Analyst worker processes, uses SQLite memory, asks Groq for model judgment, and finalizes a ranked digest when it has enough signal.

The current product path uses Groq as the hosted brain, stores memory locally in SQLite, and serves a local dashboard. You need a `GROQ_API_KEY` environment variable before running live agent workflows.

## Quick Start

Export your Groq API key:

```bash
export GROQ_API_KEY=<your-key>
```

Run the agent:

```bash
python3 -m signal_stream agent run --config configs/ai_tech.toml
```

Open the dashboard:

```bash
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

Then visit `http://127.0.0.1:8765`.

## Run Lifecycle

Each run follows the same high-level path:

1. Find the most recent `agent_runs.status = "complete"` row.
2. Fetch configured sources newer than that run, with a 6-hour overlap.
3. Drop articles already persisted by prior complete runs.
4. Cluster articles and extract entities.
5. Score each candidate with the 5-component Python rubric.
6. Fetch full article pages for the top 40 candidates.
7. Send those top 40 to Groq one article per request.
8. Publish up to 40 ranked digest signals.
9. Atomically persist articles, signals, dashboard events, and the complete run status.
10. Use the top 12 for the executive-summary view and memory.

Failed or interrupted runs do not advance the cursor and do not mark newly fetched articles as seen.

## Dashboard UI

The dashboard ships with a React + Vite frontend. Build it once before launching the dashboard:

```bash
cd web
npm install
npm run build
cd ..
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

The Python server automatically detects `web/dist/` and serves the React app. If `web/dist/` is absent, it falls back to the legacy inline dashboard so the dashboard remains usable on a fresh checkout.

The dashboard includes:

- Digest cards with images or icon fallbacks
- Signal detail pages with expanded summaries, score breakdowns, entities, and related signals
- Activity stages for collecting, filtering, clustering, scoring, full-page fetching, Groq review, digest writing, completion, and failure
- Settings editors for scoring weights, priority groups, top-N knobs, and source limits

### Development Workflow

Run both servers at the same time for hot-reloading frontend development:

```bash
# Terminal 1: Python API server
python3 -m signal_stream dashboard --config configs/ai_tech.toml

# Terminal 2: Vite dev server with /api proxy
cd web && npm run dev
```

Then open `http://localhost:5173`. Vite proxies all `/api/*` requests to the Python backend on port 8765.

## What Makes It Agentic

- `Orchestrator` is the decision-maker. It runs an observe/reason/act loop and chooses between collecting sources, asking for more context, sending work to Analyst, asking Critic for review, or finalizing.
- `Scout` is a separate Python process. It fetches RSS, Atom, YouTube, sample, report, and `html_scrape` sources, then reports source health and normalized article objects.
- `Analyst` is a separate Python process. It deduplicates, checks the seen set, scores relevance, clusters themes, fetches top candidate pages, and produces digest-ready findings.
- `Critic` is optional. It reviews the proposed digest before finalization and can request a revision round.
- SQLite memory stores prior completed runs so future runs can drop exact repeats and avoid acting like every run is day one.
- The dashboard shows agent events, tool calls, source health, memory, ranked signals, detail views, and editable settings.

The older `run` and `demo` commands still exist as legacy pipeline commands, but the main product path is `agent run`.

For a no-jargon walkthrough of the code layout, read [docs/PLAIN_ENGLISH_GUIDE.md](/Users/ricopichardo/Claude/signal/docs/PLAIN_ENGLISH_GUIDE.md).
For the simplest non-technical editing path, read [docs/EDIT_THE_BRAIN.md](/Users/ricopichardo/Claude/signal/docs/EDIT_THE_BRAIN.md).

## Brain File And Settings

The live editable brain file is [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml).
The dashboard also has a **Settings** tab that edits this file for you.

That means:

- changing prompts there changes the next run
- changing scoring bands there changes the next run
- changing behavior switches there changes the next run
- changing `analyst_review_limit`, `analyst_review_batch_size`, and `executive_summary_limit` changes how many articles Groq reviews and how many top signals feed memory
- `signal_stream/prompts.py` is only a fallback copy

The main worker mode switches live in `configs/agent_brain.toml`:

```toml
[behavior]
scout_mode = "hybrid"
analyst_mode = "hybrid"
analyst_review_limit = 40
analyst_review_batch_size = 1
executive_summary_limit = 12
```

Mode meanings:

- `code`: Python logic only
- `hybrid`: Python first, then optional model judgment
- `model`: lean more heavily on Groq judgment

`configs/ai_tech.toml` stores the profile, sources, priority groups, storage path, delivery settings, and Groq model config.

For a plain-English explanation of scoring, read [docs/SCORING_RUBRIC.md](/Users/ricopichardo/Claude/signal/docs/SCORING_RUBRIC.md).

## Exact Source List

The AI/tech source registry in `configs/ai_tech.toml` includes:

- Medium: Towards AI, Towards Data Science, Analytics Vidhya, Becoming Human: AI Magazine, Codex, Generative AI.
- Substack/newsletters: AI Supremacy, New Economies, State of AI, The Sequence, LLM Watch, Import AI, AI Top Tools Weekly, Turing Post, Decoding AI Magazine, The Neural Maze.
- Standalone blogs/newsletters: The Pragmatic Engineer, Daily Dose of Data Science, AI Daily Brief Newsletter, ByteByteGo.
- YouTube: ByteByteGo, The AI Daily Brief.
- Offline smoke test: Signal Stream AI Sample Wire.

`State of AI` is included as an on-demand/report source and disabled for normal agent runs. Turing Post uses the `html_scrape` source kind against its archive page.

## Commands

```bash
python3 -m signal_stream doctor --config configs/ai_tech.toml
python3 -m signal_stream agent run --config configs/ai_tech.toml
python3 -m signal_stream dashboard --config configs/ai_tech.toml
python3 -m signal_stream memory show --config configs/ai_tech.toml
python3 -m signal_stream show --config configs/ai_tech.toml --limit 10
```

## Offline Smoke Test

To test without live sources, temporarily enable `Signal Stream AI Sample Wire` in `configs/ai_tech.toml`, disable live RSS/YouTube/archive sources, and use the demo/offline brain settings from `configs/demo.toml`.
