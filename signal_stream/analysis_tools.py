from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from typing import Any

from .agents import ClusterAgent, EntityAgent, RelevanceAgent
from .llm import OllamaClient
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
    trace = AgentRunLog()
    ctx = _Context(config=config, storage=storage, llm=OllamaClient(config), trace=trace)

    normalized = _dedupe_exact(articles, config.agent.max_article_age_days)
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
        short_summary = first_sentences(article.body, max_sentences=2)
        expanded_summary = "" if behavior.get("summary_mode") == "short_only" else first_sentences(article.body, max_sentences=6, max_chars=1200)
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
            why_it_matters=_why_it_matters(draft, memory_hits, score),
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
            "article_text": article.body[:4500],
            "scout_topic": article.raw.get("scout_topic", ""),
            "scout_signal_type": article.raw.get("scout_signal_type", ""),
            "scout_usefulness": article.raw.get("scout_usefulness", ""),
            "scout_note": article.raw.get("scout_note", ""),
            "relevance_label": article.raw.get("scout_relevance_label", "keep"),
        }

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
    def __init__(self, config: SignalConfig, storage: SignalStorage, llm: OllamaClient, trace: AgentRunLog):
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


def _dedupe_exact(articles: list[Article], max_age_days: int) -> list[Article]:
    """Remove exact repeats and stale daily-digest items.

    Plain English: if a feed hands us a post from last year, it should not beat
    real current news in today's Signal Stream run.
    """

    seen = set()
    kept = []
    for article in articles:
        article.title = normalize_space(article.title)
        article.body = normalize_space(article.body) or article.title
        key = (article.url or article.title).lower()
        if not article.title or key in seen:
            continue
        if _is_stale(article.published_at, max_age_days):
            continue
        seen.add(key)
        kept.append(article)
    return kept


def _is_stale(value: str, max_age_days: int) -> bool:
    if max_age_days <= 0 or not value:
        return False
    parsed = _parse_date(value)
    if not parsed:
        return False
    age = datetime.now(timezone.utc) - parsed
    return age.days > max_age_days


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


def _why_it_matters(draft: Any, memory_hits: list[dict[str, Any]], score: int) -> str:
    priorities = [item["name"] for item in draft.matched_priorities[:2]]
    if memory_hits:
        return f"Related to prior coverage, but may add a new angle. Matched {', '.join(priorities) if priorities else 'general AI/tech priorities'}."
    if priorities:
        return f"Matched {', '.join(priorities)} and landed at {score}/100 on the base rubric."
    return "Potentially relevant AI/tech signal; review before acting."


def _next_steps(event_type: str) -> list[str]:
    if event_type in {"regulatory_risk", "asset_risk"}:
        return ["Check whether this changes product or vendor risk.", "Watch for second-source confirmation."]
    if event_type in {"competitor_move", "platform_shift"}:
        return ["Compare against current product assumptions.", "Track follow-up posts from competitors and builders."]
    if event_type == "market_opportunity":
        return ["Identify who benefits if this trend continues.", "Look for funding, hiring, or customer evidence."]
    return ["Save to memory if useful.", "Look for corroborating coverage before treating it as a major signal."]


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

    llm = OllamaClient(config)
    if not llm.available():
        return signals

    payload = {
        "task": "review_ranked_signals",
        "signals": [
            {
                "id": signal.id,
                "title": signal.title,
                "source": signal.source,
                "event_type": signal.event_type,
                "score": signal.score,
                "score_breakdown": signal.score_breakdown,
                "matched_priorities": signal.matched_priorities,
                "entities": signal.entities,
                "duplicate_count": signal.duplicate_count,
                "short_summary": signal.short_summary or signal.summary,
                "expanded_summary": signal.expanded_summary or signal.summary,
                "why_it_matters": signal.why_it_matters,
                "article_text": review_context.get(signal.id, {}).get("article_text", ""),
                "scout_context": {
                    "topic": review_context.get(signal.id, {}).get("scout_topic", ""),
                    "signal_type": review_context.get(signal.id, {}).get("scout_signal_type", ""),
                    "usefulness": review_context.get(signal.id, {}).get("scout_usefulness", ""),
                    "note": review_context.get(signal.id, {}).get("scout_note", ""),
                    "relevance_label": review_context.get(signal.id, {}).get("relevance_label", ""),
                },
            }
            for signal in signals[:20]
        ],
        "rules": {
            "score_adjustment_limit": int(behavior.get("model_score_adjustment_limit", 20)),
            "summary_mode": behavior.get("summary_mode", "short_expanded"),
            "entity_extraction": behavior.get("entity_extraction", "hybrid"),
        },
    }
    raw = llm.chat_json(analyst_prompt, json.dumps(payload, sort_keys=True), ANALYST_REVIEW_SCHEMA)
    if not raw:
        return signals

    reviewed = {item.get("id"): item for item in raw.get("signals", []) if item.get("id")}
    updated = []
    for signal in signals:
        item = reviewed.get(signal.id)
        if not item:
            updated.append(signal)
            continue
        model_score = int(item.get("score", signal.score))
        merged_score = _bounded_model_score(signal.score, model_score, analyst_mode, int(behavior.get("model_score_adjustment_limit", 20)))
        signal.score = max(0, min(100, merged_score))
        signal.short_summary = normalize_space(item.get("short_summary", signal.short_summary or signal.summary)) or signal.short_summary or signal.summary
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
