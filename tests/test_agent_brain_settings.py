from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from signal_stream.analysis_tools import _apply_analyst_mode, _base_score_card, _bounded_model_score, _looks_like_lazy_summary
from signal_stream.config import load_config
from signal_stream.dashboard import dashboard_settings, save_dashboard_settings
from signal_stream.models import Article, BrainConfig, Priority, Signal, SignalConfig
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
            self.assertEqual(brain["behavior"]["analyst_review_limit"], 8)
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

[brain]
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

    def test_hybrid_analyst_uses_model_owned_short_summary(self) -> None:
        class FakeBrain:
            def __init__(self, config):  # noqa: ANN001
                pass

            def available(self) -> bool:
                return True

            def chat_json(self, system, user, schema):  # noqa: ANN001
                self.last_payload = user
                return {
                    "signals": [
                        {
                            "id": "sig_1",
                            "score": 70,
                            "short_summary": "Model-written card paragraph for the reader.",
                            "expanded_summary": "Model-written expanded summary for the detail view.",
                            "why_it_matters": "Model-written strategic explanation.",
                            "entities": {"companies": ["OpenAI"]},
                        }
                    ]
                }

        config = SignalConfig(
            name="Test",
            organization="Test",
            audience="Reader",
            mission="Test",
            competitors=[],
            markets=[],
            priorities=[Priority("AI")],
            sources=[],
            storage_path=":memory:",
            output_dir=".",
            brain=BrainConfig(),
        )
        signal = Signal(
            id="sig_1",
            cluster_id="cluster",
            article_id="article",
            title="Title",
            url="",
            source="Source",
            published_at="",
            score=60,
            urgency="medium",
            event_type="platform_shift",
            summary="Code fallback.",
            why_it_matters="Code fallback why.",
            next_steps=[],
            matched_priorities=[],
            entities={},
            short_summary="Code fallback.",
            expanded_summary="Code fallback expanded.",
        )

        import signal_stream.analysis_tools as analysis_tools

        original = analysis_tools.BrainClient
        analysis_tools.BrainClient = FakeBrain
        try:
            updated = _apply_analyst_mode(
                [signal],
                config,
                "hybrid",
                "prompt",
                {"model_score_adjustment_limit": 20, "summary_mode": "short_expanded", "entity_extraction": "hybrid", "analyst_full_review": True},
                {"sig_1": {"article_text": "Full article text for the model."}},
            )[0]
        finally:
            analysis_tools.BrainClient = original

        self.assertEqual(updated.short_summary, "Model-written card paragraph for the reader.")
        self.assertEqual(updated.summary, updated.short_summary)
        self.assertEqual(updated.expanded_summary, "Model-written expanded summary for the detail view.")

    def test_lazy_model_summary_gets_repaired_by_second_model_call(self) -> None:
        class FakeBrain:
            def __init__(self, config):  # noqa: ANN001
                self.calls = 0

            def available(self) -> bool:
                return True

            def chat_json(self, system, user, schema):  # noqa: ANN001
                self.calls += 1
                if self.calls == 1:
                    return {
                        "signals": [
                            {
                                "id": "sig_1",
                                "score": 70,
                                "short_summary": "Big AI Launch: Big AI Launch details copied from the title.",
                                "expanded_summary": "Copied expanded summary.",
                                "why_it_matters": "Model why.",
                                "entities": {},
                            }
                        ]
                    }
                return {
                    "short_summary": "The launch gives developers a clearer way to evaluate agent workflows before production.",
                    "expanded_summary": "The launch gives developers a clearer way to evaluate agent workflows before production. It matters because teams can compare reliability and cost before committing.",
                }

        config = SignalConfig(
            name="Test",
            organization="Test",
            audience="Reader",
            mission="Test",
            competitors=[],
            markets=[],
            priorities=[Priority("AI")],
            sources=[],
            storage_path=":memory:",
            output_dir=".",
            brain=BrainConfig(),
        )
        signal = Signal(
            id="sig_1",
            cluster_id="cluster",
            article_id="article",
            title="Big AI Launch",
            url="",
            source="Source",
            published_at="",
            score=60,
            urgency="medium",
            event_type="platform_shift",
            summary="Code fallback.",
            why_it_matters="Code fallback why.",
            next_steps=[],
            matched_priorities=[],
            entities={},
            short_summary="Code fallback.",
            expanded_summary="Code fallback expanded.",
        )

        import signal_stream.analysis_tools as analysis_tools

        original = analysis_tools.BrainClient
        analysis_tools.BrainClient = FakeBrain
        try:
            updated = _apply_analyst_mode(
                [signal],
                config,
                "hybrid",
                "prompt",
                {"model_score_adjustment_limit": 20, "summary_mode": "short_expanded", "entity_extraction": "hybrid", "analyst_full_review": True},
                {"sig_1": {"article_text": "The company launched a new agent evaluation tool for development teams."}},
            )[0]
        finally:
            analysis_tools.BrainClient = original

        self.assertEqual(updated.short_summary, "The launch gives developers a clearer way to evaluate agent workflows before production.")

    def test_missing_batch_review_still_repairs_top_candidate_summary(self) -> None:
        class FakeBrain:
            def __init__(self, config):  # noqa: ANN001
                pass

            def available(self) -> bool:
                return True

            def chat_json(self, system, user, schema):  # noqa: ANN001
                if "review_ranked_signals" in user:
                    return None
                return {
                    "short_summary": "The story gives teams a practical way to simplify reporting logic.",
                    "expanded_summary": "The story gives teams a practical way to simplify reporting logic. It matters because repeated formatting rules can become maintenance risk.",
                }

        config = SignalConfig(
            name="Test",
            organization="Test",
            audience="Reader",
            mission="Test",
            competitors=[],
            markets=[],
            priorities=[Priority("AI")],
            sources=[],
            storage_path=":memory:",
            output_dir=".",
            brain=BrainConfig(),
        )
        signal = Signal(
            id="sig_1",
            cluster_id="cluster",
            article_id="article",
            title="Long copied title",
            url="",
            source="Source",
            published_at="",
            score=60,
            urgency="medium",
            event_type="builder_tactic",
            summary="Long copied title with article opening copied here.",
            why_it_matters="Code fallback why.",
            next_steps=[],
            matched_priorities=[],
            entities={},
            short_summary="Long copied title with article opening copied here.",
            expanded_summary="Fallback expanded.",
        )

        import signal_stream.analysis_tools as analysis_tools

        original = analysis_tools.BrainClient
        analysis_tools.BrainClient = FakeBrain
        try:
            updated = _apply_analyst_mode(
                [signal],
                config,
                "hybrid",
                "prompt",
                {"model_score_adjustment_limit": 20, "summary_mode": "short_expanded", "entity_extraction": "hybrid", "analyst_review_limit": 30},
                {"sig_1": {"article_text": "Article about simplifying DAX reporting logic."}},
            )[0]
        finally:
            analysis_tools.BrainClient = original

        self.assertEqual(updated.short_summary, "The story gives teams a practical way to simplify reporting logic.")

    def test_lazy_title_echo_is_not_accepted_as_model_summary(self) -> None:
        self.assertTrue(_looks_like_lazy_summary("Big AI Launch: Big AI Launch details here.", "Big AI Launch"))
        self.assertFalse(_looks_like_lazy_summary("The release gives developers a cheaper way to run agent workflows in production.", "Big AI Launch"))


if __name__ == "__main__":
    unittest.main()
