# Signal Stream Plain-English Guide

This project is split into a few simple jobs.

## The Big Idea

Signal Stream is not supposed to be a robot that blindly does the same checklist every time.

It should act more like a junior research assistant:

1. Look at what it knows.
2. Decide what information it needs.
3. Ask helper agents to gather or analyze information.
4. Look at the results.
5. Decide whether it needs more context or can publish the digest.

That loop is what makes it "agentic."

## The Agents

### Orchestrator

File: `signal_stream/agent_runtime.py`

This is the boss/manager agent. It decides the next move.

It can choose:

- collect sources
- analyze articles
- collect more context
- finalize the digest

### Scout

Files: `signal_stream/worker.py`, `signal_stream/source_tools.py`

Scout is the collector. It fetches RSS feeds, blog posts, and YouTube video info/transcripts.

Scout does not decide what is important. It only brings back source material and reports failures.

### Analyst

Files: `signal_stream/worker.py`, `signal_stream/analysis_tools.py`

Analyst is the judge. It removes repeats, scores stories, checks memory, and turns raw articles into useful signals.

## Memory

File: `signal_stream/storage.py`

Memory is a local SQLite database. It helps Signal Stream remember what it already covered so it does not treat the same story as new every run.

Database file:

```text
.signal_stream/signal_stream.db
```

## Dashboard

File: `signal_stream/dashboard.py`

The dashboard is a small local website. It shows:

- latest agent run
- Orchestrator decisions
- Scout and Analyst tool calls
- ranked signals
- memory

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

This is where non-technical edits should happen first.

You can change:

- sources
- how many items to fetch per source
- Ollama model name
- how old an article can be before Analyst ignores it
- Scout mode: `code`, `hybrid`, or `model`
- Analyst mode: `code`, `hybrid`, or `model`
- digest limit
- dashboard port
- priorities and keywords

## Brain File

File: `configs/agent_brain.toml`

This is now the real editable behavior source.

Plain English:

- change prompts or scoring values in this file
- run the agent again
- the new instructions are used automatically

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

If Ollama is not running, the real agentic run will fail on purpose.

That is because the approved design says the Orchestrator should have a local model brain, not just hardcoded automation.
