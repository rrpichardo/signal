import unittest
from unittest.mock import patch
from signal_stream.source_registry import SourceRecord


def _fake_record(name="Test Feed", kind="rss") -> SourceRecord:
    """Create a minimal SourceRecord for testing."""
    return SourceRecord(
        id="src_abc123",
        name=name,
        kind=kind,
        group_name="medium",
        url="http://example.com/feed",
        path=None,
        channel_id=None,
        article_link_pattern=None,
        limit_count=8,
        enabled=True,
        on_demand=False,
        origin="toml",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class TestCheckSourceHealth(unittest.TestCase):
    def test_ok_source_returns_ok_status(self):
        # A successful fetch with articles should return status "ok".
        fake = {"status": "ok", "articles": [{"id": "1"}], "error": "", "confidence": 0.9}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.article_count, 1)
        self.assertFalse(result.paywall_detected)
        self.assertEqual(result.source_id, "src_abc123")

    def test_http_402_sets_paywall(self):
        # HTTP 402 in error message should trigger paywall detection.
        fake = {"status": "error", "articles": [], "error": "HTTP 402 Payment Required", "confidence": 0.0}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.status, "paywall")
        self.assertTrue(result.paywall_detected)

    def test_paywall_keyword_sets_paywall(self):
        # Paywall keywords in error message should set paywall_detected and status.
        fake = {"status": "error", "articles": [], "error": "subscribe to continue reading", "confidence": 0.0}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertTrue(result.paywall_detected)
        self.assertEqual(result.status, "paywall")

    def test_ok_with_zero_articles_is_empty(self):
        # Status "ok" with zero articles should be downgraded to "empty".
        fake = {"status": "ok", "articles": [], "error": "", "confidence": 0.25}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.article_count, 0)

    def test_paywall_overrides_empty(self):
        # Paywall detection takes priority: paywall error with zero articles is "paywall", not "empty".
        fake = {"status": "error", "articles": [], "error": "members only content", "confidence": 0.0}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.status, "paywall")
        self.assertTrue(result.paywall_detected)

    def test_error_propagates_when_not_paywall(self):
        # A generic error (not paywall-related) should remain status "error".
        fake = {"status": "error", "articles": [], "error": "Connection timeout", "confidence": 0.0}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.status, "error")
        self.assertFalse(result.paywall_detected)
        self.assertIn("timeout", result.error_msg.lower())

    def test_checked_at_is_iso_format(self):
        # checked_at must be an ISO-8601 UTC timestamp.
        fake = {"status": "ok", "articles": [], "error": "", "confidence": 0.0}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        # Should parse as ISO-8601 without raising.
        from datetime import datetime
        dt = datetime.fromisoformat(result.checked_at.replace("Z", "+00:00"))
        self.assertIsNotNone(dt)

    def test_confidence_is_preserved(self):
        # Confidence from fetch_source should be preserved in the result.
        fake = {"status": "ok", "articles": [{"id": "1"}], "error": "", "confidence": 0.75}
        with patch("signal_stream.source_tools.fetch_source", return_value=fake):
            from signal_stream.source_health import check_source_health
            result = check_source_health(_fake_record())
        self.assertEqual(result.confidence, 0.75)


if __name__ == "__main__":
    unittest.main()
