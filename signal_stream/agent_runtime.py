from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import subprocess
import sys
from typing import Any

from .llm import OllamaClient
from .models import AgentDecision, Signal, SignalConfig, ToolCall, stable_id, utc_now_iso
from .prompt_loader import load_prompt_set
from .prompts import DECISION_SCHEMA
from .storage import SignalStorage


class AgentRuntimeError(RuntimeError):
    pass


class WorkerClient:
    """Tiny wrapper around one subagent process.

    Plain English: this starts a second Python program and sends it one JSON
    task at a time. That is what makes Scout and Analyst separate workers
    instead of just function names inside the Orchestrator.
    """

    def __init__(self, agent: str, config_path: str, timeout_seconds: int):
        self.agent = agent
        self.timeout_seconds = timeout_seconds
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "signal_stream.worker", agent, "--config", config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def request(self, task: dict[str, Any]) -> dict[str, Any]:
        if self.proc.stdin is None or self.proc.stdout is None:
            raise AgentRuntimeError(f"{self.agent} worker pipes are unavailable.")
        self.proc.stdin.write(json.dumps(task, sort_keys=True) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            error = self.proc.stderr.read() if self.proc.stderr else ""
            raise AgentRuntimeError(f"{self.agent} worker exited without a response. {error}")
        return json.loads(line)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - best-effort worker cleanup.
            self.proc.kill()
        for pipe in (self.proc.stdout, self.proc.stderr):
            if pipe:
                pipe.close()


class SignalAgentRuntime:
    """The local Signal Stream agent system.

    Plain English: this is the "manager." It does not scrape articles itself
    and it does not score them itself. It decides which helper agent should act
    next, reads their results, and decides when the digest is good enough.
    """

    def __init__(self, config: SignalConfig, config_path: str = "configs/ai_tech.toml"):
        self.config = config
        self.config_path = config_path
        self.storage = SignalStorage(config.storage_path)
        self.llm = OllamaClient(config)
        self.prompts = load_prompt_set(config.agent.brain_file)

    def run(self, goal: str | None = None) -> dict[str, Any]:
        self.storage.init()
        goal = goal or "Surface today's highest-signal AI/tech developments and prepare a digest."
        run_id = self.storage.start_agent_run(goal)
        self.storage.save_agent_event(run_id, "Orchestrator", "start", "Started local agent run.", {"goal": goal})

        if self.config.agent.require_ollama and not self.llm.available():
            message = self.llm.last_error or "Ollama is not available."
            self.storage.save_agent_event(run_id, "Orchestrator", "error", "Ollama is required for the agent brain.", {"error": message})
            self.storage.finish_agent_run(run_id, "error", {"error": message})
            raise AgentRuntimeError(f"Ollama is required for agentic mode but is unavailable: {message}")

        scout = WorkerClient("scout", self.config_path, self.config.agent.worker_timeout_seconds)
        analyst = WorkerClient("analyst", self.config_path, self.config.agent.worker_timeout_seconds)
        state: dict[str, Any] = {
            "goal": goal,
            "articles": [],
            "analysis": {},
            "context_rounds": 0,
            "actions": [],
            "finalized": False,
        }

        try:
            for iteration in range(1, self.config.agent.max_iterations + 1):
                # Step 1: the Orchestrator looks at the current state and chooses
                # the next action. This decision point is what separates an agent
                # from a fixed automation pipeline.
                decision = self._decide(run_id, state, iteration)
                state["actions"].append(asdict(decision))
                self.storage.save_agent_event(run_id, "Orchestrator", "decision", decision.thought, asdict(decision))

                if decision.action == "collect_sources":
                    # Step 2a: Scout gathers raw source material. The Orchestrator
                    # does not know how to fetch RSS or YouTube; Scout does.
                    result = self._call_worker(run_id, scout, "collect_sources", {"sources": [asdict(source) for source in self.config.sources]})
                    state["articles"] = _merge_articles(state["articles"], result.get("data", {}).get("articles", []))
                    self.storage.save_agent_event(run_id, "Scout", "observation", "Collected configured sources.", _compact_result(result))
                elif decision.action == "collect_more_context":
                    # Step 2b: If the Orchestrator is not satisfied, it can ask
                    # Scout for a targeted second look instead of blindly moving on.
                    result = self._call_worker(
                        run_id,
                        scout,
                        "collect_more_context",
                        {
                            "query": decision.target or decision.params.get("query", ""),
                            "articles": state["articles"],
                            "limit": int(decision.params.get("limit", 5)),
                        },
                    )
                    state["articles"] = _merge_articles(state["articles"], result.get("data", {}).get("articles", []))
                    state["context_rounds"] += 1
                    self.storage.save_agent_event(run_id, "Scout", "observation", "Collected additional context.", _compact_result(result))
                elif decision.action == "analyze_articles":
                    # Step 2c: Analyst judges the collected material. This worker
                    # handles dedupe, scoring, memory checks, and digest copy.
                    result = self._call_worker(run_id, analyst, "analyze_articles", {"articles": state["articles"]})
                    state["analysis"] = result.get("data", {})
                    self.storage.save_agent_event(run_id, "Analyst", "observation", "Analyzed candidate articles.", _compact_result(result))
                elif decision.action == "finalize_digest":
                    # Step 2d: The Orchestrator can stop early when it decides the
                    # result is good enough.
                    state["finalized"] = True
                    break

                if _ready_to_finalize(state, self.config.agent.min_signals):
                    self.storage.save_agent_event(run_id, "Orchestrator", "checkpoint", "Enough high-confidence signals exist; finalizing.", {})
                    state["finalized"] = True
                    break

            signals = [_signal_from_json(item) for item in state.get("analysis", {}).get("signals", [])]
            output_path = self._write_digest(run_id, state)
            self.storage.save_run([], signals, int(state.get("analysis", {}).get("cluster_count", 0)), output_path, utc_now_iso())
            for signal in signals[: self.config.digest_limit]:
                # Memory is how Signal Stream avoids acting like every day is day
                # one. Future runs can see these saved topics and downgrade repeats.
                self.storage.save_memory_for_signal(signal)
            self.storage.finish_agent_run(
                run_id,
                "complete" if state["finalized"] else "max_iterations",
                {"articles": len(state["articles"]), "signals": len(signals), "output_path": output_path},
            )
            self.storage.save_agent_event(run_id, "Orchestrator", "finish", "Finished agent run.", {"output_path": output_path})
            return {"run_id": run_id, "output_path": output_path, "articles": len(state["articles"]), "signals": len(signals)}
        finally:
            scout.close()
            analyst.close()

    def _decide(self, run_id: str, state: dict[str, Any], iteration: int) -> AgentDecision:
        """Ask Ollama what the Orchestrator should do next."""

        if self.config.agent.allow_mock_brain or not self.config.ollama.enabled:
            return _mock_decision(state)

        user = json.dumps(
            {
                "iteration": iteration,
                "goal": state["goal"],
                "article_count": len(state["articles"]),
                "signals_count": len(state.get("analysis", {}).get("signals", [])),
                "top_titles": [item.get("title") for item in state.get("analysis", {}).get("signals", [])[:5]],
                "context_rounds": state["context_rounds"],
                "previous_actions": state["actions"][-4:],
            },
            sort_keys=True,
        )
        raw = self.llm.chat_json(self.prompts["orchestrator"], user, DECISION_SCHEMA)
        if not raw:
            self.storage.save_agent_event(run_id, "Orchestrator", "warning", "Ollama decision failed; using safe local decision.", {"error": self.llm.last_error or ""})
            return _mock_decision(state)
        return AgentDecision(
            thought=str(raw.get("thought", "")),
            action=str(raw.get("action", "finalize_digest")),
            target=str(raw.get("target", "")),
            reason=str(raw.get("reason", "")),
            params=dict(raw.get("params") or {}),
        )

    def _call_worker(self, run_id: str, worker: WorkerClient, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one task to Scout or Analyst and save the tool-call receipt."""

        task_id = stable_id(run_id, worker.agent, task_type, len(payload), utc_now_iso(), prefix="task")
        result = worker.request({"task_id": task_id, "type": task_type, "payload": payload})
        self.storage.save_tool_call(
            ToolCall(
                id=stable_id(task_id, task_type, prefix="tool"),
                run_id=run_id,
                agent=worker.agent.title(),
                tool=task_type,
                status=str(result.get("status", "unknown")),
                input={"task_type": task_type},
                output=_compact_result(result),
                error=str(result.get("error", "")),
                confidence=float(result.get("confidence", 0.0)),
            )
        )
        return result

    def _write_digest(self, run_id: str, state: dict[str, Any]) -> str:
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"agent_digest_{run_id}.md"
        digest = state.get("analysis", {}).get("digest") or "# Signal Stream Digest\n\nNo digest was produced."
        path.write_text(digest, encoding="utf-8")
        return str(path)


def _mock_decision(state: dict[str, Any]) -> AgentDecision:
    """Simple test brain used only when mock mode is explicitly enabled."""

    if not state["articles"]:
        return AgentDecision("Need source material before analysis.", "collect_sources", reason="No articles have been collected.", params={})
    if not state.get("analysis"):
        return AgentDecision("Need Analyst review before finalizing.", "analyze_articles", reason="Articles exist but no ranked signals exist.", params={})
    if len(state.get("analysis", {}).get("signals", [])) < 3 and state["context_rounds"] < 1:
        return AgentDecision("Signals are thin; ask Scout for related context.", "collect_more_context", target="AI agents platform shifts", reason="Not enough candidate signals.", params={"limit": 5})
    return AgentDecision("Enough analysis exists to publish.", "finalize_digest", reason="Ranked signals are available.", params={})


def _ready_to_finalize(state: dict[str, Any], min_signals: int) -> bool:
    signals = state.get("analysis", {}).get("signals", [])
    if len(signals) >= min_signals:
        return True
    return bool(signals) and state["context_rounds"] >= 1


def _merge_articles(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {item.get("id") or item.get("url") or item.get("title") for item in existing}
    merged = list(existing)
    for item in incoming:
        key = item.get("id") or item.get("url") or item.get("title")
        if key and key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    data = dict(result.get("data") or {})
    if "articles" in data:
        data["article_count"] = len(data.pop("articles"))
    if "signals" in data:
        data["signal_count"] = len(data.get("signals", []))
        data["signals"] = data["signals"][:5]
    if "source_results" in data:
        data["source_results"] = [
            {"source": item.get("source"), "status": item.get("status"), "count": len(item.get("articles", [])), "error": item.get("error", "")}
            for item in data["source_results"]
        ]
    return {"status": result.get("status"), "confidence": result.get("confidence"), "data": data, "error": result.get("error", "")}


def _signal_from_json(item: dict[str, Any]) -> Signal:
    return Signal(
        id=str(item.get("id", "")),
        cluster_id=str(item.get("cluster_id", "")),
        article_id=str(item.get("article_id", "")),
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        source=str(item.get("source", "")),
        published_at=str(item.get("published_at", "")),
        score=int(item.get("score", 0)),
        urgency=str(item.get("urgency", "")),
        event_type=str(item.get("event_type", "")),
        summary=str(item.get("summary", "")),
        why_it_matters=str(item.get("why_it_matters", "")),
        next_steps=list(item.get("next_steps", [])),
        matched_priorities=list(item.get("matched_priorities", [])),
        entities=dict(item.get("entities", {})),
        duplicate_count=int(item.get("duplicate_count", 0)),
        score_breakdown=list(item.get("score_breakdown", [])),
        short_summary=str(item.get("short_summary", item.get("summary", ""))),
        expanded_summary=str(item.get("expanded_summary", item.get("summary", ""))),
        image_url=str(item.get("image_url", "")),
        icon_key=str(item.get("icon_key", "")),
        scout_note=str(item.get("scout_note", "")),
        relevance_label=str(item.get("relevance_label", "")),
    )
