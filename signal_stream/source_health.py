from dataclasses import dataclass
from datetime import datetime, timezone

# Paywall detection keywords. These flag sources that require subscription or account creation.
_PAYWALL_KEYWORDS = [
    "402",
    "subscribe to continue",
    "this article is for subscribers",
    "sign in to read",
    "members only",
    "premium content",
    "create a free account to read",
    "paywall",
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

    # Detect paywall based on error message keywords.
    paywall_detected = _detect_paywall(error_msg)

    # Map raw status to final status with paywall and empty checks.
    if paywall_detected:
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
    """Check if error message contains paywall keywords.

    Args:
        error_msg: The error message to inspect.

    Returns:
        True if any paywall keyword is found (case-insensitive).
    """
    msg_lower = error_msg.lower()
    return any(kw in msg_lower for kw in _PAYWALL_KEYWORDS)
