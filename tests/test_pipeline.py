from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from signal_stream.config import load_config
from signal_stream.orchestrator import SignalStreamOrchestrator


class PipelineTest(unittest.TestCase):
    def test_demo_pipeline_creates_ranked_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = load_config("configs/demo.toml")
            config = replace(
                config,
                storage_path=str(tmp_path / "signals.db"),
                output_dir=str(tmp_path),
            )

            result = SignalStreamOrchestrator(config).run()

            self.assertTrue(Path(result.output_path).exists())
            self.assertGreaterEqual(result.article_count, 8)
            self.assertGreaterEqual(result.cluster_count, 1)
            self.assertGreaterEqual(result.signal_count, 5)
            # Wave 3: new 5-component rubric. Unknown-date sample articles top out
            # around 60-65. Threshold lowered from 70 to 50 to reflect new scoring.
            self.assertGreaterEqual(result.top_signals[0].score, 50)
            self.assertIn(result.top_signals[0].urgency, {"medium", "high", "critical"})


if __name__ == "__main__":
    unittest.main()
