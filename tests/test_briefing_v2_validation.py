"""Tests for v2 briefing schema validators, analyst-item coercion, and normalize-on-read.

Covers the contract-first refactor: runtime validators enforce shape because
Groq's response_format=json_object only enforces valid JSON, not structure.
"""

from __future__ import annotations

import unittest
from typing import Any


# ── Helpers ────────────────────────────────────────────────────────────────────

class FakeBrainClient:
    """In-memory stand-in for BrainClient — returns a queued response per call."""

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.last_error: str = ""
        self.last_response_text: str = ""
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, schema=None, *, temperature: float = 0.0, required_fields=None):
        self.calls.append({"system": system, "user": user, "required_fields": required_fields})
        if not self._responses:
            self.last_error = "no more queued responses"
            return None
        resp = self._responses.pop(0)
        if resp is None:
            self.last_error = "queued None response"
        return resp


def _signal(id_: str = "sig1", **overrides):
    """Build a minimal Signal for editor tests."""
    from signal_stream.models import Signal
    base = dict(
        id=id_,
        cluster_id="cluster1",
        article_id="art1",
        title="Test Signal",
        url="https://example.com",
        source="TestSource",
        published_at="2026-05-15T00:00:00+00:00",
        score=70,
        urgency="high",
        event_type="product_launch",
        summary="Short summary text.",
        why_it_matters="",
        next_steps=[],
        matched_priorities=[],
        entities={},
        duplicate_count=0,
        score_breakdown=[],
        short_summary="Short summary text describing the signal substance.",
        expanded_summary="Expanded summary text providing additional context.",
        image_url="",
        icon_key="",
        scout_note="",
        relevance_label="",
        analyst_artifact=None,
        analyst_status="pending",
    )
    base.update(overrides)
    return Signal(**base)


# ── 1. Analyst-item validator ─────────────────────────────────────────────────

class AnalystValidatorTests(unittest.TestCase):
    def test_complete_response_passes_through(self):
        from signal_stream.analysis_tools import _validate_analyst_item
        item = {
            "id": "sig1",
            "score": 70,
            "short_summary": "Short.",
            "expanded_summary": "Expanded.",
            "entities": {"companies": ["Acme"]},
            "mechanism": "Acme raised limits 10x.",
            "key_actors": [{"name": "Acme", "role": "vendor"}],
            "affected_parties": ["enterprise buyers"],
            "evidence_excerpts": [{"quote": "We raised limits 10x.", "source_offset": 100}],
            "confidence": "high",
            "confidence_reason": "Primary source quote.",
        }
        validated, missing = _validate_analyst_item(item)
        self.assertEqual(missing, [])
        self.assertEqual(validated["confidence"], "high")
        self.assertEqual(validated["mechanism"], "Acme raised limits 10x.")

    def test_missing_artifact_fields_get_coerced(self):
        from signal_stream.analysis_tools import _validate_analyst_item
        item = {
            "id": "sig1",
            "score": 70,
            "short_summary": "Short.",
            "expanded_summary": "Expanded.",
            "entities": {},
            # mechanism/key_actors/affected_parties/evidence_excerpts/confidence missing
        }
        validated, missing = _validate_analyst_item(item)
        self.assertIn("mechanism", missing)
        self.assertIn("key_actors", missing)
        self.assertIn("affected_parties", missing)
        self.assertIn("evidence_excerpts", missing)
        self.assertIn("confidence", missing)
        self.assertEqual(validated["mechanism"], "")
        self.assertEqual(validated["key_actors"], [])
        self.assertEqual(validated["affected_parties"], [])
        self.assertEqual(validated["evidence_excerpts"], [])
        self.assertEqual(validated["confidence"], "low")

    def test_key_actors_as_string_coerced_to_empty(self):
        from signal_stream.analysis_tools import _validate_analyst_item
        item = {
            "id": "sig1",
            "score": 70,
            "short_summary": "Short.",
            "expanded_summary": "Expanded.",
            "entities": {},
            "mechanism": "x" * 50,
            "key_actors": "OpenAI, Anthropic",  # wrong shape — string not array
            "affected_parties": [],
            "evidence_excerpts": [],
            "confidence": "medium",
            "confidence_reason": "ok",
        }
        validated, missing = _validate_analyst_item(item)
        self.assertEqual(validated["key_actors"], [])
        self.assertIn("key_actors", missing)

    def test_missing_core_field_raises(self):
        from signal_stream.analysis_tools import _validate_analyst_item
        item = {"score": 70, "short_summary": "ok", "expanded_summary": "ok"}  # no id
        with self.assertRaises(ValueError):
            _validate_analyst_item(item)

    def test_invalid_confidence_defaulted_low(self):
        from signal_stream.analysis_tools import _validate_analyst_item
        item = {
            "id": "sig1",
            "score": 70,
            "short_summary": "Short.",
            "expanded_summary": "Expanded.",
            "entities": {},
            "mechanism": "ok",
            "key_actors": [{"name": "A", "role": "B"}],
            "affected_parties": ["x"],
            "evidence_excerpts": [{"quote": "x", "source_offset": 0}],
            "confidence": "EXTREME",
            "confidence_reason": "",
        }
        validated, missing = _validate_analyst_item(item)
        self.assertEqual(validated["confidence"], "low")
        self.assertIn("confidence", missing)


# ── 2. Editor-briefing validator ──────────────────────────────────────────────

class EditorBriefingValidatorTests(unittest.TestCase):
    def test_well_formed_v2_response_passes(self):
        from signal_stream.editor_tools import _validate_editor_briefing
        raw = {
            "headline": "OpenAI ships new model.",
            "summary": "Today brought multiple shifts in AI.",
            "key_takeaways": ["Takeaway one.", "Takeaway two."],
            "insights": ["Insight one."],
            "briefing_paragraphs": [
                {"heading": "Models", "body": "Body.", "bullets": ["b1"], "signal_ids": ["sig1"]},
            ],
            "key_themes": [{"label": "AI safety", "summary": "x", "signal_ids": ["sig1"]}],
            "cross_signal_narrative": "Closing synthesis.",
            "watch_items": ["Watch this."],
        }
        v = _validate_editor_briefing(raw)
        self.assertEqual(v["headline"], "OpenAI ships new model.")
        self.assertEqual(len(v["briefing_paragraphs"]), 1)
        self.assertEqual(v["briefing_paragraphs"][0]["heading"], "Models")
        self.assertEqual(v["key_takeaways"], ["Takeaway one.", "Takeaway two."])

    def test_off_schema_response_rescued_when_possible(self):
        """The exact bug-day Groq response: title/summary/insights/key_takeaways/recommendations."""
        from signal_stream.editor_tools import _validate_editor_briefing
        # This shape lacks headline AND briefing_paragraphs; the validator
        # itself returns None (caller's job to attempt rescue).
        raw = {
            "title": "Daily Briefing",
            "summary": "12 signals today.",
            "insights": ["i1"],
            "key_takeaways": ["k1"],
            "recommendations": ["r1"],
        }
        self.assertIsNone(_validate_editor_briefing(raw))

    def test_legacy_string_paragraphs_get_wrapped(self):
        from signal_stream.editor_tools import _validate_editor_briefing
        raw = {
            "headline": "Old headline",
            "briefing_paragraphs": ["Old paragraph one.", "Old paragraph two."],
        }
        v = _validate_editor_briefing(raw)
        self.assertEqual(len(v["briefing_paragraphs"]), 2)
        # Strings become objects with body filled, heading/bullets empty.
        self.assertEqual(v["briefing_paragraphs"][0]["body"], "Old paragraph one.")
        self.assertEqual(v["briefing_paragraphs"][0]["heading"], "")
        self.assertEqual(v["briefing_paragraphs"][0]["bullets"], [])

    def test_no_headline_no_paragraphs_returns_none(self):
        from signal_stream.editor_tools import _validate_editor_briefing
        self.assertIsNone(_validate_editor_briefing({"summary": "ok"}))
        self.assertIsNone(_validate_editor_briefing({}))
        self.assertIsNone(_validate_editor_briefing(None))


# ── 3. normalize-on-read (handles real DB shapes) ─────────────────────────────

class NormalizeBriefingTests(unittest.TestCase):
    def test_v2_briefing_passes_through_unchanged(self):
        from signal_stream.editor_tools import normalize_briefing_for_read, BRIEFING_SCHEMA_VERSION
        b = {"schema_version": BRIEFING_SCHEMA_VERSION, "headline": "x"}
        self.assertEqual(normalize_briefing_for_read(b), b)

    def test_v1_flat_string_paragraphs_normalized(self):
        from signal_stream.editor_tools import normalize_briefing_for_read, BRIEFING_SCHEMA_VERSION
        v1 = {
            "headline": "Yesterday's headline",
            "briefing_paragraphs": ["Para one.", "Para two."],
            "key_themes": [{"label": "x", "summary": "y", "signal_ids": []}],
            "watch_items": ["watch x"],
            "cross_signal_narrative": "narrative",
            "source_signal_ids": ["sig1"],
            "input_artifact_count": 5,
            "generated_at": "2026-05-14T00:00:00Z",
        }
        n = normalize_briefing_for_read(v1)
        self.assertEqual(n["schema_version"], BRIEFING_SCHEMA_VERSION)
        self.assertEqual(n["headline"], "Yesterday's headline")
        self.assertEqual(len(n["briefing_paragraphs"]), 2)
        self.assertEqual(n["briefing_paragraphs"][0]["body"], "Para one.")
        # Provenance preserved.
        self.assertEqual(n["source_signal_ids"], ["sig1"])
        self.assertEqual(n["generated_at"], "2026-05-14T00:00:00Z")

    def test_nested_wrapper_with_off_schema_inner_rescued(self):
        """The bug-day shape: outer provenance + inner Groq off-schema response."""
        from signal_stream.editor_tools import normalize_briefing_for_read, BRIEFING_SCHEMA_VERSION
        broken = {
            "any_artifact_truncated": False,
            "artifact_coverage": {"missing": 12, "thin": 0, "with_artifact": 0},
            "generated_at": "2026-05-15T21:07:28Z",
            "input_artifact_count": 12,
            "source_signal_ids": ["sig_a", "sig_b"],
            "briefing": {
                "title": "Signal Stream Editor Executive Briefing",
                "summary": "12 signals today across various sources.",
                "insights": ["Enterprises need AI safety frameworks."],
                "key_takeaways": ["Robust frameworks required."],
                "recommendations": ["Adopt frameworks now."],
            },
        }
        n = normalize_briefing_for_read(broken)
        self.assertEqual(n["schema_version"], BRIEFING_SCHEMA_VERSION)
        # The title got rescued as headline, summary preserved, paragraphs synthesized.
        self.assertIn("Signal Stream Editor", n["headline"])
        self.assertTrue(n["summary"])
        self.assertGreater(len(n["briefing_paragraphs"]), 0)
        self.assertGreater(len(n["key_takeaways"]), 0)
        # Provenance preserved.
        self.assertEqual(n["input_artifact_count"], 12)
        self.assertEqual(n["source_signal_ids"], ["sig_a", "sig_b"])


# ── 4. End-to-end briefing generation (with FakeBrainClient) ──────────────────

class GenerateBriefingTests(unittest.TestCase):
    def test_well_formed_response_persists_v2_shape(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts, BRIEFING_SCHEMA_VERSION
        good_response = {
            "headline": "Today's headline.",
            "summary": "Today's macro story.",
            "key_takeaways": ["t1", "t2", "t3"],
            "insights": ["i1"],
            "briefing_paragraphs": [
                {"heading": "Theme A", "body": "Body A.", "bullets": ["b1", "b2"], "signal_ids": ["sig1"]},
            ],
            "key_themes": [{"label": "AI", "summary": "x", "signal_ids": ["sig1"]}],
            "cross_signal_narrative": "Close.",
            "watch_items": ["w1"],
        }
        brain = FakeBrainClient([good_response])
        result = generate_briefing_from_artifacts(
            [_signal("sig1")], brain, "editor prompt", {"signal_count": 1}
        )
        self.assertEqual(result["schema_version"], BRIEFING_SCHEMA_VERSION)
        self.assertEqual(result["headline"], "Today's headline.")
        self.assertEqual(len(result["key_takeaways"]), 3)
        # Provenance attached.
        self.assertEqual(result["source_signal_ids"], ["sig1"])

    def test_off_schema_response_raises_after_retry(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        # Off-schema response (no headline / no briefing_paragraphs) — the
        # required_fields retry returns the same shape, then the validator
        # rejects it and we raise.
        off_schema = {"title": "x", "summary": "x", "insights": ["i"], "key_takeaways": ["k"]}
        # chat_json with required_fields=["headline", "briefing_paragraphs"] will
        # detect both missing and trigger one retry, so the fake needs two queued.
        brain = FakeBrainClient([off_schema, off_schema])
        with self.assertRaises(RuntimeError):
            generate_briefing_from_artifacts(
                [_signal("sig1")], brain, "editor prompt", {"signal_count": 1}
            )

    def test_fallback_to_short_summary_when_no_artifacts(self):
        """When 0 signals have artifacts, the Editor still gets evidence (short_summary fallback)."""
        from signal_stream.editor_tools import _build_signal_block
        sig = _signal("sig1", analyst_artifact=None)
        block = _build_signal_block(sig)
        # evidence_source must indicate fallback so the prompt can hedge.
        self.assertIn(block["evidence_source"], ("expanded_summary", "short_summary"))
        self.assertEqual(block["confidence"], "low")
        self.assertTrue(block["evidence_text"])


if __name__ == "__main__":
    unittest.main()
