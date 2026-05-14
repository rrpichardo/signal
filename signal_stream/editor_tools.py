from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


# JSONSchema for the Editor's Groq response. Required fields are the core
# narrative; all others are optional so a partial response isn't discarded.
EDITOR_BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "briefing_paragraphs": {"type": "array", "items": {"type": "string"}},
        "key_themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "signal_ids": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
            },
        },
        "watch_items": {"type": "array", "items": {"type": "string"}},
        "cross_signal_narrative": {"type": "string"},
    },
    "required": ["headline", "briefing_paragraphs", "cross_signal_narrative"],
}


def _parse_artifact(sig: dict[str, Any]) -> dict[str, Any] | None:
    """Extract artifact dict from a signal, whether stored as dict or JSON string."""
    raw = sig.get("analyst_artifact_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _is_thin(artifact: dict[str, Any]) -> bool:
    """Artifact exists but mechanism is too vague to be useful."""
    mechanism = artifact.get("mechanism", "")
    return not mechanism or len(mechanism) < 40


def _coverage(top_signals: list[dict[str, Any]]) -> tuple[dict[str, int], bool]:
    """Count artifact coverage and whether any were truncated."""
    counts = {"with_artifact": 0, "missing": 0, "thin": 0}
    any_truncated = False
    for sig in top_signals:
        artifact = _parse_artifact(sig)
        if artifact is None:
            counts["missing"] += 1
        elif _is_thin(artifact):
            counts["thin"] += 1
        else:
            counts["with_artifact"] += 1
        meta = (artifact or {}).get("_meta", {})
        if meta.get("was_truncated"):
            any_truncated = True
    return counts, any_truncated


def _build_signal_block(sig: dict[str, Any]) -> dict[str, Any]:
    """Flatten a signal + optional artifact into the payload the Editor receives."""
    block: dict[str, Any] = {
        "id": sig.get("id", ""),
        "title": sig.get("title", ""),
        "score": sig.get("score", 0),
        "short_summary": sig.get("short_summary") or sig.get("summary", ""),
        "expanded_summary": sig.get("expanded_summary") or sig.get("summary", ""),
        "source": sig.get("source", ""),
        "published_at": sig.get("published_at", ""),
    }
    artifact = _parse_artifact(sig)
    if artifact:
        # Include richer context when artifact is present.
        block["mechanism"] = artifact.get("mechanism", "")
        block["key_actors"] = artifact.get("key_actors", [])
        block["affected_parties"] = artifact.get("affected_parties", [])
        block["confidence"] = artifact.get("confidence", "")
    return block


def generate_briefing_from_artifacts(
    top_signals: list[dict[str, Any]],
    brain: Any,
    editor_prompt: str,
    run_context: dict[str, Any],
) -> dict[str, Any]:
    """One Groq call that reduces signal artifacts into an executive briefing.

    Returns the complete briefing dict. Raises RuntimeError on failure —
    callers are responsible for catching and setting briefing_status = 'failed'.
    """
    generated_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    coverage, any_truncated = _coverage(top_signals)
    signal_blocks = [_build_signal_block(sig) for sig in top_signals]

    user_payload = json.dumps(
        {
            "run_context": run_context,
            "signals": signal_blocks,
            "note": (
                "Signals that lack a 'mechanism' field have no analyst artifact yet. "
                "Use short_summary and expanded_summary for those. "
                "Do NOT invent content. Omit a signal from key_themes if you have "
                "no substantive information about it."
            ),
        },
        sort_keys=True,
    )

    raw = brain.chat_json(
        editor_prompt,
        user_payload,
        EDITOR_BRIEFING_SCHEMA,
        required_fields=["headline", "briefing_paragraphs", "cross_signal_narrative"],
    )

    if not raw:
        err = getattr(brain, "last_error", None) or "unknown error"
        raise RuntimeError(f"Editor Groq call failed: {err}")

    source_signal_ids = [sig.get("id", "") for sig in top_signals if sig.get("id")]

    return {
        "headline": str(raw.get("headline", "")),
        "briefing_paragraphs": list(raw.get("briefing_paragraphs", [])),
        "key_themes": list(raw.get("key_themes", [])),
        "watch_items": list(raw.get("watch_items", [])),
        "cross_signal_narrative": str(raw.get("cross_signal_narrative", "")),
        "source_signal_ids": source_signal_ids,
        "input_artifact_count": len(top_signals),
        "artifact_coverage": coverage,
        "any_artifact_truncated": any_truncated,
        "generated_at": generated_at,
    }
