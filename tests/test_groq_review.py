"""Wave 4 tests — top-40, full-page fetch, 1-per-request Groq, required_fields, exec summary."""

from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from signal_stream.analysis_tools import (
    _fetch_full_pages_for_top_n,
    _review_signals_in_chunks,
    analyze_articles,
)
from signal_stream.models import Article, Cluster, Signal, SignalConfig, SignalDraft, stable_id
from signal_stream.source_tools import fetch_full_article_page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config() -> SignalConfig:
    from signal_stream.config import load_config
    return load_config("configs/ai_tech.toml")


def _make_signal(
    index: int,
    score: int = 50,
    url: str = "https://example.com/article",
    body: str = "RSS body fallback text.",
) -> Signal:
    article = Article.from_fields(source="Test", title=f"Article {index}", url=url, body=body)
    return Signal(
        id=stable_id("cluster", f"article-{index}", score, prefix="sig"),
        cluster_id=f"cluster-{index}",
        article_id=article.id,
        title=f"Article {index}",
        url=url,
        source="Test",
        published_at="",
        score=score,
        urgency="medium",
        event_type="general_signal",
        summary=body[:100],
        why_it_matters="",
        next_steps=[],
        matched_priorities=[],
        entities={},
        duplicate_count=0,
        short_summary=body[:100],
        expanded_summary=body,
    )


def _make_review_context(signals: list[Signal]) -> dict:
    return {s.id: {"article_text": f"Full text of {s.title}."} for s in signals}


# ---------------------------------------------------------------------------
# Test: top-40 max to Groq
# ---------------------------------------------------------------------------

class TestTop40MaxToGroq(unittest.TestCase):
    def test_top_40_max_to_groq(self) -> None:
        """100 candidates → at most 40 reach chat_json."""
        signals = [_make_signal(i, score=100 - i) for i in range(100)]
        review_context = _make_review_context(signals)
        behavior = {"analyst_review_limit": 40, "analyst_review_batch_size": 1}

        call_count = 0

        def fake_chat_json(system, user, schema=None, *, temperature=0.0, required_fields=None):
            nonlocal call_count
            call_count += 1
            payload = json.loads(user)
            items = payload.get("signals", [])
            return {"signals": [{"id": s["id"], "score": 50, "short_summary": "s", "expanded_summary": "e", "entities": {}} for s in items]}

        config = _make_config()
        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = fake_chat_json
        mock_llm.available.return_value = True
        mock_llm.last_error = None

        with patch("signal_stream.analysis_tools.BrainClient", return_value=mock_llm):
            _review_signals_in_chunks(mock_llm, "system prompt", signals, behavior, review_context)

        self.assertLessEqual(call_count, 40)
        self.assertGreater(call_count, 0)


# ---------------------------------------------------------------------------
# Test: one article per Groq request
# ---------------------------------------------------------------------------

class TestOneArticlePerGroqRequest(unittest.TestCase):
    def test_one_article_per_groq_request(self) -> None:
        """Each chat_json call must contain exactly 1 signal when batch_size=1."""
        signals = [_make_signal(i, score=90 - i) for i in range(5)]
        review_context = _make_review_context(signals)
        behavior = {"analyst_review_limit": 5, "analyst_review_batch_size": 1}

        batch_sizes: list[int] = []

        def fake_chat_json(system, user, schema=None, *, temperature=0.0, required_fields=None):
            payload = json.loads(user)
            batch_sizes.append(len(payload.get("signals", [])))
            items = payload.get("signals", [])
            return {"signals": [{"id": s["id"], "score": 50, "short_summary": "s", "expanded_summary": "e", "entities": {}} for s in items]}

        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = fake_chat_json
        mock_llm.available.return_value = True
        mock_llm.last_error = None

        _review_signals_in_chunks(mock_llm, "sys", signals, behavior, review_context)

        self.assertEqual(len(batch_sizes), 5)
        for size in batch_sizes:
            self.assertEqual(size, 1)


# ---------------------------------------------------------------------------
# Test: full page text sent when extraction succeeds
# ---------------------------------------------------------------------------

class TestFullPageTextSentWhenExtractionSucceeds(unittest.TestCase):
    def test_full_page_text_sent_when_extraction_succeeds(self) -> None:
        """When fetch_full_article_page returns ≥200 chars, that text replaces the RSS body."""
        long_text = "A" * 500
        signals = [_make_signal(0, url="https://example.com/article", body="short rss")]
        review_context = _make_review_context(signals)

        with patch("signal_stream.analysis_tools.fetch_full_article_page", return_value=(long_text, None)):
            _, updated_ctx = _fetch_full_pages_for_top_n(signals, review_context, top_n=1)

        self.assertEqual(updated_ctx[signals[0].id]["article_text"], long_text)


# ---------------------------------------------------------------------------
# Test: RSS body used when extraction fails
# ---------------------------------------------------------------------------

class TestRSSBodyUsedWhenExtractionFails(unittest.TestCase):
    def test_rss_body_used_when_extraction_fails(self) -> None:
        """When fetch_full_article_page returns "" (error/timeout), keep the original article_text."""
        signals = [_make_signal(0, body="original rss body")]
        original_text = f"Full text of {signals[0].title}."
        review_context = {signals[0].id: {"article_text": original_text}}

        with patch("signal_stream.analysis_tools.fetch_full_article_page", return_value=("", None)):
            _, updated_ctx = _fetch_full_pages_for_top_n(signals, review_context, top_n=1)

        self.assertEqual(updated_ctx[signals[0].id]["article_text"], original_text)


# ---------------------------------------------------------------------------
# Test: oversized article truncated with warning
# ---------------------------------------------------------------------------

class TestOversizedArticleTruncatedWithWarning(unittest.TestCase):
    def test_oversized_article_truncated_with_warning(self) -> None:
        """An article > 8000 chars triggers a truncation log and retry."""
        from signal_stream.analysis_tools import _chat_json_with_truncation_fallback, _OVERSIZED_TRUNCATION

        huge_text = "X" * 20000
        signals = [_make_signal(0, body=huge_text)]
        review_context = {signals[0].id: {"article_text": huge_text}}

        call_args: list[str] = []

        def fake_chat_json(system, user, schema=None, *, temperature=0.0, required_fields=None):
            call_args.append(user)
            payload = json.loads(user)
            # Simulate context-too-large on first call, success on retry
            if len(call_args) == 1:
                mock_llm.last_error = "context length exceeded"
                return None
            # Second call (retry with truncated text)
            mock_llm.last_error = None
            items = payload.get("signals", [])
            return {"signals": [{"id": s["id"], "score": 50, "short_summary": "s", "expanded_summary": "e", "entities": {}} for s in items]}

        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = fake_chat_json
        mock_llm.last_error = None

        captured = StringIO()
        payload = json.dumps({
            "signals": [{
                "id": signals[0].id,
                "article_text": huge_text,
                "title": signals[0].title,
                "source": signals[0].source,
                "event_type": signals[0].event_type,
                "score": signals[0].score,
                "score_breakdown": [],
                "matched_priorities": [],
                "entities": {},
                "duplicate_count": 0,
            }]
        })

        with patch.object(sys, "stderr", captured):
            # Phase 2: returns (result, truncation_info) tuple.
            result, trunc_info = _chat_json_with_truncation_fallback(
                mock_llm, "sys", payload, {}, required_fields=["score", "short_summary", "expanded_summary"]
            )

        stderr_out = captured.getvalue()
        self.assertIn("truncated", stderr_out.lower())
        # On retry, article_text must be ≤ _OVERSIZED_TRUNCATION
        retry_payload = json.loads(call_args[-1])
        for item in retry_payload.get("signals", []):
            self.assertLessEqual(len(str(item.get("article_text", ""))), _OVERSIZED_TRUNCATION)
        self.assertIsNotNone(result)
        # Truncation info should carry the post-truncation char count.
        self.assertTrue(trunc_info["was_truncated"])
        self.assertEqual(trunc_info["chars_total"], 20000)
        self.assertLessEqual(trunc_info["chars_sent"], _OVERSIZED_TRUNCATION)


# ---------------------------------------------------------------------------
# Test: missing required fields triggers retry
# ---------------------------------------------------------------------------

class TestGroqResponseMissingFieldsRetriesOnce(unittest.TestCase):
    def test_groq_response_missing_fields_retries_once(self) -> None:
        """required_fields triggers a retry for flat responses (e.g. single-field calls).

        Note: the analyst review uses a nested {"signals": [...]} wrapper, so
        required_fields is NOT used there. This test covers the flat-response case
        (e.g. a future single-signal or summary endpoint).
        """
        from signal_stream.llm import BrainClient

        call_count = 0

        def fake_call_groq(system, user, temperature):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Missing short_summary on first call (flat response)
                return {"score": 50, "expanded_summary": "e"}
            # Second call (retry) has all fields
            return {"score": 50, "short_summary": "s", "expanded_summary": "e"}

        config = _make_config()
        client = BrainClient.__new__(BrainClient)
        client.config = config.brain
        client.last_error = None
        client.last_response_text = ""
        client._api_key = "fake-key"

        with patch.object(client, "_call_groq", side_effect=fake_call_groq):
            result = client.chat_json(
                "sys", "user",
                required_fields=["short_summary", "expanded_summary"],
            )

        self.assertEqual(call_count, 2)
        self.assertIsNotNone(result)
        self.assertEqual(result["short_summary"], "s")


# ---------------------------------------------------------------------------
# Test: invalid response after retry skips signal
# ---------------------------------------------------------------------------

class TestGroqResponseInvalidAfterRetrySkipsSignal(unittest.TestCase):
    def test_groq_response_invalid_after_retry_skips_signal(self) -> None:
        """If both attempts fail validation, chat_json returns None and sets last_error."""
        from signal_stream.llm import BrainClient

        def fake_call_groq(system, user, temperature):
            # Always missing required fields
            return {"signals": [{"id": "x", "score": 50}]}

        config = _make_config()
        client = BrainClient.__new__(BrainClient)
        client.config = config.brain
        client.last_error = None
        client.last_response_text = ""
        client._api_key = "fake-key"

        with patch.object(client, "_call_groq", side_effect=fake_call_groq):
            result = client.chat_json(
                "sys", "user",
                required_fields=["short_summary", "expanded_summary"],
            )

        self.assertIsNone(result)
        self.assertIn("missing", (client.last_error or "").lower())


# ---------------------------------------------------------------------------
# Test: executive summary uses top 12
# ---------------------------------------------------------------------------

class TestExecutiveSummaryUsesTop12(unittest.TestCase):
    def test_executive_summary_uses_top_12(self) -> None:
        """The behavior.executive_summary_limit=12 should be loaded from brain file."""
        from signal_stream.prompt_loader import load_behavior_settings
        behavior = load_behavior_settings("configs/agent_brain.toml")
        exec_limit = int(behavior.get("executive_summary_limit", 12))
        self.assertEqual(exec_limit, 12)


# ---------------------------------------------------------------------------
# Test: why_it_matters not in required fields (folded into short_summary)
# ---------------------------------------------------------------------------

class TestWhyItMattersFoldedIntoShortSummary(unittest.TestCase):
    def test_why_it_matters_folded_into_short_summary(self) -> None:
        """ANALYST_REVIEW_SCHEMA should not require why_it_matters."""
        from signal_stream.analysis_tools import ANALYST_REVIEW_SCHEMA
        item_schema = ANALYST_REVIEW_SCHEMA["properties"]["signals"]["items"]
        required_fields = item_schema.get("required", [])
        self.assertNotIn("why_it_matters", required_fields)
        self.assertIn("short_summary", required_fields)
        self.assertIn("expanded_summary", required_fields)


# ---------------------------------------------------------------------------
# Test: fetch_full_article_page returns (str, None) on bad URL
# ---------------------------------------------------------------------------

class TestFetchFullArticlePageFallback(unittest.TestCase):
    def test_returns_empty_on_invalid_url(self) -> None:
        """Non-HTTP URL or error should return ('', None) without raising."""
        body, img = fetch_full_article_page("not-a-url")
        self.assertEqual(body, "")
        self.assertIsNone(img)

    def test_returns_empty_on_network_error(self) -> None:
        """Network error should silently return ('', None)."""
        with patch("signal_stream.source_tools.request.urlopen", side_effect=OSError("timeout")):
            body, img = fetch_full_article_page("https://example.com/article")
        self.assertEqual(body, "")
        self.assertIsNone(img)

    def test_og_image_extracted(self) -> None:
        """og:image meta tag should be extracted from HTML."""
        html = b"""<html><head>
<meta property="og:image" content="https://example.com/img.jpg">
</head><body><article>""" + b"This is a long article body text. " * 10 + b"""</article></body></html>"""

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers.get.return_value = "text/html"
        mock_resp.read.return_value = html

        with patch("signal_stream.source_tools.request.urlopen", return_value=mock_resp):
            body, img = fetch_full_article_page("https://example.com/article")

        self.assertEqual(img, "https://example.com/img.jpg")

    def test_long_article_body_extracted(self) -> None:
        """A page with ≥200 chars in <article> should return that text."""
        long_body = "Good article content. " * 20
        html = (
            f"<html><body><article>{long_body}</article></body></html>"
        ).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.headers.get.return_value = "text/html"
        mock_resp.read.return_value = html

        with patch("signal_stream.source_tools.request.urlopen", return_value=mock_resp):
            body, img = fetch_full_article_page("https://example.com/article")

        self.assertGreaterEqual(len(body), 200)
        self.assertIn("Good article content", body)


# ---------------------------------------------------------------------------
# Phase 2: artifact assembly + confidence overrides
# ---------------------------------------------------------------------------

class TestArtifactPersistedOnSignal(unittest.TestCase):
    def test_artifact_persisted_on_signal(self) -> None:
        """After a model review pass, signal.analyst_artifact is populated."""
        from signal_stream.analysis_tools import _apply_analyst_mode
        from signal_stream.models import BrainConfig, Priority

        class FakeBrain:
            def __init__(self, config):  # noqa: ANN001
                pass

            def available(self) -> bool:
                return True

            def chat_json(self, system, user, schema=None, **kwargs):  # noqa: ANN001
                return {
                    "signals": [{
                        "id": "sig_artifact",
                        "score": 70,
                        "short_summary": "A model-written card summary that explains why this matters now.",
                        "expanded_summary": "Expanded paragraph describing the change and its forward-looking implications.",
                        "entities": {"companies": ["Anthropic"]},
                        "mechanism": "Anthropic raised its rate-limit ceiling, unblocking agent workloads that previously needed throttling.",
                        "key_actors": [{"name": "Anthropic", "role": "API provider"}],
                        "affected_parties": ["AI infrastructure teams", "Agent-framework builders"],
                        "evidence_excerpts": [{"quote": "We are raising the per-minute token cap by 10x.", "source_offset": 120}],
                        "confidence": "high",
                        "confidence_reason": "Primary-source announcement with specific numbers.",
                    }]
                }

        config = SignalConfig(
            name="Test", organization="Test", audience="Reader", mission="Test",
            competitors=[], markets=[], priorities=[Priority("AI")], sources=[],
            storage_path=":memory:", output_dir=".", brain=BrainConfig(),
        )
        signal = Signal(
            id="sig_artifact", cluster_id="c", article_id="a",
            title="Anthropic raises rate limit ceiling",
            url="https://example.com", source="Example", published_at="",
            score=60, urgency="medium", event_type="platform_shift",
            summary="", why_it_matters="", next_steps=[],
            matched_priorities=[], entities={}, duplicate_count=2,
            short_summary="", expanded_summary="",
        )

        import signal_stream.analysis_tools as analysis_tools
        original = analysis_tools.BrainClient
        analysis_tools.BrainClient = FakeBrain
        try:
            updated_signals, _ = _apply_analyst_mode(
                [signal], config, "hybrid", "prompt",
                {"analyst_full_review": True, "summary_mode": "short_expanded", "entity_extraction": "hybrid"},
                {"sig_artifact": {"article_text": "A" * 4000}},
            )
        finally:
            analysis_tools.BrainClient = original

        artifact = updated_signals[0].analyst_artifact
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact["mechanism"][:9], "Anthropic")
        self.assertEqual(artifact["key_actors"][0]["name"], "Anthropic")
        self.assertEqual(artifact["affected_parties"][0], "AI infrastructure teams")
        self.assertEqual(artifact["evidence_excerpts"][0]["source_offset"], 120)
        # No truncation, multi-source — confidence should stay "high".
        self.assertEqual(artifact["confidence"], "high")
        self.assertEqual(artifact["model_confidence"], "high")
        self.assertFalse(artifact["_meta"]["was_truncated"])
        self.assertEqual(artifact["_meta"]["extraction_quality"], "good")


class TestConfidenceDowngradedOnTruncation(unittest.TestCase):
    def test_confidence_downgraded_on_heavy_truncation(self) -> None:
        """If was_truncated and chars_sent / chars_total < 0.5, final confidence is 'low'."""
        from signal_stream.analysis_tools import _build_artifact

        signal = Signal(
            id="s", cluster_id="c", article_id="a", title="T", url="", source="",
            published_at="", score=60, urgency="medium", event_type="platform_shift",
            summary="", why_it_matters="", next_steps=[], matched_priorities=[],
            entities={}, duplicate_count=2,  # multi-source so single-source rule won't fire
        )
        item = {
            "id": "s", "score": 60,
            "short_summary": "x", "expanded_summary": "y", "entities": {},
            "mechanism": "Some mechanism prose here.",
            "key_actors": [{"name": "A", "role": "r"}],
            "affected_parties": ["x"],
            "evidence_excerpts": [{"quote": "q", "source_offset": 0}],
            "confidence": "high",
            "confidence_reason": "primary source",
            # Heavy truncation: 4000 of 20000 chars sent (20% — well below 0.5).
            "_truncation": {"was_truncated": True, "chars_total": 20000, "chars_sent": 4000},
        }
        artifact = _build_artifact(signal, item)
        self.assertEqual(artifact["model_confidence"], "high")
        self.assertEqual(artifact["confidence"], "low")
        self.assertTrue(artifact["_meta"]["was_truncated"])
        self.assertEqual(artifact["_meta"]["extraction_quality"], "poor")


class TestConfidenceDowngradedOnSingleSource(unittest.TestCase):
    def test_confidence_downgraded_on_single_source(self) -> None:
        """duplicate_count == 0 forces final confidence to 'low' regardless of model call."""
        from signal_stream.analysis_tools import _build_artifact

        signal = Signal(
            id="s", cluster_id="c", article_id="a", title="T", url="", source="",
            published_at="", score=60, urgency="medium", event_type="platform_shift",
            summary="", why_it_matters="", next_steps=[], matched_priorities=[],
            entities={}, duplicate_count=0,  # single-source story
        )
        item = {
            "id": "s", "score": 60,
            "short_summary": "x", "expanded_summary": "y", "entities": {},
            "mechanism": "Solid mechanism prose with enough detail.",
            "key_actors": [{"name": "A", "role": "r"}],
            "affected_parties": ["x"],
            "evidence_excerpts": [{"quote": "q", "source_offset": 0}],
            "confidence": "medium",
            "confidence_reason": "secondary coverage",
            # No truncation.
            "_truncation": {"was_truncated": False, "chars_total": 5000, "chars_sent": 5000},
        }
        artifact = _build_artifact(signal, item)
        self.assertEqual(artifact["model_confidence"], "medium")
        self.assertEqual(artifact["confidence"], "low")


class TestModelConfidenceKeptForTelemetry(unittest.TestCase):
    def test_model_confidence_kept_for_telemetry(self) -> None:
        """Model's self-reported confidence is preserved alongside the final value."""
        from signal_stream.analysis_tools import _build_artifact

        signal = Signal(
            id="s", cluster_id="c", article_id="a", title="T", url="", source="",
            published_at="", score=60, urgency="medium", event_type="platform_shift",
            summary="", why_it_matters="", next_steps=[], matched_priorities=[],
            entities={}, duplicate_count=0,  # forces downgrade
        )
        item = {
            "id": "s", "score": 60,
            "short_summary": "x", "expanded_summary": "y", "entities": {},
            "mechanism": "Solid mechanism.",
            "key_actors": [{"name": "A", "role": "r"}],
            "affected_parties": ["x"],
            "evidence_excerpts": [{"quote": "q", "source_offset": 0}],
            "confidence": "high",
            "confidence_reason": "primary source",
            "_truncation": {"was_truncated": False, "chars_total": 5000, "chars_sent": 5000},
        }
        artifact = _build_artifact(signal, item)
        # final confidence is downgraded by single-source rule
        self.assertEqual(artifact["confidence"], "low")
        # but the model's own call is kept for telemetry / future tuning
        self.assertEqual(artifact["model_confidence"], "high")


class TestArtifactWithMissingOptionalFields(unittest.TestCase):
    def test_thin_artifact_is_marked_partial_or_poor(self) -> None:
        """When the model omits optional fields, the artifact records missing_fields and adjusts extraction_quality."""
        from signal_stream.analysis_tools import _build_artifact

        signal = Signal(
            id="s", cluster_id="c", article_id="a", title="T", url="", source="",
            published_at="", score=60, urgency="medium", event_type="platform_shift",
            summary="", why_it_matters="", next_steps=[], matched_priorities=[],
            entities={}, duplicate_count=2,
        )
        # Model omitted all four optional structured fields.
        item = {
            "id": "s", "score": 60,
            "short_summary": "x", "expanded_summary": "y", "entities": {},
            "confidence": "medium",
            "_truncation": {"was_truncated": False, "chars_total": 5000, "chars_sent": 5000},
        }
        artifact = _build_artifact(signal, item)
        self.assertEqual(artifact["mechanism"], "")
        self.assertEqual(artifact["key_actors"], [])
        self.assertEqual(artifact["affected_parties"], [])
        self.assertEqual(artifact["evidence_excerpts"], [])
        self.assertEqual(set(artifact["_meta"]["missing_fields"]), {"mechanism", "key_actors", "affected_parties", "evidence_excerpts"})
        # 4 missing → poor extraction_quality
        self.assertEqual(artifact["_meta"]["extraction_quality"], "poor")
        # missing_count >= 3 triggers low confidence
        self.assertEqual(artifact["confidence"], "low")


class TestAnalystSchemaIncludesArtifactFields(unittest.TestCase):
    def test_optional_artifact_fields_not_required(self) -> None:
        """The new fields are present in properties but NOT in required, so missing values don't drop the signal."""
        from signal_stream.analysis_tools import ANALYST_REVIEW_SCHEMA

        item_schema = ANALYST_REVIEW_SCHEMA["properties"]["signals"]["items"]
        props = item_schema["properties"]
        # New fields exist in the schema.
        for field in ("mechanism", "key_actors", "affected_parties", "evidence_excerpts", "confidence", "confidence_reason"):
            self.assertIn(field, props)
        # But none of them are in required — Python validates post-hoc.
        required = item_schema.get("required", [])
        for field in ("mechanism", "key_actors", "affected_parties", "evidence_excerpts", "confidence", "confidence_reason"):
            self.assertNotIn(field, required)


class TestTruncationEventsSurfaced(unittest.TestCase):
    def test_review_returns_truncation_events_when_fallback_fires(self) -> None:
        """_review_signals_in_chunks emits one truncation_event per truncated signal."""
        huge_text = "Y" * 20000
        signal = _make_signal(0, body=huge_text)
        review_context = {signal.id: {"article_text": huge_text}}
        behavior = {"analyst_review_limit": 5, "analyst_review_batch_size": 1}

        call_count = 0

        def fake_chat_json(system, user, schema=None, *, temperature=0.0, required_fields=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_llm.last_error = "context length exceeded"
                return None
            mock_llm.last_error = None
            payload = json.loads(user)
            items = payload.get("signals", [])
            return {"signals": [{"id": s["id"], "score": 50, "short_summary": "s", "expanded_summary": "e", "entities": {}} for s in items]}

        mock_llm = MagicMock()
        mock_llm.chat_json.side_effect = fake_chat_json
        mock_llm.last_error = None

        reviewed, truncation_events = _review_signals_in_chunks(
            mock_llm, "sys", [signal], behavior, review_context
        )

        self.assertEqual(len(truncation_events), 1)
        self.assertEqual(truncation_events[0]["signal_id"], signal.id)
        self.assertEqual(truncation_events[0]["chars_total"], 20000)
        self.assertLessEqual(truncation_events[0]["chars_sent"], 8000)


if __name__ == "__main__":
    unittest.main()
