from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import xml.etree.ElementTree as ET
from typing import Any
from urllib import request

from .llm import BrainClient
from .models import (
    AgentRunLog,
    Article,
    Cluster,
    ClusterInsight,
    Signal,
    SignalConfig,
    SignalDraft,
    stable_id,
)
from .storage import SignalStorage
from .text import clean_html, extract_named_entities, first_sentences, jaccard, normalize_space, phrase_hits, tokenize


URGENCY_TERMS = [
    "breaking",
    "urgent",
    "investigation",
    "lawsuit",
    "security",
    "outage",
    "breach",
    "funding",
    "acquisition",
    "risk",
    "compliance",
    "regulatory",
]


@dataclass
class AgentContext:
    config: SignalConfig
    storage: SignalStorage
    llm: BrainClient
    trace: AgentRunLog = field(default_factory=AgentRunLog)
    priority_adjustments: dict[str, float] = field(default_factory=dict)


class FeedbackAgent:
    name = "FeedbackAgent"

    def run(self, ctx: AgentContext) -> None:
        ctx.priority_adjustments = ctx.storage.load_priority_adjustments()
        ctx.trace.add(self.name, "Loaded feedback-based priority adjustments.", adjustments=ctx.priority_adjustments)


class IngestAgent:
    name = "IngestAgent"

    def run(self, ctx: AgentContext) -> list[Article]:
        articles: list[Article] = []
        for source in ctx.config.sources:
            if not source.enabled:
                continue
            if source.kind in {"sample", "json"}:
                loaded = self._load_json_source(source.name, source.path, source.limit)
            elif source.kind in {"rss", "atom"}:
                loaded = self._load_feed_source(source.name, source.url, source.limit)
            else:
                ctx.trace.add(self.name, "Skipped source with unsupported kind.", source=source.name, kind=source.kind)
                continue
            articles.extend(loaded)
            ctx.trace.add(self.name, "Loaded source.", source=source.name, kind=source.kind, count=len(loaded))
        return articles

    def _load_json_source(self, source_name: str, path: str | None, limit: int) -> list[Article]:
        if not path:
            return []
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        articles = []
        for item in data[:limit]:
            articles.append(
                Article.from_fields(
                    source=item.get("source") or source_name,
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    published_at=item.get("published_at", ""),
                    body=item.get("body", ""),
                    raw=item,
                )
            )
        return articles

    def _load_feed_source(self, source_name: str, url: str | None, limit: int) -> list[Article]:
        if not url:
            return []
        req = request.Request(url, headers={"User-Agent": "SignalStreamPrototype/0.1"})
        with request.urlopen(req, timeout=20) as response:
            payload = response.read(2_500_000)
        root = ET.fromstring(payload)
        entries = self._feed_entries(root)[:limit]
        return [self._article_from_entry(source_name, entry) for entry in entries]

    def _feed_entries(self, root: ET.Element) -> list[ET.Element]:
        local = _local_name(root.tag)
        if local == "feed":
            return [child for child in list(root) if _local_name(child.tag) == "entry"]
        channel = next((child for child in root.iter() if _local_name(child.tag) == "channel"), root)
        return [child for child in list(channel) if _local_name(child.tag) == "item"]

    def _article_from_entry(self, source_name: str, entry: ET.Element) -> Article:
        title = _child_text(entry, "title")
        url = _entry_link(entry)
        published_at = _child_text(entry, "published", "updated", "pubDate", "date")
        body = clean_html(_child_text(entry, "description", "summary", "encoded", "content"))
        return Article.from_fields(source=source_name, title=title, url=url, published_at=published_at, body=body, raw={"feed": source_name})


class NormalizeAgent:
    name = "NormalizeAgent"

    def run(self, ctx: AgentContext, articles: list[Article]) -> list[Article]:
        seen: set[str] = set()
        normalized: list[Article] = []
        for article in articles:
            article.title = normalize_space(article.title)
            article.body = normalize_space(clean_html(article.body))
            if not article.title:
                continue
            if not article.body:
                article.body = article.title
            key = stable_id(article.url or article.title, article.source, prefix="norm")
            if key in seen:
                continue
            seen.add(key)
            normalized.append(article)
        ctx.trace.add(self.name, "Cleaned and deduplicated exact repeats.", before=len(articles), after=len(normalized))
        return normalized


class ClusterAgent:
    name = "ClusterAgent"

    def run(self, ctx: AgentContext, articles: list[Article]) -> list[Cluster]:
        clusters: list[Cluster] = []
        cluster_tokens: list[set[str]] = []
        cluster_title_tokens: list[set[str]] = []

        for article in articles:
            body_tokens = tokenize(f"{article.title} {article.body}")
            title_tokens = tokenize(article.title)
            best_index = -1
            best_score = 0.0

            for index, cluster in enumerate(clusters):
                score = max(jaccard(body_tokens, cluster_tokens[index]), jaccard(title_tokens, cluster_title_tokens[index]))
                lead = cluster.articles[0]
                same_url = article.url and article.url == lead.url
                same_title = article.title.lower() == lead.title.lower()
                if same_url or same_title:
                    score = 1.0
                if score > best_score:
                    best_index = index
                    best_score = score

            if best_index >= 0 and best_score >= ctx.config.similarity_threshold:
                clusters[best_index].articles.append(article)
                cluster_tokens[best_index] |= body_tokens
                cluster_title_tokens[best_index] |= title_tokens
                clusters[best_index].similarity = max(clusters[best_index].similarity, best_score)
            else:
                clusters.append(Cluster(id=stable_id(article.id, prefix="cluster"), articles=[article]))
                cluster_tokens.append(set(body_tokens))
                cluster_title_tokens.append(set(title_tokens))

        for cluster in clusters:
            cluster.id = stable_id(*(article.id for article in cluster.articles), prefix="cluster")

        ctx.trace.add(self.name, "Grouped near-duplicate coverage.", articles=len(articles), clusters=len(clusters))
        return clusters


class EntityAgent:
    name = "EntityAgent"

    def run(self, ctx: AgentContext, clusters: list[Cluster]) -> list[ClusterInsight]:
        insights: list[ClusterInsight] = []
        for cluster in clusters:
            text = _cluster_text(cluster)
            entities = {
                "competitors": phrase_hits(text, ctx.config.competitors),
                "markets": phrase_hits(text, ctx.config.markets),
                "organizations": extract_named_entities(text, ctx.config.competitors),
            }
            insights.append(ClusterInsight(cluster=cluster, entities=entities, text=text))
        ctx.trace.add(self.name, "Extracted configured entities and organization names.", clusters=len(insights))
        return insights


class BriefingAgent:
    name = "BriefingAgent"

    def run(self, ctx: AgentContext, drafts: list[SignalDraft]) -> list[Signal]:
        signals: list[Signal] = []
        llm_available = ctx.llm.available()
        if not llm_available:
            ctx.trace.add(self.name, "Brain unavailable, using deterministic summaries.", error=ctx.llm.last_error or "")

        for draft in drafts:
            article = draft.cluster.articles[0]
            llm_brief = ctx.llm.summarize_signal(draft, ctx.config) if llm_available else None
            summary = normalize_space((llm_brief or {}).get("summary")) or first_sentences(article.body)
            why = normalize_space((llm_brief or {}).get("why_it_matters")) or _heuristic_why(draft)
            next_steps = (llm_brief or {}).get("next_steps") or _next_steps(draft)
            next_steps = [normalize_space(step) for step in next_steps if normalize_space(step)][:3]
            signal_id = stable_id(draft.cluster.id, article.title, draft.score, prefix="sig")
            signals.append(
                Signal(
                    id=signal_id,
                    cluster_id=draft.cluster.id,
                    article_id=article.id,
                    title=article.title,
                    url=article.url,
                    source=article.source,
                    published_at=article.published_at,
                    score=draft.score,
                    urgency=draft.urgency,
                    event_type=draft.event_type,
                    summary=summary,
                    why_it_matters=why,
                    next_steps=next_steps,
                    matched_priorities=draft.matched_priorities,
                    entities=draft.entities,
                    duplicate_count=max(0, len(draft.cluster.articles) - 1),
                )
            )
        ctx.trace.add(self.name, "Prepared executive signal briefs.", signals=len(signals), llm_used=llm_available)
        return signals


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(entry: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(entry):
        if _local_name(child.tag).lower() in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _entry_link(entry: ET.Element) -> str:
    for child in list(entry):
        if _local_name(child.tag).lower() != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href
        if child.text:
            return child.text.strip()
    return ""


def _cluster_text(cluster: Cluster) -> str:
    parts = []
    for article in cluster.articles:
        parts.append(f"{article.title}. {article.body}")
    return normalize_space(" ".join(parts))


def _heuristic_why(draft: SignalDraft) -> str:
    priorities = [item["name"] for item in draft.matched_priorities[:2]]
    competitors = draft.entities.get("competitors", [])
    markets = draft.entities.get("markets", [])
    pieces = []
    if priorities:
        pieces.append(f"Touches {', '.join(priorities)}")
    if competitors:
        pieces.append(f"mentions {', '.join(competitors[:3])}")
    if markets:
        pieces.append(f"affects {', '.join(markets[:3])}")
    if not pieces:
        return "May matter as an early external signal, but it needs human review before action."
    return f"{'; '.join(pieces)}. Score {draft.score} with {draft.urgency} urgency."


def _next_steps(draft: SignalDraft) -> list[str]:
    if draft.event_type == "asset_risk":
        return [
            "Flag exposed clients and pause recommendations tied to the asset category.",
            "Request provenance, ownership, and litigation checks before any transaction advice.",
            "Prepare a short risk note for advisors handling similar consignments.",
        ]
    if draft.event_type == "regulatory_risk":
        return [
            "Ask compliance to review whether current diligence workflows cover the new requirement.",
            "Update client intake questions for high-value or cross-border transactions.",
            "Track the next regulatory milestone and likely enforcement date.",
        ]
    if draft.event_type == "competitor_move":
        return [
            "Compare the competitor move with current advisory offers and fees.",
            "Identify clients most likely to notice the change.",
            "Draft a response option for sales or relationship managers.",
        ]
    if draft.event_type == "market_opportunity":
        return [
            "Size the affected client segment and likely near-term demand.",
            "Find three target accounts that could benefit from proactive outreach.",
            "Monitor follow-up sources for confirmation before committing resources.",
        ]
    return [
        "Assign an owner to validate the signal.",
        "Check whether existing clients, competitors, or markets are directly exposed.",
        "Revisit this item in the next briefing if corroborating sources appear.",
    ]
