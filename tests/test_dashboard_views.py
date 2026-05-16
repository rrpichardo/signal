"""Tests for Wave 5 dashboard view behaviour: slim list, detail endpoint, exec summary, settings validation."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from signal_stream.storage import SignalStorage
from signal_stream.prompt_loader import save_brain_file


# ---------------------------------------------------------------------------
# Helpers — build minimal Signal rows directly in SQLite so we can control
# every field without going through the full Agent pipeline.
# ---------------------------------------------------------------------------

def _make_storage() -> SignalStorage:
    """Return a SignalStorage backed by a fresh temp file (auto-cleaned by pytest)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = SignalStorage(tmp.name)
    store.init()
    return store


def _insert_signal(
    store: SignalStorage,
    *,
    signal_id: str,
    score: int = 50,
    title: str = "Test Signal",
    score_breakdown: list | None = None,
    created_at: str = "2026-01-01T12:00:00Z",
) -> None:
    """Insert a minimal signal row directly so tests don't need a full agent run."""
    breakdown = json.dumps(score_breakdown or [{"component": "priority_match", "points": score, "max": 25}])
    with store.connect() as conn:
        conn.execute(
            """
            insert into signals (
                id, cluster_id, article_id, title, url, source, published_at, score, urgency, event_type,
                summary, short_summary, expanded_summary, why_it_matters, next_steps_json, score_breakdown_json,
                matched_priorities_json, entities_json, image_url, icon_key, scout_note, relevance_label,
                duplicate_count, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, "cluster-1", "article-1", title,
                "https://example.com/article", "Test Source",
                "2026-01-01T11:00:00Z", score, "high", "platform_shift",
                "Summary text.", "Short summary.", "Expanded summary.", "Why it matters.",
                "[]", breakdown, "[]", "{}",
                "", "signal", "", "keep",
                0, created_at,
            ),
        )


# ---------------------------------------------------------------------------
# test_list_endpoint_omits_score_breakdown
# ---------------------------------------------------------------------------

def test_list_endpoint_omits_score_breakdown():
    """list_signals_paged (slim mode) must NOT include a score_breakdown key."""
    store = _make_storage()
    _insert_signal(store, signal_id="sig-1", score=80)

    result = store.list_signals_paged()

    assert len(result["items"]) == 1
    item = result["items"][0]
    # slim=True means score_breakdown_json is popped before parse — key absent
    assert "score_breakdown" not in item
    assert "score_breakdown_json" not in item


# ---------------------------------------------------------------------------
# test_detail_endpoint_includes_score_breakdown
# ---------------------------------------------------------------------------

def test_detail_endpoint_includes_score_breakdown():
    """get_signal (full mode) must include a parsed score_breakdown list."""
    store = _make_storage()
    breakdown = [{"component": "priority_match", "points": 20, "max": 25}]
    _insert_signal(store, signal_id="sig-2", score=70, score_breakdown=breakdown)

    signal = store.get_signal("sig-2")

    assert signal is not None
    assert "score_breakdown" in signal
    assert isinstance(signal["score_breakdown"], list)
    # The inserted breakdown should round-trip
    assert signal["score_breakdown"][0]["component"] == "priority_match"


# ---------------------------------------------------------------------------
# test_executive_summary_endpoint_returns_top_12
# ---------------------------------------------------------------------------

def test_executive_summary_endpoint_returns_top_12():
    """list_signals_executive(limit=12) returns the 12 highest-scoring signals."""
    store = _make_storage()
    # Insert 15 signals with distinct scores so the ordering is unambiguous.
    for i in range(15):
        _insert_signal(store, signal_id=f"sig-{i}", score=i * 5, title=f"Signal {i}")

    top = store.list_signals_executive(limit=12)

    assert len(top) == 12
    # Must be sorted highest score first
    scores = [s["score"] for s in top]
    assert scores == sorted(scores, reverse=True)
    # Top signal should be the one with score=70 (i=14 → 14*5=70)
    assert top[0]["score"] == 70
    # No score_breakdown in executive slim view
    for item in top:
        assert "score_breakdown" not in item


# ---------------------------------------------------------------------------
# test_settings_save_validates_component_weights_sum_100
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# test_executive_briefing_no_runs_returns_skipped
# ---------------------------------------------------------------------------

def test_executive_briefing_no_runs_returns_skipped():
    """get_latest_briefing returns status='skipped' and null briefing when DB has no complete runs."""
    store = _make_storage()

    result = store.get_latest_briefing()

    assert result["briefing"] is None
    assert result["briefing_status"] == "skipped"
    assert result["generated_at"] is None
    assert result["source_signal_ids"] == []
    assert result["run_id"] is None


# ---------------------------------------------------------------------------
# test_executive_briefing_returns_briefing_when_present
# ---------------------------------------------------------------------------

def test_executive_briefing_returns_briefing_when_present():
    """get_latest_briefing returns the briefing_json parsed from the latest complete run."""
    store = _make_storage()
    run_id = store.start_agent_run("test run")
    briefing_payload = {
        "headline": "AI Week in Review",
        "briefing_paragraphs": ["Para one.", "Para two."],
        "key_themes": [{"label": "Theme A", "signal_ids": [], "summary": "About theme A."}],
        "watch_items": ["Watch item 1"],
        "source_signal_ids": ["sig-1", "sig-2"],
        "input_artifact_count": 2,
        "artifact_coverage": {"with_artifact": 2, "missing": 0, "thin": 0},
        "any_artifact_truncated": False,
        "generated_at": "2026-01-01T12:00:00Z",
    }
    # Manually write briefing_json + briefing_status to simulate an Editor run.
    with store.connect() as conn:
        conn.execute(
            "update agent_runs set status = 'complete', completed_at = ?, briefing_json = ?, briefing_status = ? where id = ?",
            (
                "2026-01-01T12:00:00Z",
                json.dumps(briefing_payload),
                "generated",
                run_id,
            ),
        )

    result = store.get_latest_briefing()

    assert result["briefing_status"] == "generated"
    assert result["briefing"] is not None
    assert result["briefing"]["headline"] == "AI Week in Review"
    assert result["generated_at"] == "2026-01-01T12:00:00Z"
    assert result["source_signal_ids"] == ["sig-1", "sig-2"]
    assert result["run_id"] == run_id


# ---------------------------------------------------------------------------
# test_executive_briefing_missing_column_old_db
# ---------------------------------------------------------------------------

def test_executive_briefing_missing_column_old_db():
    """get_latest_briefing must not 500 when briefing_json column is absent (old DB).

    _ensure_column adds it on init(), so after init() the column always exists.
    This test verifies the column was added and a null briefing row hydrates cleanly.
    """
    store = _make_storage()
    run_id = store.start_agent_run("test old run")
    # Flip to complete but leave briefing_json as NULL (simulates an old run pre-Phase 3).
    with store.connect() as conn:
        conn.execute(
            "update agent_runs set status = 'complete', completed_at = ? where id = ?",
            ("2026-01-01T12:00:00Z", run_id),
        )

    result = store.get_latest_briefing()

    # Should return skipped/null gracefully, not raise.
    assert result["briefing"] is None
    assert result["briefing_status"] == "skipped"
    assert result["run_id"] == run_id


# ---------------------------------------------------------------------------
# test_analyst_artifact_included_in_get_signal
# ---------------------------------------------------------------------------

def test_analyst_artifact_included_in_get_signal():
    """get_signal (detail endpoint) must include parsed analyst_artifact when present."""
    store = _make_storage()
    _insert_signal(store, signal_id="sig-artifact", score=80)
    artifact = {
        "mechanism": "Groq cut latency by caching model weights on DRAM.",
        "key_actors": [{"name": "Groq", "role": "chip designer"}],
        "confidence": "high",
        "confidence_reason": "Multiple corroborating sources.",
        "_meta": {"was_truncated": False, "chars_total": 5000, "chars_sent": 5000},
    }
    with store.connect() as conn:
        conn.execute(
            "update signals set analyst_artifact_json = ? where id = ?",
            (json.dumps(artifact), "sig-artifact"),
        )

    signal = store.get_signal("sig-artifact")

    assert signal is not None
    assert "analyst_artifact" in signal
    art = signal["analyst_artifact"]
    assert art is not None
    assert art["mechanism"].startswith("Groq")
    assert art["confidence"] == "high"
    assert art["_meta"]["was_truncated"] is False


# ---------------------------------------------------------------------------
# test_old_signal_without_artifact_returns_null
# ---------------------------------------------------------------------------

def test_old_signal_without_artifact_returns_null():
    """get_signal must return analyst_artifact: null for signals that predate Phase 2."""
    store = _make_storage()
    _insert_signal(store, signal_id="sig-old", score=60)
    # Leave analyst_artifact_json as NULL (default for old rows).

    signal = store.get_signal("sig-old")

    assert signal is not None
    # Key is present, value is None — frontend checks for null, not key absence.
    assert "analyst_artifact" in signal
    assert signal["analyst_artifact"] is None


# ---------------------------------------------------------------------------
# test_settings_save_validates_component_weights_sum_100
# ---------------------------------------------------------------------------

def test_settings_save_validates_component_weights_sum_100():
    """save_brain_file must raise ValueError when V2 value weights don't sum to 20."""
    with tempfile.TemporaryDirectory() as tmpdir:
        brain_path = Path(tmpdir) / "brain.toml"
        # Write a brain file with valid starting weights so the path exists.
        valid_brain = {
            "scoring": {
                "value_weights": {
                    "relevance_to_richard": 5,
                    "strategic_importance": 5,
                    "actionability": 3,
                    "credibility": 3,
                    "novelty": 2,
                    "time_sensitivity": 2,
                }
            }
        }
        save_brain_file(brain_path, valid_brain)

        # Now try to save an invalid set that sums to 18 instead of 20.
        bad_brain = {
            "scoring": {
                "value_weights": {
                    "relevance_to_richard": 4,
                    "strategic_importance": 4,
                    "actionability": 3,
                    "credibility": 3,
                    "novelty": 2,
                    "time_sensitivity": 2,
                }
            }
        }
        with pytest.raises(ValueError, match="20"):
            save_brain_file(brain_path, bad_brain)
