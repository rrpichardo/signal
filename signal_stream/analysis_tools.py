from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from typing import Any

from .agents import ClusterAgent, EntityAgent, RelevanceAgent
from .llm import BrainClient
from .models import AgentRunLog, Article, ClusterInsight, Signal, SignalConfig, stable_id
from .orchestrator import render_digest
from .prompt_loader import DEFAULT_SCORING_RUBRIC
from .storage import SignalStorage
from .text import first_sentences, normalize_space


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
                    "why_it_matters": {"type": "string"},
                    "entities": {"type": "object"},
                },
                "required": ["id", "score", "short_summary", "expanded_summary", "why_it_matters", "entities"],
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
    drafts = RelevanceAgent().run(ctx, insights)

    behavior = behavior or {}
    signals = []
    review_context: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        article = draft.cluster.articles[0]
        event_type = _theme(draft, insights)
        memory_hits = storage.memory_matches(f"{article.title} {article.body}", limit=3)
        score, score_breakdown = _base_score_card(article, draft, memory_hits, event_type, scoring_rubric or DEFAULT_SCORING_RUBRIC, behavior)
        # Code-path fallback: hand the LLM raw material, not a heuristic summary.
        # The Analyst prompt specifies exactly how to rewrite this into proper prose.
        # When the brain is unavailable the raw excerpt is shown as-is — honest fallback.
        short_summary = first_sentences(article.body, max_sentences=2, max_chars=200)
        expanded_summary = "" if behavior.get("summary_mode") == "short_only" else first_sentences(article.body, max_sentences=4, max_chars=600)
        visuals_mode = str(behavior.get("visuals_mode", "image_icon"))
        image_url = str(article.raw.get("image_url", "")) if visuals_mode == "image_icon" else ""
        icon_key = _icon_key(event_type) if visuals_mode != "none" else ""
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
            event_type=event_type,
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

    # Sort before the model pass so the dashboard's most visible cards get
    # model-written summaries first.
    signals.sort(key=lambda item: item.score, reverse=True)
    signals = _apply_analyst_mode(signals, config, analyst_mode, analyst_prompt, behavior, review_context)
    signals.sort(key=lambda item: item.score, reverse=True)
    digest = render_digest(config, signals, trace.events)
    return {
        "article_count": len(normalized),
        "cluster_count": len(clusters),
        "signals": [_signal_json(signal) for signal in signals],
        "digest": digest,
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


def _theme(draft: Any, insights: list[ClusterInsight]) -> str:
    if draft.event_type != "general_signal":
        return draft.event_type
    if draft.entities.get("competitors"):
        return "platform_shift"
    return "industry_signal"



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
    }


def _apply_analyst_mode(
    signals: list[Signal],
    config: SignalConfig,
    analyst_mode: str,
    analyst_prompt: str,
    behavior: dict[str, Any],
    review_context: dict[str, dict[str, Any]],
) -> list[Signal]:
    """Optionally let the model polish Analyst output.

    Plain English: the code still does the dependable base work first
    (dedupe, scoring, memory penalties). In hybrid/model mode, the LLM gets a
    second pass to improve human-judgment fields like summary and why-it-matters.
    """
    if analyst_mode not in {"hybrid", "model"} or not signals:
        return signals

    llm = BrainClient(config)
    if not llm.available():
        return signals

    # The focused summary repair is the default for local models because it is
    # fast and directly handles the visible card paragraph. Full review can be
    # turned on later from the brain file if a stronger model is available.
    reviewed = _review_signals_in_chunks(llm, analyst_prompt, signals, behavior, review_context) if behavior.get("analyst_full_review") else {}
    updated = []
    review_limit = int(behavior.get("analyst_review_limit", 30))
    for index, signal in enumerate(signals):
        item = reviewed.get(signal.id)
        if not item:
            if index < review_limit:
                repaired = _repair_lazy_summary(llm, analyst_prompt, signal, review_context.get(signal.id, {}), behavior)
                if repaired.get("short_summary"):
                    signal.short_summary = repaired["short_summary"]
                    signal.summary = signal.short_summary
                if behavior.get("summary_mode") != "short_only" and repaired.get("expanded_summary"):
                    signal.expanded_summary = repaired["expanded_summary"]
            updated.append(signal)
            continue
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
        updated.append(signal)
    return updated


def _review_signals_in_chunks(
    llm: BrainClient,
    analyst_prompt: str,
    signals: list[Signal],
    behavior: dict[str, Any],
    review_context: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Review top signals in small model calls.

    Plain English: local models struggle with giant prompts. Smaller batches are
    slower than one huge request, but much more likely to actually produce the
    model-written summaries we want.
    """

    limit = int(behavior.get("analyst_review_limit", 30))
    candidates = signals[:limit]
    reviewed: dict[str, dict[str, Any]] = {}
    for index in range(0, len(candidates), 5):
        chunk = candidates[index : index + 5]
        payload = {
            "task": "review_ranked_signals",
            "signals": [_review_payload(signal, review_context.get(signal.id, {})) for signal in chunk],
            "rules": {
                "score_adjustment_limit": int(behavior.get("model_score_adjustment_limit", 20)),
                "summary_mode": behavior.get("summary_mode", "short_expanded"),
                "entity_extraction": behavior.get("entity_extraction", "hybrid"),
                "short_summary_owner": "model",
                "short_summary_instruction": "Write a fresh 2-3 sentence card summary from article_text. Do not repeat the title. Do not copy the opening sentence. Use plain English.",
            },
        }
        raw = llm.chat_json(analyst_prompt, json.dumps(payload, sort_keys=True), ANALYST_REVIEW_SCHEMA)
        if not raw:
            continue
        for item in raw.get("signals", []):
            if item.get("id"):
                reviewed[item["id"]] = item
    return reviewed


def _review_payload(signal: Signal, context: dict[str, Any]) -> dict[str, Any]:
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
        "why_it_matters": signal.why_it_matters,
        "article_text": str(context.get("article_text", ""))[:1800],
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


def _base_score_card(
    article: Article,
    draft: Any,
    memory_hits: list[dict[str, Any]],
    event_type: str,
    scoring_rubric: dict[str, Any],
    behavior: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    """Build Analyst's explicit base rubric.

    Plain English: the code cannot "understand" nuance like an LLM, so it uses
    visible clues that often correlate with importance. The model can then
    review this scorecard and disagree when the meaning of the story warrants it.
    """

    duplicate_count = max(0, len(draft.cluster.articles) - 1)
    freshness = _freshness_points(article.published_at, scoring_rubric)
    max_points = dict(scoring_rubric.get("max_points", {}))
    priority_match = min(int(max_points.get("priority_match", 25)), round(sum(float(item.get("points", 0.0)) for item in draft.matched_priorities[:2])))
    competitor_hits = len({item.lower() for item in draft.entities.get("competitors", [])})
    organization_hits = len({item.lower() for item in draft.entities.get("organizations", [])})
    major_players = min(int(max_points.get("major_player", 15)), (competitor_hits * 7) + max(0, organization_hits - competitor_hits) * 2)
    event_strength = int(dict(scoring_rubric.get("event_strength", {})).get(event_type, dict(scoring_rubric.get("event_strength", {})).get("default", 8)))
    corroboration = min(int(max_points.get("corroboration", 10)), duplicate_count * 3 + (2 if duplicate_count else 0))
    repeat_step = _repeat_penalty_step(str(behavior.get("repeat_penalty_strength", "strong")))
    repeat_penalty = -min(int(max_points.get("repeat_penalty", 20)), len(memory_hits) * repeat_step)
    low_value_penalty = -int(max_points.get("low_value_penalty", 15)) if _looks_low_value(article, scoring_rubric) else 0

    breakdown = [
        _score_line("Freshness", freshness, _freshness_reason(article.published_at)),
        _score_line("Priority match", priority_match, _priority_reason(draft)),
        _score_line("Major-player involvement", major_players, _major_player_reason(draft)),
        _score_line("Event strength", event_strength, f"Classified as {event_type.replace('_', ' ')}."),
        _score_line("Corroboration", corroboration, f"{duplicate_count + 1} article(s) in this coverage cluster."),
        _score_line("Repeat penalty", repeat_penalty, f"{len(memory_hits)} recent memory hit(s) suggest possible repetition."),
        _score_line("Low-value content penalty", low_value_penalty, _low_value_reason(article, scoring_rubric)),
    ]
    total = sum(int(item["points"]) for item in breakdown)
    return max(0, min(100, total)), breakdown


def _freshness_points(published_at: str, scoring_rubric: dict[str, Any]) -> int:
    freshness = dict(scoring_rubric.get("freshness", {}))
    age = _article_age_days(published_at)
    if age is None:
        return int(freshness.get("unknown", 10))
    if age <= 1:
        return int(freshness.get("within_1_day", 20))
    if age <= 3:
        return int(freshness.get("within_3_days", 17))
    if age <= 7:
        return int(freshness.get("within_7_days", 13))
    return int(freshness.get("older", 8))


def _article_age_days(published_at: str) -> int | None:
    parsed = _parse_date(published_at)
    if not parsed:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def _freshness_reason(published_at: str) -> str:
    age = _article_age_days(published_at)
    if age is None:
        return "Publication date unclear, so freshness gets a neutral score."
    if age <= 1:
        return "Published within the last day."
    if age <= 3:
        return "Published within the last 3 days."
    if age <= 7:
        return "Published within the last week."
    return "Older than a week, so freshness score is reduced."


def _priority_reason(draft: Any) -> str:
    if not draft.matched_priorities:
        return "Did not clearly match configured priority themes."
    names = ", ".join(item.get("name", "Unnamed priority") for item in draft.matched_priorities[:2])
    return f"Matched {names}."


def _major_player_reason(draft: Any) -> str:
    competitors = draft.entities.get("competitors", [])
    organizations = draft.entities.get("organizations", [])
    if competitors:
        return f"Involves known major players: {', '.join(competitors[:3])}."
    if organizations:
        return f"Mentions named organizations: {', '.join(organizations[:3])}."
    return "No major known player was detected."


def _looks_low_value(article: Article, scoring_rubric: dict[str, Any]) -> bool:
    text = f"{article.title} {article.body}".lower()
    phrases = [str(item).lower() for item in scoring_rubric.get("low_value_phrases", [])]
    return any(phrase in text for phrase in phrases)


def _low_value_reason(article: Article, scoring_rubric: dict[str, Any]) -> str:
    if _looks_low_value(article, scoring_rubric):
        return "Looks like promotional, roundup, or low-signal content."
    return "No obvious low-value or promotional pattern detected."


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


def _repeat_penalty_step(strength: str) -> int:
    if strength == "light":
        return 5
    if strength == "medium":
        return 8
    return 12


def _icon_key(event_type: str) -> str:
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
