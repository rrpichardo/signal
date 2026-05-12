from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .analysis_tools import analyze_articles, score_digest_quality
from .config import load_config
from .llm import BrainClient
from .models import SourceConfig
from .prompt_loader import load_behavior_settings, load_prompt_set, load_scoring_rubric
from .source_tools import enrich_articles_with_model, fetch_context, fetch_source
from .storage import SignalStorage


# How far back of the last complete run's started_at to fetch. The overlap
# absorbs late-publishing feeds and reduces the risk of missing a story that
# landed just before the previous run started. Storage.is_article_seen()
# drops the dupes that fall inside the overlap.
CURSOR_OVERLAP = timedelta(hours=6)


def main(argv: list[str] | None = None) -> int:
    """Run one subagent process.

    Plain English: the Orchestrator starts this file twice:
    once as Scout and once as Analyst. Each worker waits for JSON tasks,
    completes the task it understands, and returns JSON.
    """

    parser = argparse.ArgumentParser(description="Signal Stream worker process.")
    parser.add_argument("agent", choices=["scout", "analyst", "critic"])
    parser.add_argument("--config", default="configs/ai_tech.toml")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    storage = SignalStorage(config.storage_path)
    storage.init()
    prompts = load_prompt_set(config.agent.brain_file)
    scoring_rubric = load_scoring_rubric(config.agent.brain_file)
    behavior = load_behavior_settings(config.agent.brain_file)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            task = json.loads(line)
            result = handle_task(args.agent, config, storage, prompts, scoring_rubric, behavior, task)
        except Exception as exc:  # noqa: BLE001 - workers must report errors as JSON.
            result = {
                "task_id": "unknown",
                "agent": args.agent,
                "status": "error",
                "data": {},
                "error": f"{exc}\n{traceback.format_exc(limit=4)}",
                "confidence": 0.0,
            }
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0


def handle_task(
    agent: str,
    config: Any,
    storage: SignalStorage,
    prompts: dict[str, str],
    scoring_rubric: dict[str, Any],
    behavior: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """Route a task to the correct tool for this worker."""

    task_id = str(task.get("task_id", "unknown"))
    task_type = str(task.get("type", ""))
    payload = dict(task.get("payload") or {})

    if agent == "scout":
        if task_type == "collect_sources":
            sources = [_source_from_dict(item) for item in payload.get("sources", [])]
            # Resolve the cursor once per collect_sources task: the started_at
            # of the most recent COMPLETE agent_run, minus a 6-hour overlap.
            # First run (no complete history) leaves published_after as None,
            # which fetch_source treats as the preserved per-source.limit path.
            published_after = _resolve_cursor(storage)
            results = [
                fetch_source(source, published_after=published_after)
                for source in sources
                if source.enabled and not source.on_demand
            ]
            articles = []
            for result in results:
                articles.extend(result.get("articles", []))
            if _mode(behavior, config, "scout") in {"hybrid", "model"}:
                llm = BrainClient(config)
                articles = enrich_articles_with_model(
                    llm,
                    prompts["scout"],
                    articles,
                    relevance_policy=str(behavior.get("relevance_policy", "soft_keep")),
                    scout_note_enabled=bool(behavior.get("scout_note_enabled", True)),
                )
            return _ok(task_id, agent, {"source_results": results, "articles": articles}, _avg_confidence(results))
        if task_type == "collect_more_context":
            result = fetch_context(str(payload.get("query", "")), list(payload.get("articles", [])), int(payload.get("limit", 5)))
            if _mode(behavior, config, "scout") in {"hybrid", "model"}:
                llm = BrainClient(config)
                result["articles"] = enrich_articles_with_model(
                    llm,
                    prompts["scout"],
                    list(result.get("articles", [])),
                    max_items=5,
                    relevance_policy=str(behavior.get("relevance_policy", "soft_keep")),
                    scout_note_enabled=bool(behavior.get("scout_note_enabled", True)),
                )
            return _ok(task_id, agent, result, float(result.get("confidence", 0.0)))

    if agent == "analyst" and task_type == "analyze_articles":
        data = analyze_articles(
            config,
            storage,
            list(payload.get("articles", [])),
            analyst_mode=_mode(behavior, config, "analyst"),
            analyst_prompt=prompts["analyst"],
            scoring_rubric=scoring_rubric,
            behavior=behavior,
        )
        return _ok(task_id, agent, data, 0.82 if data.get("signals") else 0.25)

    if agent == "critic" and task_type == "critique_digest":
        # The Critic scores the Analyst's ranked signals before the Orchestrator
        # decides whether to ship. It runs code checks always and optionally calls
        # the LLM in hybrid/model mode using the analyst_mode setting as a proxy
        # (critics review Analyst output, so the same mode switch makes sense).
        llm = BrainClient(config) if _mode(behavior, config, "analyst") in {"hybrid", "model"} else None
        data = score_digest_quality(
            signals=list(payload.get("signals", [])),
            critic_prompt=prompts.get("critic", ""),
            llm=llm,
            critic_mode=_mode(behavior, config, "analyst"),
        )
        # Confidence is high when the score is high — nothing suspicious.
        confidence = max(0.1, min(1.0, data.get("score", 0) / 100))
        return _ok(task_id, agent, data, confidence)

    return {
        "task_id": task_id,
        "agent": agent,
        "status": "error",
        "data": {},
        "error": f"{agent} cannot handle task type {task_type!r}",
        "confidence": 0.0,
    }


def _ok(task_id: str, agent: str, data: dict[str, Any], confidence: float) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "agent": agent,
        "status": "ok",
        "data": data,
        "error": "",
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _source_from_dict(item: dict[str, Any]) -> SourceConfig:
    path = item.get("path")
    if path:
        path = str(Path(path).expanduser())
    return SourceConfig(
        name=str(item.get("name", "Unnamed source")),
        kind=str(item.get("kind", "rss")),
        url=item.get("url"),
        path=path,
        group=str(item.get("group", "general")),
        channel_id=item.get("channel_id"),
        on_demand=bool(item.get("on_demand", False)),
        limit=int(item.get("limit", 25)),
        enabled=bool(item.get("enabled", True)),
    )


def _avg_confidence(results: list[dict[str, Any]]) -> float:
    if not results:
        return 0.0
    return sum(float(item.get("confidence", 0.0)) for item in results) / len(results)


def _resolve_cursor(storage: SignalStorage) -> datetime | None:
    """Compute the cursor from the last complete run's started_at minus overlap.

    Plain English: the run cursor is "last successful run, minus 6 hours."
    On the very first run (or any run where no prior run reached status =
    complete) this returns None, which tells fetch_source to fall back to
    each source's own `limit` value.
    """

    started_at = storage.latest_complete_agent_run_started_at()
    if not started_at:
        return None
    parsed = _parse_iso(started_at)
    if parsed is None:
        return None
    return parsed - CURSOR_OVERLAP


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mode(behavior: dict[str, Any], config: Any, agent: str) -> str:
    """Choose the mode from the brain file, with the old config as fallback."""

    fallback = config.agent.scout_mode if agent == "scout" else config.agent.analyst_mode
    return str(behavior.get(f"{agent}_mode") or fallback or "code").lower()


if __name__ == "__main__":
    raise SystemExit(main())
