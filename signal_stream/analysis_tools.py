from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from typing import Any

import sys

from .agents import ClusterAgent, EntityAgent
from .llm import BrainClient
from .models import AgentRunLog, Article, ClusterInsight, Signal, SignalConfig, SignalDraft, stable_id, utc_now_iso
from .prompt_loader import DEFAULT_SCORING_RUBRIC
from .source_tools import fetch_full_article_page
from .storage import SignalStorage
from .text import first_sentences, normalize_space, phrase_hits


# ---------------------------------------------------------------------------
# Event-type classification (moved here from agents.py — Wave 3)
# ---------------------------------------------------------------------------

# Keywords that determine which event-type bucket a story falls into.
# The bucket with the most hits wins. If no bucket wins and competitors
# are mentioned, the story becomes a "competitor_move".
EVENT_KEYWORDS: dict[str, list[str]] = {
    "platform_shift": ["model", "platform", "API", "agent", "agents", "developer", "enterprise", "pricing", "release", "launch"],
    "startup_signal": ["funding", "seed", "Series A", "Series B", "startup", "valuation", "venture", "YC", "acquisition", "acquires"],
    "infrastructure_signal": ["NVIDIA", "GPU", "chip", "compute", "cloud", "inference", "training", "data center", "latency"],
    "regulatory_risk": ["regulation", "regulatory", "policy", "copyright", "privacy", "security", "safety", "EU AI Act", "compliance", "lawsuit"],
    "builder_tactic": ["architecture", "engineering", "RAG", "fine-tuning", "eval", "evaluation", "prompt", "workflow", "case study"],
}

# Strategic verb phrases used by _score_company_match to detect high-impact
# watchlist stories (e.g. "Anthropic acquires X" or "OpenAI launches Y").
_STRATEGIC_VERBS: frozenset[str] = frozenset([
    "acquires", "acquisition", "launches", "releases", "announces",
    "partners", "partnership", "raises", "ipo", "open-sources",
    "shutting down", "lays off", "hired", "appoints",
])

# Low-value content markers — applied by _score_event_strength to detect
# promotional, roundup, and opinion pieces.
_LOW_VALUE_PHRASES: frozenset[str] = frozenset([
    "top 10", "best of", "roundup", "listicle", "webinar",
    "register now", "sponsored", "job opening", "we are hiring",
    "newsletter", "conference",
])


ANALYST_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "integer"},
                    "short_summary": {"type": "string"},
                    "expanded_summary": {"type": "string"},
                    "entities": {"type": "object"},
                    # Phase 2: richer artifact fields. Optional on purpose —
                    # if the model omits or truncates them the signal is still
                    # accepted; Python post-validates and downgrades confidence.
                    "mechanism": {"type": "string"},
                    "key_actors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {"type": "string"},
                            },
                        },
                    },
                    "affected_parties": {"type": "array", "items": {"type": "string"}},
                    "evidence_excerpts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "quote": {"type": "string"},
                                "source_offset": {"type": "integer"},
                            },
                        },
                    },
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence_reason": {"type": "string"},
                },
                # why_it_matters dropped — strategic implication folded into short_summary.
                # Keep it in storage and API for old-run compatibility.
                "required": ["id", "score", "short_summary", "expanded_summary", "entities"],
            },
        }
    },
    "required": ["signals"],
}

SUMMARY_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "short_summary": {"type": "string"},
        "expanded_summary": {"type": "string"},
    },
    "required": ["short_summary", "expanded_summary"],
}


def analyze_articles(
    config: SignalConfig,
    storage: SignalStorage,
    articles_json: list[dict[str, Any]],
    *,
    analyst_mode: str = "code",
    analyst_prompt: str = "",
    scoring_rubric: dict[str, Any] | None = None,
    behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn collected articles into ranked signals.

    Plain English: this is Analyst's main tool. It removes repeats, scores what
    matters, checks memory, and returns a digest-ready list.
    """

    articles = [_article_from_json(item) for item in articles_json]
    # Drop anything we already ranked in a prior complete run. This is what
    # makes the cursor + 6-hour overlap safe: the worker can over-fetch on
    # purpose, and the seen-set kills the dupes before they reach scoring.
    articles = [a for a in articles if not storage.is_article_seen(a.id, a.url)]
    trace = AgentRunLog()
    ctx = _Context(config=config, storage=storage, llm=BrainClient(config), trace=trace)

    normalized = _dedupe_exact(articles)
    clusters = ClusterAgent().run(ctx, normalized)
    insights = EntityAgent().run(ctx, clusters)
    # Build drafts directly — no RelevanceAgent. Scoring happens below via
    # _base_score_card, which is the single source of truth for Signal.score.
    drafts = build_drafts_from_insights(ctx, insights)

    behavior = behavior or {}
    rubric = scoring_rubric or DEFAULT_SCORING_RUBRIC
    signals = []
    review_context: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        article = draft.cluster.articles[0]
        score, score_breakdown = _base_score_card(article, draft, rubric)
        # Code-path fallback: hand the LLM raw material, not a heuristic summary.
        # The Analyst prompt specifies exactly how to rewrite this into proper prose.
        # When the brain is unavailable the raw excerpt is shown as-is — honest fallback.
        short_summary = first_sentences(article.body, max_sentences=2, max_chars=200)
        expanded_summary = "" if behavior.get("summary_mode") == "short_only" else first_sentences(article.body, max_sentences=4, max_chars=600)
        visuals_mode = str(behavior.get("visuals_mode", "image_icon"))
        image_url = str(article.raw.get("image_url", "")) if visuals_mode == "image_icon" else ""
        icon_key = _icon_key(draft.event_type, draft.entities, draft.text) if visuals_mode != "none" else ""
        signal = Signal(
            id=stable_id(draft.cluster.id, article.title, score, prefix="sig"),
            cluster_id=draft.cluster.id,
            article_id=article.id,
            title=article.title,
            url=article.url,
            source=article.source,
            published_at=article.published_at,
            score=score,
            urgency=_urgency(score, config.critical_threshold),
            event_type=draft.event_type,
            summary=short_summary,
            why_it_matters="",  # LLM Analyst writes this; Critic flags it if empty.
            next_steps=[],
            matched_priorities=draft.matched_priorities,
            entities=draft.entities,
            duplicate_count=max(0, len(draft.cluster.articles) - 1),
            score_breakdown=score_breakdown,
            short_summary=short_summary,
            expanded_summary=expanded_summary,
            image_url=image_url,
            icon_key=icon_key,
            scout_note=str(article.raw.get("scout_note", "")),
            relevance_label=str(article.raw.get("scout_relevance_label", "keep")),
        )
        signals.append(signal)
        review_context[signal.id] = {
            "article_text": article.body[:2500],
            "scout_topic": article.raw.get("scout_topic", ""),
            "scout_signal_type": article.raw.get("scout_signal_type", ""),
            "scout_usefulness": article.raw.get("scout_usefulness", ""),
            "scout_note": article.raw.get("scout_note", ""),
            "relevance_label": article.raw.get("scout_relevance_label", "keep"),
        }

    # Sort before the model pass so the top-N fetch and model review both
    # operate on the highest-scoring candidates first.
    signals.sort(key=lambda item: item.score, reverse=True)

    # Fetch full article pages for the top-N candidates so the model reviewer
    # gets richer text than the RSS body alone. Only the top analyst_review_limit
    # articles are fetched; the rest stay on their RSS bodies.
    review_limit = int(behavior.get("analyst_review_limit", 40))
    short_body_ids: set[str] = set()
    if behavior.get("analyst_full_review"):
        signals, review_context, short_body_ids = _fetch_full_pages_for_top_n(signals, review_context, review_limit)

    signals, truncation_events, analyst_failures = _apply_analyst_mode(
        signals, config, analyst_mode, analyst_prompt, behavior, review_context,
        short_body_ids=short_body_ids,
    )
    # Promote og:image for any signal that has no feed image but has a fetched og:image.
    # Runs in all modes — the og:image was already extracted during full-page fetch.
    _promote_og_images(signals, review_context)
    signals.sort(key=lambda item: item.score, reverse=True)
    digest = render_digest(config, signals, trace.events)
    return {
        "article_count": len(normalized),
        "cluster_count": len(clusters),
        "signals": [_signal_json(signal) for signal in signals],
        "digest": digest,
        # Phase 2: surface truncation events so the orchestrator can emit a
        # `truncated_article` activity event per affected signal.
        "truncation_events": truncation_events,
        # Phase 3: per-signal Groq review failures for activity logging (selected signals only).
        "analyst_failures": analyst_failures,
        "trace": [{"agent": event.agent, "message": event.message, "metadata": event.metadata} for event in trace.events],
    }


class _Context:
    def __init__(self, config: SignalConfig, storage: SignalStorage, llm: BrainClient, trace: AgentRunLog):
        self.config = config
        self.storage = storage
        self.llm = llm
        self.trace = trace
        self.priority_adjustments = storage.load_priority_adjustments()


def _article_from_json(item: dict[str, Any]) -> Article:
    return Article(
        id=str(item.get("id") or stable_id(item.get("source"), item.get("url"), item.get("title"), prefix="art")),
        source=str(item.get("source", "Unknown")),
        title=str(item.get("title", "")),
        url=str(item.get("url", "")),
        published_at=str(item.get("published_at", "")),
        body=str(item.get("body", "")),
        fetched_at=str(item.get("fetched_at", "")),
        raw=dict(item.get("raw") or {}),
    )


def _dedupe_exact(articles: list[Article]) -> list[Article]:
    """Remove exact within-run repeats.

    Plain English: if Scout fetched two copies of the same URL in one run,
    keep only one. Cross-run freshness is handled by the cursor (worker) and
    storage.is_article_seen (analyst entry point), so the old "stale older
    than N days" check no longer belongs here.
    """

    seen = set()
    kept = []
    for article in articles:
        article.title = normalize_space(article.title)
        article.body = normalize_space(article.body) or article.title
        key = (article.url or article.title).lower()
        if not article.title or key in seen:
            continue
        seen.add(key)
        kept.append(article)
    return kept


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _urgency(score: int, critical_threshold: int) -> str:
    if score >= critical_threshold:
        return "critical"
    if score >= 72:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _signal_json(signal: Signal) -> dict[str, Any]:
    return {
        "id": signal.id,
        "cluster_id": signal.cluster_id,
        "article_id": signal.article_id,
        "title": signal.title,
        "url": signal.url,
        "source": signal.source,
        "published_at": signal.published_at,
        "score": signal.score,
        "urgency": signal.urgency,
        "event_type": signal.event_type,
        "summary": signal.summary,
        "short_summary": signal.short_summary or signal.summary,
        "expanded_summary": signal.expanded_summary or signal.summary,
        "why_it_matters": signal.why_it_matters,
        "next_steps": signal.next_steps,
        "matched_priorities": signal.matched_priorities,
        "entities": signal.entities,
        "duplicate_count": signal.duplicate_count,
        "score_breakdown": signal.score_breakdown,
        "image_url": signal.image_url,
        "icon_key": signal.icon_key,
        "scout_note": signal.scout_note,
        "relevance_label": signal.relevance_label,
        # Phase 2: artifact rides on the signal dict through the worker stdout
        # and back into agent_runtime so save_run_atomic can persist it.
        "analyst_artifact": signal.analyst_artifact,
        # Phase 3: review status tracking — also crosses the worker boundary.
        "analyst_status": signal.analyst_status,
        "analyst_error_type": signal.analyst_error_type,
        "analyst_error_message": signal.analyst_error_message,
        "analyst_attempt_count": signal.analyst_attempt_count,
        "analyst_last_attempt_at": signal.analyst_last_attempt_at,
    }


def _apply_model_updates(
    signal: Signal,
    item: dict[str, Any],
    analyst_mode: str,
    behavior: dict[str, Any],
    config: SignalConfig,
    llm: BrainClient,
    analyst_prompt: str,
    review_context: dict[str, dict[str, Any]],
) -> None:
    """Apply a successful Groq review result to a Signal in-place.

    Shared by the first-pass and retry-pass success paths so the scoring,
    summary, entity, and artifact logic stays in one place.
    """
    model_score = int(item.get("score", signal.score))
    merged_score = _bounded_model_score(signal.score, model_score, analyst_mode, int(behavior.get("model_score_adjustment_limit", 20)))
    signal.score = max(0, min(100, merged_score))
    model_short = normalize_space(item.get("short_summary", ""))
    if _looks_like_lazy_summary(model_short, signal.title):
        repaired = _repair_lazy_summary(llm, analyst_prompt, signal, review_context.get(signal.id, {}), behavior)
        model_short = repaired.get("short_summary", "")
        if behavior.get("summary_mode") != "short_only" and repaired.get("expanded_summary"):
            signal.expanded_summary = repaired["expanded_summary"]
    signal.short_summary = model_short or signal.short_summary or signal.summary
    if behavior.get("summary_mode") == "short_only":
        signal.expanded_summary = ""
    else:
        signal.expanded_summary = normalize_space(item.get("expanded_summary", signal.expanded_summary or signal.short_summary)) or signal.expanded_summary or signal.short_summary
    signal.summary = signal.short_summary
    signal.why_it_matters = normalize_space(item.get("why_it_matters", signal.why_it_matters)) or signal.why_it_matters
    signal.entities = _merge_entities(signal.entities, item.get("entities", {}), behavior)
    signal.urgency = _urgency(signal.score, config.critical_threshold)
    signal.analyst_artifact = _build_artifact(signal, item)


def _apply_analyst_mode(
    signals: list[Signal],
    config: SignalConfig,
    analyst_mode: str,
    analyst_prompt: str,
    behavior: dict[str, Any],
    review_context: dict[str, dict[str, Any]],
    short_body_ids: set[str] | None = None,
) -> tuple[list[Signal], list[dict[str, Any]], list[dict[str, Any]]]:
    """Optionally let the model polish Analyst output.

    Plain English: the code still does the dependable base work first
    (dedupe, scoring, memory penalties). In hybrid/model mode, the LLM gets a
    second pass to improve human-judgment fields like summary and why-it-matters.

    Phase 2: also assembles the per-signal analyst_artifact and surfaces
    truncation_events for the orchestrator to log as activity events.

    Phase 3: returns a third element — analyst_failures (list of dicts for activity
    logging). Also writes analyst_status and related fields onto each Signal.
    Terminal guarantee: no signal exits this function with analyst_status='pending_retry'.
    """
    # Code path or brain unavailable: mark all signals as skipped (intentional non-model).
    if analyst_mode not in {"hybrid", "model"} or not signals:
        for signal in signals:
            signal.analyst_status = "skipped"
        return signals, [], []

    llm = BrainClient(config)
    if not llm.available():
        for signal in signals:
            signal.analyst_status = "skipped"
        return signals, [], []

    review_limit = int(behavior.get("analyst_review_limit", 40))
    truncation_events: list[dict[str, Any]] = []

    # When analyst_full_review is disabled: mark selected signals as skipped
    # (user deliberately turned off Groq review — not a failure).
    # Default True: absent key means do the review; only explicit False skips it.
    if not behavior.get("analyst_full_review", True):
        for signal in signals[:review_limit]:
            signal.analyst_status = "skipped"
        return signals, [], []

    # First pass: send top-N to Groq.
    reviewed, truncation_events, statuses = _review_signals_in_chunks(
        llm, analyst_prompt, signals, behavior, review_context, short_body_ids=short_body_ids
    )

    updated = []
    for index, signal in enumerate(signals):
        item = reviewed.get(signal.id)
        if not item:
            # Signal not reviewed: either out of review limit or Groq failed.
            st = statuses.get(signal.id, {})
            if index >= review_limit:
                # Outside the review window — intentionally not attempted.
                signal.analyst_status = "skipped"
            else:
                signal.analyst_status = st.get("status", "failed")
                signal.analyst_error_type = st.get("error_type")
                signal.analyst_error_message = st.get("error_message")
                signal.analyst_attempt_count = 1 if signal.id in statuses else 0
                signal.analyst_last_attempt_at = st.get("last_attempt_at")
                # Lazy summary repair for text quality (does not change analyst_status).
                repaired = _repair_lazy_summary(llm, analyst_prompt, signal, review_context.get(signal.id, {}), behavior)
                if repaired.get("short_summary"):
                    signal.short_summary = repaired["short_summary"]
                    signal.summary = signal.short_summary
                if behavior.get("summary_mode") != "short_only" and repaired.get("expanded_summary"):
                    signal.expanded_summary = repaired["expanded_summary"]
            updated.append(signal)
            continue
        # Success path.
        signal.analyst_status = "success"
        signal.analyst_attempt_count = 1
        signal.analyst_last_attempt_at = statuses.get(signal.id, {}).get("last_attempt_at")
        _apply_model_updates(signal, item, analyst_mode, behavior, config, llm, analyst_prompt, review_context)
        updated.append(signal)
    signals = updated

    # Second-pass retry: re-attempt signals marked pending_retry, sorted by score.
    retry_max = int(behavior.get("analyst_retry_max_attempts", 1))
    if retry_max > 0:
        pending = sorted(
            [s for s in signals if s.analyst_status == "pending_retry"],
            key=lambda s: s.score,
            reverse=True,
        )
        if pending:
            reviewed2, _, statuses2 = _review_signals_in_chunks(
                llm, analyst_prompt, pending, behavior, review_context, short_body_ids=short_body_ids
            )
            for signal in pending:
                signal.analyst_attempt_count += 1
                st2 = statuses2.get(signal.id, {})
                signal.analyst_last_attempt_at = st2.get("last_attempt_at") or signal.analyst_last_attempt_at
                item2 = reviewed2.get(signal.id)
                if item2:
                    signal.analyst_status = "success"
                    signal.analyst_error_type = None
                    signal.analyst_error_message = None
                    _apply_model_updates(signal, item2, analyst_mode, behavior, config, llm, analyst_prompt, review_context)
                else:
                    # Cap at retry_max — no third pass.
                    signal.analyst_status = "failed"
                    signal.analyst_error_type = st2.get("error_type") or signal.analyst_error_type
                    signal.analyst_error_message = st2.get("error_message") or signal.analyst_error_message

    # Terminal finalization: no pending_retry must escape this function.
    for signal in signals:
        if signal.analyst_status == "pending_retry":
            signal.analyst_status = "failed"

    # Collect failures within the review window for activity logging (one event per signal).
    analyst_failures = [
        {
            "signal_id": signal.id,
            "title": signal.title,
            "error_type": signal.analyst_error_type,
            "error_message": signal.analyst_error_message,
            "attempt_count": signal.analyst_attempt_count,
        }
        for signal in signals[:review_limit]
        if signal.analyst_status == "failed"
    ]

    return signals, truncation_events, analyst_failures


def _build_artifact(signal: Signal, item: dict[str, Any]) -> dict[str, Any]:
    """Construct the analyst_artifact blob from the model's response and Python overrides.

    Plain English: the Groq response already passed schema validation (the required
    fields exist). Optional fields may be missing or empty — that's fine, we just
    note the gaps. We capture what the model reported, then Python rules can
    downgrade confidence when something looks shaky (heavy truncation or a
    single-source story).
    """
    # Trim any internal-only keys before stamping the artifact.
    trunc_info = item.get("_truncation") or {}

    mechanism = normalize_space(str(item.get("mechanism", "")))
    confidence_reason = normalize_space(str(item.get("confidence_reason", "")))

    # Coerce model_confidence to one of {low, medium, high}; everything else
    # collapses to "low" so downgrade math has a known starting point.
    raw_confidence = str(item.get("confidence", "")).strip().lower()
    if raw_confidence not in {"low", "medium", "high"}:
        raw_confidence = "low"
    model_confidence = raw_confidence

    # Collect optional structured fields. The schema lets these be omitted, but
    # any provided list/dict gets normalized to the shape consumers can rely on.
    key_actors_raw = item.get("key_actors") or []
    key_actors: list[dict[str, str]] = []
    if isinstance(key_actors_raw, list):
        for entry in key_actors_raw:
            if isinstance(entry, dict):
                key_actors.append({
                    "name": normalize_space(str(entry.get("name", ""))),
                    "role": normalize_space(str(entry.get("role", ""))),
                })

    affected_parties_raw = item.get("affected_parties") or []
    affected_parties: list[str] = []
    if isinstance(affected_parties_raw, list):
        affected_parties = [normalize_space(str(p)) for p in affected_parties_raw if str(p).strip()]

    evidence_raw = item.get("evidence_excerpts") or []
    evidence: list[dict[str, Any]] = []
    if isinstance(evidence_raw, list):
        for entry in evidence_raw:
            if isinstance(entry, dict):
                evidence.append({
                    "quote": normalize_space(str(entry.get("quote", ""))),
                    "source_offset": int(entry.get("source_offset", 0)) if str(entry.get("source_offset", "")).lstrip("-").isdigit() else 0,
                })

    # Build the _meta block. We treat the model's "thin output" (missing optional
    # fields or short mechanism) as a quality signal that Python can act on
    # later — we record it here, not as an enum yet, but as a count.
    missing_fields = []
    if not mechanism:
        missing_fields.append("mechanism")
    if not key_actors:
        missing_fields.append("key_actors")
    if not affected_parties:
        missing_fields.append("affected_parties")
    if not evidence:
        missing_fields.append("evidence_excerpts")

    chars_total = int(trunc_info.get("chars_total", 0))
    chars_sent = int(trunc_info.get("chars_sent", chars_total))
    was_truncated = bool(trunc_info.get("was_truncated", False))

    # extraction_quality is a rough roll-up: poor when truncated heavily OR many
    # optional fields are missing; partial when one or two are missing; good otherwise.
    if (was_truncated and chars_total > 0 and chars_sent / chars_total < 0.5) or len(missing_fields) >= 3:
        extraction_quality = "poor"
    elif missing_fields:
        extraction_quality = "partial"
    else:
        extraction_quality = "good"

    meta = {
        "was_truncated": was_truncated,
        "chars_total": chars_total,
        "chars_sent": chars_sent,
        "extraction_quality": extraction_quality,
        "missing_fields": missing_fields,
    }

    # Apply Python's confidence-override rules. The model's own value is kept
    # under model_confidence for telemetry — the post-override value is what
    # downstream consumers (Editor, UI badges) read.
    final_confidence = _override_confidence(
        model_confidence=model_confidence,
        was_truncated=was_truncated,
        chars_total=chars_total,
        chars_sent=chars_sent,
        duplicate_count=signal.duplicate_count,
        missing_count=len(missing_fields),
    )

    return {
        "mechanism": mechanism,
        "key_actors": key_actors,
        "affected_parties": affected_parties,
        "evidence_excerpts": evidence,
        "confidence": final_confidence,
        "confidence_reason": confidence_reason,
        "model_confidence": model_confidence,
        # Critic flags are populated by the Critic worker in Phase 3+; reserve
        # the key so the JSON shape is stable across phases.
        "critic_flags": [],
        "_meta": meta,
    }


def _override_confidence(
    *,
    model_confidence: str,
    was_truncated: bool,
    chars_total: int,
    chars_sent: int,
    duplicate_count: int,
    missing_count: int,
) -> str:
    """Python rules that can only downgrade the model's confidence, never raise it.

    Plain English: the model self-reports how sure it is, but Python overrides
    when the evidence base is thin — heavy truncation, single-source story, or
    too many optional fields missing all collapse the result to "low".
    """
    # Heavy truncation = lost more than half the article. Hard downgrade.
    if was_truncated and chars_total > 0 and (chars_sent / chars_total) < 0.5:
        return "low"
    # Single-source (no corroborating articles in the cluster) erodes confidence.
    if duplicate_count == 0:
        return "low"
    # Three or more optional fields empty signals a thin output — match the
    # extraction_quality="poor" threshold so the artifact reads coherently.
    if missing_count >= 3:
        return "low"
    return model_confidence


def _promote_og_images(signals: list[Signal], review_context: dict[str, dict[str, Any]]) -> None:
    """Promote og:image to signal.image_url when the feed provided no image."""
    for signal in signals:
        if not signal.image_url:
            og = review_context.get(signal.id, {}).get("og_image", "")
            if og and _is_valid_og_image(og):
                signal.image_url = og


def _is_valid_og_image(url: str) -> bool:
    """Return True if url looks like a real image (not a tracking pixel)."""
    if not url.startswith(("http://", "https://")):
        return False
    lower = url.lower()
    if "pixel" in lower or "track" in lower:
        return False
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(url).query)
    if qs.get("w") == ["1"] or qs.get("h") == ["1"]:
        return False
    return True


_FULL_PAGE_MIN_CHARS = 200    # body shorter than this → keep RSS body
_OVERSIZED_TRUNCATION = 8000  # chars — truncate then retry once on context-too-large


def _fetch_full_pages_for_top_n(
    signals: list[Signal],
    review_context: dict[str, dict[str, Any]],
    top_n: int,
) -> tuple[list[Signal], dict[str, dict[str, Any]], set[str]]:
    """Replace article_text in review_context with the full page body for the top-N signals.

    Side-effect: if og:image is found, it is written into review_context so
    _apply_analyst_mode can propagate it to signal.image_url (Wave 5 wires this up fully).
    Falls back to the RSS body already in review_context when extraction fails or is too short.

    Phase 3: also returns short_body_ids — set of signal IDs where extraction yielded
    < _FULL_PAGE_MIN_CHARS. The reviewer loop appends 'short_body' to error_message for
    those signals when Groq review also fails, so the cause is visible without a new column.
    """
    short_body_ids: set[str] = set()
    for signal in signals[:top_n]:
        ctx = dict(review_context.get(signal.id, {}))
        body, og_image = fetch_full_article_page(signal.url)
        if len(body) >= _FULL_PAGE_MIN_CHARS:
            ctx["article_text"] = body
        else:
            short_body_ids.add(signal.id)
            if body:  # non-empty but short — log for visibility
                print(
                    f"[signal_stream] fetch_full_article_page: extraction yielded <{_FULL_PAGE_MIN_CHARS} chars for {signal.url!r}; using RSS body.",
                    file=sys.stderr,
                )
        if og_image:
            ctx["og_image"] = og_image
        review_context[signal.id] = ctx
    return signals, review_context, short_body_ids


def _chat_json_with_truncation_fallback(
    llm: BrainClient,
    system: str,
    user: str,
    schema: dict[str, Any],
    required_fields: list[str] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Call chat_json; on a context-too-large error, truncate article_text to 8000 chars and retry.

    Groq signals a too-large context via HTTP 400 with 'context_length_exceeded'
    (or similar) in the error body. We detect any None return whose last_error
    contains a size hint and retry once with the article body truncated.

    Phase 2: returns (result, truncation_info). truncation_info has shape
    {"was_truncated": bool, "chars_total": int, "chars_sent": int}. callers persist
    it into the per-signal artifact _meta so downstream confidence logic and
    activity events can react.
    """
    # Sum of all article_text lengths in the payload — used as the "what was
    # available" baseline so the artifact can report chars_total / chars_sent.
    chars_total = _sum_article_text_chars(user)
    truncation_info: dict[str, Any] = {
        "was_truncated": False,
        "chars_total": chars_total,
        "chars_sent": chars_total,
    }
    raw = llm.chat_json(system, user, schema, required_fields=required_fields)
    if raw is not None:
        return raw, truncation_info
    last_err = (llm.last_error or "").lower()
    # CAUTION: keep these specific to context-size errors. Do NOT include "limit"
    # or "token" — those appear in rate-limit errors ("rate limit exceeded",
    # "Hit Groq rate limit three times") and would trigger an unnecessary retry.
    if not any(kw in last_err for kw in ("context", "too large", "length")):
        return None, truncation_info
    # Parse payload, truncate article_text, retry once.
    try:
        payload = json.loads(user)
        for signal_item in payload.get("signals", []):
            text = str(signal_item.get("article_text", ""))
            if len(text) > _OVERSIZED_TRUNCATION:
                signal_item["article_text"] = text[:_OVERSIZED_TRUNCATION]
                truncation_info["was_truncated"] = True
                print(
                    f"[signal_stream] Article too large for Groq; truncated to {_OVERSIZED_TRUNCATION} chars and retrying.",
                    file=sys.stderr,
                )
        raw = llm.chat_json(system, json.dumps(payload, sort_keys=True), schema, required_fields=None)
        # chars_sent reflects post-truncation total so confidence logic can compare ratios.
        truncation_info["chars_sent"] = _sum_article_text_chars(json.dumps(payload, sort_keys=True))
    except (json.JSONDecodeError, Exception):  # noqa: BLE001
        pass
    if raw is None:
        print(
            "[signal_stream] Groq still failed after truncation; skipping this signal.",
            file=sys.stderr,
        )
    return raw, truncation_info


def _sum_article_text_chars(user_payload: str) -> int:
    """Count total article_text characters across all signals in a chat_json user payload.

    Used to populate truncation_info.chars_total / chars_sent.
    Returns 0 if the payload doesn't parse — we treat unknown as zero so the
    confidence-downgrade math fails safe (treats it as unknown, not truncated).
    """
    try:
        payload = json.loads(user_payload)
    except (json.JSONDecodeError, TypeError):
        return 0
    total = 0
    for signal_item in payload.get("signals", []):
        total += len(str(signal_item.get("article_text", "")))
    return total


def _review_signals_in_chunks(
    llm: BrainClient,
    analyst_prompt: str,
    signals: list[Signal],
    behavior: dict[str, Any],
    review_context: dict[str, dict[str, Any]],
    short_body_ids: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Review top signals one-per-Groq-request.

    Plain English: batch_size=1 sends each article to Groq individually. This is
    slower but much more reliable — the model can focus on one article at a time
    instead of juggling a batch. Groq's rate limit (30 req/min) is not a concern
    with the 60s 429 retry already in BrainClient.

    Phase 2: returns (reviewed, truncation_events). truncation_events is a list
    of {signal_id, chars_total, chars_sent} dicts surfaced for activity logging.
    The reviewed dict embeds the same info under each signal's "_truncation" key
    so the artifact builder can stamp it into _meta.

    Phase 3: also returns statuses dict {signal_id: {status, error_type, error_message,
    last_attempt_at}}. attempt_count in statuses is always 1 per call to this function
    — callers accumulate across passes. 'short_body_ids' is the set of signal IDs where
    full-page extraction yielded short text; 'short_body' is appended to error_message
    for those signals when Groq also fails, so the compound cause is visible.
    """
    from datetime import datetime, timezone
    limit = int(behavior.get("analyst_review_limit", 40))
    batch_size = max(1, int(behavior.get("analyst_review_batch_size", 1)))
    candidates = signals[:limit]
    reviewed: dict[str, dict[str, Any]] = {}
    truncation_events: list[dict[str, Any]] = []
    statuses: dict[str, dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for index in range(0, len(candidates), batch_size):
        chunk = candidates[index : index + batch_size]
        payload = {
            "task": "review_ranked_signals",
            "signals": [_review_payload(signal, review_context.get(signal.id, {})) for signal in chunk],
            "rules": {
                "score_adjustment_limit": int(behavior.get("model_score_adjustment_limit", 20)),
                "summary_mode": behavior.get("summary_mode", "short_expanded"),
                "entity_extraction": behavior.get("entity_extraction", "hybrid"),
                "short_summary_owner": "model",
                "short_summary_instruction": "Write a fresh 2-3 sentence card summary from article_text. Do not repeat the title. Do not copy the opening sentence. Fold the strategic implication in — no separate why_it_matters field. Use plain English.",
            },
        }
        user_msg = json.dumps(payload, sort_keys=True)
        # required_fields is intentionally NOT passed here because the analyst
        # response wraps fields inside {"signals": [{...}]}, not at the top level.
        # Validation happens below via result.get("signals").
        raw, trunc_info = _chat_json_with_truncation_fallback(
            llm,
            analyst_prompt,
            user_msg,
            ANALYST_REVIEW_SCHEMA,
            required_fields=None,
        )
        if raw is None:
            # Classify error type by inspecting llm.last_error.
            # CAUTION: substring matching is fragile against Groq error format changes.
            # The raw last_error string is always stored in error_message so format
            # shifts surface as 'unknown' with the actual text still visible for debugging.
            raw_error = (llm.last_error or "").lower()
            if any(kw in raw_error for kw in ("rate limit", "three times", "429")):
                error_type = "rate_limit"
                review_status = "pending_retry"
            elif "timeout" in raw_error:
                error_type = "timeout"
                review_status = "pending_retry"
            elif any(kw in raw_error for kw in ("json", "decode")):
                error_type = "invalid_json"
                review_status = "failed"
            elif any(kw in raw_error for kw in ("context", "length")):
                error_type = "extraction_failed"
                review_status = "failed"
            else:
                error_type = "unknown"
                review_status = "failed"

            for signal in chunk:
                # Always store the raw error text (capped) so future format shifts are debuggable.
                short_note = " | short_body" if short_body_ids and signal.id in short_body_ids else ""
                error_msg = (llm.last_error or "")[:200] + short_note
                statuses[signal.id] = {
                    "status": review_status,
                    "error_type": error_type,
                    "error_message": error_msg,
                    "last_attempt_at": now_iso,
                }
            print(
                f"[signal_stream] Groq review failed for signal index {index} "
                f"({error_type}); marking as {review_status}.",
                file=sys.stderr,
            )
            continue
        for item in raw.get("signals", []):
            if not item.get("id"):
                continue
            # Stamp truncation info onto the returned item so the artifact builder
            # downstream can fold it into _meta without re-plumbing.
            item["_truncation"] = dict(trunc_info)
            reviewed[item["id"]] = item
            statuses[item["id"]] = {
                "status": "success",
                "error_type": None,
                "error_message": None,
                "last_attempt_at": now_iso,
            }
            # Only surface a truncation_event when the fallback actually fired —
            # one event per signal in the truncated chunk so the activity log
            # shows which articles got cut down.
            if trunc_info.get("was_truncated"):
                truncation_events.append({
                    "signal_id": item["id"],
                    "chars_total": int(trunc_info.get("chars_total", 0)),
                    "chars_sent": int(trunc_info.get("chars_sent", 0)),
                })
    return reviewed, truncation_events, statuses


def _review_payload(signal: Signal, context: dict[str, Any]) -> dict[str, Any]:
    # Send article_text in full — no [:1800] truncation. The oversized-article
    # policy in _apply_analyst_mode handles the rare case where a single article
    # exceeds Groq's context limit.
    return {
        "id": signal.id,
        "title": signal.title,
        "source": signal.source,
        "event_type": signal.event_type,
        "score": signal.score,
        "score_breakdown": signal.score_breakdown,
        "matched_priorities": signal.matched_priorities,
        "entities": signal.entities,
        "duplicate_count": signal.duplicate_count,
        "article_text": str(context.get("article_text", "")),
        "source_facts": {
            "title": signal.title,
            "source": signal.source,
            "event_type": signal.event_type,
            "published_at": signal.published_at,
            "score_breakdown": signal.score_breakdown,
        },
        "scout_context": {
            "topic": context.get("scout_topic", ""),
            "signal_type": context.get("scout_signal_type", ""),
            "usefulness": context.get("scout_usefulness", ""),
            "note": context.get("scout_note", ""),
            "relevance_label": context.get("relevance_label", ""),
        },
    }


def _repair_lazy_summary(
    llm: BrainClient,
    analyst_prompt: str,
    signal: Signal,
    context: dict[str, Any],
    behavior: dict[str, Any],
) -> dict[str, str]:
    """Ask the model again when the first card summary just copied source text.

    Plain English: the card paragraph is supposed to be written by the model.
    If the batch review gets lazy, this smaller second prompt gives the model a
    cleaner job: write only the summaries from the article text.
    """

    article_text = str(context.get("article_text", ""))
    if not article_text:
        return {}
    user = json.dumps(
        {
            "task": "write_model_owned_signal_summary",
            "title": signal.title,
            "source": signal.source,
            "event_type": signal.event_type,
            "article_text": article_text,
            "rules": {
                "short_summary": "Write 2-3 plain-English sentences. Do not repeat the title. Do not copy the article opening.",
                "expanded_summary": "Write up to two short paragraphs unless summary_mode is short_only.",
                "summary_mode": behavior.get("summary_mode", "short_expanded"),
            },
        },
        sort_keys=True,
    )
    raw = llm.chat_json(analyst_prompt, user, SUMMARY_REPAIR_SCHEMA)
    if not raw:
        return {}
    short = normalize_space(raw.get("short_summary", ""))
    if _looks_like_lazy_summary(short, signal.title):
        return {}
    return {
        "short_summary": short,
        "expanded_summary": normalize_space(raw.get("expanded_summary", "")),
    }


def _event_type(text: str, competitor_hits: list[str]) -> str:
    """Classify a story into one of our event-type buckets.

    Plain English: the bucket with the most keyword matches wins.
    Falls back to competitor_move when competitors are named but no bucket
    dominates, and to general_signal when there are no strong signals.
    """
    best_type = "general_signal"
    best_hits = 0
    for etype, keywords in EVENT_KEYWORDS.items():
        hit_count = len(phrase_hits(text, keywords))
        if hit_count > best_hits:
            best_type = etype
            best_hits = hit_count
    if competitor_hits and best_type == "general_signal":
        return "competitor_move"
    return best_type


def _match_priorities(
    text: str,
    priorities: list,  # list[Priority]
    priority_adjustments: dict[str, float],
) -> list[dict[str, Any]]:
    """Return weighted keyword hits for each priority group that matches.

    Plain English: for every configured priority, count how many of its
    keywords appear in the text. Feedback adjustments shift the weight up or
    down. Only groups with at least one hit are returned.
    """
    matched = []
    for priority in priorities:
        hits = phrase_hits(text, priority.keywords)
        if not hits:
            continue
        adjustment = priority_adjustments.get(priority.name, 0.0)
        effective_weight = max(0.3, priority.weight + adjustment)
        matched.append({
            "name": priority.name,
            "hits": hits[:8],
            "raw_count": len(hits),
            "weight": round(effective_weight, 2),
        })
    return matched


def build_drafts_from_insights(
    ctx: Any,
    insights: list[ClusterInsight],
) -> list[SignalDraft]:
    """Build SignalDraft objects without scoring.

    Shared by the agentic path (analyze_articles) and the legacy orchestrator.
    Scoring — the single source of truth — happens downstream via _base_score_card.
    """
    drafts = []
    for insight in insights:
        text = insight.text
        competitor_hits = insight.entities.get("competitors", [])
        matched_priorities = _match_priorities(text, ctx.config.priorities, ctx.priority_adjustments)
        etype = _event_type(text, competitor_hits)
        drafts.append(SignalDraft(
            cluster=insight.cluster,
            entities=insight.entities,
            matched_priorities=matched_priorities,
            event_type=etype,
            text=text,
        ))
    return drafts


def _base_score_card(
    article: Article,
    draft: Any,
    scoring_rubric: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    """5-component explicit scoring rubric. Max 100 points.

    Components and caps: priority match (25), company match (25),
    recency (15), event strength (25), corroboration (10).

    Plain English: this is the single source of truth for Signal.score.
    No memory penalty, no repeat penalty, no double-counting with a
    separate RelevanceAgent. The model Analyst can still adjust the score
    up or down inside the limit set in the brain file.
    """
    pm_band, pm_pts = _score_priority_match(
        draft.matched_priorities,
        dict(scoring_rubric.get("priority_match_bands", {})),
    )
    cm_band, cm_pts = _score_company_match(
        article,
        draft,
        dict(scoring_rubric.get("company_match_bands", {})),
    )
    rec_band, rec_pts = _score_recency(
        article,
        dict(scoring_rubric.get("recency_bands", {})),
    )
    es_band, es_pts = _score_event_strength(
        article,
        draft,
        dict(scoring_rubric.get("event_strength_bands", {})),
    )
    corr_band, corr_pts = _score_corroboration(
        draft,
        dict(scoring_rubric.get("corroboration_bands", {})),
    )

    priority_names = ", ".join(item["name"] for item in draft.matched_priorities[:2]) or "none"
    breakdown = [
        _score_line("Priority match", pm_pts, f"Band: {pm_band}. Matched: {priority_names}."),
        _score_line("Company match", cm_pts, f"Band: {cm_band}."),
        _score_line("Recency", rec_pts, f"Band: {rec_band}."),
        _score_line("Event strength", es_pts, f"Band: {es_band}. Classified as {draft.event_type.replace('_', ' ')}."),
        _score_line("Corroboration", corr_pts, f"Band: {corr_band}. {len(draft.cluster.articles)} article(s) in this coverage cluster."),
    ]
    total = sum(item["points"] for item in breakdown)
    return max(0, min(100, total)), breakdown


def _score_priority_match(
    matched_priorities: list[dict[str, Any]],
    bands: dict[str, int],
) -> tuple[str, int]:
    """Map weighted keyword intensity to a priority-match band.

    Intensity = max(weight × raw_count) across matched groups.
    Higher-weight groups reach top bands with fewer hits than low-weight groups.
    """
    if not matched_priorities:
        return "no_match", bands.get("no_match", 0)
    intensity = max(item["weight"] * item["raw_count"] for item in matched_priorities)
    if intensity >= 20:
        return "direct_high_impact", bands.get("direct_high_impact", 25)
    if intensity >= 14:
        return "one_central_with_support", bands.get("one_central_with_support", 20)
    if intensity >= 7:
        return "one_central_or_two_weak", bands.get("one_central_or_two_weak", 15)
    if intensity >= 3:
        return "one_relevant_not_central", bands.get("one_relevant_not_central", 10)
    return "weak_incidental", bands.get("weak_incidental", 5)


def _score_company_match(
    article: Article,
    draft: Any,
    bands: dict[str, int],
) -> tuple[str, int]:
    """Score how prominently a watchlist company features in the story.

    Draft.entities["competitors"] already contains only the companies that
    matched our watchlist, so we only need to check position and strategic verbs.
    """
    competitors = draft.entities.get("competitors", [])
    if not competitors:
        return "no_match", bands.get("no_match", 0)

    title_lower = article.title.lower()
    text_lower = f"{article.title} {article.body}".lower()

    # Title mention + a strategic verb = the story is directly about this company acting
    for comp in competitors:
        if comp.lower() in title_lower:
            has_strategic = any(verb in text_lower for verb in _STRATEGIC_VERBS)
            if has_strategic:
                return "watchlist_strategic_action", bands.get("watchlist_strategic_action", 25)
            return "watchlist_in_title_or_lede", bands.get("watchlist_in_title_or_lede", 20)

    # Multiple distinct watchlist companies in the body = central theme
    if len({c.lower() for c in competitors}) >= 2:
        return "watchlist_central", bands.get("watchlist_central", 15)

    # Single company: judge by mention count
    hit_count = text_lower.count(competitors[0].lower())
    if hit_count >= 3:
        return "relevant_not_central", bands.get("relevant_not_central", 10)
    return "one_passing", bands.get("one_passing", 5)


def _score_recency(
    article: Article,
    bands: dict[str, int],
) -> tuple[str, int]:
    """Map article age to a recency band."""
    age = _article_age_days(article.published_at)
    if age is None:
        return "unknown", bands.get("unknown", 7)
    if age <= 1:
        return "within_1_day", bands.get("within_1_day", 15)
    if age <= 3:
        return "within_3_days", bands.get("within_3_days", 12)
    if age <= 7:
        return "within_7_days", bands.get("within_7_days", 9)
    return "older", bands.get("older", 6)


def _score_event_strength(
    article: Article,
    draft: Any,
    bands: dict[str, int],
) -> tuple[str, int]:
    """Map event type + low-value check to a strength band.

    Low-value filter runs first: promotional, roundup, and opinion content
    gets capped at 5 regardless of event type.
    """
    text_lower = f"{article.title} {article.body}".lower()

    # Low-value content gets a hard cap
    if any(phrase in text_lower for phrase in _LOW_VALUE_PHRASES):
        return "opinion_or_listicle", bands.get("opinion_or_listicle", 5)

    etype = draft.event_type
    # Platform launches with an explicit launch verb = strongest signal
    if etype == "platform_shift" and any(
        kw in text_lower for kw in ["launch", "release", "announces", "new model", "frontier"]
    ):
        return "major_platform_shift", bands.get("major_platform_shift", 25)
    if etype in {"regulatory_risk", "startup_signal"}:
        return "launch_funding_regulation", bands.get("launch_funding_regulation", 20)
    if etype in {"platform_shift", "competitor_move", "infrastructure_signal", "market_opportunity"}:
        return "product_update_or_signal", bands.get("product_update_or_signal", 15)
    # builder_tactic, industry_signal, general_signal
    return "useful_analysis", bands.get("useful_analysis", 10)


def _score_corroboration(
    draft: Any,
    bands: dict[str, int],
) -> tuple[str, int]:
    """Score how many independent sources cover the same story."""
    articles = draft.cluster.articles
    distinct_sources = len({a.source for a in articles})

    if distinct_sources <= 1 and len(articles) <= 1:
        return "single", bands.get("single", 0)
    if distinct_sources <= 1:
        return "same_source_repeated", bands.get("same_source_repeated", 3)
    if distinct_sources == 2:
        return "two_independent", bands.get("two_independent", 5)
    if distinct_sources == 3:
        return "three_or_more_independent", bands.get("three_or_more_independent", 8)
    return "broad_cross_type", bands.get("broad_cross_type", 10)


def _article_age_days(published_at: str) -> int | None:
    parsed = _parse_date(published_at)
    if not parsed:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def _score_line(name: str, points: int, reason: str) -> dict[str, Any]:
    return {"name": name, "points": int(points), "reason": reason}


def _looks_like_lazy_summary(summary: str, title: str) -> bool:
    """Catch model outputs that just echo the title or scraped opening.

    Plain English: the short card paragraph should read like an analyst wrote
    it. If it starts by repeating the headline, we keep the fallback instead of
    pretending that was a real model summary.
    """

    cleaned = normalize_space(summary).lower()
    title_cleaned = normalize_space(title).lower()
    if not cleaned:
        return True
    if title_cleaned and cleaned.startswith(title_cleaned[: min(50, len(title_cleaned))]):
        return True
    return len(cleaned.split()) > 90


def _rough_summary(text: str) -> str:
    """Fallback summary used only when the model cannot review the signal.

    Plain English: in hybrid/model mode, the brain owns the final short summary.
    This rough version keeps the app usable if the local model is unavailable.
    """

    return first_sentences(text, max_sentences=2)


def _rough_expanded_summary(text: str) -> str:
    """Fallback expanded summary for model-off or model-failure runs."""

    return first_sentences(text, max_sentences=6, max_chars=1200)


def _bounded_model_score(base_score: int, model_score: int, analyst_mode: str, limit: int) -> int:
    """Keep model score changes understandable.

    Plain English: the model can disagree with the rubric, but in hybrid mode it
    cannot swing the score wildly unless the brain file allows a bigger limit.
    """

    if analyst_mode == "model":
        return model_score
    lower = base_score - max(0, limit)
    upper = base_score + max(0, limit)
    return max(lower, min(upper, model_score))


def _merge_entities(existing: dict[str, list[str]], model_entities: Any, behavior: dict[str, Any]) -> dict[str, list[str]]:
    mode = str(behavior.get("entity_extraction", "hybrid"))
    if not isinstance(model_entities, dict):
        return existing
    if mode == "known_list":
        return existing
    merged = {} if mode == "model" else {key: list(value) for key, value in existing.items()}
    for key, values in model_entities.items():
        if not isinstance(values, list):
            continue
        bucket = merged.setdefault(str(key), [])
        seen = {item.lower() for item in bucket}
        for value in values:
            text = normalize_space(str(value))
            if text and text.lower() not in seen:
                bucket.append(text)
                seen.add(text.lower())
    return merged


def _icon_key(event_type: str, entities: dict | None = None, topic: str = "") -> str:
    # Company-specific icons take priority — a story about NVIDIA chips is more
    # usefully represented by a chip icon than by the generic event-type icon.
    _COMPANY_ICONS: dict[str, str] = {
        "nvidia": "chip",
        "amd": "chip",
        "intel": "chip",
        "anthropic": "claude",
        "openai": "openai",
        "google": "google",
        "deepmind": "google",
        "meta": "meta",
        "microsoft": "microsoft",
        "apple": "apple",
        "amazon": "amazon",
        "xai": "xai",
        "mistral": "mistral",
        "perplexity": "perplexity",
    }
    if entities:
        for comp in entities.get("competitors", []):
            icon = _COMPANY_ICONS.get(comp.lower())
            if icon:
                return icon
    # Topic fallback: "chip"/"gpu" in topic text → chip icon
    if topic:
        topic_lower = topic.lower()
        if any(kw in topic_lower for kw in ("gpu", "chip", "accelerator", "tpu")):
            return "chip"
        if any(kw in topic_lower for kw in ("regulation", "policy", "copyright", "safety")):
            return "risk"
    # Event-type default
    mapping = {
        "platform_shift": "platform",
        "competitor_move": "competitor",
        "regulatory_risk": "risk",
        "asset_risk": "risk",
        "infrastructure_signal": "infrastructure",
        "startup_signal": "startup",
        "builder_tactic": "builder",
        "market_opportunity": "market",
    }
    return mapping.get(event_type, "signal")


# ---------------------------------------------------------------------------
# Digest renderer (moved here from orchestrator.py to break circular import)
# ---------------------------------------------------------------------------

def render_digest(config: SignalConfig, signals: list[Signal], events: object) -> str:
    top_signals = signals[: config.digest_limit]
    critical = [signal for signal in top_signals if signal.urgency == "critical"]

    lines = [
        "# Signal Stream Briefing",
        "",
        f"Generated: {utc_now_iso()}",
        f"Organization: {config.organization or config.name}",
        f"Audience: {config.audience}",
        "",
        "## Executive Snapshot",
        "",
        f"- Signals reviewed: {len(signals)}",
        f"- Critical alerts: {len(critical)}",
        f"- Delivery mode: local Markdown digest",
        "",
    ]

    if critical:
        lines.extend(["## Critical Alerts", ""])
        for signal in critical:
            lines.extend(_signal_block(signal))

    lines.extend(["## Ranked Signals", ""])
    for signal in top_signals:
        lines.extend(_signal_block(signal))

    lines.extend(["## Agent Trace", ""])
    for event in events:
        meta = f" {event.metadata}" if event.metadata else ""
        lines.append(f"- {event.agent}: {event.message}{meta}")
    lines.append("")
    return "\n".join(lines)


def _signal_block(signal: Signal) -> list[str]:
    source = signal.source
    if signal.published_at:
        source = f"{source}, {signal.published_at}"
    duplicate_note = f" ({signal.duplicate_count} related item{'s' if signal.duplicate_count != 1 else ''})" if signal.duplicate_count else ""
    lines = [
        f"### {signal.score}/100 - {signal.title}",
        "",
        f"- Urgency: {signal.urgency}",
        f"- Event type: {signal.event_type}{duplicate_note}",
        f"- Source: {source}",
    ]
    if signal.url:
        lines.append(f"- Link: {signal.url}")
    lines.extend(
        [
            "",
            f"Summary: {signal.short_summary or signal.summary}",
            "",
            f"Expanded summary: {signal.expanded_summary or signal.short_summary or signal.summary}",
            "",
            f"Why it matters: {signal.why_it_matters}",
            "",
        ]
    )
    if signal.scout_note:
        lines.extend([f"Scout note: {signal.scout_note}", ""])
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Critic tool
# ---------------------------------------------------------------------------

CRITIC_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "weak_indices": {"type": "array", "items": {"type": "integer"}},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["score", "weak_indices", "reasons"],
}

def score_digest_quality(
    signals: list[dict[str, Any]],
    critic_prompt: str,
    llm: Any,
    *,
    critic_mode: str = "code",
) -> dict[str, Any]:
    """Score the proposed digest and flag weak signals for revision.

    Returns a dict with keys: score (0-100), weak_indices (list[int]), reasons (list[str]).

    The code path does structural integrity only: are required fields present?
    All content judgment (low-value phrases, score quality, duplicate detection,
    summary quality) belongs to the LLM Critic via critic_prompt. The code path
    is a safety net for when the brain is unavailable — not a rule engine.
    """

    if not signals:
        # Nothing to critique; report a perfect score so the runtime finalizes.
        return {"score": 100, "weak_indices": [], "reasons": []}

    weak_indices: list[int] = []
    reasons: list[str] = []

    # Structural integrity only — these are data checks, not content judgments.
    for idx, signal in enumerate(signals):
        problems: list[str] = []

        # A missing why_it_matters means the LLM Analyst didn't run or failed.
        why = str(signal.get("why_it_matters", "")).strip()
        if not why:
            problems.append("why_it_matters is missing — Analyst did not complete this field")

        # A missing summary means the signal has no usable content at all.
        summary = str(signal.get("short_summary", signal.get("summary", ""))).strip()
        if not summary:
            problems.append("short_summary is missing — signal has no content")

        if problems:
            weak_indices.append(idx)
            reasons.append("; ".join(problems))

    # Compute a rough aggregate score based on fraction of clean signals.
    clean_fraction = (len(signals) - len(weak_indices)) / len(signals)
    # Penalise harder as more signals are weak (non-linear).
    code_score = int(clean_fraction ** 1.5 * 100)

    # LLM-based check — only in hybrid or model mode, and only when an LLM is
    # available. The code score is returned immediately if the LLM is skipped or
    # fails, so the runtime is never blocked by a brain outage.
    if critic_mode in {"hybrid", "model"} and llm is not None:
        try:
            if llm.available():
                signal_summary = json.dumps(
                    [
                        {
                            "index": i,
                            "title": s.get("title", ""),
                            "score": s.get("score", 0),
                            "short_summary": s.get("short_summary", s.get("summary", ""))[:200],
                            "why_it_matters": s.get("why_it_matters", "")[:150],
                        }
                        for i, s in enumerate(signals)
                    ],
                    ensure_ascii=False,
                )
                raw = llm.chat_json(critic_prompt, signal_summary, CRITIC_REVIEW_SCHEMA)
                if isinstance(raw, dict):
                    model_score = int(raw.get("score", code_score))
                    model_weak = [int(i) for i in raw.get("weak_indices", []) if 0 <= int(i) < len(signals)]
                    model_reasons = [str(r) for r in raw.get("reasons", [])]
                    # Merge model findings with code findings; deduplicate indices.
                    seen = set(weak_indices)
                    for i, reason in zip(model_weak, model_reasons):
                        if i not in seen:
                            weak_indices.append(i)
                            reasons.append(f"[model] {reason}")
                            seen.add(i)
                    # In hybrid mode, blend code and model scores; model wins in model mode.
                    if critic_mode == "model":
                        final_score = model_score
                    else:
                        final_score = (code_score + model_score) // 2
                    return {"score": final_score, "weak_indices": sorted(weak_indices), "reasons": reasons}
        except Exception:  # noqa: BLE001 - critic failure must not abort the run.
            pass

    return {"score": code_score, "weak_indices": sorted(weak_indices), "reasons": reasons}
