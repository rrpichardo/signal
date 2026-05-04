from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from signal_stream.analysis_tools import _base_score_card, _bounded_model_score
from signal_stream.config import load_config
from signal_stream.dashboard import dashboard_settings, save_dashboard_settings
from signal_stream.models import Article
from signal_stream.prompt_loader import load_behavior_settings, load_brain_file, save_brain_file
from signal_stream.source_tools import enrich_articles_with_model


class AgentBrainSettingsTest(unittest.TestCase):
    def test_brain_file_loads_and_saves_non_technical_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_brain.toml"

            save_brain_file(
                path,
                {
                    "behavior": {"relevance_policy": "soft_keep", "model_score_adjustment_limit": 20},
                    "prompts": {"scout": "Scout test prompt"},
                    "scoring": {"max_points": {"repeat_penalty": 30}, "low_value_phrases": ["promo"]},
                },
            )

            brain = load_brain_file(path)
            self.assertEqual(brain["behavior"]["relevance_policy"], "soft_keep")
            self.assertEqual(brain["behavior"]["model_score_adjustment_limit"], 20)
            self.assertEqual(brain["prompts"]["scout"], "Scout test prompt")
            self.assertEqual(brain["scoring"]["max_points"]["repeat_penalty"], 30)
            self.assertIn("promo", brain["scoring"]["low_value_phrases"])

    def test_scout_soft_keep_keeps_model_labeled_drop_items(self) -> None:
        class FakeLLM:
            def available(self) -> bool:
                return True

            def chat_json(self, system, user, schema):  # noqa: ANN001
                return {
                    "items": [
                        {
                            "id": "article-1",
                            "relevance_label": "drop",
                            "topic": "generic promotion",
                            "signal_type": "general_signal",
                            "usefulness": "low",
                            "scout_note": "Looks weak, but soft keep should preserve it.",
                        }
                    ]
                }

        articles = [{"id": "article-1", "title": "Promo", "source": "Test", "body": "Register now.", "raw": {}}]
        enriched = enrich_articles_with_model(FakeLLM(), "prompt", articles, relevance_policy="soft_keep")

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["raw"]["scout_relevance_label"], "drop")
        self.assertIn("soft keep", enriched[0]["raw"]["scout_note"])

    def test_model_score_adjustment_is_capped_in_hybrid_mode(self) -> None:
        self.assertEqual(_bounded_model_score(60, 95, "hybrid", 20), 80)
        self.assertEqual(_bounded_model_score(60, 10, "hybrid", 20), 40)
        self.assertEqual(_bounded_model_score(60, 95, "model", 20), 95)

    def test_strong_repeat_penalty_is_larger_but_does_not_hide_story(self) -> None:
        class Draft:
            matched_priorities = []
            entities = {"competitors": [], "organizations": []}
            cluster = type("Cluster", (), {"articles": [object()]})()

        article = Article.from_fields(source="Test", title="Repeat story", body="AI platform news")
        score, breakdown = _base_score_card(
            article,
            Draft(),
            memory_hits=[{"id": "old"}],
            event_type="platform_shift",
            scoring_rubric=load_brain_file(None)["scoring"],
            behavior={"repeat_penalty_strength": "strong"},
        )

        repeat_line = next(item for item in breakdown if item["name"] == "Repeat penalty")
        self.assertEqual(repeat_line["points"], -12)
        self.assertGreaterEqual(score, 0)

    def test_dashboard_settings_helpers_read_and_write_brain_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brain_path = tmp_path / "agent_brain.toml"
            config_path = tmp_path / "config.toml"
            config_path.write_text(
                f"""
[profile]
name = "Test"
organization = "Test"
audience = "Reader"
mission = "Test"
competitors = []
markets = []

[storage]
path = "{tmp_path / 'signals.db'}"

[delivery]
output_dir = "{tmp_path}"

[agent]
brain_file = "{brain_path}"

[ollama]
enabled = false
""".strip(),
                encoding="utf-8",
            )
            config = load_config(config_path)
            save_brain_file(brain_path, {})

            settings = dashboard_settings(config)
            settings["behavior"]["relevance_policy"] = "hard_drop"
            saved = save_dashboard_settings(config, settings)

            self.assertEqual(saved["behavior"]["relevance_policy"], "hard_drop")
            self.assertEqual(load_behavior_settings(brain_path)["relevance_policy"], "hard_drop")


if __name__ == "__main__":
    unittest.main()
