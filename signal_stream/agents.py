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
    SignalConfig,
    SignalDraft,
    stable_id,
)
from .storage import SignalStorage
from .text import clean_html, extract_named_entities, jaccard, normalize_space, phrase_hits, tokenize


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


