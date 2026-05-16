"""Signal Stream scoring rubric tests.

Covers: Richard Signal Score V2, 7 priority groups, event-type preservation,
single score-source invariant, and RelevanceAgent deletion.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from signal_stream.analysis_tools import (
    _base_score_card,
    _event_type,
    _match_priorities,
    _score_event_strength,
    _score_priority_match,
    build_drafts_from_insights,
)
from signal_stream.config import load_config
from signal_stream.models import Article, Cluster, ClusterInsight, Priority, SignalDraft, utc_now_iso
from signal_stream.prompt_loader import DEFAULT_SCORING_RUBRIC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(title: str, body: str = "", published_at: str = "") -> Article:
    return Article.from_fields(source="Test", title=title, body=body, published_at=published_at)


def _make_draft(
    article: Article,
    *,
    event_type: str = "general_signal",
    matched_priorities: list | None = None,
    competitors: list | None = None,
    extra_articles: list | None = None,
) -> SignalDraft:
    articles = [article] + (extra_articles or [])
    cluster = Cluster(id="cluster_test", articles=articles)
    return SignalDraft(
        cluster=cluster,
        entities={"competitors": competitors or [], "markets": [], "organizations": []},
        matched_priorities=matched_priorities or [],
        event_type=event_type,
        text=f"{article.title} {article.body}",
    )


# ---------------------------------------------------------------------------
# Test: priority keywords load from config
# ---------------------------------------------------------------------------

class TestPriorityKeywordsLoad(unittest.TestCase):
    def test_priority_keywords_load(self) -> None:
        """Config parser must load all 7 new priority groups with correct weights."""
        config = load_config("configs/ai_tech.toml")
        names = [p.name for p in config.priorities]

        # All 7 groups present
        self.assertEqual(len(config.priorities), 7, f"Expected 7 priorities, got {len(config.priorities)}: {names}")

        # Spot-check names and weights
        by_name = {p.name: p for p in config.priorities}
        self.assertIn("Frontier AI Product And Model Launches", by_name)
        self.assertAlmostEqual(by_name["Frontier AI Product And Model Launches"].weight, 2.8)
        self.assertIn("Agents And Developer Workflows", by_name)
        self.assertAlmostEqual(by_name["Agents And Developer Workflows"].weight, 2.3)
        self.assertIn("High-Signal Builder Tactics", by_name)
        self.assertAlmostEqual(by_name["High-Signal Builder Tactics"].weight, 1.2)

        # Each group has keywords
        for priority in config.priorities:
            self.assertGreater(len(priority.keywords), 0, f"Priority '{priority.name}' has no keywords")


# ---------------------------------------------------------------------------
# Test: Claude launch scores high on priority_match
# ---------------------------------------------------------------------------

class TestClaudeLaunchScoresHighPriorityMatch(unittest.TestCase):
    def test_claude_launch_scores_high_priority_match(self) -> None:
        """An Anthropic/Claude model launch must reach priority_match >= 20."""
        config = load_config("configs/ai_tech.toml")
        text = (
            "Anthropic launches Claude 4 with enhanced reasoning capabilities. "
            "The new frontier model includes tool use, MCP support, and expanded API access."
        )
        matched = _match_priorities(text, config.priorities, {})
        # At least one group must match
        self.assertGreater(len(matched), 0, "Expected at least one priority group to match")

        # Priority match score must be >= 20
        bands = DEFAULT_SCORING_RUBRIC["priority_match_bands"]
        _band, pts = _score_priority_match(matched, bands)
        self.assertGreaterEqual(pts, 20, f"Expected priority_match >= 20 for a Claude launch, got {pts}")


# ---------------------------------------------------------------------------
# Test: listicle scores low event_strength
# ---------------------------------------------------------------------------

class TestListicleLowEventStrength(unittest.TestCase):
    def test_listicle_scores_low_event_strength(self) -> None:
        """A 'Top 10 AI tools' roundup must land in opinion_or_listicle (5 pts)."""
        article = _make_article(
            "Top 10 AI Tools You Need in 2025",
            body="Here are the best AI tools for productivity. Register now for our webinar.",
        )
        draft = _make_draft(article, event_type="general_signal")
        bands = DEFAULT_SCORING_RUBRIC["event_strength_bands"]
        band, pts = _score_event_strength(article, draft, bands)
        self.assertEqual(band, "opinion_or_listicle")
        self.assertEqual(pts, 5)


# ---------------------------------------------------------------------------
# Test: NVIDIA inference scores high on infrastructure
# ---------------------------------------------------------------------------

class TestNvidiaInfrastructure(unittest.TestCase):
    def test_nvidia_inference_scores_high_infrastructure(self) -> None:
        """NVIDIA + inference story must classify as infrastructure_signal."""
        text = "NVIDIA releases new H100 inference chips with record throughput and lower latency."
        competitor_hits = ["NVIDIA"]
        etype = _event_type(text, competitor_hits)
        self.assertEqual(etype, "infrastructure_signal")

    def test_nvidia_infrastructure_event_strength(self) -> None:
        """infrastructure_signal maps to product_update_or_signal (15 pts)."""
        article = _make_article(
            "NVIDIA releases H100 inference chips",
            body="NVIDIA releases new H100 inference chips with record GPU throughput.",
        )
        draft = _make_draft(article, event_type="infrastructure_signal", competitors=["NVIDIA"])
        bands = DEFAULT_SCORING_RUBRIC["event_strength_bands"]
        band, pts = _score_event_strength(article, draft, bands)
        self.assertEqual(band, "product_update_or_signal")
        self.assertEqual(pts, 15)


# ---------------------------------------------------------------------------
# Test: Richard Signal Score V2 behavior
# ---------------------------------------------------------------------------

class TestRichardSignalScoreV2(unittest.TestCase):
    def test_inference_cost_story_scores_as_must_read(self) -> None:
        article = _make_article(
            "NVIDIA announces lower inference cost for enterprise AI workloads",
            body=(
                "NVIDIA announced a new inference platform with 10x throughput, "
                "lower latency, API pricing changes, and enterprise customers using it "
                "for agent workloads."
            ),
            published_at=utc_now_iso(),
        )
        article.source = "Reuters"
        article.url = "https://www.reuters.com/technology/nvidia-inference-cost"
        draft = _make_draft(
            article,
            event_type="infrastructure_signal",
            competitors=["NVIDIA"],
            matched_priorities=[
                {"name": "AI Infrastructure, Chips, And Inference", "hits": ["NVIDIA", "inference", "cost"], "raw_count": 8, "weight": 2.8}
            ],
        )

        score, breakdown = _base_score_card(article, draft, DEFAULT_SCORING_RUBRIC)

        self.assertGreaterEqual(score, 85)
        names = {item["name"] for item in breakdown}
        self.assertIn("Trust penalty", names)
        self.assertIn("Score band", names)

    def test_promo_event_registration_is_hard_capped(self) -> None:
        article = _make_article(
            "Register now for the ultimate AI leadership conference",
            body="Sponsored webinar with early bird tickets and a limited time discount.",
            published_at=utc_now_iso(),
        )
        draft = _make_draft(article, event_type="general_signal")

        score, breakdown = _base_score_card(article, draft, DEFAULT_SCORING_RUBRIC)

        self.assertLessEqual(score, 25)
        self.assertIn("Hard cap", {item["name"] for item in breakdown})

    def test_single_source_sensational_claim_is_capped(self) -> None:
        article = _make_article(
            "SHOCKING truth about OpenAI governance they don't want you to know!",
            body="Anonymous insiders say a secret collapse is coming and AI will change everything.",
            published_at=utc_now_iso(),
        )
        article.source = "Unknown"
        draft = _make_draft(
            article,
            event_type="general_signal",
            competitors=["OpenAI"],
            matched_priorities=[
                {"name": "AI Regulation, Safety, Copyright, And Platform Risk", "hits": ["governance"], "raw_count": 3, "weight": 1.7}
            ],
        )

        score, _breakdown = _base_score_card(article, draft, DEFAULT_SCORING_RUBRIC)

        self.assertLessEqual(score, 60)

    def test_generic_tutorial_is_capped_below_strategic_signals(self) -> None:
        article = _make_article(
            "How to build a RAG chatbot: beginner tutorial",
            body="A step by step guide with generic tips and the best AI tools for a demo chatbot.",
            published_at=utc_now_iso(),
        )
        draft = _make_draft(article, event_type="builder_tactic")

        score, _breakdown = _base_score_card(article, draft, DEFAULT_SCORING_RUBRIC)

        self.assertLessEqual(score, 45)


# ---------------------------------------------------------------------------
# Test: _base_score_card is the only score source (agentic path)
# ---------------------------------------------------------------------------

class TestScoreCardIsOnlyScoreSource(unittest.TestCase):
    def test_score_card_is_only_score_source(self) -> None:
        """Signal.score must always equal _base_score_card output — no double-counting."""
        from signal_stream.analysis_tools import analyze_articles
        from signal_stream.models import BrainConfig, SignalConfig
        from signal_stream.storage import SignalStorage

        config = SignalConfig(
            name="Test",
            organization="Test",
            audience="Reader",
            mission="Test",
            competitors=["OpenAI", "Anthropic"],
            markets=["AI"],
            priorities=[
                Priority(name="Frontier AI", weight=2.8, keywords=["OpenAI", "launch", "model"]),
            ],
            sources=[],
            storage_path=":memory:",
            output_dir=".",
            brain=BrainConfig(),
        )
        storage = SignalStorage(":memory:")
        storage.init()

        articles_json = [
            {
                "id": "art_001",
                "source": "TechCrunch",
                "title": "OpenAI launches GPT-5 with advanced reasoning",
                "url": "https://example.com/1",
                "published_at": "2026-05-12T10:00:00Z",
                "body": "OpenAI today launched GPT-5 featuring a new reasoning model and API access.",
                "fetched_at": "2026-05-12T11:00:00Z",
                "raw": {},
            }
        ]

        captured_outputs: list[tuple[int, list]] = []

        original_base = __import__("signal_stream.analysis_tools", fromlist=["_base_score_card"])._base_score_card

        def recording_base_score_card(article, draft, rubric):
            result = original_base(article, draft, rubric)
            captured_outputs.append(result)
            return result

        with patch("signal_stream.analysis_tools._base_score_card", side_effect=recording_base_score_card):
            result = analyze_articles(config, storage, articles_json)

        # Exactly one signal produced
        self.assertEqual(len(result["signals"]), 1)
        signal = result["signals"][0]

        # Signal.score must match _base_score_card's output
        self.assertEqual(len(captured_outputs), 1, "Expected _base_score_card to be called exactly once")
        expected_score = captured_outputs[0][0]
        self.assertEqual(signal["score"], expected_score,
                         f"Signal.score={signal['score']} != _base_score_card output={expected_score}")


# ---------------------------------------------------------------------------
# Test: RelevanceAgent no longer importable from agents
# ---------------------------------------------------------------------------

class TestRelevanceAgentNoLongerImported(unittest.TestCase):
    def test_relevance_agent_no_longer_imported(self) -> None:
        """RelevanceAgent must not exist in signal_stream.agents after Wave 3."""
        import signal_stream.agents as agents_module
        self.assertFalse(
            hasattr(agents_module, "RelevanceAgent"),
            "RelevanceAgent still exists in signal_stream.agents — delete it.",
        )

    def test_analysis_tools_does_not_import_relevance_agent(self) -> None:
        """analysis_tools.py must not import RelevanceAgent."""
        import signal_stream.analysis_tools as at_module
        self.assertFalse(
            hasattr(at_module, "RelevanceAgent"),
            "RelevanceAgent still referenced in analysis_tools.",
        )


# ---------------------------------------------------------------------------
# Test: event type classification preserved after move
# ---------------------------------------------------------------------------

class TestEventTypeClassificationPreserved(unittest.TestCase):
    def test_competitor_move_when_only_competitor_hits(self) -> None:
        """When no event-type keyword wins but competitors are mentioned → competitor_move."""
        text = "Anthropic is expanding its team."
        etype = _event_type(text, competitor_hits=["Anthropic"])
        self.assertEqual(etype, "competitor_move")

    def test_regulatory_risk_detected(self) -> None:
        """Regulation-heavy text → regulatory_risk."""
        text = "The EU AI Act introduces new compliance and privacy requirements for model providers."
        etype = _event_type(text, competitor_hits=[])
        self.assertEqual(etype, "regulatory_risk")

    def test_startup_signal_detected(self) -> None:
        """Funding language → startup_signal."""
        text = "AI startup raises $100M Series B led by top venture firms."
        etype = _event_type(text, competitor_hits=[])
        self.assertEqual(etype, "startup_signal")

    def test_general_signal_fallback(self) -> None:
        """Text with no strong keywords and no competitors → general_signal."""
        text = "An interesting perspective on the future of computing."
        etype = _event_type(text, competitor_hits=[])
        self.assertEqual(etype, "general_signal")


# ---------------------------------------------------------------------------
# Test: V2 scoring weights are normalized
# ---------------------------------------------------------------------------

class TestScoringWeightsNormalized(unittest.TestCase):
    def test_value_weights_sum_to_20(self) -> None:
        """Six 1-5 dimensions need weights summing to 20 for a 100-point value score."""
        weights = DEFAULT_SCORING_RUBRIC["value_weights"]
        total = sum(weights.values())
        self.assertEqual(total, 20, f"Value weights sum to {total}, expected 20. Values: {weights}")

    def test_trust_weights_sum_to_1(self) -> None:
        """Trust deficit weights need to sum to 1 before the penalty scale is applied."""
        weights = DEFAULT_SCORING_RUBRIC["trust_weights"]
        total = sum(weights.values())
        self.assertAlmostEqual(total, 1.0, msg=f"Trust weights sum to {total}, expected 1.0. Values: {weights}")

    def test_each_band_section_has_expected_keys(self) -> None:
        """Each band section must be present and non-empty in the default rubric."""
        expected_sections = [
            "recency_bands",
            "event_strength_bands",
            "priority_match_bands",
            "company_match_bands",
            "corroboration_bands",
            "hard_caps",
        ]
        for section in expected_sections:
            self.assertIn(section, DEFAULT_SCORING_RUBRIC, f"Missing section: {section}")
            self.assertGreater(len(DEFAULT_SCORING_RUBRIC[section]), 0, f"Empty section: {section}")


if __name__ == "__main__":
    unittest.main()
