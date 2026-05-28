"""Tests for markdown-preserving summaries and the brief-only editor_model knob.

Covers the changes that let the extended summary + brief carry real structure:
  - preserve_markdown_text keeps newlines/pipes (so bullets/tables survive save)
  - the analyst save path no longer flattens expanded_summary
  - editor_model threads from behavior → worker → generate_briefing_from_artifacts
  - load_behavior_settings / _render_brain_toml round-trip editor_model case-intact
  - BrainClient.model_available() checks the /models list; available() is unchanged
  - briefing normalization keeps markdown characters intact
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch


# ── preserve_markdown_text ──────────────────────────────────────────────────────

class TestPreserveMarkdownText(unittest.TestCase):
    def test_keeps_newlines_and_pipes(self):
        from signal_stream.text import preserve_markdown_text
        md = "## Head\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |"
        out = preserve_markdown_text(md)
        self.assertIn("\n", out)
        self.assertIn("|", out)
        self.assertEqual(out.count("- "), 2)

    def test_collapses_intra_line_spaces_only(self):
        from signal_stream.text import preserve_markdown_text
        out = preserve_markdown_text("a    b\tc")
        self.assertEqual(out, "a b c")

    def test_squeezes_three_or_more_blank_lines(self):
        from signal_stream.text import preserve_markdown_text
        out = preserve_markdown_text("a\n\n\n\n\nb")
        self.assertNotIn("\n\n\n", out)
        self.assertIn("a\n\nb", out)

    def test_empty_input(self):
        from signal_stream.text import preserve_markdown_text
        self.assertEqual(preserve_markdown_text(None), "")
        self.assertEqual(preserve_markdown_text(""), "")

    def test_normalize_space_still_flattens(self):
        # Guard the contract: single-line fields must keep using normalize_space.
        from signal_stream.text import normalize_space
        self.assertNotIn("\n", normalize_space("a\nb\nc"))


# ── analyst save path keeps markdown ────────────────────────────────────────────

class TestApplyModelUpdatesKeepsMarkdown(unittest.TestCase):
    def _make_signal(self) -> Any:
        from signal_stream.models import Signal
        return Signal(
            id="s1", cluster_id="c", article_id="a", title="A Real Headline",
            url="https://example.com", source="src", published_at="",
            score=60, urgency="medium", event_type="product_launch",
            summary="", why_it_matters="", next_steps=[], matched_priorities=[],
            entities={}, duplicate_count=0, score_breakdown=[],
            short_summary="", expanded_summary="", image_url="", icon_key="",
            scout_note="", relevance_label="",
        )

    def test_markdown_expanded_summary_survives(self):
        from signal_stream.analysis_tools import _apply_model_updates
        signal = self._make_signal()
        md = "Lede sentence.\n\n- **Anthropic** shipped X\n- pricing dropped\n\n| tier | price |\n|---|---|\n| pro | $20 |\n\n**Why it matters:** budgets shift."
        item = {
            "score": 70,
            "short_summary": "A crisp human-written summary of the news.",
            "expanded_summary": md,
        }
        config = MagicMock(critical_threshold=86)
        _apply_model_updates(
            signal, item, analyst_mode="hybrid", behavior={},
            config=config, llm=MagicMock(), analyst_prompt="p", review_context={},
        )
        # Structure preserved.
        self.assertIn("\n", signal.expanded_summary)
        self.assertIn("|", signal.expanded_summary)
        self.assertIn("- **Anthropic** shipped X", signal.expanded_summary)
        self.assertIn("**Why it matters:**", signal.expanded_summary)
        # short_summary stays single-line.
        self.assertNotIn("\n", signal.short_summary)
        self.assertEqual(signal.short_summary, "A crisp human-written summary of the news.")


# ── editor_model threading ──────────────────────────────────────────────────────

class TestEditorModelThreading(unittest.TestCase):
    def _make_signal(self) -> Any:
        from signal_stream.models import Signal
        return Signal(
            id="s1", cluster_id="c", article_id="a", title="T", url="u", source="src",
            published_at="", score=70, urgency="high", event_type="pe", summary="s",
            why_it_matters="", next_steps=[], matched_priorities=[], entities={},
            duplicate_count=0, score_breakdown=[], short_summary="ss",
            expanded_summary="es", image_url="", icon_key="", scout_note="",
            relevance_label="", analyst_artifact={"mechanism": "M" * 60, "confidence": "high"},
            analyst_status="success",
        )

    def test_generate_passes_model_to_chat_json(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = MagicMock()
        brain.chat_json.return_value = {"headline": "H", "briefing_paragraphs": ["p"]}
        brain.last_error = ""
        generate_briefing_from_artifacts(
            [self._make_signal()], brain, "prompt", {}, editor_model="openai/gpt-oss-120b",
        )
        self.assertEqual(brain.chat_json.call_args.kwargs.get("model"), "openai/gpt-oss-120b")

    def test_generate_defaults_model_to_none(self):
        from signal_stream.editor_tools import generate_briefing_from_artifacts
        brain = MagicMock()
        brain.chat_json.return_value = {"headline": "H", "briefing_paragraphs": ["p"]}
        brain.last_error = ""
        generate_briefing_from_artifacts([self._make_signal()], brain, "prompt", {})
        self.assertIsNone(brain.chat_json.call_args.kwargs.get("model"))

    def test_worker_reads_editor_model_from_behavior(self):
        # The actual Groq call happens in the worker process — verify the worker
        # reads behavior.editor_model and forwards it.
        import signal_stream.worker as worker
        signal_dict = {
            "id": "s1", "cluster_id": "", "article_id": "", "title": "T", "url": "u",
            "source": "src", "published_at": "", "score": 60, "urgency": "medium",
            "event_type": "pe", "summary": "s", "why_it_matters": "", "next_steps": [],
            "matched_priorities": [], "entities": {}, "duplicate_count": 0,
            "score_breakdown": [], "short_summary": "ss", "expanded_summary": "es",
            "image_url": "", "icon_key": "", "scout_note": "", "relevance_label": "",
            "analyst_artifact": {"mechanism": "M" * 60, "confidence": "high", "_meta": {"was_truncated": False}},
        }
        task = {"task_id": "t1", "type": "generate_briefing", "payload": {"signals": [signal_dict]}}
        with patch.object(worker, "generate_briefing_from_artifacts", return_value={"artifact_coverage": {}}) as gen, \
             patch.object(worker, "BrainClient", return_value=MagicMock()):
            worker.handle_task(
                "editor", MagicMock(), MagicMock(), {"editor": "p"}, {},
                {"editor_model": "openai/gpt-oss-120b"}, task,
            )
        self.assertEqual(gen.call_args.kwargs.get("editor_model"), "openai/gpt-oss-120b")


# ── behavior settings round-trip ────────────────────────────────────────────────

class TestEditorModelSettings(unittest.TestCase):
    def test_default_behavior_has_editor_model(self):
        from signal_stream.prompt_loader import DEFAULT_BEHAVIOR_SETTINGS
        self.assertIn("editor_model", DEFAULT_BEHAVIOR_SETTINGS)

    def test_render_round_trip_preserves_case(self):
        import tomllib
        from signal_stream.prompt_loader import (
            DEFAULT_BEHAVIOR_SETTINGS, DEFAULT_PROMPTS, DEFAULT_SCORING_RUBRIC,
            _render_brain_toml,
        )
        behavior = dict(DEFAULT_BEHAVIOR_SETTINGS)
        behavior["editor_model"] = "openai/gpt-oss-120b"
        rendered = _render_brain_toml(dict(DEFAULT_PROMPTS), dict(DEFAULT_SCORING_RUBRIC), behavior)
        reparsed = tomllib.loads(rendered)
        self.assertEqual(reparsed["behavior"]["editor_model"], "openai/gpt-oss-120b")

    def test_empty_editor_model_round_trips(self):
        import tomllib
        from signal_stream.prompt_loader import (
            DEFAULT_BEHAVIOR_SETTINGS, DEFAULT_PROMPTS, DEFAULT_SCORING_RUBRIC,
            _render_brain_toml,
        )
        behavior = dict(DEFAULT_BEHAVIOR_SETTINGS)
        behavior["editor_model"] = ""
        rendered = _render_brain_toml(dict(DEFAULT_PROMPTS), dict(DEFAULT_SCORING_RUBRIC), behavior)
        reparsed = tomllib.loads(rendered)
        self.assertEqual(reparsed["behavior"]["editor_model"], "")

    def test_manifest_exposes_editor_model(self):
        from signal_stream.settings_manifest import manifest_index
        entry = manifest_index().get("behavior.editor_model")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["control"], "select")
        self.assertIn("openai/gpt-oss-120b", entry["options"])


# ── model_available ─────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestModelAvailable(unittest.TestCase):
    def _client(self):
        from signal_stream.llm import BrainClient
        client = BrainClient(MagicMock())
        client._api_key = "test-key"  # bypass the no-key short-circuit
        return client

    def test_true_for_listed_model(self):
        body = json.dumps({"data": [{"id": "openai/gpt-oss-120b"}, {"id": "llama-4-scout"}]}).encode()
        with patch("signal_stream.llm.request.urlopen", return_value=_FakeResp(body)):
            self.assertTrue(self._client().model_available("openai/gpt-oss-120b"))

    def test_false_for_missing_model(self):
        body = json.dumps({"data": [{"id": "llama-4-scout"}]}).encode()
        with patch("signal_stream.llm.request.urlopen", return_value=_FakeResp(body)):
            self.assertFalse(self._client().model_available("openai/gpt-oss-120b"))

    def test_false_for_empty_id(self):
        self.assertFalse(self._client().model_available(""))


# ── briefing normalization keeps markdown ───────────────────────────────────────

class TestBriefingKeepsMarkdown(unittest.TestCase):
    def test_validate_keeps_markdown_chars(self):
        from signal_stream.editor_tools import _validate_editor_briefing
        raw = {
            "headline": "H",
            "summary": "**bold** and a table follows",
            "key_takeaways": ["- keep **x** and 2 | 3"],
            "briefing_paragraphs": [
                {"heading": "T", "body": "row:\n\n| a | b |\n|---|---|\n| 1 | 2 |", "bullets": ["**b1**"], "signal_ids": []}
            ],
        }
        out = _validate_editor_briefing(raw)
        self.assertIn("**bold**", out["summary"])
        self.assertIn("**x**", out["key_takeaways"][0])
        self.assertIn("|", out["briefing_paragraphs"][0]["body"])
        self.assertIn("**b1**", out["briefing_paragraphs"][0]["bullets"][0])


if __name__ == "__main__":
    unittest.main()
