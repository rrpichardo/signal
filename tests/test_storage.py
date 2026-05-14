"""Phase 2: analyst_artifact column migration and round-trip tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from signal_stream.models import Article, Signal, utc_now_iso
from signal_stream.storage import SignalStorage


def _make_storage() -> SignalStorage:
    # Each test gets its own temp DB so they can run in parallel.
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    storage = SignalStorage(tmp.name)
    storage.init()
    return storage


def _make_article(idx: int = 0) -> Article:
    # Plain article fixture — only used to satisfy the foreign-key-ish "article_id" reference on signals.
    return Article(
        id=f"art_{idx}",
        source="Test Source",
        title=f"Article {idx}",
        url=f"https://example.com/article-{idx}",
        published_at="2026-05-13T00:00:00Z",
        body=f"Body text for article {idx}.",
        fetched_at=utc_now_iso(),
    )


def _make_signal(idx: int = 0, artifact: dict | None = None) -> Signal:
    # Build a minimal Signal that mirrors what the analyst would produce.
    # When `artifact` is passed it rides through save_run_atomic into the new column.
    return Signal(
        id=f"sig_{idx}",
        cluster_id=f"cluster_{idx}",
        article_id=f"art_{idx}",
        title=f"Signal {idx}",
        url=f"https://example.com/article-{idx}",
        source="Test Source",
        published_at="2026-05-13T00:00:00Z",
        score=60,
        urgency="medium",
        event_type="platform_shift",
        summary="short summary text",
        why_it_matters="",
        next_steps=[],
        matched_priorities=[],
        entities={},
        duplicate_count=2,
        short_summary="short summary text",
        expanded_summary="expanded text",
        analyst_artifact=artifact,
    )


class TestAnalystArtifactRoundTrip(unittest.TestCase):
    def test_artifact_round_trips_through_storage(self) -> None:
        """Writing a Signal with analyst_artifact and reading it back yields the same dict."""
        storage = _make_storage()
        artifact = {
            "mechanism": "A clear mechanism statement.",
            "key_actors": [{"name": "Anthropic", "role": "provider"}],
            "affected_parties": ["enterprise legal teams"],
            "evidence_excerpts": [{"quote": "rate limit raised 10x", "source_offset": 42}],
            "confidence": "high",
            "confidence_reason": "primary source",
            "model_confidence": "high",
            "critic_flags": [],
            "_meta": {"was_truncated": False, "chars_total": 4000, "chars_sent": 4000, "extraction_quality": "good", "missing_fields": []},
        }
        article = _make_article(0)
        signal = _make_signal(0, artifact=artifact)

        run_id = storage.start_agent_run("test goal")
        storage.save_run_atomic(
            articles=[article],
            signals=[signal],
            cluster_count=1,
            output_path="/tmp/digest.md",
            started_at=utc_now_iso(),
            run_id=run_id,
            summary={"articles": 1, "signals": 1},
        )

        row = storage.get_signal("sig_0")
        self.assertIsNotNone(row)
        self.assertEqual(row["analyst_artifact"], artifact)


class TestSignalsWithoutArtifactReturnNull(unittest.TestCase):
    def test_signal_without_artifact_hydrates_with_none(self) -> None:
        """Code-path signals (no model review) save with null artifact and hydrate to None."""
        storage = _make_storage()
        article = _make_article(0)
        signal = _make_signal(0, artifact=None)

        run_id = storage.start_agent_run("test goal")
        storage.save_run_atomic(
            articles=[article],
            signals=[signal],
            cluster_count=1,
            output_path="/tmp/digest.md",
            started_at=utc_now_iso(),
            run_id=run_id,
            summary={"articles": 1, "signals": 1},
        )

        row = storage.get_signal("sig_0")
        self.assertIsNotNone(row)
        self.assertIsNone(row["analyst_artifact"])


class TestEnsureColumnAddsAnalystArtifact(unittest.TestCase):
    def test_old_db_gets_column_added_on_init(self) -> None:
        """An existing DB without analyst_artifact_json picks up the column on init()."""
        # Build a "legacy" DB by creating the signals table without the new column.
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        legacy_path = tmp.name
        conn = sqlite3.connect(legacy_path)
        conn.execute(
            """
            create table signals (
                id text primary key,
                cluster_id text not null,
                article_id text not null,
                title text not null,
                url text,
                source text,
                published_at text,
                score integer not null,
                urgency text not null,
                event_type text not null,
                summary text,
                why_it_matters text,
                next_steps_json text,
                matched_priorities_json text,
                entities_json text,
                duplicate_count integer default 0,
                created_at text not null
            )
            """
        )
        conn.commit()
        conn.close()

        # Confirm pre-state: no analyst_artifact_json column.
        with sqlite3.connect(legacy_path) as check:
            cols = {row[1] for row in check.execute("pragma table_info(signals)").fetchall()}
        self.assertNotIn("analyst_artifact_json", cols)

        # init() should add the column without raising.
        storage = SignalStorage(legacy_path)
        storage.init()

        with sqlite3.connect(legacy_path) as check:
            cols = {row[1] for row in check.execute("pragma table_info(signals)").fetchall()}
        self.assertIn("analyst_artifact_json", cols)


class TestOldSignalRowsHydrateSafely(unittest.TestCase):
    def test_signal_row_with_null_artifact_does_not_raise(self) -> None:
        """A row where analyst_artifact_json is NULL must hydrate cleanly with None."""
        storage = _make_storage()
        # Insert a row directly to simulate a legacy row that never had the artifact written.
        with storage.connect() as conn:
            conn.execute(
                """
                insert into signals (
                    id, cluster_id, article_id, title, url, source, published_at, score, urgency, event_type,
                    summary, short_summary, expanded_summary, why_it_matters, next_steps_json,
                    score_breakdown_json, matched_priorities_json, entities_json, image_url, icon_key,
                    scout_note, relevance_label, duplicate_count, analyst_artifact_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sig_legacy", "c", "a", "Legacy", "", "", "", 50, "medium", "platform_shift",
                    "summary", "short", "expanded", "", "[]",
                    "[]", "[]", "{}", "", "",
                    "", "keep", 0, None, utc_now_iso(),
                ),
            )

        row = storage.get_signal("sig_legacy")
        self.assertIsNotNone(row)
        self.assertIsNone(row["analyst_artifact"])

    def test_corrupt_artifact_json_hydrates_to_none(self) -> None:
        """If the stored JSON is malformed, hydration tolerates it instead of raising."""
        storage = _make_storage()
        with storage.connect() as conn:
            conn.execute(
                """
                insert into signals (
                    id, cluster_id, article_id, title, url, source, published_at, score, urgency, event_type,
                    summary, short_summary, expanded_summary, why_it_matters, next_steps_json,
                    score_breakdown_json, matched_priorities_json, entities_json, image_url, icon_key,
                    scout_note, relevance_label, duplicate_count, analyst_artifact_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sig_bad_json", "c", "a", "Corrupt", "", "", "", 50, "medium", "platform_shift",
                    "summary", "short", "expanded", "", "[]",
                    "[]", "[]", "{}", "", "",
                    "", "keep", 0, "{not valid json", utc_now_iso(),
                ),
            )

        row = storage.get_signal("sig_bad_json")
        self.assertIsNotNone(row)
        self.assertIsNone(row["analyst_artifact"])


if __name__ == "__main__":
    unittest.main()
