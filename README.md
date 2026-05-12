# Signal Stream

Signal Stream is a local-first AI/tech intelligence agent. It is built to be agentic, not just automated: an Orchestrator agent decides what to do next, starts separate Scout and Analyst worker processes, inspects their results, uses memory, and then finalizes a digest when it has enough signal.

The approved MVP runs on demand, uses Groq (cloud) as the agent brain, stores memory in SQLite, and serves a local dashboard. Requires a `GROQ_API_KEY` environment variable.

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

## Dashboard UI

The dashboard ships with a modern React + Vite frontend. Build it once before launching the dashboard:

```bash
cd web
npm install
npm run build
cd ..
python3 -m signal_stream dashboard --config configs/ai_tech.toml
```

The Python server automatically detects `web/dist/` and serves the React app. If `web/dist/` is absent, it falls back to the legacy inline dashboard so the dashboard never breaks on a fresh checkout.

### Development workflow

Run both servers at the same time for hot-reloading frontend dev:

```bash
# Terminal 1 — Python API server
python3 -m signal_stream dashboard --config configs/ai_tech.toml

# Terminal 2 — Vite dev server with /api proxy
cd web && npm run dev
```

Then open `http://localhost:5173`. Vite proxies all `/api/*` requests to the Python backend on port 8765.

For a 2020 Surface, start with `qwen3:1.7b` or `llama3.2:1b`. If you have 16 GB RAM and can tolerate slower runs, try `qwen3:4b` or `llama3.2:3b`.

## What Makes It Agentic

- `Orchestrator` is the decision-maker. It runs an observe/reason/act loop and chooses between collecting sources, asking for more context, sending work to Analyst, or finalizing.
- `Scout` is a separate Python process. It fetches RSS/blog sources and YouTube channel feeds/transcripts, then reports source health and normalized article objects.
- `Analyst` is a separate Python process. It deduplicates, checks memory, scores relevance, clusters themes, and produces digest-ready findings.
- SQLite memory stores previous signals so future runs can downgrade repeats.
- Analyst ignores old daily-feed items by default so stale posts do not crowd out current signals.
- The dashboard shows agent events, tool calls, source health, memory hits, and ranked signals.

The older `run` and `demo` commands still exist as legacy pipeline commands, but the main product path is `agent run`.

For a no-jargon walkthrough of the code layout, read `docs/PLAIN_ENGLISH_GUIDE.md`.
For the simplest non-technical editing path, read [docs/EDIT_THE_BRAIN.md](/Users/ricopichardo/Claude/signal/docs/EDIT_THE_BRAIN.md).

## Brain File And Agent Modes

The live editable brain file is [configs/agent_brain.toml](/Users/ricopichardo/Claude/signal/configs/agent_brain.toml).
The dashboard also has a **Settings** tab that edits this file for you.

That means:

- changing prompts there changes the next run automatically
- changing scoring values there changes the next run automatically
- changing behavior switches there changes the next run automatically
- `signal_stream/prompts.py` is now just the fallback default copy

The main worker mode switches now live in `configs/agent_brain.toml`:

```toml
[behavior]
scout_mode = "hybrid"
analyst_mode = "hybrid"
relevance_policy = "soft_keep"
```

Mode meanings:

- `code`: normal Python logic only
- `hybrid`: Python first, then optional model judgment
- `model`: lean more heavily on the model

`configs/ai_tech.toml` still stores sources, storage paths, and model host settings.

For a plain-English explanation of the scoring system, read [docs/SCORING_RUBRIC.md](/Users/ricopichardo/Claude/signal/docs/SCORING_RUBRIC.md).

## Exact Source List

The AI/tech source registry in `configs/ai_tech.toml` includes the exact list from the Claude shared chat:

- Medium: Towards AI, Towards Data Science, Analytics Vidhya, Becoming Human: AI Magazine, Codex, Generative AI.
- Substack/newsletters: AI Supremacy, New Economies, State of AI, The Sequence, LLM Watch, Import AI, AI Top Tools Weekly, Turing Post, Decoding AI Magazine, The Neural Maze.
- Standalone blogs/newsletters: The Pragmatic Engineer, Daily Dose of Data Science, AI Daily Brief Newsletter, ByteByteGo.
- YouTube: ByteByteGo, The AI Daily Brief.

`State of AI` is included as an on-demand/report source and disabled for normal daily-style runs.

## Commands

```bash
python3 -m signal_stream doctor --config configs/ai_tech.toml
python3 -m signal_stream agent run --config configs/ai_tech.toml
python3 -m signal_stream dashboard --config configs/ai_tech.toml
python3 -m signal_stream memory show --config configs/ai_tech.toml
python3 -m signal_stream show --config configs/ai_tech.toml --limit 10
```

## Offline Smoke Test

If you want to test without hitting live sources, temporarily enable `Signal Stream AI Sample Wire` in `configs/ai_tech.toml` and disable the live RSS/YouTube sources.
