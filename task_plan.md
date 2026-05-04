# Signal Stream Agentic System Plan

## Goal
Create a free/local-first agentic Signal Stream prototype that can run on modest hardware, including a 2020 Surface. The approved target is AI/tech intelligence, local Ollama, separate-process Scout and Analyst subagents, SQLite memory, and a local dashboard.

## Phases
| Phase | Status | Notes |
|---|---|---|
| 1. Understand pitch and constraints | complete | Extracted pitch deck text and verified local-model options. |
| 2. Scaffold local agent runtime | complete | Added config, source registry, storage primitives, worker modules, dashboard. |
| 3. Implement agentic workers | in_progress | Orchestrator loop, Scout process, Analyst process, memory, tool calls. |
| 4. Verify runnable demo | pending | Run tests and smoke commands. |
| 5. Document setup and local model path | complete | README with Ollama/free setup and exact source list. |

## Decisions
- Use Python standard library as much as possible so setup stays free and light.
- Use TOML config through `tomllib`, available in modern Python, instead of adding YAML dependencies.
- Agentic path requires Ollama by default. Tests and offline smoke paths may use a mock brain only when explicitly enabled.
- Store state locally in SQLite and JSONL/Markdown outputs.
- Provide sample articles so the demo works even without network.
- The main source list is the AI/tech list from the Claude shared chat, not the art-auction pitch persona.

## Errors Encountered
| Error | Attempt | Resolution |
|---|---|---|
| `pdfinfo` and `pdftotext` unavailable | Extract pitch deck text through shell PDF tools | Used bundled Python runtime with `pypdf`. |
