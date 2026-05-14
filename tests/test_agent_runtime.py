from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from signal_stream.agent_runtime import SignalAgentRuntime
from signal_stream.config import load_config
from signal_stream.editor_tools import evaluate_fallback_eligibility, run_fulltext_fallback
from signal_stream.models import Signal, stable_id, utc_now_iso
from signal_stream.storage import SignalStorage


class AgentRuntimeTest(unittest.TestCase):
    def test_mock_brain_runtime_uses_worker_processes_and_memory(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        sample_path = repo / "data" / "ai_sample_articles.json"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "agent_test.toml"
            config_path.write_text(
                textwrap.dedent(
                    f"""
                    [profile]
                    name = "Signal Stream Test"
                    organization = "Signal Stream"
                    audience = "AI test reader"
                    mission = "Find high-signal AI/tech news."
                    competitors = ["OpenAI", "Anthropic", "NVIDIA"]
                    markets = ["AI", "agents", "chips", "enterprise software"]

                    [storage]
                    path = "{tmp_path / 'signals.db'}"

                    [delivery]
                    output_dir = "{tmp_path}"
                    digest_limit = 5
                    critical_threshold = 82
                    similarity_threshold = 0.48

                    [agent]
                    max_iterations = 4
                    dashboard_port = 8877
                    worker_timeout_seconds = 30
                    require_brain = false
                    allow_mock_brain = true

                    [brain]
                    model = "meta-llama/llama-4-scout-17b-16e-instruct"
                    timeout_seconds = 10

                    [[priorities]]
                    name = "AI platform shifts"
                    description = "Model, platform, and agent moves."
                    weight = 2.2
                    keywords = ["OpenAI", "Anthropic", "agents", "model", "platform", "enterprise"]

                    [[priorities]]
                    name = "Infrastructure and chips"
                    description = "GPU and compute signals."
                    weight = 1.7
                    keywords = ["NVIDIA", "GPU", "inference", "compute", "cloud"]

                    [[sources]]
                    name = "Signal Stream AI Sample Wire"
                    kind = "sample"
                    group = "sample"
                    path = "{sample_path}"
                    limit = 20
                    enabled = true
                    """
                ).strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)

            result = SignalAgentRuntime(config, config_path=str(config_path)).run()

            self.assertTrue(Path(result["output_path"]).exists())
            self.assertGreaterEqual(result["articles"], 5)
            self.assertGreaterEqual(result["signals"], 3)

            storage = SignalStorage(config.storage_path)
            run = storage.latest_agent_run()
            self.assertIsNotNone(run)
            events = storage.agent_events(run["id"])
            tools = storage.tool_calls(run["id"])
            memory = storage.list_memory()

            self.assertTrue(any(event["agent"] == "Scout" for event in events))
            self.assertTrue(any(event["agent"] == "Analyst" for event in events))
            self.assertTrue(any(call["agent"] == "Scout" for call in tools))
            self.assertTrue(any(call["agent"] == "Analyst" for call in tools))
            self.assertGreaterEqual(len(memory), 3)


class EditorFallbackCursorRegressionTest(unittest.TestCase):
    """Regression tests: fallback failures must never block run completion.

    Phase 4 contract: run_fulltext_fallback is best-effort. Every fetch or
    Groq call failure is caught internally. The calling code (Phase 3 Editor)
    therefore cannot have its run stalled by any fallback error path.
    """

    def _make_signal(self, signal_id: str, score: int = 60) -> Signal:
        return Signal(
            id=signal_id,
            cluster_id=f"cluster-{signal_id}",
            article_id=f"art-{signal_id}",
            title=f"Article {signal_id}",
            url="https://example.com/article",
            source="Test",
            published_at="2025-01-01T00:00:00Z",
            score=score,
            urgency="medium",
            event_type="general_signal",
            summary="Test.",
            why_it_matters="",
            next_steps=[],
            matched_priorities=[],
            entities={},
        )

    def test_all_fallback_fetches_fail_run_completes(self) -> None:
        """run_fulltext_fallback returns without exception when all fetches yield empty body."""
        with tempfile.TemporaryDirectory() as tmp:
            storage = SignalStorage(Path(tmp) / "test.db")
            storage.init()
            signals = [self._make_signal(f"s{i}") for i in range(3)]
            artifacts: dict = {}

            from unittest.mock import MagicMock
            brain = MagicMock()
            brain.last_error = ""

            with patch("signal_stream.editor_tools.fetch_full_article_page", return_value=("", None)):
                # Must complete without raising — this is what lets the run cursor advance
                result = run_fulltext_fallback(
                    signals=signals,
                    artifacts=artifacts,
                    brain=brain,
                    storage=storage,
                    cap=3,
                    analyst_prompt="test",
                )

            # All failed → all None, no Groq calls attempted
            for sig in signals:
                self.assertIsNone(result[sig.id])
            brain.chat_json.assert_not_called()

    def test_evaluate_fallback_returns_subset_within_cap(self) -> None:
        """evaluate_fallback_eligibility never returns more than cap, regardless of eligible count."""
        signals = [self._make_signal(f"s{i}", score=60 - i) for i in range(10)]
        artifacts: dict = {}  # all missing → all eligible

        for cap in [0, 1, 3, 5]:
            result = evaluate_fallback_eligibility(signals, artifacts, cap=cap)
            self.assertLessEqual(len(result), cap, f"Cap={cap} produced {len(result)} results")


if __name__ == "__main__":
    unittest.main()
