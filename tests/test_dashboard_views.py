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

def test_settings_save_validates_component_weights_sum_100():
    """save_brain_file must raise ValueError when component weights don't sum to 100."""
    with tempfile.TemporaryDirectory() as tmpdir:
        brain_path = Path(tmpdir) / "brain.toml"
        # Write a brain file with valid starting weights so the path exists.
        valid_brain = {
            "scoring": {
                "components": {
                    "priority_match": 25,
                    "company_match": 25,
                    "recency": 15,
                    "event_strength": 25,
                    "corroboration": 10,
                }
            }
        }
        save_brain_file(brain_path, valid_brain)

        # Now try to save an invalid set that sums to 90 instead of 100.
        bad_brain = {
            "scoring": {
                "components": {
                    "priority_match": 20,  # -5 here
                    "company_match": 25,
                    "recency": 15,
                    "event_strength": 20,  # -5 here too → sum = 90
                    "corroboration": 10,
                }
            }
        }
        with pytest.raises(ValueError, match="100"):
            save_brain_file(brain_path, bad_brain)
