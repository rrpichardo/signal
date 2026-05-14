"""Phase 4 tests — controlled full-text fallback for Editor artifacts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from signal_stream.editor_tools import (
    _fallback_priority,
    _PRIORITY_CRITIC_FLAGS,
    _PRIORITY_HEAVY_TRUNCATION,
    _PRIORITY_LEAD_STORY,
    _PRIORITY_LOW_CONFIDENCE,
    _PRIORITY_MISSING_ARTIFACT,
    _PRIORITY_VAGUE_MECHANISM,
    evaluate_fallback_eligibility,
    run_fulltext_fallback,
)
from signal_stream.models import Article, Signal, stable_id, utc_now_iso
from signal_stream.storage import SignalStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    signal_id: str,
    score: int = 50,
    url: str = "https://example.com/article",
) -> Signal:
    return Signal(
        id=signal_id,
        cluster_id=f"cluster-{signal_id}",
        article_id=f"art-{signal_id}",
        title=f"Article {signal_id}",
        url=url,
        source="Test",
        published_at="2025-01-01T00:00:00Z",
        score=score,
        urgency="medium",
        event_type="general_signal",
        summary="Test summary.",
        why_it_matters="",
        next_steps=[],
        matched_priorities=[],
        entities={},
        duplicate_count=0,
        short_summary="Test short summary.",
        expanded_summary="Test expanded summary.",
    )


def _make_storage(tmp: Path) -> SignalStorage:
    storage = SignalStorage(tmp / "test.db")
    storage.init()
    return storage


def _persist_signal(storage: SignalStorage, signal: Signal) -> None:
    """Insert a signal row so update_signal_artifact can find it."""
    article = Article.from_fields(
        source=signal.source,
        title=signal.title,
        url=signal.url,
        published_at=signal.published_at,
        body="test body",
    )
    # Use save_run_atomic so articles and signals both land atomically.
    run_id = storage.start_agent_run("test")
    storage.save_run_atomic(
        articles=[article],
        signals=[signal],
        cluster_count=1,
        output_path="/tmp/test.md",
        started_at=utc_now_iso(),
        run_id=run_id,
    )


def _good_artifact(mechanism: str = "A detailed mechanism description here for testing.") -> dict:
    return {
        "mechanism": mechanism,
        "key_actors": [],
        "affected_parties": [],
        "evidence_excerpts": [],
        "confidence": "medium",
        "confidence_reason": "Looks solid.",
        "model_confidence": "medium",
        "critic_flags": [],
        "_meta": {
            "was_truncated": False,
            "chars_total": 5000,
            "chars_sent": 5000,
            "extraction_quality": "good",
        },
    }


def _mock_brain_with_review(mechanism: str = "Refreshed mechanism from full text.") -> MagicMock:
    """Brain whose chat_json returns a valid ANALYST_REVIEW_SCHEMA response."""
    brain = MagicMock()
    brain.last_error = ""
    brain.chat_json.return_value = {
        "signals": [
            {
                "id": "placeholder",  # overwritten per signal in _review_payload
                "score": 50,
                "short_summary": "Short refreshed summary.",
                "expanded_summary": mechanism,
                "entities": {},
            }
        ]
    }
    return brain


# ---------------------------------------------------------------------------
# _fallback_priority unit tests
# ---------------------------------------------------------------------------

class TestFallbackPriorityUnit(unittest.TestCase):
    def test_missing_artifact_highest_priority(self) -> None:
        self.assertEqual(_fallback_priority(None, rank=10), _PRIORITY_MISSING_ARTIFACT)

    def test_empty_dict_treated_as_missing(self) -> None:
        self.assertEqual(_fallback_priority({}, rank=10), _PRIORITY_MISSING_ARTIFACT)

    def test_heavy_truncation_triggers(self) -> None:
        artifact = {
            "_meta": {"was_truncated": True, "chars_total": 10000, "chars_sent": 4000},
            "mechanism": "Some detailed mechanism text here.",
            "confidence": "medium",
            "critic_flags": [],
        }
        self.assertEqual(_fallback_priority(artifact, rank=10), _PRIORITY_HEAVY_TRUNCATION)

    def test_light_truncation_does_not_trigger_as_heavy(self) -> None:
        # chars_sent / chars_total = 0.7 → above 0.5 threshold → not heavy truncation
        artifact = {
            "_meta": {"was_truncated": True, "chars_total": 10000, "chars_sent": 7000},
            "mechanism": "A long enough mechanism description to pass the vague check here.",
            "confidence": "medium",
            "critic_flags": [],
        }
        # Not missing, not heavy truncation. Check that it's NOT returned as heavy_truncation.
        result = _fallback_priority(artifact, rank=10)
        self.assertNotEqual(result, _PRIORITY_HEAVY_TRUNCATION)

    def test_critic_flags_triggers(self) -> None:
        artifact = dict(_good_artifact())
        artifact["critic_flags"] = ["promotional residue"]
        self.assertEqual(_fallback_priority(artifact, rank=10), _PRIORITY_CRITIC_FLAGS)

    def test_low_confidence_triggers(self) -> None:
        artifact = dict(_good_artifact())
        artifact["confidence"] = "low"
        self.assertEqual(_fallback_priority(artifact, rank=10), _PRIORITY_LOW_CONFIDENCE)

    def test_vague_mechanism_triggers(self) -> None:
        artifact = dict(_good_artifact(mechanism="Too short."))  # under 40 chars
        self.assertEqual(_fallback_priority(artifact, rank=10), _PRIORITY_VAGUE_MECHANISM)

    def test_lead_story_triggers_for_top_3(self) -> None:
        # A good artifact at rank 0 still gets PRIORITY_LEAD_STORY
        self.assertEqual(_fallback_priority(_good_artifact(), rank=0), _PRIORITY_LEAD_STORY)
        self.assertEqual(_fallback_priority(_good_artifact(), rank=2), _PRIORITY_LEAD_STORY)

    def test_no_trigger_for_rank_3_plus_good_artifact(self) -> None:
        self.assertIsNone(_fallback_priority(_good_artifact(), rank=3))

    def test_priority_ordering(self) -> None:
        # missing < heavy_truncation < critic < low_confidence < vague < lead
        self.assertLess(_PRIORITY_MISSING_ARTIFACT, _PRIORITY_HEAVY_TRUNCATION)
        self.assertLess(_PRIORITY_HEAVY_TRUNCATION, _PRIORITY_CRITIC_FLAGS)
        self.assertLess(_PRIORITY_CRITIC_FLAGS, _PRIORITY_LOW_CONFIDENCE)
        self.assertLess(_PRIORITY_LOW_CONFIDENCE, _PRIORITY_VAGUE_MECHANISM)
        self.assertLess(_PRIORITY_VAGUE_MECHANISM, _PRIORITY_LEAD_STORY)


# ---------------------------------------------------------------------------
# evaluate_fallback_eligibility tests
# ---------------------------------------------------------------------------

class TestEvaluateFallbackEligibility(unittest.TestCase):
    def test_heavy_truncation_triggers_fallback(self) -> None:
        signal = _make_signal("s1", score=60)
        artifacts = {
            "s1": {
                "_meta": {"was_truncated": True, "chars_total": 10000, "chars_sent": 4000},
                "mechanism": "Some mechanism text here is present.",
                "confidence": "medium",
                "critic_flags": [],
            }
        }
        result = evaluate_fallback_eligibility([signal], artifacts, cap=3)
        self.assertIn(signal, result)

    def test_low_confidence_triggers_fallback(self) -> None:
        signal = _make_signal("s1", score=55)
        artifacts = {"s1": dict(_good_artifact(), confidence="low")}
        result = evaluate_fallback_eligibility([signal], artifacts, cap=3)
        self.assertIn(signal, result)

    def test_cap_enforced_when_5_eligible(self) -> None:
        # All artifacts missing → all 5 eligible, but cap=3 returns only 3.
        signals = [_make_signal(f"s{i}", score=50 - i) for i in range(5)]
        artifacts: dict = {}
        result = evaluate_fallback_eligibility(signals, artifacts, cap=3)
        self.assertEqual(len(result), 3)

    def test_cap_zero_returns_empty(self) -> None:
        signals = [_make_signal(f"s{i}") for i in range(3)]
        artifacts: dict = {}
        result = evaluate_fallback_eligibility(signals, artifacts, cap=0)
        self.assertEqual(result, [])

    def test_non_lead_story_signals_with_good_artifacts_not_eligible(self) -> None:
        # With 5 signals that all have good artifacts, only the top-3 (lead stories, rank 0-2)
        # fire a trigger. Signals at rank 3+ have no trigger and never appear in the result.
        signals = [_make_signal(f"s{i}", score=50) for i in range(5)]
        artifacts = {f"s{i}": _good_artifact() for i in range(5)}
        result = evaluate_fallback_eligibility(signals, artifacts, cap=5)
        result_ids = {s.id for s in result}
        # Rank 3+ signals must not appear — they have no trigger
        self.assertNotIn("s3", result_ids)
        self.assertNotIn("s4", result_ids)
        # Exactly the 3 lead-story signals are eligible
        self.assertEqual(len(result), 3)

    def test_missing_beats_heavy_truncation_in_ordering(self) -> None:
        # s1 has heavy truncation, s2 has missing artifact. With cap=1, s2 should win.
        s1 = _make_signal("s1", score=90)
        s2 = _make_signal("s2", score=80)
        artifacts = {
            "s1": {
                "_meta": {"was_truncated": True, "chars_total": 10000, "chars_sent": 2000},
                "mechanism": "Some mechanism text here.",
                "confidence": "medium",
                "critic_flags": [],
            },
            # s2 missing artifact
        }
        result = evaluate_fallback_eligibility([s1, s2], artifacts, cap=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "s2")  # missing artifact wins

    def test_higher_score_breaks_tie_within_priority(self) -> None:
        # Both s1 and s2 are missing artifacts (same priority). Higher score wins the cap slot.
        s1 = _make_signal("s1", score=70)
        s2 = _make_signal("s2", score=90)
        artifacts: dict = {}
        result = evaluate_fallback_eligibility([s1, s2], artifacts, cap=1)
        self.assertEqual(result[0].id, "s2")


# ---------------------------------------------------------------------------
# run_fulltext_fallback integration tests
# ---------------------------------------------------------------------------

class TestRunFulltextFallback(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = _make_storage(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_and_persist(self, signal_id: str, score: int = 60) -> Signal:
        signal = _make_signal(signal_id, score=score)
        _persist_signal(self.storage, signal)
        return signal

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_refreshed_artifact_replaces_original(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Full article body text of significant length for testing purposes.", None)
        signal = self._make_and_persist("s1")
        artifacts: dict = {}  # missing → eligible
        brain = _mock_brain_with_review("A specific refreshed mechanism from full text.")

        result = run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test analyst prompt.",
        )

        self.assertIn("s1", result)
        artifact = result["s1"]
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact["_meta"]["refresh_source"], "editor_fallback")

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_refreshed_artifact_persisted_to_db(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Long enough body text here for the test.", None)
        signal = self._make_and_persist("s2")
        artifacts: dict = {}
        brain = _mock_brain_with_review("Persisted mechanism text.")

        run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test analyst prompt.",
        )

        stored = self.storage.get_signal_artifacts(["s2"])
        self.assertIn("s2", stored)
        artifact = stored["s2"]
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact["_meta"]["refresh_source"], "editor_fallback")

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_refreshed_mechanism_visible_for_briefing(self, mock_fetch: MagicMock) -> None:
        """Mechanism from refreshed artifact is available for Phase 3 Editor to include in briefing."""
        refreshed_mechanism = "Anthropic released Claude 4 with extended context, targeting enterprise workflows."
        mock_fetch.return_value = ("Full article body with detailed content.", None)
        signal = self._make_and_persist("s3")
        artifacts: dict = {}
        brain = _mock_brain_with_review(refreshed_mechanism)

        result = run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test analyst prompt.",
        )

        artifact = result["s3"]
        self.assertIsNotNone(artifact)
        assert artifact is not None
        # The mechanism from the re-review is available for the Phase 3 Editor
        # to include in cross_signal_narrative when generating the briefing.
        self.assertEqual(artifact["mechanism"], refreshed_mechanism)

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_fallback_failure_on_one_does_not_abort_others(self, mock_fetch: MagicMock) -> None:
        # First fetch fails (returns empty), second succeeds.
        mock_fetch.side_effect = [
            ("", None),                                       # s1: empty body → skip
            ("Good body text for the second signal.", None),  # s2: succeeds
        ]
        s1 = self._make_and_persist("s1", score=80)
        s2 = self._make_and_persist("s2", score=70)
        artifacts: dict = {}  # both missing
        brain = _mock_brain_with_review("Mechanism from second signal.")

        result = run_fulltext_fallback(
            signals=[s1, s2],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test analyst prompt.",
        )

        # s1 failed → original None kept; s2 succeeded → refreshed artifact
        self.assertIsNone(result["s1"])
        self.assertIsNotNone(result["s2"])
        assert result["s2"] is not None
        self.assertEqual(result["s2"]["_meta"]["refresh_source"], "editor_fallback")

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_network_exception_does_not_abort_other_signals(self, mock_fetch: MagicMock) -> None:
        # First fetch raises an exception, second succeeds.
        mock_fetch.side_effect = [
            Exception("Network timeout"),
            ("Good body text here.", None),
        ]
        s1 = self._make_and_persist("s1", score=80)
        s2 = self._make_and_persist("s2", score=70)
        artifacts: dict = {}
        brain = _mock_brain_with_review("Mechanism for s2.")

        # Must not raise
        result = run_fulltext_fallback(
            signals=[s1, s2],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        self.assertIsNone(result["s1"])
        self.assertIsNotNone(result["s2"])

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_all_fallback_fetches_fail_returns_gracefully(self, mock_fetch: MagicMock) -> None:
        """Run completes without exception even when every fallback fetch fails."""
        mock_fetch.return_value = ("", None)  # all fetches yield empty body
        signals = [self._make_and_persist(f"s{i}", score=60 - i) for i in range(3)]
        artifacts: dict = {}
        brain = _mock_brain_with_review("Should not be called.")

        # Must not raise — fallbacks fail silently, run still completes
        result = run_fulltext_fallback(
            signals=signals,
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        # All failed → all still None (original state preserved)
        for sig in signals:
            self.assertIsNone(result[sig.id])
        brain.chat_json.assert_not_called()

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_groq_failure_does_not_abort_other_signals(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Good body text here.", None)
        s1 = self._make_and_persist("s1", score=80)
        s2 = self._make_and_persist("s2", score=70)
        artifacts: dict = {}

        brain = MagicMock()
        brain.last_error = ""
        # First call fails (returns None), second succeeds
        brain.chat_json.side_effect = [
            None,
            {"signals": [{"id": "s2", "score": 70, "short_summary": "s", "expanded_summary": "Mechanism for s2.", "entities": {}}]},
        ]

        result = run_fulltext_fallback(
            signals=[s1, s2],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        self.assertIsNone(result["s1"])
        self.assertIsNotNone(result["s2"])

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_cap_respected_in_run_fulltext_fallback(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Good body text here.", None)
        signals = [self._make_and_persist(f"s{i}", score=60 - i) for i in range(5)]
        artifacts: dict = {}  # all missing
        brain = _mock_brain_with_review("Mechanism.")

        run_fulltext_fallback(
            signals=signals,
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        # Only 3 Groq calls should have been made (cap=3)
        self.assertEqual(brain.chat_json.call_count, 3)

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_truncation_metadata_captured_correctly(self, mock_fetch: MagicMock) -> None:
        # Body longer than _OVERSIZED_TRUNCATION → was_truncated=True
        long_body = "x" * 9000
        mock_fetch.return_value = (long_body, None)
        signal = self._make_and_persist("s1")
        artifacts: dict = {}
        brain = _mock_brain_with_review("Mechanism from long article.")

        result = run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        artifact = result["s1"]
        self.assertIsNotNone(artifact)
        assert artifact is not None
        meta = artifact["_meta"]
        self.assertTrue(meta["was_truncated"])
        self.assertEqual(meta["chars_total"], 9000)
        self.assertEqual(meta["chars_sent"], 8000)

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_critic_flags_cleared_after_refresh(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Full article body text.", None)
        signal = self._make_and_persist("s1")
        artifacts = {
            "s1": dict(_good_artifact(), critic_flags=["promotional residue"])
        }
        brain = _mock_brain_with_review("Clean mechanism after re-review.")

        result = run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
        )

        artifact = result["s1"]
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact["critic_flags"], [])

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_log_fn_called_on_success(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("Good body text here.", None)
        signal = self._make_and_persist("s1")
        artifacts: dict = {}
        brain = _mock_brain_with_review("Mechanism.")
        log_events: list[tuple] = []

        def log_fn(event_type: str, message: str, payload: dict) -> None:
            log_events.append((event_type, message))

        run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
            log_fn=log_fn,
        )

        event_types = [e[0] for e in log_events]
        self.assertIn("editor_fallback_started", event_types)
        self.assertIn("editor_fallback_success", event_types)

    @patch("signal_stream.editor_tools.fetch_full_article_page")
    def test_log_fn_called_on_failure(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = ("", None)  # empty → failure
        signal = self._make_and_persist("s1")
        artifacts: dict = {}
        brain = _mock_brain_with_review("Not called.")
        log_events: list[tuple] = []

        def log_fn(event_type: str, message: str, payload: dict) -> None:
            log_events.append((event_type, message))

        run_fulltext_fallback(
            signals=[signal],
            artifacts=artifacts,
            brain=brain,
            storage=self.storage,
            cap=3,
            analyst_prompt="Test prompt.",
            log_fn=log_fn,
        )

        event_types = [e[0] for e in log_events]
        self.assertIn("editor_fallback_failed", event_types)


# ---------------------------------------------------------------------------
# Storage round-trip tests
# ---------------------------------------------------------------------------

class TestStorageArtifactRoundtrip(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = _make_storage(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_update_and_get_artifact(self) -> None:
        signal = _make_signal("s1")
        _persist_signal(self.storage, signal)
        artifact = _good_artifact()
        artifact["_meta"]["refresh_source"] = "editor_fallback"

        self.storage.update_signal_artifact("s1", artifact)

        stored = self.storage.get_signal_artifacts(["s1"])
        self.assertIn("s1", stored)
        result = stored["s1"]
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["_meta"]["refresh_source"], "editor_fallback")

    def test_old_signal_returns_none_artifact(self) -> None:
        # Signal row inserted without analyst_artifact_json → should return None, not raise.
        signal = _make_signal("s_old")
        _persist_signal(self.storage, signal)

        stored = self.storage.get_signal_artifacts(["s_old"])
        self.assertIsNone(stored.get("s_old"))

    def test_missing_signal_id_returns_none(self) -> None:
        stored = self.storage.get_signal_artifacts(["nonexistent_id"])
        self.assertIsNone(stored.get("nonexistent_id"))

    def test_empty_id_list_returns_empty_dict(self) -> None:
        result = self.storage.get_signal_artifacts([])
        self.assertEqual(result, {})

    def test_get_signal_includes_analyst_artifact(self) -> None:
        signal = _make_signal("s1")
        _persist_signal(self.storage, signal)
        artifact = _good_artifact()
        self.storage.update_signal_artifact("s1", artifact)

        hydrated = self.storage.get_signal("s1")
        self.assertIsNotNone(hydrated)
        assert hydrated is not None
        self.assertIsNotNone(hydrated.get("analyst_artifact"))
        self.assertEqual(hydrated["analyst_artifact"]["confidence"], "medium")

    def test_old_signal_hydrates_with_null_analyst_artifact(self) -> None:
        signal = _make_signal("s_old2")
        _persist_signal(self.storage, signal)

        hydrated = self.storage.get_signal("s_old2")
        self.assertIsNotNone(hydrated)
        assert hydrated is not None
        # Key must exist but value must be None — no crash, no KeyError
        self.assertIn("analyst_artifact", hydrated)
        self.assertIsNone(hydrated["analyst_artifact"])

    def test_ensure_column_safe_on_existing_db(self) -> None:
        # Calling init() twice must not raise (ensure_column is idempotent).
        self.storage.init()
        self.storage.init()


if __name__ == "__main__":
    unittest.main()
