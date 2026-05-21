"""Wave 4 tests — top-40, full-page fetch, 1-per-request Groq, required_fields, exec summary."""

from __future__ import annotations

import json
import sys
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from signal_stream.analysis_tools import (
    _apply_analyst_mode,
    _fetch_full_pages_for_top_n,
    _review_signals_in_chunks,
    _signal_json,
    analyze_articles,
)
from signal_stream.agent_runtime import _signal_from_json
from signal_stream.editor_tools import generate_briefing_from_artifacts
from signal_stream.models import Article, Cluster, Signal, SignalConfig, SignalDraft, stable_id, BrainConfig, Priority
from signal_stream.source_tools import fetch_full_article_page
from signal_stream.storage import SignalStorage


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
            _, updated_ctx, _ = _fetch_full_pages_for_top_n(signals, review_context, top_n=1)

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
            _, updated_ctx, _ = _fetch_full_pages_for_top_n(signals, review_context, top_n=1)

        self.assertEqual(updated_ctx[signals[0].id]["article_text"], original_text)


# ---------------------------------------------------------------------------
# Test: oversized article truncated with warning
# ---------------------------------------------------------------------------

class TestOversizedArticleTruncatedWithWarning(unittest.TestCase):
    def test_oversized_article_truncated_with_warning(self) -> None:
        """An article > retry_max_chars triggers a smart-trim log and retry."""
        from signal_stream.analysis_tools import _chat_json_with_truncation_fallback

        # Must exceed the default retry_max_chars (36 000 = 72 000 // 2) to trigger trim.
        huge_text = "X" * 80000
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
        self.assertIn("trimmed", stderr_out.lower())
        # On retry, article_text must be ≤ the default retry_max_chars (72 000 // 2 = 36 000).
        retry_payload = json.loads(call_args[-1])
        for item in retry_payload.get("signals", []):
            self.assertLessEqual(len(str(item.get("article_text", ""))), 36000)
        self.assertIsNotNone(result)
        # Truncation info should carry the post-truncation char count.
        self.assertTrue(trunc_info["was_truncated"])
        self.assertEqual(trunc_info["chars_total"], 80000)
        self.assertLessEqual(trunc_info["chars_sent"], 36000)


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
            updated_signals, _, _ = _apply_analyst_mode(
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
    def test_artifact_fields_listed_required_for_documentation(self) -> None:
        """Artifact fields are documented as required in the schema, but enforcement
        happens at runtime in _validate_analyst_item (Groq's response_format=json_object
        does not enforce nested required arrays). The schema 'required' list is
        documentation for human readers + the prompt."""
        from signal_stream.analysis_tools import ANALYST_REVIEW_SCHEMA

        item_schema = ANALYST_REVIEW_SCHEMA["properties"]["signals"]["items"]
        props = item_schema["properties"]
        required = item_schema.get("required", [])
        for field in ("mechanism", "key_actors", "affected_parties", "evidence_excerpts", "confidence", "confidence_reason"):
            self.assertIn(field, props)
            self.assertIn(field, required)


class TestTruncationEventsSurfaced(unittest.TestCase):
    def test_review_returns_truncation_events_when_fallback_fires(self) -> None:
        """_review_signals_in_chunks emits one truncation_event per truncated signal."""
        # Must exceed the default max_article_chars (72 000) so _review_payload pre-trims,
        # then exceed retry_max_chars (36 000) so the fallback retry also trims.
        huge_text = "Y" * 80000
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

        reviewed, truncation_events, _ = _review_signals_in_chunks(
            mock_llm, "sys", [signal], behavior, review_context
        )

        self.assertEqual(len(truncation_events), 1)
        self.assertEqual(truncation_events[0]["signal_id"], signal.id)
        # chars_total reflects text after _review_payload pre-trim (≤ 72 000).
        self.assertLessEqual(truncation_events[0]["chars_total"], 72000)
        # chars_sent reflects the retry trim (≤ retry_max_chars = 36 000).
        self.assertLessEqual(truncation_events[0]["chars_sent"], 36000)


# ---------------------------------------------------------------------------
# Phase 3 test helpers
# ---------------------------------------------------------------------------

def _make_config_minimal() -> SignalConfig:
    """Minimal SignalConfig that satisfies _apply_analyst_mode without loading TOML."""
    return SignalConfig(
        name="Test", organization="Test", audience="Reader", mission="Test",
        competitors=[], markets=[], priorities=[Priority("AI")], sources=[],
        storage_path=":memory:", output_dir=".", brain=BrainConfig(),
    )


def _valid_groq_response(signals: list[Signal]) -> dict:
    """Groq response payload that passes schema validation for all given signals."""
    return {
        "signals": [
            {
                "id": s.id,
                "score": 70,
                "short_summary": "A model-written summary.",
                "expanded_summary": "An expanded paragraph.",
                "entities": {},
                "mechanism": "A clear mechanism description with enough detail.",
                "key_actors": [],
                "affected_parties": [],
                "evidence_excerpts": [],
                "confidence": "medium",
                "confidence_reason": "Corroborated coverage.",
            }
            for s in signals
        ]
    }


# ---------------------------------------------------------------------------
# Test 1: Second-pass retry succeeds after rate-limit on first pass
# ---------------------------------------------------------------------------

class TestSecondPassRetrySucceedsAfterRateLimit(unittest.TestCase):
    def test_retry_succeeds(self) -> None:
        """First-pass Groq call fails with rate_limit → signal gets pending_retry → second pass succeeds."""
        signal = _make_signal(0, score=80)
        review_context = _make_review_context([signal])
        config = _make_config_minimal()

        call_count = [0]

        class FakeBrain:
            def __init__(self, cfg):
                self.last_error = None

            def available(self):
                return True

            def chat_json(self, system, user, schema=None, **kwargs):
                call_count[0] += 1
                payload = json.loads(user)
                sigs = payload.get("signals", [])
                if call_count[0] == 1:
                    self.last_error = "rate limit exceeded"
                    return None
                self.last_error = None
                return _valid_groq_response([SimpleNamespace(id=s["id"]) for s in sigs])

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, failures = _apply_analyst_mode(
                [signal], config, "hybrid", "prompt",
                {"analyst_full_review": True, "analyst_review_limit": 5,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        self.assertEqual(result[0].analyst_status, "success")
        # First pass + retry = 2 analyst-level attempts (chat_json was called twice).
        self.assertEqual(result[0].analyst_attempt_count, 2)
        self.assertIsNotNone(result[0].analyst_artifact)
        # No failures in the output because the retry succeeded.
        self.assertEqual(failures, [])


# ---------------------------------------------------------------------------
# Test 2: Rate-limit exhausts both passes → failed
# ---------------------------------------------------------------------------

class TestRateLimitExhaustsBothPasses(unittest.TestCase):
    def test_both_passes_fail(self) -> None:
        """When Groq returns rate_limit on both passes the signal must end as failed."""
        signal = _make_signal(0, score=80)
        review_context = _make_review_context([signal])
        config = _make_config_minimal()

        class FakeBrain:
            def __init__(self, cfg):
                self.last_error = "rate limit exceeded"

            def available(self):
                return True

            def chat_json(self, system, user, schema=None, **kwargs):
                return None

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, failures = _apply_analyst_mode(
                [signal], config, "hybrid", "prompt",
                {"analyst_full_review": True, "analyst_review_limit": 5,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        self.assertEqual(result[0].analyst_status, "failed")
        self.assertEqual(result[0].analyst_error_type, "rate_limit")
        self.assertIsNone(result[0].analyst_artifact)
        self.assertEqual(len(failures), 1)


# ---------------------------------------------------------------------------
# Test 3: Malformed JSON gets failed status without a retry attempt
# ---------------------------------------------------------------------------

class TestMalformedJsonNoRetry(unittest.TestCase):
    def test_invalid_json_fails_immediately(self) -> None:
        """A json-decode error produces failed+invalid_json immediately (no retry)."""
        signal = _make_signal(0, score=80)
        review_context = _make_review_context([signal])
        behavior = {"analyst_review_limit": 5, "analyst_review_batch_size": 1}

        call_count = [0]

        class FakeLLM:
            last_error = "json decode error: unexpected token"

            def chat_json(self, system, user, schema=None, **kwargs):
                call_count[0] += 1
                return None

        llm = FakeLLM()
        _, _, statuses = _review_signals_in_chunks(llm, "sys", [signal], behavior, review_context)

        # json errors classify as failed (no pending_retry → no retry pass attempted).
        self.assertEqual(statuses[signal.id]["status"], "failed")
        self.assertEqual(statuses[signal.id]["error_type"], "invalid_json")
        # Only one call because invalid_json doesn't trigger the truncation retry.
        self.assertEqual(call_count[0], 1)


# ---------------------------------------------------------------------------
# Test 4: Short-body annotation appears in error_message when review also fails
# ---------------------------------------------------------------------------

class TestExtractionShortBodyAnnotated(unittest.TestCase):
    def test_short_body_appears_in_error_message(self) -> None:
        """When full-page extraction is short AND Groq fails, error_message includes 'short_body'."""
        signal = _make_signal(0, score=70, url="https://example.com/article")
        review_context = _make_review_context([signal])
        behavior = {"analyst_review_limit": 5, "analyst_review_batch_size": 1}

        class FakeLLM:
            last_error = "rate limit exceeded"

            def chat_json(self, system, user, schema=None, **kwargs):
                return None

        llm = FakeLLM()
        short_body_ids = {signal.id}
        _, _, statuses = _review_signals_in_chunks(
            llm, "sys", [signal], behavior, review_context, short_body_ids=short_body_ids
        )

        err_msg = statuses[signal.id].get("error_message", "")
        self.assertIn("short_body", err_msg)
        self.assertEqual(statuses[signal.id]["error_type"], "rate_limit")


# ---------------------------------------------------------------------------
# Test 5: No pending_retry escapes _apply_analyst_mode
# ---------------------------------------------------------------------------

class TestNoPendingRetryEscapesAnalyzeArticles(unittest.TestCase):
    def test_no_pending_retry_in_output(self) -> None:
        """Terminal invariant: _apply_analyst_mode must not return any pending_retry signals."""
        signals = [_make_signal(i, score=80 - i) for i in range(3)]
        review_context = _make_review_context(signals)
        config = _make_config_minimal()

        class FakeBrain:
            def __init__(self, cfg):
                self.last_error = "rate limit exceeded"

            def available(self):
                return True

            def chat_json(self, system, user, schema=None, **kwargs):
                return None

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, _ = _apply_analyst_mode(
                signals, config, "hybrid", "prompt",
                {"analyst_full_review": True, "analyst_review_limit": 5,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        pending_retry = [s for s in result if s.analyst_status == "pending_retry"]
        self.assertEqual(pending_retry, [], "pending_retry must never escape _apply_analyst_mode")


# ---------------------------------------------------------------------------
# Test 6: analyst_status='success' iff analyst_artifact is non-null
# ---------------------------------------------------------------------------

class TestSuccessRequiresArtifact(unittest.TestCase):
    def test_success_iff_artifact_present(self) -> None:
        """North-star invariant: for every output signal, success↔artifact and failure↔no-artifact."""
        s_ok = _make_signal(0, score=90)
        s_fail = _make_signal(1, score=80)
        review_context = {**_make_review_context([s_ok]), **_make_review_context([s_fail])}
        config = _make_config_minimal()
        call_count = [0]

        class FakeBrain:
            def __init__(self, cfg):
                self.last_error = None

            def available(self):
                return True

            def chat_json(self, system, user, schema=None, **kwargs):
                call_count[0] += 1
                payload = json.loads(user)
                sigs = payload.get("signals", [])
                # First call (s_ok) succeeds; all others fail.
                if call_count[0] == 1:
                    self.last_error = None
                    return _valid_groq_response([SimpleNamespace(id=s["id"]) for s in sigs])
                self.last_error = "rate limit exceeded"
                return None

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, _ = _apply_analyst_mode(
                [s_ok, s_fail], config, "hybrid", "prompt",
                {"analyst_full_review": True, "analyst_review_limit": 5,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        for sig in result:
            if sig.analyst_status == "success":
                self.assertIsNotNone(sig.analyst_artifact, f"{sig.id}: success with NULL artifact")
            else:
                self.assertIsNone(sig.analyst_artifact, f"{sig.id}: non-success with artifact present")


# ---------------------------------------------------------------------------
# Test 7: analyst_failures only contains selected signals that failed
# ---------------------------------------------------------------------------

class TestActivityEventLoggedPerFailedSelectedSignal(unittest.TestCase):
    def test_analyst_failures_per_selected_signal_only(self) -> None:
        """analyst_failures contains exactly one entry per failed selected signal.

        Signals outside the review limit (not selected) must NOT appear in the list.
        """
        selected = _make_signal(0, score=90)
        unselected = _make_signal(1, score=30)
        review_context = {**_make_review_context([selected]), **_make_review_context([unselected])}
        config = _make_config_minimal()

        class FakeBrain:
            def __init__(self, cfg):
                self.last_error = "rate limit exceeded"

            def available(self):
                return True

            def chat_json(self, system, user, schema=None, **kwargs):
                return None

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, analyst_failures = _apply_analyst_mode(
                [selected, unselected], config, "hybrid", "prompt",
                # review_limit=1 so only `selected` is within scope.
                {"analyst_full_review": True, "analyst_review_limit": 1,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        failed_ids = {f["signal_id"] for f in analyst_failures}
        self.assertIn(selected.id, failed_ids)
        self.assertNotIn(unselected.id, failed_ids)


# ---------------------------------------------------------------------------
# Test 8: API exposes new fields via _hydrate_signal_row round-trip
# ---------------------------------------------------------------------------

class TestApiExposesNewFields(unittest.TestCase):
    def test_new_fields_appear_in_hydrated_row(self) -> None:
        """New analyst_status columns must flow through _hydrate_signal_row into the API dict."""
        import tempfile, os

        # Build a signal object with non-default values for the new columns.
        sig = _make_signal(0, score=75)
        sig.analyst_status = "failed"
        sig.analyst_error_type = "rate_limit"
        sig.analyst_error_message = "rate limit exceeded | short_body"
        sig.analyst_attempt_count = 2
        sig.analyst_last_attempt_at = "2026-01-01T12:00:00Z"
        sig.analyst_artifact = None

        # Persist via storage round-trip using a temp file DB.
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = SignalStorage(db_path)
            storage.init()
            storage.save_run([
                Article.from_fields("Test", sig.title, url=sig.url, body="body"),
            ], [sig], cluster_count=1, output_path=".", started_at="2026-01-01T11:00:00Z")

            hydrated = storage.get_signal(sig.id)
        finally:
            os.unlink(db_path)

        self.assertIsNotNone(hydrated)
        self.assertEqual(hydrated["analyst_status"], "failed")
        self.assertEqual(hydrated["analyst_error_type"], "rate_limit")
        self.assertIn("short_body", hydrated["analyst_error_message"])
        self.assertEqual(hydrated["analyst_attempt_count"], 2)
        self.assertEqual(hydrated["analyst_last_attempt_at"], "2026-01-01T12:00:00Z")


# ---------------------------------------------------------------------------
# Test 9: Editor excludes failed signals from briefing evidence
# ---------------------------------------------------------------------------

class TestEditorExcludesFailedFromEvidence(unittest.TestCase):
    def test_failed_signal_not_in_briefing_payload(self) -> None:
        """generate_briefing_from_artifacts must only pass success+artifact signals to Groq."""
        good = _make_signal(0, score=90)
        good.analyst_status = "success"
        good.analyst_artifact = {"mechanism": "Good mechanism with plenty of detail.", "key_actors": [], "affected_parties": [], "evidence_excerpts": [], "confidence": "high", "confidence_reason": "primary"}

        bad = _make_signal(1, score=70)
        bad.analyst_status = "failed"
        bad.analyst_artifact = None

        received_payload = {}

        class FakeBrain:
            last_error = ""
            def chat_json(self, system, user, schema=None, **kwargs):
                received_payload.update(json.loads(user))
                return {
                    "headline": "H",
                    "summary": "S",
                    "key_takeaways": ["k"],
                    "briefing_paragraphs": [
                        {"heading": "T", "body": "p", "bullets": ["b"], "signal_ids": [good.id]}
                    ],
                }

        brain = FakeBrain()
        generate_briefing_from_artifacts([good, bad], brain, "prompt", {})

        # When at least one signal has an artifact, only those signals appear in
        # the payload — the failed signal is filtered out. (The v2 block uses
        # signal_id, not id, as the field name.)
        sent_ids = {s["signal_id"] for s in received_payload.get("signals", [])}
        self.assertIn(good.id, sent_ids)
        self.assertNotIn(bad.id, sent_ids)


# ---------------------------------------------------------------------------
# Test 10: Signal JSON serialisation round-trips all new fields
# ---------------------------------------------------------------------------

class TestSignalJsonRoundTrip(unittest.TestCase):
    def test_new_fields_survive_worker_boundary(self) -> None:
        """Every new analyst_status field must survive _signal_json → _signal_from_json."""
        sig = _make_signal(0, score=65)
        sig.analyst_status = "failed"
        sig.analyst_error_type = "invalid_json"
        sig.analyst_error_message = "json decode error"
        sig.analyst_attempt_count = 1
        sig.analyst_last_attempt_at = "2026-05-14T10:00:00Z"
        sig.analyst_artifact = None

        serialized = _signal_json(sig)
        recovered = _signal_from_json(serialized)

        self.assertEqual(recovered.analyst_status, "failed")
        self.assertEqual(recovered.analyst_error_type, "invalid_json")
        self.assertEqual(recovered.analyst_error_message, "json decode error")
        self.assertEqual(recovered.analyst_attempt_count, 1)
        self.assertEqual(recovered.analyst_last_attempt_at, "2026-05-14T10:00:00Z")
        self.assertIsNone(recovered.analyst_artifact)


# ---------------------------------------------------------------------------
# Test 11: Orphan sweep finalizes transient states from non-complete runs
# ---------------------------------------------------------------------------

class TestOrphanSweepFinalizes(unittest.TestCase):
    def test_pending_retry_from_interrupted_run_becomes_failed(self) -> None:
        """A pending_retry signal from an interrupted run must flip to failed on next init()."""
        import tempfile, os, sqlite3 as _sqlite3

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = SignalStorage(db_path)
            storage.init()

            now_ts = "2026-01-01T12:00:00Z"
            earlier_ts = "2026-01-01T11:59:59Z"

            # Insert an interrupted agent_run and a signal in pending_retry state.
            with _sqlite3.connect(db_path) as conn:
                conn.execute(
                    "insert into agent_runs (id, goal, status, started_at, completed_at) "
                    "values (?, ?, ?, ?, ?)",
                    ("run_orphan", "test", "interrupted", earlier_ts, now_ts),
                )
                conn.execute(
                    "insert into signals "
                    "(id, cluster_id, article_id, title, url, source, published_at, "
                    "score, urgency, event_type, summary, analyst_status, "
                    "analyst_last_attempt_at, created_at) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("sig_orphan", "cl", "art_orphan", "Orphan", "", "Src", "",
                     50, "medium", "general_signal", "body", "pending_retry",
                     earlier_ts, earlier_ts),  # analyst_last_attempt_at set → sweep should flip
                )

            # Re-running init() triggers the orphan sweep.
            storage.init()

            with _sqlite3.connect(db_path) as conn:
                conn.row_factory = _sqlite3.Row
                row = conn.execute("select analyst_status from signals where id = 'sig_orphan'").fetchone()

            self.assertEqual(row["analyst_status"], "failed")
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Test 12: analyst_full_review=False marks selected signals as skipped
# ---------------------------------------------------------------------------

class TestFullReviewDisabledMarksSkipped(unittest.TestCase):
    def test_skipped_when_full_review_disabled(self) -> None:
        """When analyst_full_review is False, selected signals get analyst_status='skipped'."""
        signals = [_make_signal(i, score=70 - i) for i in range(3)]
        review_context = _make_review_context(signals)
        config = _make_config_minimal()

        class FakeBrain:
            def __init__(self, cfg):
                pass

            def available(self):
                return True

        import signal_stream.analysis_tools as at
        original = at.BrainClient
        at.BrainClient = FakeBrain
        try:
            result, _, failures = _apply_analyst_mode(
                signals, config, "hybrid", "prompt",
                {"analyst_full_review": False, "analyst_review_limit": 5,
                 "analyst_review_batch_size": 1, "analyst_retry_max_attempts": 1,
                 "model_score_adjustment_limit": 20, "summary_mode": "short_expanded",
                 "entity_extraction": "hybrid"},
                review_context,
            )
        finally:
            at.BrainClient = original

        for sig in result[:3]:
            self.assertEqual(sig.analyst_status, "skipped")
            self.assertIsNone(sig.analyst_error_type)
            self.assertEqual(sig.analyst_attempt_count, 0)
        self.assertEqual(failures, [])


# ---------------------------------------------------------------------------
# Test 13: Unknown error format preserved verbatim in error_message
# ---------------------------------------------------------------------------

class TestRawErrorMessagePreservedOnUnknown(unittest.TestCase):
    def test_raw_error_in_error_message_on_unknown_format(self) -> None:
        """An unrecognised Groq error string must be stored verbatim so format drift is debuggable."""
        signal = _make_signal(0, score=60)
        review_context = _make_review_context([signal])
        behavior = {"analyst_review_limit": 5, "analyst_review_batch_size": 1}
        raw_error = "some new groq error format we haven't seen"

        class FakeLLM:
            last_error = raw_error

            def chat_json(self, system, user, schema=None, **kwargs):
                return None

        llm = FakeLLM()
        _, _, statuses = _review_signals_in_chunks(llm, "sys", [signal], behavior, review_context)

        self.assertEqual(statuses[signal.id]["error_type"], "unknown")
        self.assertIn(raw_error[:50], statuses[signal.id].get("error_message", ""))


# ---------------------------------------------------------------------------
# Bug-fix tests — verified against the post-PR-1 code before patching
# ---------------------------------------------------------------------------

# Bug 1: Orphan sweep must NOT flip pending signals that never had a review attempt
class TestOrphanSweepNoFalsePositives(unittest.TestCase):
    def test_pending_no_attempt_not_flipped_by_orphan_sweep(self) -> None:
        """pending signals with analyst_last_attempt_at=NULL must survive the sweep unchanged.

        Regression guard: the orphan sweep's date-join previously matched backfill-assigned
        pending signals (analyst_last_attempt_at=NULL) whenever any non-complete agent_run
        had a completed_at >= the signal's created_at. On a DB with 403 pending signals and
        24 non-complete runs spanning the date range, this flipped 375 rows to 'failed'.

        The fix gates the sweep on analyst_last_attempt_at IS NOT NULL, so only signals that
        actually started a Phase-3 review attempt can be swept as orphans.
        """
        import tempfile, os, sqlite3 as _sqlite3

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = SignalStorage(db_path)
            storage.init()

            sig_ts = "2026-01-01T10:00:00Z"
            # Non-complete run whose completed_at is >= signal's created_at — triggers the bad join.
            interrupted_ts = "2026-01-01T12:00:00Z"

            with _sqlite3.connect(db_path) as conn:
                conn.execute(
                    "insert into agent_runs (id, goal, status, started_at, completed_at) "
                    "values (?, ?, ?, ?, ?)",
                    ("run_interrupted", "test", "interrupted", sig_ts, interrupted_ts),
                )
                conn.execute(
                    "insert into signals "
                    "(id, cluster_id, article_id, title, url, source, published_at, "
                    "score, urgency, event_type, summary, analyst_status, "
                    "analyst_last_attempt_at, created_at) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    # analyst_last_attempt_at=NULL → backfill/code-mode row, never attempted
                    ("sig_backfill", "cl", "art_backfill", "Backfill Signal", "", "Src", "",
                     80, "medium", "general_signal", "body", "pending",
                     None, sig_ts),
                )

            # Orphan sweep fires on re-init.
            storage.init()

            with _sqlite3.connect(db_path) as conn:
                conn.row_factory = _sqlite3.Row
                row = conn.execute(
                    "select analyst_status from signals where id = 'sig_backfill'"
                ).fetchone()

            self.assertEqual(
                row["analyst_status"], "pending",
                "A pending signal with no attempt timestamp must not be flipped by the orphan sweep",
            )
        finally:
            os.unlink(db_path)


# Bug 2: Editor worker must preserve analyst_status through JSON boundary
class TestEditorWorkerPreservesAnalystStatus(unittest.TestCase):
    def test_success_signal_evidence_reaches_groq_via_worker(self) -> None:
        """analyst_status='success' must survive handle_task rehydration so the signal is used as evidence.

        Regression guard: handle_task('editor', ...) previously omitted the 5 Phase-3
        fields from its _Signal(...) constructor, defaulting analyst_status to 'pending'
        and causing _is_analyst_evidence to filter every signal out of evidence.
        """
        from signal_stream.worker import handle_task

        good = _make_signal(0, score=90)
        good.analyst_status = "success"
        good.analyst_artifact = {
            "mechanism": "A detailed mechanism with plenty of text to satisfy validation.",
            "key_actors": [],
            "affected_parties": [],
            "evidence_excerpts": [],
            "confidence": "high",
            "confidence_reason": "primary",
        }

        # Serialize as agent_runtime does when dispatching to the editor worker.
        good_dict = _signal_json(good)

        received_payload: dict = {}

        class FakeBrain:
            def __init__(self, cfg):
                pass

            last_error = ""

            def chat_json(self, system, user, schema=None, **kwargs):
                received_payload.update(json.loads(user))
                return {
                    "headline": "H",
                    "summary": "S",
                    "key_takeaways": ["k"],
                    "briefing_paragraphs": [
                        {"heading": "T", "body": "paragraph", "bullets": [], "signal_ids": [good.id]},
                    ],
                }

        config = _make_config_minimal()
        task = {
            "task_id": "t_editor",
            "type": "generate_briefing",
            "payload": {"signals": [good_dict], "run_context": {}},
        }

        with patch("signal_stream.worker.BrainClient", FakeBrain):
            handle_task("editor", config, MagicMock(), {"editor": "You are the editor."}, {}, {}, task)

        # v2 block uses signal_id, not id.
        sent_ids = {s["signal_id"] for s in received_payload.get("signals", [])}
        self.assertIn(
            good.id, sent_ids,
            "Success signal must appear in Groq evidence payload after worker-boundary rehydration",
        )


# Bug 3: list_signals_paged and list_signals_executive must expose new analyst columns
class TestPagedAndExecutiveSelectsExposeAnalystStatus(unittest.TestCase):
    def _persist_failed_signal(self, storage: "SignalStorage") -> "Signal":
        sig = _make_signal(0, score=65)
        sig.analyst_status = "failed"
        sig.analyst_error_type = "rate_limit"
        sig.analyst_error_message = "exceeded"
        sig.analyst_attempt_count = 2
        sig.analyst_last_attempt_at = "2026-01-01T12:00:00Z"
        sig.analyst_artifact = None
        storage.save_run(
            [Article.from_fields("Src", sig.title, url=sig.url, body="body")],
            [sig],
            cluster_count=1,
            output_path=".",
            started_at="2026-01-01T11:00:00Z",
        )
        return sig

    def test_list_signals_paged_returns_analyst_status(self) -> None:
        """list_signals_paged must include analyst_status in each returned row."""
        import tempfile, os

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = SignalStorage(db_path)
            storage.init()
            sig = self._persist_failed_signal(storage)

            page = storage.list_signals_paged()
            items = page["items"]
        finally:
            os.unlink(db_path)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].get("analyst_status"), "failed",
            "list_signals_paged must return analyst_status from the DB, not the hydration default",
        )

    def test_list_signals_executive_returns_analyst_status(self) -> None:
        """list_signals_executive must include analyst_status in each returned row."""
        import tempfile, os

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            storage = SignalStorage(db_path)
            storage.init()
            sig = self._persist_failed_signal(storage)

            items = storage.list_signals_executive()
        finally:
            os.unlink(db_path)

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].get("analyst_status"), "failed",
            "list_signals_executive must return analyst_status from the DB, not the hydration default",
        )


# Bug 6: _signal_block must suppress raw body for failed/pending signals
class TestSignalBlockSuppressesBodyForFailed(unittest.TestCase):
    def test_failed_signal_raw_body_not_in_block(self) -> None:
        """_signal_block must not emit short_summary/expanded_summary for failed signals.

        Regression guard: the per-run .md digest file previously printed raw RSS bodies
        (e.g. cookie banners) for signals that failed Groq review, because _signal_block
        used short_summary unconditionally.
        """
        from signal_stream.analysis_tools import _signal_block

        raw_body = "ACCEPT ALL COOKIES to continue browsing. Privacy policy. GDPR consent."
        sig = _make_signal(0, score=60, body=raw_body)
        sig.short_summary = raw_body
        sig.expanded_summary = raw_body
        sig.analyst_status = "failed"
        sig.analyst_error_type = "rate_limit"

        block = "\n".join(_signal_block(sig))

        self.assertNotIn(
            "ACCEPT ALL COOKIES", block,
            "_signal_block must suppress raw RSS body for failed signals",
        )
        self.assertIn(
            "Analyst review unavailable", block,
            "_signal_block must include a fallback notice for failed signals",
        )


if __name__ == "__main__":
    unittest.main()
