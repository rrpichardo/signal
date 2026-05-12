from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from signal_stream.agent_runtime import SignalAgentRuntime
from signal_stream.config import load_config
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


if __name__ == "__main__":
    unittest.main()
