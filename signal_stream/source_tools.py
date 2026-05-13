from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any
from urllib import error, request

from .llm import BrainClient
from .models import Article, SourceConfig
from .text import clean_html, normalize_space


USER_AGENT = "SignalStreamAgentic/0.1"
YOUTUBE_VIDEO_RE = re.compile(r"(?:video:videoId|yt:videoId)$")
# Global cap applied when a cursor is in play. The 6-hour overlap window in
# the worker can still match more entries than the cap on chatty feeds, so we
# keep only the freshest 20 and log a `source_capped` event for the rest.
CURSOR_FETCH_CAP = 20
SCOUT_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevance_label": {"type": "string"},
                    "topic": {"type": "string"},
                    "signal_type": {"type": "string"},
                    "usefulness": {"type": "string"},
                    "scout_note": {"type": "string"},
                },
                "required": ["id", "relevance_label", "topic", "signal_type", "usefulness", "scout_note"],
            },
        }
    },
    "required": ["items"],
}


def fetch_source(source: SourceConfig, published_after: datetime | None = None) -> dict[str, Any]:
    """Fetch one source and return a safe JSON result.

    Plain English: this is Scout's main tool. It tries a source, catches
    failures, and reports what happened instead of crashing the whole run.
    When `published_after` is set, only entries newer than that timestamp
    survive — that's how the cursor advances each run. On a first run (no
    prior complete agent_runs row), `published_after` is None and the
    source's own `limit` controls how many entries come back.
    """

    try:
        if source.kind in {"sample", "json"}:
            articles = _load_json_source(source)
        elif source.kind in {"rss", "atom"}:
            articles = _load_feed_source(source)
        elif source.kind == "youtube":
            articles = _load_youtube_source(source)
        elif source.kind == "html_scrape":
            articles = _load_html_scrape_source(source)
        elif source.kind == "report":
            return {
                "source": source.name,
                "status": "skipped",
                "articles": [],
                "error": "Report/on-demand source; skipped during normal agent run.",
                "confidence": 0.8,
            }
        else:
            return {
                "source": source.name,
                "status": "error",
                "articles": [],
                "error": f"Unsupported source kind: {source.kind}",
                "confidence": 0.0,
            }
    except Exception as exc:  # noqa: BLE001 - source failures should be data, not run-ending exceptions.
        return {
            "source": source.name,
            "status": "error",
            "articles": [],
            "error": str(exc),
            "confidence": 0.0,
        }

    # Apply the cursor filter + global cap after the loader returns. We do
    # this here rather than inside each loader so RSS, JSON, and YouTube all
    # share one definition of "what counts as new since last time."
    filtered, capped_count = _apply_cursor_and_cap(articles, published_after, CURSOR_FETCH_CAP)
    result: dict[str, Any] = {
        "source": source.name,
        "status": "ok",
        "articles": [_article_json(article) for article in filtered],
        "error": "",
        "confidence": 0.9 if filtered else 0.25,
    }
    if capped_count is not None:
        # Surfaced by the orchestrator as a `source_capped` agent_event so the
        # dashboard can show that this feed had more new entries than the cap.
        result["source_capped"] = capped_count
    return result


def _apply_cursor_and_cap(
    articles: list[Article],
    published_after: datetime | None,
    cap: int,
) -> tuple[list[Article], int | None]:
    """Filter to articles newer than `published_after` and apply the per-fetch cap.

    Plain English: on a first run (no cursor) we trust the loader's own
    source.limit slice and return as-is. On every later run we drop entries
    older than the cursor, sort newest-first, and keep at most `cap` items.
    If we had to drop entries because of the cap, the pre-cap count is
    returned so Scout can log a `source_capped` warning.
    """

    if published_after is None:
        return articles, None

    fresh: list[Article] = []
    for article in articles:
        parsed = _parse_entry_date(article.published_at)
        # Articles without a parseable date stay in — better to over-include
        # than silently drop legitimate news because a feed used an oddball
        # date format. The Analyst's own dedup/seen check is the safety net.
        if parsed is None or parsed > published_after:
            fresh.append(article)

    def _sort_key(item: Article) -> datetime:
        parsed = _parse_entry_date(item.published_at)
        return parsed or datetime.min.replace(tzinfo=timezone.utc)

    fresh.sort(key=_sort_key, reverse=True)

    if len(fresh) > cap:
        return fresh[:cap], len(fresh)
    return fresh, None


def _parse_entry_date(value: str) -> datetime | None:
    """Best-effort parse of RFC 2822 (RSS) or ISO 8601 (Atom) dates."""

    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_context(query: str, articles: list[dict[str, Any]], limit: int = 5) -> dict[str, Any]:
    """Find already-collected articles related to a topic."""

    words = {word.lower() for word in query.split() if len(word) > 3}
    ranked = []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('body', '')}".lower()
        overlap = sum(1 for word in words if word in text)
        if overlap:
            item = dict(article)
            item["context_overlap"] = overlap
            ranked.append(item)
    ranked.sort(key=lambda item: item["context_overlap"], reverse=True)
    return {
        "query": query,
        "articles": ranked[:limit],
        "status": "ok",
        "confidence": 0.65 if ranked else 0.2,
    }


def fetch_full_article_page(url: str, timeout: int = 10) -> tuple[str, str | None]:
    """Fetch an article URL and extract the main readable text + og:image.

    Strategy (Option A — stdlib only): strip script/style/nav/header/footer/aside
    tags, then return the longest contiguous text block from <article> if present,
    else <main>, else <body>. Falls back to ("", None) on any network or parse error.

    Returns:
        (body_text, og_image_url) — body_text is "" on failure; og_image_url
        is None when no og:image meta tag was found.
    """
    if not url or not url.startswith(("http://", "https://")):
        return "", None
    try:
        req = request.Request(url, headers={"User-Agent": USER_AGENT})
        with request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type:
                return "", None
            raw_html = resp.read(2_000_000).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - any failure → safe fallback
        return "", None

    parser = _ArticlePageParser()
    try:
        parser.feed(raw_html)
    except Exception:  # noqa: BLE001 - html.parser can raise on malformed HTML
        return "", None

    return parser.best_text(), parser.og_image


class _ArticlePageParser(HTMLParser):
    """Best-effort readable-text extractor. Zero deps — stdlib only."""

    # Tags whose entire subtree we skip (nav/boilerplate noise).
    _SKIP_TAGS: frozenset[str] = frozenset(["script", "style", "nav", "header", "footer", "aside", "noscript", "form"])
    # Content-container tags we prefer over <body>.
    _CONTAINER_PRIORITY: tuple[str, ...] = ("article", "main", "div", "section", "body")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth: int = 0   # nesting depth inside a skip tag
        self._containers: dict[str, list[str]] = {tag: [] for tag in self._CONTAINER_PRIORITY}
        self._current_containers: list[str] = []  # stack of open container tags
        self._text_buf: list[str] = []             # text for current deepest container
        self.og_image: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        # Capture og:image from <meta property="og:image" content="...">
        if tag == "meta" and attr_map.get("property") == "og:image":
            self.og_image = attr_map.get("content") or None

        if self._skip_depth > 0:
            self._skip_depth += 1
            return
        if tag in self._SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag in self._CONTAINER_PRIORITY:
            # Save the current buffer as a candidate for the parent container,
            # then start a fresh buffer for this container.
            if self._current_containers:
                parent = self._current_containers[-1]
                self._containers[parent].append(normalize_space(" ".join(self._text_buf)))
            self._text_buf = []
            self._current_containers.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in self._CONTAINER_PRIORITY and self._current_containers and self._current_containers[-1] == tag:
            container = self._current_containers.pop()
            self._containers[container].append(normalize_space(" ".join(self._text_buf)))
            self._text_buf = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            self._text_buf.append(text)

    def best_text(self) -> str:
        # Flush any remaining buffer into body
        if self._text_buf:
            self._containers["body"].append(normalize_space(" ".join(self._text_buf)))
        # Try containers in priority order; pick longest non-empty result.
        for tag in self._CONTAINER_PRIORITY:
            combined = " ".join(chunk for chunk in self._containers[tag] if chunk)
            combined = normalize_space(combined)
            if len(combined) >= 200:
                return combined
        return ""


def enrich_articles_with_model(
    llm: BrainClient,
    scout_prompt: str,
    articles: list[dict[str, Any]],
    *,
    max_items: int = 12,
    relevance_policy: str = "soft_keep",
    scout_note_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Give Scout a lightweight model pass when hybrid/model mode is enabled.

    Plain English: Scout still uses code to fetch articles. The model only adds
    labels that help later steps understand what each article seems to be about.
    """

    if not articles or not llm.available():
        return articles

    sample = [
        {
            "id": article.get("id", ""),
            "title": article.get("title", ""),
            "source": article.get("source", ""),
            "body": str(article.get("body", ""))[:900],
        }
        for article in articles[:max_items]
    ]
    user = json.dumps(
        {
            "task": "enrich_fetched_articles",
            "articles": sample,
            "fields": ["relevance_label", "topic", "signal_type", "usefulness", "scout_note"],
            "relevance_labels": ["keep", "borderline", "drop"],
            "relevance_policy": relevance_policy,
        },
        sort_keys=True,
    )
    raw = llm.chat_json(scout_prompt, user, SCOUT_ENRICH_SCHEMA)
    if not raw:
        return articles

    enriched = {item.get("id"): item for item in raw.get("items", []) if item.get("id")}
    merged = []
    for article in articles:
        item = dict(article)
        extra = enriched.get(item.get("id"))
        if extra:
            raw_data = dict(item.get("raw") or {})
            raw_data["scout_relevance_label"] = _clean_relevance(extra.get("relevance_label", "borderline"))
            raw_data["scout_topic"] = extra.get("topic", "")
            raw_data["scout_signal_type"] = extra.get("signal_type", "")
            raw_data["scout_usefulness"] = extra.get("usefulness", "")
            raw_data["scout_note"] = extra.get("scout_note", "") if scout_note_enabled else ""
            item["raw"] = raw_data
        if relevance_policy == "hard_drop" and dict(item.get("raw") or {}).get("scout_relevance_label") == "drop":
            continue
        merged.append(item)
    return merged


def _title_from_url(url: str) -> str:
    """Derive a readable title from a URL path slug when no better title is available."""
    slug = url.rstrip("/").split("/")[-1]
    return normalize_space(slug.replace("-", " ").replace("_", " "))[:120] or url


def _load_html_scrape_source(source: SourceConfig) -> list[Article]:
    """Fetch an archive/index page and return articles found via linked pages.

    Strategy: fetch the archive URL, find all hrefs that contain the
    article_link_pattern (defaults to '/p/' for Substack-style archives), then
    call fetch_full_article_page() for each unique link up to source.limit.
    Titles are extracted from the raw HTML <title> tag or derived from the URL
    slug as a fallback (the Analyst's body text carries the real content anyway).
    """
    if not source.url:
        return []

    pattern = source.article_link_pattern or "/p/"
    # Escape the pattern for use in regex, then match it anywhere in an href.
    link_re = re.compile(
        r'href=["\']([^"\']*' + re.escape(pattern) + r'[^"\']*)["\']',
        re.IGNORECASE,
    )
    title_re = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

    # Archive page fetch errors propagate to fetch_source() so it can report
    # status='error'. Per-article failures further down are still silently skipped.
    req = request.Request(source.url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=25) as resp:
        raw_html = resp.read(2_000_000).decode("utf-8", errors="replace")

    parsed_base = urllib.parse.urlparse(source.url)
    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

    seen_urls: set[str] = set()
    article_urls: list[str] = []
    for m in link_re.finditer(raw_html):
        href = m.group(1).split("#")[0].strip()  # strip anchors
        if not href:
            continue
        if href.startswith("//"):
            href = f"{parsed_base.scheme}:{href}"
        elif href.startswith("/"):
            href = f"{base_origin}{href}"
        elif not href.startswith(("http://", "https://")):
            href = f"{source.url.rstrip('/')}/{href}"
        if href not in seen_urls:
            seen_urls.add(href)
            article_urls.append(href)
        if len(article_urls) >= source.limit:
            break

    articles = []
    for url in article_urls[:source.limit]:
        body_text, og_image = fetch_full_article_page(url, timeout=10)
        # Derive title from the raw <title> tag via a fresh fetch only when the
        # body was successfully extracted — skip expensive re-fetch on failures.
        # For articles where body is too short we still record the URL for dedup.
        article_html_title = ""
        try:
            req2 = request.Request(url, headers={"User-Agent": USER_AGENT})
            with request.urlopen(req2, timeout=10) as resp2:
                head_html = resp2.read(16_000).decode("utf-8", errors="replace")
            m2 = title_re.search(head_html)
            if m2:
                article_html_title = normalize_space(m2.group(1))
        except Exception:  # noqa: BLE001 - title is optional; body text is primary
            pass
        title = article_html_title or _title_from_url(url)
        raw: dict[str, Any] = {"image_url": og_image or "", "source_kind": "html_scrape"}
        articles.append(
            Article.from_fields(
                source=source.name,
                title=title,
                url=url,
                body=body_text,
                raw=raw,
            )
        )
    return articles


def _load_json_source(source: SourceConfig) -> list[Article]:
    if not source.path:
        return []
    data = json.loads(Path(source.path).read_text(encoding="utf-8"))
    return [
        Article.from_fields(
            source=item.get("source") or source.name,
            title=item.get("title", ""),
            url=item.get("url", ""),
            published_at=item.get("published_at", ""),
            body=item.get("body", ""),
            raw=item,
        )
        for item in data[: source.limit]
    ]


def _load_feed_source(source: SourceConfig) -> list[Article]:
    if not source.url:
        return []
    root = _fetch_xml(source.url)
    entries = _feed_entries(root)[: source.limit]
    return [_article_from_entry(source, entry) for entry in entries]


def _load_youtube_source(source: SourceConfig) -> list[Article]:
    feed_url = source.url or f"https://www.youtube.com/feeds/videos.xml?channel_id={source.channel_id}"
    root = _fetch_xml(feed_url)
    entries = _feed_entries(root)[: source.limit]
    articles = []
    for entry in entries:
        title = _child_text(entry, "title")
        video_id = _youtube_video_id(entry)
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else _entry_link(entry)
        published_at = _child_text(entry, "published", "updated")
        transcript = _fetch_youtube_transcript(video_id) if video_id else ""
        body = transcript or clean_html(_child_text(entry, "description", "summary"))
        raw = {"video_id": video_id, "transcript_available": bool(transcript), "source_type": "youtube"}
        articles.append(Article.from_fields(source=source.name, title=title, url=url, published_at=published_at, body=body, raw=raw))
    return articles


def _fetch_xml(url: str) -> ET.Element:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    with request.urlopen(req, timeout=25) as response:
        payload = response.read(3_000_000)
    return ET.fromstring(payload)


def _fetch_youtube_transcript(video_id: str | None) -> str:
    if not video_id:
        return ""
    params = urllib.parse.urlencode({"v": video_id, "fmt": "srv3", "lang": "en"})
    url = f"https://video.google.com/timedtext?{params}"
    try:
        root = _fetch_xml(url)
    except (error.URLError, ET.ParseError, TimeoutError, ValueError):
        return ""
    chunks = []
    for text in root.iter():
        if _local_name(text.tag) == "text":
            chunks.append("".join(text.itertext()))
    return normalize_space(clean_html(" ".join(chunks)))


def _feed_entries(root: ET.Element) -> list[ET.Element]:
    local = _local_name(root.tag)
    if local == "feed":
        return [child for child in list(root) if _local_name(child.tag) == "entry"]
    channel = next((child for child in root.iter() if _local_name(child.tag) == "channel"), root)
    return [child for child in list(channel) if _local_name(child.tag) == "item"]


def _article_from_entry(source: SourceConfig, entry: ET.Element) -> Article:
    title = _child_text(entry, "title")
    url = _entry_link(entry)
    published_at = _child_text(entry, "published", "updated", "pubDate", "date")
    body = clean_html(_child_text(entry, "description", "summary", "encoded", "content"))
    return Article.from_fields(source=source.name, title=title, url=url, published_at=published_at, body=body, raw={"feed": source.name, "image_url": _entry_image(entry)})


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(entry: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(entry):
        local = _local_name(child.tag).lower()
        if local in wanted or local.split(":")[-1] in wanted:
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


def _entry_image(entry: ET.Element) -> str:
    """Best-effort article image extraction from common RSS/Atom fields."""

    for child in entry.iter():
        local = _local_name(child.tag).lower()
        url = child.attrib.get("url") or child.attrib.get("href")
        if url and (local in {"thumbnail", "content", "enclosure"} or "image" in child.attrib.get("type", "")):
            return url.strip()
    for child in entry.iter():
        local = _local_name(child.tag).lower()
        if local in {"image", "logo"} and child.text:
            return child.text.strip()
    return ""


def _clean_relevance(value: object) -> str:
    label = str(value or "").strip().lower().replace(" ", "_")
    return label if label in {"keep", "borderline", "drop"} else "borderline"


def _youtube_video_id(entry: ET.Element) -> str:
    for child in entry.iter():
        if YOUTUBE_VIDEO_RE.search(child.tag):
            return (child.text or "").strip()
    link = _entry_link(entry)
    parsed = urllib.parse.urlparse(link)
    return urllib.parse.parse_qs(parsed.query).get("v", [""])[0]


def _article_json(article: Article) -> dict[str, Any]:
    return {
        "id": article.id,
        "source": article.source,
        "title": article.title,
        "url": article.url,
        "published_at": article.published_at,
        "body": article.body,
        "fetched_at": article.fetched_at,
        "raw": article.raw,
    }
