from dataclasses import dataclass
from datetime import datetime, timezone
import re

# Paywall detection keywords. These flag sources that require subscription or account creation.
_PAYWALL_KEYWORDS = [
    "402",
    "paid subscribers",
    "paying subscribers",
    "subscribers only",
    "subscribe to continue",
    "subscribe to unlock",
    "subscribe to read",
    "upgrade to paid",
    "upgrade to unlock",
    "this article is for subscribers",
    "this post is for paid subscribers",
    "sign in to read",
    "members only",
    "premium content",
    "create a free account to read",
    "paywall",
]
_PAYWALL_PATTERNS = [
    # Substack renders paid posts as "May 14, 2026 ∙ Paid 6 Share".
    re.compile(r"(?:^|\s)[∙•·]\s*paid\b", re.IGNORECASE),
    re.compile(r"\bpaid\s+(?:post|episode|newsletter|subscriber|subscribers|subscription|tier|only)\b", re.IGNORECASE),
    re.compile(r"\b(?:for|only for)\s+(?:paid|paying)\s+subscribers\b", re.IGNORECASE),
]


@dataclass
class SourceHealthResult:
    """Result of a health check on one source.

    Attributes:
        source_id: Stable ID of the source being checked.
        source_name: Human-readable source name (denormalized for display).
        checked_at: ISO-8601 UTC timestamp when the check ran.
        status: One of "ok", "error", "paywall", "empty", "skipped".
        error_msg: Error message if status is "error" or "paywall".
        article_count: Number of articles fetched (before any filtering).
        paywall_detected: True if paywall keywords found in error_msg.
        confidence: Fetch confidence score (0.0 to 1.0).
    """
    source_id: str
    source_name: str
    checked_at: str
    status: str
    error_msg: str
    article_count: int
    paywall_detected: bool
    confidence: float


def check_source_health(record) -> SourceHealthResult:
    """Run a live health check on one SourceRecord using the existing fetch pipeline.

    This function converts a SourceRecord to a SourceConfig, fetches from the source,
    detects common failure modes (paywall, empty results), and returns a structured
    health result.

    Args:
        record: A SourceRecord to check.

    Returns:
        A SourceHealthResult with status, error details, and article count.
    """
    from signal_stream.source_tools import fetch_source
    from signal_stream.source_registry import source_record_to_config

    # Convert registry record to config for the fetch pipeline.
    config = source_record_to_config(record)
    checked_at = datetime.now(timezone.utc).isoformat()

    # Run the fetch. published_after=None means "give me everything (up to limit)".
    fetch_result = fetch_source(config, published_after=None)

    # Extract the raw result fields.
    raw_status = fetch_result.get("status", "error")
    error_msg = fetch_result.get("error", "")
    articles = fetch_result.get("articles", [])
    confidence = fetch_result.get("confidence", 0.0)

    # Detect paid/gated content from the error, feed snippets, and a small
    # article-page sample. Many feeds are readable even when the posts are paid.
    paywall_detected = (
        _detect_paywall(error_msg)
        or _detect_paywall_in_articles(articles)
        or (raw_status == "ok" and _detect_paywall_in_article_pages(articles))
    )

    # Map raw status to final status. A readable feed can still include paid
    # posts, so keep status="ok" when fetch succeeded and surface the paid bit
    # through paywall_detected.
    if paywall_detected and raw_status != "ok":
        final_status = "paywall"
    elif raw_status == "ok" and len(articles) == 0:
        final_status = "empty"
    else:
        final_status = raw_status

    return SourceHealthResult(
        source_id=record.id,
        source_name=record.name,
        checked_at=checked_at,
        status=final_status,
        error_msg=error_msg,
        article_count=len(articles),
        paywall_detected=paywall_detected,
        confidence=confidence,
    )


def _detect_paywall(error_msg: str) -> bool:
    """Check if text contains paid/gated access markers.

    Args:
        error_msg: The text to inspect.

    Returns:
        True if any paid/gated marker is found (case-insensitive).
    """
    msg_lower = error_msg.lower()
    return (
        any(kw in msg_lower for kw in _PAYWALL_KEYWORDS)
        or any(pattern.search(error_msg) for pattern in _PAYWALL_PATTERNS)
    )


def _detect_paywall_in_articles(articles: list[dict]) -> bool:
    """Inspect fetched feed/article snippets for paid/gated markers."""
    for article in articles[:5]:
        raw = article.get("raw") if isinstance(article, dict) else None
        raw_text = ""
        if isinstance(raw, dict):
            raw_text = " ".join(str(v) for v in raw.values() if isinstance(v, (str, int, float, bool)))
        text = " ".join([
            str(article.get("title", "")),
            str(article.get("body", "")),
            str(article.get("url", "")),
            raw_text,
        ])
        if _detect_paywall(text):
            return True
    return False


def _detect_paywall_in_article_pages(articles: list[dict]) -> bool:
    """Fetch a small article sample and inspect page text for paid markers."""
    from signal_stream.source_tools import fetch_full_article_page

    checked = 0
    for article in articles:
        url = str(article.get("url", ""))
        if not url.startswith(("http://", "https://")):
            continue
        checked += 1
        body_text, _ = fetch_full_article_page(url, timeout=6)
        if _detect_paywall(body_text):
            return True
        if checked >= 3:
            break
    return False
