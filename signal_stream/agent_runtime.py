from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import queue
import subprocess
import sys
import threading
import tomllib
import traceback
from typing import Any

from .llm import BrainClient
from .models import AgentDecision, Article, Signal, SignalConfig, ToolCall, stable_id, utc_now_iso
from .prompt_loader import load_behavior_settings, load_prompt_set
from .prompts import DECISION_SCHEMA
from .storage import SignalStorage


class AgentRuntimeError(RuntimeError):
    pass


class WorkerClient:
    """Tiny wrapper around one subagent process.

    Plain English: this starts a second Python program and sends it one JSON
    task at a time. That is what makes Scout, Analyst, and Critic separate
    workers instead of just function names inside the Orchestrator.

    M2 fix: stderr is sent to DEVNULL so a chatty worker can never fill the
    64 KB pipe buffer and deadlock the orchestrator. Workers already report
    errors as JSON on stdout, so stderr is not load-bearing.

    M1 fix: stdout.readline() is wrapped in a thread + queue so the
    worker_timeout_seconds config value is actually enforced. Previously this
    call blocked forever if a worker hung, making the timeout setting a no-op.

    M3 fix: stdin.write is also wrapped in a thread + join with timeout. A
    multi-MB payload (e.g. 110 articles) easily exceeds the OS pipe buffer
    (~16-64 KB on macOS); if the worker stalls before draining its stdin,
    a naive write blocks the orchestrator forever with no timeout coverage.
    """

    def __init__(self, agent: str, config_path: str, timeout_seconds: int):
        self.agent = agent
        self.timeout_seconds = timeout_seconds
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "signal_stream.worker", agent, "--config", config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # DEVNULL prevents the stderr pipe buffer (~64 KB) from filling when
            # a worker prints warnings or long tracebacks, which would otherwise
            # cause a deadlock as both sides wait for the other to drain.
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        # Background reader thread feeds stdout lines into a queue so request()
        # can block with a timeout instead of blocking on readline() forever.
        self._q: queue.Queue[str | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        """Drain the worker's stdout into a queue; put None on EOF."""
        try:
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self._q.put(line)
        finally:
            self._q.put(None)

    def _send_payload(self, payload: str, error_box: list[BaseException]) -> None:
        """Write one task line to the worker's stdin from a helper thread."""
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except BaseException as exc:  # noqa: BLE001 - bubble through error_box.
            # BrokenPipeError when the worker died, or anything else: capture
            # so the caller can surface it instead of dying inside this thread.
            error_box.append(exc)

    def request(self, task: dict[str, Any]) -> dict[str, Any]:
        if self.proc.stdin is None:
            raise AgentRuntimeError(f"{self.agent} worker stdin is unavailable.")

        # Hand the write off to a thread so a stuck worker can't block the
        # orchestrator past timeout_seconds. The reader was already async; now
        # the write is too, giving full round-trip timeout coverage.
        payload = json.dumps(task, sort_keys=True) + "\n"
        write_error: list[BaseException] = []
        writer = threading.Thread(
            target=self._send_payload,
            args=(payload, write_error),
            daemon=True,
            name=f"{self.agent}-writer",
        )
        writer.start()
        writer.join(timeout=self.timeout_seconds)
        if writer.is_alive():
            # Worker stalled before draining stdin. Kill the subprocess so the
            # blocked write unwinds with BrokenPipeError, then surface a clear
            # error. The writer thread is daemon=True so it dies with the proc.
            self.proc.kill()
            raise AgentRuntimeError(
                f"{self.agent} worker stalled accepting input after {self.timeout_seconds}s. "
                "Consider raising worker_timeout_seconds in the config."
            )
        if write_error:
            # Most commonly BrokenPipeError when the worker exited early.
            raise AgentRuntimeError(
                f"{self.agent} worker dropped before reading task: {write_error[0]}"
            )

        try:
            line = self._q.get(timeout=self.timeout_seconds)
        except queue.Empty:
            self.proc.kill()
            raise AgentRuntimeError(
                f"{self.agent} worker timed out after {self.timeout_seconds}s. "
                "Consider raising worker_timeout_seconds in the config."
            )
        if line is None:
            raise AgentRuntimeError(f"{self.agent} worker exited without a response.")
        return json.loads(line)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - best-effort worker cleanup.
            self.proc.kill()
        if self.proc.stdout:
            self.proc.stdout.close()


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
        self.llm = BrainClient(config)
        self.prompts = load_prompt_set(config.agent.brain_file)
        self.behavior = load_behavior_settings(config.agent.brain_file)

        # Merge only explicitly-set critic fields from the brain file into
        # config.agent. self.behavior blends file values with defaults — checking
        # it would silently override the agent TOML even when the brain file
        # doesn't mention critic settings at all. Read the raw file instead.
        _brain_path = Path(config.agent.brain_file).expanduser().resolve()
        if _brain_path.exists():
            with _brain_path.open("rb") as _fh:
                _brain_behavior = tomllib.load(_fh).get("behavior", {})
            if "enable_critic" in _brain_behavior:
                self.config.agent.enable_critic = bool(_brain_behavior["enable_critic"])
            if "max_critic_rounds" in _brain_behavior:
                self.config.agent.max_critic_rounds = int(_brain_behavior["max_critic_rounds"])
            if "critic_score_threshold" in _brain_behavior:
                self.config.agent.critic_score_threshold = int(_brain_behavior["critic_score_threshold"])

    def run(self, goal: str | None = None) -> dict[str, Any]:
        self.storage.init()
        goal = goal or "Surface today's highest-signal AI/tech developments and prepare a digest."
        run_id = self.storage.start_agent_run(goal)
        self.storage.save_agent_event(run_id, "Orchestrator", "start", "Started local agent run.", {"goal": goal})
        _run_completed = False

        if self.config.agent.require_brain and not self.llm.available():
            message = self.llm.last_error or "Brain (Groq) is not available."
            self.storage.save_agent_event(run_id, "Orchestrator", "error", "Groq brain is required but unavailable.", {"error": message})
            self.storage.finish_agent_run(run_id, "error", {"error": message})
            raise AgentRuntimeError(f"Brain (Groq) is required for agentic mode but is unavailable: {message}")

        scout = WorkerClient("scout", self.config_path, self.config.agent.worker_timeout_seconds)
        analyst = WorkerClient("analyst", self.config_path, self.config.agent.worker_timeout_seconds)
        # Critic worker is opt-in. Spawn it only when enable_critic is set so
        # existing runs that don't use it pay no subprocess overhead.
        critic: WorkerClient | None = (
            WorkerClient("critic", self.config_path, self.config.agent.worker_timeout_seconds)
            if self.config.agent.enable_critic
            else None
        )
        # Editor worker always spawned — it is a pure reducer (no article fetches in the
        # base path) so its overhead is minimal. Phase 4 fallback runs inside the worker too.
        editor = WorkerClient("editor", self.config_path, self.config.agent.worker_timeout_seconds)
        state: dict[str, Any] = {
            "goal": goal,
            "articles": [],
            "analysis": {},
            "context_rounds": 0,
            "critic_rounds": 0,
            "critic_notes": [],
            "actions": [],
            "finalized": False,
            # Tells _mock_decision whether to issue critique_digest. Only relevant
            # when allow_mock_brain=True; real brain decisions come from Groq.
            "enable_critic_mock": self.config.agent.enable_critic,
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
                    # Surface any per-source cap warnings as their own timeline
                    # events so the dashboard activity panel can show that a
                    # feed had more new entries than the 20-per-source cap.
                    for sr in result.get("data", {}).get("source_results", []) or []:
                        capped = sr.get("source_capped")
                        if capped:
                            self.storage.save_agent_event(
                                run_id,
                                "Scout",
                                "source_capped",
                                f"{sr.get('source', '?')} returned {capped} new entries; kept latest 20.",
                                {"source": sr.get("source"), "count": int(capped)},
                            )
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
                    review_limit = int(self.behavior.get("analyst_review_limit", 40))
                    article_count = len(state["articles"])
                    if self.behavior.get("analyst_full_review"):
                        self.storage.save_agent_event(
                            run_id, "Analyst", "fetching_full_articles",
                            f"Fetching full article pages for top {min(review_limit, article_count)}/{article_count} candidates.",
                            {"review_limit": review_limit, "article_count": article_count},
                        )
                        self.storage.save_agent_event(
                            run_id, "Analyst", "groq_reviewing",
                            f"Sending top {min(review_limit, article_count)} articles to Groq one-per-request.",
                            {"batch_size": int(self.behavior.get("analyst_review_batch_size", 1)), "review_limit": review_limit},
                        )
                    result = self._call_worker(run_id, analyst, "analyze_articles", {"articles": state["articles"]})
                    state["analysis"] = result.get("data", {})
                    self.storage.save_agent_event(run_id, "Analyst", "observation", "Analyzed candidate articles.", _compact_result(result))
                    # Phase 2: surface each truncated article as its own event so
                    # the activity log shows exactly which signals lost text — and
                    # the dashboard can badge low-confidence rows accordingly.
                    for trunc in list(state["analysis"].get("truncation_events", [])):
                        self.storage.save_agent_event(
                            run_id,
                            "Analyst",
                            "truncated_article",
                            f"Article truncated for Groq review (signal {trunc.get('signal_id')}).",
                            trunc,
                        )
                    # One event per failed selected signal so the activity log
                    # surfaces exactly which reviews Groq couldn't complete.
                    for failure in list(state["analysis"].get("analyst_failures", [])):
                        self.storage.save_agent_event(
                            run_id,
                            "Analyst",
                            "groq_review_failed",
                            f"Groq review failed for '{failure.get('title', '?')}' ({failure.get('error_type', 'unknown')}).",
                            failure,
                        )
                    # One event per signal whose analyst response had to be
                    # coerced (missing/malformed artifact fields). High counts
                    # indicate the analyst prompt is drifting or the model is
                    # silently omitting fields the schema documents as required.
                    coercion_events = list(state["analysis"].get("coercion_events", []))
                    if coercion_events:
                        self.storage.save_agent_event(
                            run_id,
                            "Analyst",
                            "artifact_coerced",
                            f"Coerced artifact fields on {len(coercion_events)} signal(s).",
                            {"count": len(coercion_events), "samples": coercion_events[:5]},
                        )
                    # Safety net: if the Critic has exhausted its revision rounds and
                    # the LLM keeps choosing analyze_articles instead of critique_digest,
                    # ship the best signals now rather than burning iterations until
                    # max_iterations triggers an "interrupted" status.
                    if (
                        state["critic_rounds"] >= self.config.agent.max_critic_rounds
                        and state.get("analysis", {}).get("signals")
                    ):
                        self.storage.save_agent_event(
                            run_id, "Orchestrator", "observation",
                            "Critic rounds exhausted after re-analysis; finalizing.",
                            {"critic_rounds": state["critic_rounds"]},
                        )
                        state["finalized"] = True
                        break
                elif decision.action == "critique_digest":
                    # Step 2d: Critic reviews the Analyst's ranked signals before
                    # the Orchestrator decides to ship. If the score is below the
                    # configured threshold and critic rounds remain, revision notes
                    # are attached to state so the next _decide() call sees them.
                    if critic is None:
                        # Critic not spawned (enable_critic=false). Auto-approve so
                        # the Orchestrator can pick this action without crashing when
                        # allow_mock_brain is on.
                        self.storage.save_agent_event(run_id, "Critic", "skipped", "Critic is disabled; auto-approving digest.", {})
                        state["finalized"] = True
                        break
                    result = self._call_worker(
                        run_id,
                        critic,
                        "critique_digest",
                        {"signals": state.get("analysis", {}).get("signals", [])},
                    )
                    critic_data = result.get("data", {})
                    critic_score = int(critic_data.get("score", 100))
                    weak_indices = list(critic_data.get("weak_indices", []))
                    revision_reasons = list(critic_data.get("reasons", []))
                    self.storage.save_agent_event(
                        run_id,
                        "Critic",
                        "observation",
                        f"Scored digest {critic_score}/100; {len(weak_indices)} weak signal(s).",
                        {"score": critic_score, "weak_indices": weak_indices, "reasons": revision_reasons},
                    )
                    if (
                        critic_score < self.config.agent.critic_score_threshold
                        and state["critic_rounds"] < self.config.agent.max_critic_rounds
                    ):
                        # Revision requested: store notes for the Orchestrator's next
                        # decision call, increment the counter so we don't loop forever.
                        state["critic_notes"].extend(revision_reasons)
                        state["critic_rounds"] += 1
                        self.storage.save_agent_event(run_id, "Orchestrator", "revision", "Critic requested revision; looping.", {"critic_rounds": state["critic_rounds"]})
                    else:
                        # Score at/above threshold, or max rounds exhausted — publish.
                        state["finalized"] = True
                        break
                elif decision.action == "finalize_digest":
                    # Step 2e: The Orchestrator can stop early when it decides the
                    # result is good enough.
                    state["finalized"] = True
                    break

            signals = [_signal_from_json(item) for item in state.get("analysis", {}).get("signals", [])]
            output_path = self._write_digest(run_id, state)
            summary = {
                "articles": len(state["articles"]),
                "signals": len(signals),
                "cluster_count": int(state.get("analysis", {}).get("cluster_count", 0)),
                "output_path": output_path,
            }
            if state["finalized"]:
                # Convert the article dicts that bounced through Scout's stdout
                # back into Article objects so the atomic save can persist them
                # to the seen-set. Only complete runs advance the cursor — the
                # whole insert + status='complete' transition happens here.
                collected = [_article_from_state_dict(item) for item in state["articles"]]
                # executive_summary_limit: how many top signals get saved to memory.
                # Top-12 stay in the dedup set; rest are digest-only.
                exec_limit = int(self.behavior.get("executive_summary_limit", 12))
                exec_signals = signals[:exec_limit]
                self.storage.save_agent_event(
                    run_id, "Analyst", "writing_digest",
                    f"Preparing digest: {len(signals)} signals, executive summary: top {exec_limit}.", {}
                )
                # Phase 3: Editor reduces the top signals into an executive briefing.
                # _call_editor always returns a 3-tuple and never raises — failure logs
                # and sets briefing_status='failed' but the run still completes.
                raw_exec_signals = list(state.get("analysis", {}).get("signals", []))[:exec_limit]
                briefing_json, briefing_status, briefing_error = self._call_editor(
                    run_id, editor, raw_exec_signals
                )
                self.storage.save_run_atomic(
                    articles=collected,
                    signals=signals,
                    cluster_count=int(state.get("analysis", {}).get("cluster_count", 0)),
                    output_path=output_path,
                    started_at=utc_now_iso(),
                    run_id=run_id,
                    summary=summary,
                    briefing_json=briefing_json,
                    briefing_status=briefing_status,
                    briefing_error=briefing_error,
                )
                for signal in exec_signals:
                    # Memory is how Signal Stream avoids acting like every day is day
                    # one. Future runs can see these saved topics and downgrade repeats.
                    # Skip failed/pending signals — their summaries may be raw RSS
                    # fallbacks (cookie banners, etc.) that should not enter memory.
                    if signal.analyst_status not in ("success", "skipped"):
                        continue
                    self.storage.save_memory_for_signal(signal)
            else:
                # max_iterations was reached without the Orchestrator deciding to
                # finalize. Mark the run "interrupted" and DO NOT persist articles
                # or signals — that would corrupt the cursor for the next run.
                self.storage.finish_agent_run(run_id, "interrupted", summary)
            self.storage.save_agent_event(run_id, "Orchestrator", "finish", "Finished agent run.", {"output_path": output_path})
            _run_completed = True
            return {"run_id": run_id, "output_path": output_path, "articles": len(state["articles"]), "signals": len(signals)}
        except BaseException as exc:
            # Covers KeyboardInterrupt, SystemExit (SIGTERM handler), and unexpected exceptions.
            # Ensures the run row never stays stuck at status='running' after a crash.
            if not _run_completed:
                # Build a human-readable reason from the exception itself so the
                # dashboard can show WHY a run failed instead of just a red badge.
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                last_action = state["actions"][-1].get("action") if state["actions"] else None
                # Drop an error event into the timeline FIRST so the failure is
                # visible even if the row update below fails for any reason.
                try:
                    self.storage.save_agent_event(
                        run_id,
                        "Orchestrator",
                        "error",
                        reason,
                        {"traceback": traceback.format_exc(), "last_action": last_action},
                    )
                except Exception as event_exc:  # noqa: BLE001 - log and continue cleanup.
                    print(
                        f"[signal_stream] failed to log error event for {run_id}: {event_exc}",
                        file=sys.stderr,
                    )
                try:
                    self.storage.finish_agent_run(
                        run_id,
                        "failed",
                        {"reason": reason, "last_action": last_action},
                    )
                except Exception as cleanup_exc:  # noqa: BLE001 - surface, don't swallow.
                    print(
                        f"[signal_stream] failed to mark {run_id} failed: {cleanup_exc}",
                        file=sys.stderr,
                    )
            raise
        finally:
            scout.close()
            analyst.close()
            if critic is not None:
                critic.close()
            editor.close()

    def _decide(self, run_id: str, state: dict[str, Any], iteration: int) -> AgentDecision:
        """Ask the brain what the Orchestrator should do next."""

        if self.config.agent.allow_mock_brain:
            return _mock_decision(state)

        user = json.dumps(
            {
                "iteration": iteration,
                "goal": state["goal"],
                "article_count": len(state["articles"]),
                "signals_count": len(state.get("analysis", {}).get("signals", [])),
                "top_titles": [item.get("title") for item in state.get("analysis", {}).get("signals", [])[:5]],
                "context_rounds": state["context_rounds"],
                # Critic revision notes are passed here so the Orchestrator can
                # react to specific quality problems in its next decision.
                "critic_rounds": state.get("critic_rounds", 0),
                "critic_notes": state.get("critic_notes", []),
                "previous_actions": state["actions"][-4:],
            },
            sort_keys=True,
        )
        raw = self.llm.chat_json(self.prompts["orchestrator"], user, DECISION_SCHEMA)
        # Log the full brain trace — system prompt, user payload, raw model
        # response — as one event so the dashboard timeline can show exactly
        # what was sent to Groq and exactly what came back. getattr() guards
        # against test mocks that don't expose last_response_text.
        self.storage.save_agent_event(
            run_id,
            "Orchestrator",
            "llm_trace",
            "Brain call: prompt + raw response.",
            {
                "system": self.prompts["orchestrator"],
                "user": user,
                "raw_response": getattr(self.llm, "last_response_text", ""),
                "parse_error": self.llm.last_error if not raw else "",
            },
        )
        if not raw:
            self.storage.save_agent_event(run_id, "Orchestrator", "warning", "Brain decision failed; using safe local decision.", {"error": self.llm.last_error or ""})
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
                # Echo the actual task payload, not just its type — debugging the
                # Orchestrator means knowing what query Scout was asked for, not
                # only that "collect_more_context" was called.
                input={"task_type": task_type, "payload": payload},
                # Store the full uncompacted result so the side-sheet inspector
                # in the dashboard shows every article Scout pulled and every
                # signal Analyst produced. _compact_result is still used for
                # the timeline event payloads above; the table that drives the
                # inspector pane gets the real data.
                output=result,
                error=str(result.get("error", "")),
                confidence=float(result.get("confidence", 0.0)),
            )
        )
        return result

    def _call_editor(
        self,
        run_id: str,
        editor: WorkerClient,
        raw_signals: list[dict[str, Any]],
    ) -> tuple[str | None, str, str]:
        """Ask the Editor to generate an executive briefing. Always returns a 3-tuple.

        Returns (briefing_json_str, briefing_status, briefing_error).
        Never raises — failure is logged and reported as status='failed' so
        save_run_atomic still completes with a null briefing.
        """
        if not raw_signals:
            self.storage.save_agent_event(run_id, "Editor", "skipped", "No signals; skipping briefing.", {})
            return None, "skipped", ""

        import time as _time
        # The analyst just made up to analyst_review_limit Groq calls, which
        # often exhausts the per-minute rate limit. A short pause lets the
        # Groq rate-limit window recover before the editor's single call.
        _time.sleep(30)

        try:
            result = self._call_worker(run_id, editor, "generate_briefing", {
                "signals": raw_signals,
                "run_context": {"signal_count": len(raw_signals)},
            })
            if result.get("status") == "error":
                err = str(result.get("error", "unknown"))
                self.storage.save_agent_event(run_id, "Editor", "failed", f"Editor worker error: {err}", {"error": err})
                return None, "failed", err

            data = result.get("data", {})
            briefing = data.get("briefing")
            status = str(data.get("briefing_status", "skipped"))

            if not briefing:
                self.storage.save_agent_event(run_id, "Editor", "skipped", "Editor returned no briefing.", {})
                return None, "skipped", ""

            briefing_str = json.dumps(briefing, sort_keys=True)
            self.storage.save_agent_event(
                run_id, "Editor", "generated",
                f"Executive briefing generated ({status}).",
                {"status": status, "headline": briefing.get("headline", "")},
            )
            return briefing_str, status, ""

        except Exception as exc:  # noqa: BLE001 - Editor failure must never block the run
            err = f"{type(exc).__name__}: {exc}"
            self.storage.save_agent_event(run_id, "Editor", "failed", f"Unexpected error: {err}", {"error": err})
            return None, "failed", err

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
    # When the Critic is enabled and has not yet reviewed, request a critique
    # before finalizing. This makes the mock brain exercise the full four-agent
    # loop in tests when enable_critic=true.
    if state.get("critic_rounds", 0) == 0 and state.get("enable_critic_mock"):
        return AgentDecision("Analyst is done; let Critic review before shipping.", "critique_digest", reason="Critic has not reviewed yet.", params={})
    return AgentDecision("Enough analysis exists to publish.", "finalize_digest", reason="Ranked signals are available.", params={})


def _article_from_state_dict(item: dict[str, Any]) -> Article:
    """Rehydrate an Article from the dict shape that travels over worker stdout."""

    return Article(
        id=str(item.get("id", "")),
        source=str(item.get("source", "")),
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        published_at=str(item.get("published_at", "")),
        body=str(item.get("body", "")),
        fetched_at=str(item.get("fetched_at", "")),
        raw=dict(item.get("raw") or {}),
    )


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
    # Phase 2: pass the artifact through the worker boundary if present. A dict
    # stays a dict; anything else (including None) becomes None so the storage
    # layer can write null rather than serialize garbage.
    artifact = item.get("analyst_artifact")
    if not isinstance(artifact, dict):
        artifact = None
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
        analyst_artifact=artifact,
        # Phase 3: review status fields — default to safe values when missing from
        # older worker output so deserialization never fails on a partial result.
        analyst_status=str(item.get("analyst_status") or "pending"),
        analyst_error_type=item.get("analyst_error_type") or None,
        analyst_error_message=item.get("analyst_error_message") or None,
        analyst_attempt_count=int(item.get("analyst_attempt_count") or 0),
        analyst_last_attempt_at=item.get("analyst_last_attempt_at") or None,
    )
