"""
Editor tools — controlled full-text fallback for insufficient analyst artifacts.

Phase 3 will add generate_briefing_from_artifacts() here.
Phase 4 ships: evaluate_fallback_eligibility() + run_fulltext_fallback().

Design contract:
  - Python decides which signals to re-fetch (trigger evaluation + cap).
  - Groq re-reviews each selected signal with fresh full-page text.
  - Every fallback fetch/review is independently best-effort.
  - Nothing here can block a run from completing.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .analysis_tools import ANALYST_REVIEW_SCHEMA, _OVERSIZED_TRUNCATION, _chat_json_with_truncation_fallback, _review_payload
from .llm import BrainClient
from .models import Signal
from .source_tools import fetch_full_article_page
from .storage import SignalStorage


# Trigger priorities — lower number = fetched first when cap is tight.
_PRIORITY_MISSING_ARTIFACT = 0    # null/empty artifact: no evidence at all
_PRIORITY_HEAVY_TRUNCATION = 1    # >50% of article was cut before Groq saw it
_PRIORITY_CRITIC_FLAGS = 2        # Critic flagged this signal for revision
_PRIORITY_LOW_CONFIDENCE = 3      # model or Python downgraded to "low"
_PRIORITY_VAGUE_MECHANISM = 4     # mechanism under 40 chars — too thin to use
_PRIORITY_LEAD_STORY = 5          # top-3 by score: always worth best evidence

_MECHANISM_MIN_CHARS = 40         # mechanism shorter than this = vague


def evaluate_fallback_eligibility(
    signals: list[Signal],
    artifacts: dict[str, dict[str, Any] | None],
    cap: int,
) -> list[Signal]:
    """Return up to `cap` signals that qualify for full-text fallback.

    Signals are ranked by trigger severity (missing artifact first, lead stories
    last). Within each priority tier, higher score wins the slot.
    """
    eligible: list[tuple[int, int, Signal]] = []
    for rank, signal in enumerate(signals):
        artifact = artifacts.get(signal.id)
        priority = _fallback_priority(artifact, rank)
        if priority is not None:
            # Negate score so sort is ascending-priority / descending-score
            eligible.append((priority, -signal.score, signal))

    eligible.sort(key=lambda x: (x[0], x[1]))
    return [signal for _, _, signal in eligible[:cap]]


def _fallback_priority(
    artifact: dict[str, Any] | None,
    rank: int,
) -> int | None:
    """Return a numeric priority for fallback eligibility, or None if not needed."""

    # Trigger 1: artifact missing entirely — nothing for the Editor to reduce over.
    if not artifact:
        return _PRIORITY_MISSING_ARTIFACT

    meta = artifact.get("_meta") or {}
    chars_total = int(meta.get("chars_total") or 0)
    chars_sent = int(meta.get("chars_sent") or 0)
    was_truncated = bool(meta.get("was_truncated"))

    # Trigger 2: heavy truncation — less than half the article reached Groq.
    if was_truncated and chars_total > 0 and chars_sent / chars_total < 0.5:
        return _PRIORITY_HEAVY_TRUNCATION

    # Trigger 3: Critic left flags on this signal that haven't been resolved.
    if artifact.get("critic_flags"):
        return _PRIORITY_CRITIC_FLAGS

    # Trigger 4: confidence downgraded to low by the hybrid confidence logic.
    if artifact.get("confidence") == "low":
        return _PRIORITY_LOW_CONFIDENCE

    # Trigger 5: mechanism is absent or too vague to be useful in a briefing.
    mechanism = str(artifact.get("mechanism") or "").strip()
    if len(mechanism) < _MECHANISM_MIN_CHARS:
        return _PRIORITY_VAGUE_MECHANISM

    # Trigger 6: lead-story position — top-3 signals deserve the best evidence.
    if rank < 3:
        return _PRIORITY_LEAD_STORY

    return None


def run_fulltext_fallback(
    signals: list[Signal],
    artifacts: dict[str, dict[str, Any] | None],
    brain: BrainClient,
    storage: SignalStorage,
    cap: int,
    analyst_prompt: str,
    log_fn: Callable[[str, str, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any] | None]:
    """Re-fetch full article text and regenerate artifacts for thin signals.

    Returns a copy of `artifacts` with refreshed entries for each signal that
    was processed. Failures are logged and skipped — the return dict always
    covers all input signal IDs.

    `log_fn` should have signature (event_type, message, payload) and is
    typically a closure over storage.save_agent_event bound to the current
    run_id and "Editor" agent name.
    """
    refreshed: dict[str, dict[str, Any] | None] = dict(artifacts)
    # Fill in None for any signal not yet in the artifacts map
    for sig in signals:
        if sig.id not in refreshed:
            refreshed[sig.id] = None

    eligible = evaluate_fallback_eligibility(signals, refreshed, cap)
    if not eligible:
        return refreshed

    for signal in eligible:
        _run_single_fallback(signal, refreshed, brain, storage, analyst_prompt, log_fn)

    return refreshed


def _run_single_fallback(
    signal: Signal,
    refreshed: dict[str, dict[str, Any] | None],
    brain: BrainClient,
    storage: SignalStorage,
    analyst_prompt: str,
    log_fn: Callable[[str, str, dict[str, Any]], None] | None,
) -> None:
    """Attempt one fallback fetch+review for a single signal. Isolated — never raises."""
    signal_id = signal.id

    def _log(event_type: str, message: str, payload: dict[str, Any]) -> None:
        if log_fn:
            try:
                log_fn(event_type, message, payload)
            except Exception:  # noqa: BLE001 - logging must never abort the fallback
                pass

    _log("editor_fallback_started", f"Starting full-text fallback for {signal_id}.", {"signal_id": signal_id, "url": signal.url})

    try:
        body, og_image = fetch_full_article_page(signal.url)
        if not body:
            _log("editor_fallback_failed", f"Empty body for {signal_id}; keeping original artifact.", {"signal_id": signal_id, "reason": "empty_body"})
            return

        chars_total = len(body)
        was_truncated = chars_total > _OVERSIZED_TRUNCATION
        chars_sent = min(chars_total, _OVERSIZED_TRUNCATION)

        review_ctx: dict[str, Any] = {"article_text": body}
        if og_image:
            review_ctx["og_image"] = og_image

        payload = {
            "task": "review_ranked_signals",
            "signals": [_review_payload(signal, review_ctx)],
            # score_adjustment_limit=0 so the Editor fallback never changes scores —
            # only artifact quality improves, not rank order.
            "rules": {
                "score_adjustment_limit": 0,
                "summary_mode": "short_expanded",
                "entity_extraction": "hybrid",
                "short_summary_owner": "model",
                "short_summary_instruction": (
                    "Write a fresh 2-3 sentence summary from article_text. "
                    "Do not repeat the title. Fold the strategic implication in."
                ),
            },
        }

        raw = _chat_json_with_truncation_fallback(
            brain,
            analyst_prompt,
            json.dumps(payload, sort_keys=True),
            ANALYST_REVIEW_SCHEMA,
            required_fields=None,
        )

        if not raw or not raw.get("signals"):
            _log("editor_fallback_failed", f"Groq review returned nothing for {signal_id}; keeping original artifact.", {"signal_id": signal_id, "reason": "groq_empty"})
            return

        reviewed = raw["signals"][0]

        # Build the refreshed artifact.  mechanism maps to expanded_summary —
        # the most complete prose description of what happened and why.
        existing = refreshed.get(signal_id) or {}
        new_artifact: dict[str, Any] = {
            "mechanism": reviewed.get("expanded_summary") or existing.get("mechanism") or "",
            "key_actors": existing.get("key_actors") or [],
            "affected_parties": existing.get("affected_parties") or [],
            "evidence_excerpts": existing.get("evidence_excerpts") or [],
            "confidence": existing.get("confidence") or "medium",
            "confidence_reason": "Refreshed via editor full-text fallback.",
            "model_confidence": existing.get("model_confidence") or "medium",
            "critic_flags": [],  # clear flags after re-review
            "_meta": {
                "was_truncated": was_truncated,
                "chars_total": chars_total,
                "chars_sent": chars_sent,
                "extraction_quality": "good" if chars_total > 1000 else "partial",
                "refresh_source": "editor_fallback",
            },
        }

        refreshed[signal_id] = new_artifact
        storage.update_signal_artifact(signal_id, new_artifact)

        _log("editor_fallback_success", f"Refreshed artifact for {signal_id}.", {"signal_id": signal_id, "chars_total": chars_total, "was_truncated": was_truncated})

    except Exception as exc:  # noqa: BLE001 - fallback failures must never surface up
        print(f"[signal_stream] editor_fallback: unexpected error for {signal_id}: {exc}", file=sys.stderr)
        _log("editor_fallback_failed", f"Unexpected error for {signal_id}: {exc}", {"signal_id": signal_id, "reason": str(exc)})
