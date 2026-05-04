from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from .models import AgentConfig, OllamaConfig, Priority, SignalConfig, SourceConfig


def _resolve(base_dir: Path, value: str | None, default: str) -> str:
    raw = value or default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def load_config(path: str | Path = "configs/demo.toml") -> SignalConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    base_dir = config_path.parent
    profile = raw.get("profile", {})
    storage = raw.get("storage", {})
    delivery = raw.get("delivery", {})
    ollama = raw.get("ollama", {})
    agent = raw.get("agent", {})

    priorities = [
        Priority(
            name=str(item.get("name", "Unnamed priority")),
            description=str(item.get("description", "")),
            weight=float(item.get("weight", 1.0)),
            keywords=_list(item.get("keywords", [])),
        )
        for item in raw.get("priorities", [])
    ]

    sources = [
        SourceConfig(
            name=str(item.get("name", "Unnamed source")),
            kind=str(item.get("kind", "rss")).lower(),
            url=item.get("url"),
            path=_resolve(base_dir, item.get("path"), "") if item.get("path") else None,
            group=str(item.get("group", "general")),
            channel_id=item.get("channel_id"),
            on_demand=bool(item.get("on_demand", False)),
            limit=int(item.get("limit", 25)),
            enabled=bool(item.get("enabled", True)),
        )
        for item in raw.get("sources", [])
    ]

    return SignalConfig(
        name=str(profile.get("name", "Signal Stream")),
        organization=str(profile.get("organization", "")),
        audience=str(profile.get("audience", "")),
        mission=str(profile.get("mission", "")),
        competitors=_list(profile.get("competitors", [])),
        markets=_list(profile.get("markets", [])),
        priorities=priorities,
        sources=sources,
        storage_path=_resolve(base_dir, storage.get("path"), "../.signal_stream/signal_stream.db"),
        output_dir=_resolve(base_dir, delivery.get("output_dir"), "../outputs"),
        digest_limit=int(delivery.get("digest_limit", 10)),
        critical_threshold=int(delivery.get("critical_threshold", 82)),
        similarity_threshold=float(delivery.get("similarity_threshold", 0.52)),
        ollama=OllamaConfig(
            enabled=bool(ollama.get("enabled", True)),
            model=str(ollama.get("model", "qwen3:1.7b")),
            host=str(ollama.get("host", "http://localhost:11434")).rstrip("/"),
            timeout_seconds=int(ollama.get("timeout_seconds", 45)),
        ),
        agent=AgentConfig(
            max_iterations=int(agent.get("max_iterations", 6)),
            min_signals=int(agent.get("min_signals", 8)),
            dashboard_port=int(agent.get("dashboard_port", 8765)),
            worker_timeout_seconds=int(agent.get("worker_timeout_seconds", 120)),
            max_article_age_days=int(agent.get("max_article_age_days", 14)),
            brain_file=_resolve(base_dir, agent.get("brain_file") or agent.get("prompt_file"), "agent_brain.toml"),
            prompt_file=_resolve(base_dir, agent.get("brain_file") or agent.get("prompt_file"), "agent_brain.toml"),
            scout_mode=str(agent.get("scout_mode", "code")).lower(),
            analyst_mode=str(agent.get("analyst_mode", "code")).lower(),
            require_ollama=bool(agent.get("require_ollama", True)),
            allow_mock_brain=bool(agent.get("allow_mock_brain", False)),
        ),
    )
