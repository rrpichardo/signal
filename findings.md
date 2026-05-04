# Signal Stream Findings

## Pitch Deck
- Signal Stream is positioned as "strategic intelligence": a web platform that monitors external information, uses NLP to score news by goal relevance, and delivers decision-ready updates.
- Core capabilities from the deck: summarize, rank, identify entities, find similar items, interpret meaning/subtext.
- Differentiators: scored to user priorities, urgency scoring, executive briefs with next steps, feedback-based personalization, three delivery modes: alerts, digest, dashboard.
- Primary users: executives, strategy analysts, product leaders, sales.
- Workflow from deck: ingest trusted sources, process/filter with NLP and clustering, analyze/prioritize against strategy, deliver daily digest and critical alerts.

## Free/Local Model Notes
- Ollama docs say the local API is available at `http://localhost:11434/api` after installation.
- Ollama supports macOS, Windows, and Linux.
- Ollama model pages list small models appropriate for low-power machines, including `llama3.2:1b`, `llama3.2:3b`, `qwen3:0.6b`, `qwen3:1.7b`, and `qwen3:4b`.
- The prototype should not require a model; use Ollama only when available.

## Prototype Direction
- User clarified the architecture must be agentic, not automation.
- Approved agent brain: local Ollama.
- Approved subagent type: separate Python processes.
- Approved first output: local dashboard.
- Main agents: Orchestrator, Scout, Analyst.
- Exact source groups from Claude chat: Medium sources, Substack/newsletters, standalone blogs/newsletters, ByteByteGo and The AI Daily Brief YouTube.
