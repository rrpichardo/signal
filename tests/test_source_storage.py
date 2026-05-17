# tests/test_source_storage.py
# Tests for the sources and source_health tables added to SignalStorage.
# SourceHealthResult is defined inline here so this file doesn't depend on
# Task 3 (signal_stream/source_health.py) being complete yet.

import os
import tempfile
import unittest
from dataclasses import dataclass


# ── Inline stub — mirrors the real SourceHealthResult from Task 3 ─────────────
# Once source_health.py is created, this stub can be replaced with a real import.
@dataclass
class SourceHealthResult:
    source_id: str
    source_name: str
    checked_at: str
    status: str
    error_msg: str
    article_count: int
    paywall_detected: bool
    confidence: float


# ── Fake source config — mimics a TOML SourceConfig object ───────────────────
# Uses the same attribute names that source_config_to_record() reads via getattr().
@dataclass
class FakeSourceConfig:
    name: str
    kind: str = "rss"
    group: str = "medium"
    url: str = "https://example.com/feed"
    path: str | None = None
    channel_id: str | None = None
    article_link_pattern: str | None = None
    limit: int = 8
    enabled: bool = True
    on_demand: bool = False


class TestSourceStorage(unittest.TestCase):
    def setUp(self):
        # Each test gets its own temp DB — no shared state between tests.
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        from signal_stream.storage import SignalStorage
        self.storage = SignalStorage(self.db_path)
        # init() creates all tables including sources and source_health.
        self.storage.init()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_init_sources_inserts_new_rows(self):
        # Two distinct sources should produce two rows.
        sources = [FakeSourceConfig("Feed A"), FakeSourceConfig("Feed B", url="https://b.com/feed")]
        self.storage.init_sources_from_config(sources)
        rows = self.storage.list_sources(include_disabled=True)
        self.assertEqual(len(rows), 2)

    def test_init_sources_is_idempotent(self):
        # Running init twice on the same source list must not duplicate rows.
        sources = [FakeSourceConfig("Feed A")]
        self.storage.init_sources_from_config(sources)
        self.storage.init_sources_from_config(sources)
        rows = self.storage.list_sources(include_disabled=True)
        self.assertEqual(len(rows), 1)

    def test_init_sources_does_not_overwrite_enabled(self):
        """SQLite owns enabled after first import; TOML changes don't overwrite it."""
        # Insert with enabled=True, then toggle off via the API.
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A", enabled=True)])
        rows = self.storage.list_sources(include_disabled=True)
        self.storage.toggle_source(rows[0].id, False)

        # Re-run init from TOML (still says enabled=True) — should NOT flip it back.
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A", enabled=True)])
        rows = self.storage.list_sources(include_disabled=True)
        # The SQLite value (False) must be preserved; TOML cannot override it.
        self.assertFalse(rows[0].enabled)

    def test_toggle_source(self):
        # Toggling a source off should be reflected on the next read.
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A")])
        row = self.storage.list_sources(include_disabled=True)[0]
        self.storage.toggle_source(row.id, False)
        rows = self.storage.list_sources(include_disabled=True)
        self.assertFalse(rows[0].enabled)

    def test_soft_delete_excludes_from_list(self):
        # Soft-deleted sources must not appear in any list_sources result.
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A")])
        row = self.storage.list_sources(include_disabled=True)[0]
        self.storage.soft_delete_source(row.id)
        rows = self.storage.list_sources(include_disabled=True)
        self.assertEqual(len(rows), 0)

    def test_list_sources_excludes_disabled_by_default(self):
        # Default call (include_disabled=False) should only return enabled sources.
        self.storage.init_sources_from_config([
            FakeSourceConfig("On", enabled=True),
            FakeSourceConfig("Off", url="https://off.com/feed", enabled=False),
        ])
        rows = self.storage.list_sources()  # default: include_disabled=False
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "On")

    def test_save_and_retrieve_source_health(self):
        # A health result saved for a source should be retrievable by source_id.
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A")])
        row = self.storage.list_sources(include_disabled=True)[0]
        result = SourceHealthResult(
            source_id=row.id, source_name="Feed A",
            checked_at="2026-01-01T00:00:00+00:00",
            status="ok", error_msg="", article_count=5,
            paywall_detected=False, confidence=0.9,
        )
        self.storage.save_source_health(result)
        health = self.storage.get_latest_source_health(row.id)
        self.assertIsNotNone(health)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["article_count"], 5)

    def test_get_all_latest_source_health(self):
        # get_all_latest_source_health should return one entry per source.
        self.storage.init_sources_from_config([
            FakeSourceConfig("Feed A"),
            FakeSourceConfig("Feed B", url="https://b.com/feed"),
        ])
        rows = self.storage.list_sources(include_disabled=True)
        for row in rows:
            result = SourceHealthResult(
                source_id=row.id, source_name=row.name,
                checked_at="2026-01-01T00:00:00+00:00",
                status="ok", error_msg="", article_count=3,
                paywall_detected=False, confidence=0.9,
            )
            self.storage.save_source_health(result)
        all_health = self.storage.get_all_latest_source_health()
        self.assertEqual(len(all_health), 2)

    def test_get_all_latest_health_with_duplicate_timestamps(self):
        """Two health rows with the same checked_at should not duplicate the result."""
        self.storage.init_sources_from_config([FakeSourceConfig("Feed A")])
        row = self.storage.list_sources(include_disabled=True)[0]
        # Insert two rows for the same source with the identical checked_at timestamp.
        # The MAX(id) tiebreaker must collapse them to a single dict entry.
        same_ts = "2026-01-01T00:00:00+00:00"
        for status in ("ok", "error"):
            result = SourceHealthResult(
                source_id=row.id, source_name="Feed A",
                checked_at=same_ts, status=status, error_msg="",
                article_count=0, paywall_detected=False, confidence=0.9,
            )
            self.storage.save_source_health(result)
        all_health = self.storage.get_all_latest_source_health()
        # Only one entry in the result dict — the later insert wins, not two entries.
        self.assertEqual(len(all_health), 1)


if __name__ == "__main__":
    unittest.main()
