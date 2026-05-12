# Signal Stream

Signal Stream is a local-first AI/tech intelligence agent that produces a daily digest of high-signal developments across AI, infrastructure, startups, and regulation. Originated as a Design for AI class project at Tepper. Conceptually linked to a broader idea called SignalIQ.

## Stack

- **Language:** Python 3.11+ (stdlib only — zero third-party deps)
- **Agent brain:** Groq cloud API (model: `meta-llama/llama-4-scout-17b-16e-instruct`). Requires `GROQ_API_KEY` env var.
- **Memory:** SQLite (stored at `../.signal_stream/signal_stream.db`)
- **Frontend:** React + Vite + shadcn/ui (`web/`) served by the Python dashboard
- **Deployment:** Local-first. No remote deployment configured. Future direction: Railway (not yet wired up).

## Architecture

Four agents, each a separate concern. Scout, Analyst, and Critic run as separate Python processes spawned by Orchestrator.

- **Orchestrator** — the decision-maker. Runs an observe/reason/act loop. Decides whether to collect more sources, send work to Scout, Analyst, or Critic, or finalize.
- **Scout** — separate process. Fetches RSS, blog, and YouTube feeds. Reports source health and normalized article objects.
- **Analyst** — separate process. Deduplicates, checks memory, scores relevance, clusters themes, produces digest-ready findings.
- **Critic** — separate process, **opt-in** (`enable_critic` in `agent_brain.toml`). Reviews the Analyst's ranked digest, scores it 0–100, flags weak signals, and triggers revision rounds until the score clears `critic_score_threshold` or `max_critic_rounds` is hit.

Agents communicate via explicit inputs/outputs. Keep this design — it's ready for true parallelization.

## Known gaps (worth fixing)

- None currently open.

## Key Files

| File | Purpose |
|---|---|
| `configs/agent_brain.toml` | Live-editable brain: prompts, scoring weights, behavior modes, critic knobs. The dashboard Settings tab edits this directly (when the React UI catches up). |
| `configs/ai_tech.toml` | Sources list, storage paths, Ollama model settings, delivery config. |
| `signal_stream/orchestrator.py` | Orchestrator agent logic |
| `signal_stream/agent_runtime.py` | Multi-agent run loop including the critic round |
| `signal_stream/worker.py` | Scout, Analyst, and Critic worker processes |
| `signal_stream/agents.py` | Agent class definitions |
| `signal_stream/analysis_tools.py` | Analyst + Critic tool implementations and JSON schemas |
| `signal_stream/llm.py` | Ollama interface |
| `signal_stream/storage.py` | SQLite memory |
| `signal_stream/dashboard.py` | Legacy inline HTML dashboard (fallback) — currently has more features than the React UI |
| `web/src/pages/SettingsPage.tsx` | React Settings page that edits agent_brain.toml |
| `docs/PLAIN_ENGLISH_GUIDE.md` | No-jargon codebase walkthrough |
| `docs/EDIT_THE_BRAIN.md` | Simplest path to changing agent behavior |
| `docs/SCORING_RUBRIC.md` | Plain-English scoring explanation |

## Setup

```bash
# Backend has zero pip dependencies — just need Python 3.11+ and a GROQ_API_KEY
export GROQ_API_KEY=<your-key>

# Build the frontend once
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

## Adding a New Source

Edit `configs/ai_tech.toml`. Add a `[[sources]]` block:

```toml
[[sources]]
name = "Source Name"
kind = "rss"          # rss | youtube | sample | report
group = "substack"    # medium | substack | newsletter_blog | youtube | on_demand | sample
url = "https://example.com/feed"
limit = 8
enabled = true
```

For YouTube, use `kind = "youtube"` and add a `channel_id` field.

## Behavior Modes

Set in `configs/agent_brain.toml` (or via the dashboard Settings tab):

- `code` — pure Python logic, no model calls
- `hybrid` — Python first, optional model judgment (current default)
- `model` — lean heavily on the model

## Constraints and Gotchas

- `GROQ_API_KEY` must be exported before `agent run`. The app does NOT auto-load `.env` — either `export GROQ_API_KEY=<key>` or `source .env` first.
- The dashboard uses a PID file to guarantee one instance. Don't bypass it.
- Agent prompts and model defaults live in `configs/agent_brain.toml`. `signal_stream/prompts.py` is the fallback copy only — edit the TOML, not the Python file.
- React UI lags behind Python features (see Known Gaps). When adding a new agent capability, update both `dashboard.py` (legacy) and `web/src/` (React) — and the TypeScript types in `web/src/lib/types.ts`.
- Offline smoke test: enable `Signal Stream AI Sample Wire` in `ai_tech.toml` and disable live sources.
