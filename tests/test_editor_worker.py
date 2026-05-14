"""Tests for the Phase 3 Editor worker: briefing reducer + worker handler + storage."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_signal(
    id_: str = "sig1",
    title: str = "Test Signal",
    score: int = 70,
    short_summary: str = "Short summary here.",
    expanded_summary: str = "Expanded summary here.",
    mechanism: str = "A" * 50,  # long enough to pass _is_thin check
    confidence: str = "high",
    was_truncated: bool = False,
) -> Any:
    """Return a minimal mock Signal with an attached artifact."""
    from signal_stream.models import Signal
    artifact = {
        "mechanism": mechanism,
        "key_actors": [{"name": "Acme Corp", "role": "lead"}],
        "affected_parties": ["enterprise buyers"],
        "confidence": confidence,
        "_meta": {"was_truncated": was_truncated, "chars_total": 5000, "chars_sent": 5000},
    }
    return Signal(
        id=id_,
        cluster_id="cluster1",
        article_id="art1",
        title=title,
        url="https://example.com/test",
        source="Test Source",
        published_at="2024-01-01T00:00:00+00:00",
        score=score,
        urgency="high",
        event_type="product_launch",
        summary=short_summary,
        why_it_matters="",
        next_steps=[],
        matched_priorities=[],
        entities={},
        duplicate_count=0,
        score_breakdown=[],
        short_summary=short_summary,
        expanded_summary=expanded_summary,
        image_url="",
        icon_key="",
        scout_note="",
        relevance_label="",
        analyst_artifact=artifact,
    )


def _make_brain(return_value: dict | None = None, last_error: str = "") -> MagicMock:
    """Return a mock BrainClient whose chat_json() returns `return_value`."""
    brain = MagicMock()
    brain.chat_json.return_value = return_value
    brain.last_error = last_error
    return brain


# ── TestEditorToolsCoverage ────────────────────────────────────────────────────

class TestEditorToolsCoverage(unittest.TestCase):
    """Unit tests for _parse_artifact, _is_thin, and _coverage helpers."""

    def test_parse_artifact_returns_dict_when_present(self):
        from signal_stream.editor_tools import _parse_artifact
        sig = _make_signal(mechanism="A" * 50)
        result = _parse_artifact(sig)
        self.assertIsInstance(result, dict)
        self.assertIn("mechanism", result)

    def test_parse_artifact_returns_none_when_absent(self):
        from signal_stream.editor_tools import _parse_artifact
        sig = _make_signal()
        sig.analyst_artifact = None
        self.assertIsNone(_parse_artifact(sig))

    def test_is_thin_true_for_short_mechanism(self):
        from signal_stream.editor_tools import _is_thin
        self.assertTrue(_is_thin({"mechanism": "too short"}))

    def test_is_thin_false_for_long_mechanism(self):
        from signal_stream.editor_tools import _is_thin
        self.assertFalse(_is_thin({"mechanism": "A" * 50}))

    def test_coverage_counts_correctly(self):
        from signal_stream.editor_tools import _coverage
        sig_good = _make_signal(id_="s1", mechanism="A" * 50)
        sig_thin = _make_signal(id_="s2", mechanism="short")
        sig_none = _make_signal(id_="s3")
        sig_none.analyst_artifact = None
        result = _coverage([sig_good, sig_thin, sig_none])
        cov = result["artifact_coverage"]
        self.assertEqual(cov["with_artifact"], 1)
        self.assertEqual(cov["thin"], 1)
        self.assertEqual(cov["missing"], 1)

    def test_coverage_detects_truncation(self):
        from signal_stream.editor_tools import _coverage
        sig = _make_signal(was_truncated=True)
        result = _coverage([sig])
        self.assertTrue(result["any_artifact_truncated"])


# ── TestGenerateBriefingFromArtifacts ─────────────────────────────────────────

class TestGenerateBriefingFromArtifacts(unittest.TestCase):
    """Tests for the main reducer function."""

    def _valid_groq_response(self) -> dict:
        return {
            "headline": "AI platforms accelerate.",
            "briefing_paragraphs": ["Para one.", "Para two."],
            "key_themes": [{"label": "Infra", "signal_ids": ["sig1"], "summary": "Cloud build-out."}],
            "watch_items": ["Watch for pricing changes."],
            "cross_signal_narrative": "Macro story here.",
        }

    def test_returns_briefing_dict_on_success(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = _make_brain(self._valid_groq_response())
        signals = [_make_signal()]
        result = generate_briefing_from_artifacts(signals, brain, "editor prompt", {})
        self.assertEqual(result["headline"], "AI platforms accelerate.")
        self.assertIn("generated_at", result)
        self.assertIn("source_signal_ids", result)

    def test_raises_when_no_signals(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = _make_brain(self._valid_groq_response())
        with self.assertRaises(RuntimeError):
            generate_briefing_from_artifacts([], brain, "editor prompt", {})

    def test_raises_when_groq_returns_nothing(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = _make_brain(None, last_error="rate limit")
        with self.assertRaises(RuntimeError) as ctx:
            generate_briefing_from_artifacts([_make_signal()], brain, "editor prompt", {})
        self.assertIn("rate limit", str(ctx.exception))

    def test_attaches_provenance_fields(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = _make_brain(self._valid_groq_response())
        result = generate_briefing_from_artifacts([_make_signal(id_="abc")], brain, "", {})
        self.assertEqual(result["source_signal_ids"], ["abc"])
        self.assertEqual(result["input_artifact_count"], 1)
        self.assertIn("artifact_coverage", result)

    def test_partial_status_when_coverage_has_gaps(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts, _coverage
        sig_thin = _make_signal(mechanism="short")
        brain = _make_brain(self._valid_groq_response())
        result = generate_briefing_from_artifacts([sig_thin], brain, "", {})
        cov = result["artifact_coverage"]
        # thin count should be 1 since mechanism is under 40 chars
        self.assertEqual(cov["thin"], 1)

    def test_calls_brain_with_correct_task(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = _make_brain(self._valid_groq_response())
        generate_briefing_from_artifacts([_make_signal()], brain, "my prompt", {"run_id": "r1"})
        call_args = brain.chat_json.call_args
        # system prompt is first positional arg
        self.assertEqual(call_args[0][0], "my prompt")
        payload = json.loads(call_args[0][1])
        self.assertEqual(payload["task"], "generate_executive_briefing")
        self.assertEqual(len(payload["signals"]), 1)


# ── TestEditorWorkerHandler ────────────────────────────────────────────────────

class TestEditorWorkerHandler(unittest.TestCase):
    """Tests for the editor branch in worker.handle_task."""

    def _make_config(self):
        cfg = MagicMock()
        cfg.agent.scout_mode = "code"
        cfg.agent.analyst_mode = "code"
        return cfg

    def _make_signal_dict(self, id_: str = "s1", mechanism: str = "A" * 50) -> dict:
        return {
            "id": id_, "cluster_id": "", "article_id": "", "title": "T", "url": "u",
            "source": "src", "published_at": "", "score": 60, "urgency": "medium",
            "event_type": "pe", "summary": "s", "why_it_matters": "", "next_steps": [],
            "matched_priorities": [], "entities": {}, "duplicate_count": 0,
            "score_breakdown": [], "short_summary": "ss", "expanded_summary": "es",
            "image_url": "", "icon_key": "", "scout_note": "", "relevance_label": "",
            "analyst_artifact": {"mechanism": mechanism, "confidence": "high", "_meta": {"was_truncated": False}},
        }

    def test_skips_when_no_signals(self):
        from signal_stream.worker import handle_task
        storage = MagicMock()
        prompts = {"editor": "Be an editor."}
        task = {"task_id": "t1", "type": "generate_briefing", "payload": {"signals": []}}
        result = handle_task("editor", self._make_config(), storage, prompts, {}, {}, task)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["briefing_status"], "skipped")

    def test_returns_error_for_unknown_task_type(self):
        from signal_stream.worker import handle_task
        task = {"task_id": "t1", "type": "unknown_task", "payload": {}}
        result = handle_task("editor", self._make_config(), MagicMock(), {}, {}, {}, task)
        self.assertEqual(result["status"], "error")


# ── TestEditorIntegration ──────────────────────────────────────────────────────

class TestEditorIntegration(unittest.TestCase):
    """Integration tests that check _call_editor and save_run_atomic together."""

    def _make_runtime(self, db_path: str):
        """Create a SignalAgentRuntime pointed at a temp database."""
        from signal_stream.agent_runtime import SignalAgentRuntime
        from signal_stream.config import load_config

        config = load_config("configs/ai_tech.toml")
        config.storage_path = db_path
        config.agent.allow_mock_brain = True
        config.agent.require_brain = False
        config.agent.enable_critic = False
        runtime = SignalAgentRuntime(config, config_path="configs/ai_tech.toml")
        runtime.storage.init()
        return runtime

    def test_call_editor_returns_skipped_when_no_signals(self):
        from signal_stream.agent_runtime import WorkerClient
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        runtime = self._make_runtime(db_path)
        run_id = runtime.storage.start_agent_run("test goal")
        editor = MagicMock(spec=WorkerClient)
        editor.agent = "editor"
        result = runtime._call_editor(run_id, editor, [])
        self.assertEqual(result, (None, "skipped", ""))

    def test_call_editor_handles_worker_error_gracefully(self):
        from signal_stream.agent_runtime import WorkerClient
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        runtime = self._make_runtime(db_path)
        run_id = runtime.storage.start_agent_run("test goal")
        editor = MagicMock(spec=WorkerClient)
        editor.agent = "editor"
        editor.request.return_value = {"status": "error", "data": {}, "error": "groq timeout", "confidence": 0.0}
        # _call_worker calls editor.request internally but we need to patch storage.save_tool_call too
        runtime.storage.save_tool_call = MagicMock()
        # Patch _call_worker to use the mock directly
        from signal_stream.models import ToolCall, stable_id, utc_now_iso
        with patch.object(runtime, "_call_worker", return_value={"status": "error", "data": {}, "error": "groq timeout"}):
            json_str, status, err = runtime._call_editor(run_id, editor, [{"id": "s1"}])
        self.assertIsNone(json_str)
        self.assertEqual(status, "failed")
        self.assertIn("groq timeout", err)

    def test_call_editor_raises_are_caught(self):
        from signal_stream.agent_runtime import WorkerClient
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        runtime = self._make_runtime(db_path)
        run_id = runtime.storage.start_agent_run("test goal")
        editor = MagicMock(spec=WorkerClient)
        editor.agent = "editor"
        with patch.object(runtime, "_call_worker", side_effect=RuntimeError("network error")):
            json_str, status, err = runtime._call_editor(run_id, editor, [{"id": "s1"}])
        self.assertIsNone(json_str)
        self.assertEqual(status, "failed")
        self.assertIn("network error", err)

    def test_briefing_failure_does_not_block_run_completion(self):
        """A failing Editor must not prevent save_run_atomic from committing."""
        from signal_stream.storage import SignalStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        storage = SignalStorage(db_path)
        storage.init()
        run_id = storage.start_agent_run("test goal")
        # Call save_run_atomic with briefing_status='failed' and null briefing_json
        storage.save_run_atomic(
            articles=[],
            signals=[],
            cluster_count=0,
            output_path="/tmp/test.md",
            started_at="2024-01-01T00:00:00+00:00",
            run_id=run_id,
            summary={"articles": 0, "signals": 0},
            briefing_json=None,
            briefing_status="failed",
            briefing_error="groq timeout",
        )
        # Run should be complete even though briefing failed
        with storage.connect() as conn:
            row = conn.execute("select status, briefing_status from agent_runs where id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["briefing_status"], "failed")

    def test_briefing_json_round_trips_through_get_latest_briefing(self):
        """A generated briefing should be readable back from get_latest_briefing."""
        from signal_stream.storage import SignalStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        storage = SignalStorage(db_path)
        storage.init()
        run_id = storage.start_agent_run("test goal")
        briefing = {
            "headline": "AI shifts.",
            "briefing_paragraphs": ["Para."],
            "key_themes": [],
            "watch_items": [],
            "cross_signal_narrative": "Macro.",
            "source_signal_ids": ["s1"],
            "input_artifact_count": 1,
            "artifact_coverage": {"with_artifact": 1, "missing": 0, "thin": 0},
            "any_artifact_truncated": False,
            "generated_at": "2024-01-01T00:00:00+00:00",
        }
        storage.save_run_atomic(
            articles=[],
            signals=[],
            cluster_count=0,
            output_path="/tmp/test.md",
            started_at="2024-01-01T00:00:00+00:00",
            run_id=run_id,
            summary={},
            briefing_json=json.dumps(briefing),
            briefing_status="generated",
            briefing_error="",
        )
        result = storage.get_latest_briefing()
        self.assertEqual(result["briefing_status"], "generated")
        self.assertEqual(result["briefing"]["headline"], "AI shifts.")


# ── TestStorageBriefingColumns ─────────────────────────────────────────────────

class TestStorageBriefingColumns(unittest.TestCase):
    """Verify the schema has briefing columns and get_latest_briefing handles edge cases."""

    def _make_storage(self) -> Any:
        from signal_stream.storage import SignalStorage
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        storage = SignalStorage(db_path)
        storage.init()
        return storage

    def test_briefing_columns_exist_after_init(self):
        storage = self._make_storage()
        with storage.connect() as conn:
            cols = [row["name"] for row in conn.execute("pragma table_info(agent_runs)").fetchall()]
        self.assertIn("briefing_json", cols)
        self.assertIn("briefing_status", cols)
        self.assertIn("briefing_error", cols)

    def test_get_latest_briefing_returns_skipped_when_no_runs(self):
        storage = self._make_storage()
        result = storage.get_latest_briefing()
        self.assertIsNone(result["briefing"])
        self.assertEqual(result["briefing_status"], "skipped")

    def test_save_run_atomic_accepts_none_briefing(self):
        storage = self._make_storage()
        run_id = storage.start_agent_run("test")
        # Should not raise when briefing kwargs are omitted (defaults to None)
        storage.save_run_atomic(
            articles=[], signals=[], cluster_count=0,
            output_path="/tmp/x.md", started_at="2024-01-01T00:00:00+00:00",
            run_id=run_id, summary={},
        )
        with storage.connect() as conn:
            row = conn.execute("select status from agent_runs where id = ?", (run_id,)).fetchone()
        self.assertEqual(row["status"], "complete")


if __name__ == "__main__":
    unittest.main()
