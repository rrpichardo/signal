from __future__ import annotations

import argparse
from pathlib import Path

from .agent_runtime import AgentRuntimeError, SignalAgentRuntime
from .config import load_config
from .dashboard import serve_dashboard
from .llm import OllamaClient
from .orchestrator import SignalStreamOrchestrator
from .storage import SignalStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Signal Stream local agentic intelligence prototype.")
    parser.add_argument("--config", default="configs/ai_tech.toml", help="Path to a Signal Stream TOML config.")

    subparsers = parser.add_subparsers(dest="command")

    agent_parser = subparsers.add_parser("agent", help="Run the local agentic Orchestrator.")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command")
    agent_run_parser = agent_subparsers.add_parser("run", help="Run the Orchestrator + Scout + Analyst.")
    agent_run_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    agent_run_parser.add_argument("--goal", default="", help="Optional run goal for the Orchestrator.")

    run_parser = subparsers.add_parser("run", help="Run the legacy configured intelligence pipeline.")
    run_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    run_parser.add_argument("--output", help="Optional Markdown output path.")

    demo_parser = subparsers.add_parser("demo", help="Run the legacy demo pipeline with sample data.")
    demo_parser.add_argument("--config", default="configs/demo.toml", help="Path to a Signal Stream TOML config.")
    demo_parser.add_argument("--output", help="Optional Markdown output path.")

    show_parser = subparsers.add_parser("show", help="Show recent saved signals.")
    show_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    show_parser.add_argument("--limit", type=int, default=10)

    dashboard_parser = subparsers.add_parser("dashboard", help="Serve the local Signal Stream dashboard.")
    dashboard_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=None)

    memory_parser = subparsers.add_parser("memory", help="Inspect Signal Stream memory.")
    memory_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    memory_parser.add_argument("memory_command", choices=["show"])
    memory_parser.add_argument("--limit", type=int, default=20)

    feedback_parser = subparsers.add_parser("feedback", help="Record usefulness feedback for a signal.")
    feedback_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    feedback_parser.add_argument("--signal-id", required=True)
    feedback_parser.add_argument("--label", required=True, choices=["useful", "not_useful", "critical", "irrelevant"])
    feedback_parser.add_argument("--note", default="")

    doctor_parser = subparsers.add_parser("doctor", help="Check config, storage, and optional Ollama connectivity.")
    doctor_parser.add_argument("--config", default=None, help="Path to a Signal Stream TOML config.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "agent"
    config_path = args.config or "configs/ai_tech.toml"
    config = load_config(config_path)

    if command == "agent":
        if (args.agent_command or "run") != "run":
            parser.print_help()
            return 2
        try:
            result = SignalAgentRuntime(config, config_path=config_path).run(goal=args.goal or None)
        except AgentRuntimeError as exc:
            print(f"Agent run failed: {exc}")
            print("Tip: start Ollama and pull a small model, for example `ollama pull qwen3:1.7b`.")
            return 1
        print(f"Agent run: {result['run_id']}")
        print(f"Digest: {result['output_path']}")
        print(f"Articles: {result['articles']} | Signals: {result['signals']}")
        print(f"Dashboard: python3 -m signal_stream dashboard --config {config_path}")
        return 0

    if command in {"run", "demo"}:
        result = SignalStreamOrchestrator(config).run(output_path=getattr(args, "output", None))
        print(f"Digest: {result.output_path}")
        print(f"Articles: {result.article_count} | Clusters: {result.cluster_count} | Signals: {result.signal_count}")
        print("Top signals:")
        for signal in result.top_signals[:5]:
            print(f"- {signal.id} | {signal.score}/100 | {signal.urgency} | {signal.title}")
        return 0

    if command == "show":
        storage = SignalStorage(config.storage_path)
        storage.init()
        rows = storage.list_signals(limit=args.limit)
        if not rows:
            print("No saved signals yet. Run `python3 -m signal_stream demo` first.")
            return 0
        for row in rows:
            print(f"{row['id']} | {row['score']}/100 | {row['urgency']} | {row['title']}")
        return 0

    if command == "dashboard":
        # Pass config_path so the Run button can re-create SignalAgentRuntime.
        serve_dashboard(config, host=args.host, port=args.port, config_path=config_path)
        return 0

    if command == "memory":
        storage = SignalStorage(config.storage_path)
        storage.init()
        for row in storage.list_memory(limit=args.limit):
            print(f"{row['created_at']} | {row['topic']} | {row['title']}")
        return 0

    if command == "feedback":
        storage = SignalStorage(config.storage_path)
        storage.init()
        storage.add_feedback(args.signal_id, args.label, args.note)
        print(f"Recorded {args.label} feedback for {args.signal_id}.")
        return 0

    if command == "doctor":
        storage = SignalStorage(config.storage_path)
        storage.init()
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Config: {Path(args.config).resolve()}")
        print(f"Storage: {storage.path}")
        print(f"Output dir: {output_dir.resolve()}")
        print(f"Sources enabled: {sum(1 for source in config.sources if source.enabled)}")
        if config.ollama.enabled:
            client = OllamaClient(config)
            print(f"Ollama: {'available' if client.available() else 'unavailable'} ({config.ollama.host}, {config.ollama.model})")
            if client.last_error:
                print(f"Ollama error: {client.last_error}")
        else:
            print("Ollama: disabled; agent mode needs it unless allow_mock_brain is true.")
        return 0

    parser.print_help()
    return 2
