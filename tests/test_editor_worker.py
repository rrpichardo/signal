from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any

from signal_stream.editor_tools import (
    EDITOR_BRIEFING_SCHEMA,
    _coverage,
    generate_briefing_from_artifacts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    id: str = "sig_001",
    title: str = "Test Signal",
    score: int = 80,
    short_summary: str = "A brief summary.",
    expanded_summary: str = "A longer expanded summary of the signal.",
    analyst_artifact_json: Any = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "score": score,
        "short_summary": short_summary,
        "expanded_summary": expanded_summary,
        "source": "test-source",
        "published_at": "2026-05-13T00:00:00Z",
        "analyst_artifact_json": analyst_artifact_json,
    }


def _rich_artifact(mechanism: str = "A detailed causal mechanism explaining exactly what happened and why.") -> dict[str, Any]:
    return {
        "mechanism": mechanism,
        "key_actors": [{"name": "Anthropic", "role": "developer"}],
        "affected_parties": ["enterprise AI buyers"],
        "confidence": "high",
        "confidence_reason": "multiple independent sources corroborate",
        "_meta": {"was_truncated": False, "chars_total": 5000, "chars_sent": 5000},
    }


class _FakeBrain:
    """Fake BrainClient that returns a valid briefing dict."""

    def __init__(self, response: dict[str, Any] | None = None):
        self._response = response or {
            "headline": "AI shifts accelerate across enterprise stacks.",
            "briefing_paragraphs": ["Paragraph one.", "Paragraph two."],
            "key_themes": [{"label": "Platform moves", "signal_ids": ["sig_001"], "summary": "Big changes."}],
            "watch_items": ["Watch OpenAI pricing."],
            "cross_signal_narrative": "The common thread is infrastructure consolidation.",
        }
        self.last_error: str | None = None
        self.calls: list[dict[str, Any]] = []

    def chat_json(
        self,
        system: str,
        user: str,
        schema: Any = None,
        *,
        temperature: float = 0.0,
        required_fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        self.calls.append({"system": system, "user": user})
        return self._response


class _FailingBrain:
    """Fake BrainClient that always returns None (simulates no API key)."""

    def __init__(self) -> None:
        self.last_error = "GROQ_API_KEY not set."

    def chat_json(self, *args: Any, **kwargs: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Unit tests for editor_tools.py
# ---------------------------------------------------------------------------

class TestEditorToolsCoverage(unittest.TestCase):
    def test_all_missing_artifacts(self) -> None:
        signals = [_make_signal(id=f"s{i}") for i in range(3)]
        counts, any_truncated = _coverage(signals)
        self.assertEqual(counts["missing"], 3)
        self.assertEqual(counts["with_artifact"], 0)
        self.assertFalse(any_truncated)

    def test_rich_artifact_counted(self) -> None:
        signals = [_make_signal(analyst_artifact_json=_rich_artifact())]
        counts, _ = _coverage(signals)
        self.assertEqual(counts["with_artifact"], 1)
        self.assertEqual(counts["missing"], 0)

    def test_thin_artifact_counted_separately(self) -> None:
        # mechanism under 40 chars → thin
        signals = [_make_signal(analyst_artifact_json=_rich_artifact(mechanism="Too short."))]
        counts, _ = _coverage(signals)
        self.assertEqual(counts["thin"], 1)
        self.assertEqual(counts["with_artifact"], 0)

    def test_truncated_artifact_sets_flag(self) -> None:
        artifact = _rich_artifact()
        artifact["_meta"]["was_truncated"] = True
        signals = [_make_signal(analyst_artifact_json=artifact)]
        _, any_truncated = _coverage(signals)
        self.assertTrue(any_truncated)

    def test_string_artifact_json_parsed(self) -> None:
        # Artifacts stored as JSON strings (DB round-trip) must still be counted.
        artifact = _rich_artifact()
        signals = [_make_signal(analyst_artifact_json=json.dumps(artifact))]
        counts, _ = _coverage(signals)
        self.assertEqual(counts["with_artifact"], 1)


class TestGenerateBriefingFromArtifacts(unittest.TestCase):
    def _run_context(self) -> dict[str, Any]:
        return {"organization": "Test Co", "audience": "AI analysts", "priorities": []}

    def test_produces_briefing_with_valid_brain(self) -> None:
        signals = [_make_signal(id=f"sig_{i:03d}") for i in range(3)]
        brain = _FakeBrain()
        result = generate_briefing_from_artifacts(signals, brain, "system prompt", self._run_context())
        self.assertIn("headline", result)
        self.assertIn("briefing_paragraphs", result)
        self.assertIn("cross_signal_narrative", result)
        self.assertIn("artifact_coverage", result)
        self.assertIn("generated_at", result)

    def test_source_signal_ids_populated(self) -> None:
        signals = [_make_signal(id=f"sig_{i:03d}") for i in range(5)]
        brain = _FakeBrain()
        result = generate_briefing_from_artifacts(signals, brain, "prompt", self._run_context())
        self.assertEqual(result["source_signal_ids"], [f"sig_{i:03d}" for i in range(5)])

    def test_input_artifact_count_matches(self) -> None:
        signals = [_make_signal() for _ in range(7)]
        brain = _FakeBrain()
        result = generate_briefing_from_artifacts(signals, brain, "prompt", self._run_context())
        self.assertEqual(result["input_artifact_count"], 7)

    def test_raises_on_brain_failure(self) -> None:
        signals = [_make_signal()]
        brain = _FailingBrain()
        with self.assertRaises(RuntimeError):
            generate_briefing_from_artifacts(signals, brain, "prompt", self._run_context())

    def test_no_article_text_in_groq_payload(self) -> None:
        # Signals must not carry raw article body text to Groq.
        signals = [_make_signal(short_summary="Summary only.")]
        brain = _FakeBrain()
        generate_briefing_from_artifacts(signals, brain, "prompt", self._run_context())
        user_payload = json.loads(brain.calls[0]["user"])
        for block in user_payload.get("signals", []):
            # "body" is an Article field, not a signal field. Confirm it's absent.
            self.assertNotIn("body", block)

    def test_artifact_coverage_reflects_missing(self) -> None:
        signals = [
            _make_signal(id="a", analyst_artifact_json=_rich_artifact()),  # with_artifact
            _make_signal(id="b"),  # missing
            _make_signal(id="c", analyst_artifact_json=_rich_artifact(mechanism="Short")),  # thin
        ]
        brain = _FakeBrain()
        result = generate_briefing_from_artifacts(signals, brain, "prompt", self._run_context())
        cov = result["artifact_coverage"]
        self.assertEqual(cov["with_artifact"], 1)
        self.assertEqual(cov["missing"], 1)
        self.assertEqual(cov["thin"], 1)


# ---------------------------------------------------------------------------
# Worker handler unit tests (test the generate_briefing task routing)
# ---------------------------------------------------------------------------

class TestEditorWorkerHandler(unittest.TestCase):
    """Test the worker's handle_task routing for the editor agent, isolated from subprocess."""

    def _handle(self, payload: dict[str, Any], prompts: dict[str, str] | None = None) -> dict[str, Any]:
        from signal_stream.worker import handle_task

        # Minimal fake config with the fields BrainClient reads.
        class FakeBrain:
            model = "fake-model"
            timeout_seconds = 10

        class FakeAgentConfig:
            scout_mode = "code"
            analyst_mode = "code"

        class FakeConfig:
            brain = FakeBrain()
            agent = FakeAgentConfig()

        # Fake storage — editor path doesn't use storage in Phase 3.
        storage = None

        used_prompts = prompts or {}
        scoring_rubric: dict[str, Any] = {}
        behavior: dict[str, Any] = {}

        task = {"task_id": "t_001", "type": "generate_briefing", "payload": payload}
        return handle_task("editor", FakeConfig(), storage, used_prompts, scoring_rubric, behavior, task)

    def test_zero_signals_returns_skipped(self) -> None:
        result = self._handle({"signals": [], "run_context": {}})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["briefing_status"], "skipped")
        self.assertIsNone(result["data"]["briefing"])

    def test_unknown_task_type_returns_error(self) -> None:
        from signal_stream.worker import handle_task

        class FakeBrain:
            model = "x"
            timeout_seconds = 5

        class FakeAgentConfig:
            scout_mode = "code"
            analyst_mode = "code"

        class FakeConfig:
            brain = FakeBrain()
            agent = FakeAgentConfig()

        task = {"task_id": "t_002", "type": "unknown_type", "payload": {}}
        result = handle_task("editor", FakeConfig(), None, {}, {}, {}, task)
        self.assertEqual(result["status"], "error")


# ---------------------------------------------------------------------------
# Integration tests using SignalAgentRuntime
# ---------------------------------------------------------------------------

def _make_config_text(tmp_path: Path, sample_path: Path, enable_critic: bool = False) -> str:
    return textwrap.dedent(
        f"""
        [profile]
        name = "Signal Stream Editor Test"
        organization = "Signal Stream"
        audience = "AI test reader"
        mission = "Find high-signal AI/tech news."
        competitors = ["OpenAI", "Anthropic"]
        markets = ["AI", "agents"]

        [storage]
        path = "{tmp_path / 'signals_editor.db'}"

        [delivery]
        output_dir = "{tmp_path}"
        digest_limit = 5
        critical_threshold = 82
        similarity_threshold = 0.48

        [agent]
        max_iterations = 4
        dashboard_port = 8880
        worker_timeout_seconds = 30
        require_brain = false
        allow_mock_brain = true
        enable_critic = {"true" if enable_critic else "false"}

        [brain]
        model = "meta-llama/llama-4-scout-17b-16e-instruct"
        timeout_seconds = 10

        [[priorities]]
        name = "AI platform shifts"
        description = "Model and platform moves."
        weight = 2.2
        keywords = ["OpenAI", "Anthropic", "agents", "model"]

        [[sources]]
        name = "Signal Stream AI Sample Wire"
        kind = "sample"
        group = "sample"
        path = "{sample_path}"
        limit = 20
        enabled = true
        """
    ).strip()


class TestEditorIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[1]
        self.sample_path = self.repo / "data" / "ai_sample_articles.json"

    def test_briefing_failure_does_not_block_run_completion(self) -> None:
        """Editor failing (no GROQ_API_KEY in test env) must not break the run."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "editor_fail_test.toml"
            config_path.write_text(_make_config_text(tmp_path, self.sample_path), encoding="utf-8")
            config = load_config(config_path)

            result = SignalAgentRuntime(config, config_path=str(config_path)).run()
            self.assertTrue(Path(result["output_path"]).exists())

            storage = SignalStorage(config.storage_path)
            run = storage.latest_agent_run()
            # Run must complete — Editor failure never blocks this.
            self.assertIsNotNone(run)
            self.assertIn(run["status"], {"complete", "interrupted"})

    def test_cursor_advances_even_when_briefing_fails(self) -> None:
        """The run cursor (latest complete run) advances regardless of Editor outcome."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cursor_test.toml"
            config_path.write_text(_make_config_text(tmp_path, self.sample_path), encoding="utf-8")
            config = load_config(config_path)

            SignalAgentRuntime(config, config_path=str(config_path)).run()

            storage = SignalStorage(config.storage_path)
            # A complete run must exist for the cursor to advance.
            cursor = storage.latest_complete_agent_run_started_at()
            self.assertIsNotNone(cursor)

    def test_editor_events_logged_after_analyst(self) -> None:
        """Editor activity events must appear in the timeline and come after Analyst events."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "editor_events_test.toml"
            config_path.write_text(_make_config_text(tmp_path, self.sample_path), encoding="utf-8")
            config = load_config(config_path)

            SignalAgentRuntime(config, config_path=str(config_path)).run()

            storage = SignalStorage(config.storage_path)
            run = storage.latest_agent_run()
            events = storage.agent_events(run["id"])
            agents = [e["agent"] for e in events]

            # Editor must appear in the timeline.
            self.assertIn("Editor", agents)

            # Every Editor event must come after the last Analyst event.
            analyst_indices = [i for i, a in enumerate(agents) if a == "Analyst"]
            editor_indices = [i for i, a in enumerate(agents) if a == "Editor"]
            if analyst_indices and editor_indices:
                self.assertGreater(min(editor_indices), max(analyst_indices))

    def test_briefing_status_written_to_storage(self) -> None:
        """briefing_status must be persisted in agent_runs after a complete run."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "briefing_status_test.toml"
            config_path.write_text(_make_config_text(tmp_path, self.sample_path), encoding="utf-8")
            config = load_config(config_path)

            SignalAgentRuntime(config, config_path=str(config_path)).run()

            storage = SignalStorage(config.storage_path)
            run = storage.latest_agent_run()
            self.assertIsNotNone(run)

            # get_run_briefing must return a status (either failed or skipped
            # in the test environment since no API key is available).
            briefing_data = storage.get_run_briefing(run["id"])
            self.assertIsNotNone(briefing_data)
            self.assertIn(briefing_data["briefing_status"], {"failed", "skipped", "generated", "partial"})

    def test_editor_runs_after_critic_approval(self) -> None:
        """With enable_critic=true, Editor events must appear after Critic events."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "editor_after_critic_test.toml"
            config_path.write_text(
                _make_config_text(tmp_path, self.sample_path, enable_critic=True),
                encoding="utf-8",
            )
            config = load_config(config_path)

            SignalAgentRuntime(config, config_path=str(config_path)).run()

            storage = SignalStorage(config.storage_path)
            run = storage.latest_agent_run()
            events = storage.agent_events(run["id"])
            agents = [e["agent"] for e in events]

            # If both Critic and Editor ran, Editor must follow Critic.
            critic_indices = [i for i, a in enumerate(agents) if a == "Critic"]
            editor_indices = [i for i, a in enumerate(agents) if a == "Editor"]
            if critic_indices and editor_indices:
                self.assertGreater(min(editor_indices), max(critic_indices))


# ---------------------------------------------------------------------------
# Storage unit tests for new columns / get_run_briefing
# ---------------------------------------------------------------------------

class TestStorageBriefingColumns(unittest.TestCase):
    def test_get_run_briefing_returns_none_for_missing_run(self) -> None:
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            storage = SignalStorage(Path(tmp) / "test.db")
            storage.init()
            result = storage.get_run_briefing("nonexistent_run_id")
            self.assertIsNone(result)

    def test_briefing_round_trips_through_save_run_atomic(self) -> None:
        from signal_stream.models import Article, Signal, utc_now_iso
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            storage = SignalStorage(Path(tmp) / "rt_test.db")
            storage.init()
            run_id = storage.start_agent_run("test briefing round-trip")

            briefing = {
                "headline": "Test headline.",
                "briefing_paragraphs": ["Para one."],
                "key_themes": [],
                "watch_items": [],
                "cross_signal_narrative": "Narrative here.",
                "source_signal_ids": [],
                "input_artifact_count": 0,
                "artifact_coverage": {"with_artifact": 0, "missing": 0, "thin": 0},
                "any_artifact_truncated": False,
                "generated_at": "2026-05-13T00:00:00Z",
            }
            import json
            briefing_str = json.dumps(briefing, sort_keys=True)

            storage.save_run_atomic(
                articles=[],
                signals=[],
                cluster_count=0,
                output_path="/tmp/digest.md",
                started_at=utc_now_iso(),
                run_id=run_id,
                summary={},
                briefing_json=briefing_str,
                briefing_status="generated",
                briefing_error="",
            )

            result = storage.get_run_briefing(run_id)
            self.assertIsNotNone(result)
            self.assertEqual(result["briefing_status"], "generated")
            self.assertEqual(result["briefing"]["headline"], "Test headline.")

    def test_old_db_without_briefing_columns_does_not_crash(self) -> None:
        """init() must add new columns to an existing DB that doesn't have them."""
        import sqlite3
        from signal_stream.storage import SignalStorage

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "old.db"
            # Create a minimal agent_runs table without briefing columns.
            conn = sqlite3.connect(db_path)
            conn.execute(
                "create table agent_runs (id text primary key, goal text not null, "
                "status text not null, started_at text not null, completed_at text, summary_json text)"
            )
            conn.commit()
            conn.close()

            storage = SignalStorage(db_path)
            # init() must run without error and add the missing columns.
            storage.init()

            # Verify columns now exist.
            conn = sqlite3.connect(db_path)
            cols = {row[1] for row in conn.execute("pragma table_info(agent_runs)").fetchall()}
            conn.close()
            self.assertIn("briefing_json", cols)
            self.assertIn("briefing_status", cols)
            self.assertIn("briefing_error", cols)


if __name__ == "__main__":
    unittest.main()
