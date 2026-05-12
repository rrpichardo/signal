"""Tests for Wave 2: cursor fetching + atomic seen persistence.

Plain English: these tests pin down four invariants that the Wave 2 changes
introduce: (1) the run cursor only advances on status='complete' agent_runs,
(2) Scout's published_after fetch filter respects that cursor, (3) articles
already in storage are dropped before scoring, and (4) save_run_atomic either
fully persists a run or doesn't persist anything at all.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path

from signal_stream.analysis_tools import analyze_articles
from signal_stream.config import load_config
from signal_stream.models import (
    Article,
    Signal,
    SourceConfig,
    utc_now_iso,
)
from signal_stream.source_tools import fetch_source
from signal_stream.storage import SignalStorage
from signal_stream.worker import _resolve_cursor, CURSOR_OVERLAP


def _iso_at(when: datetime) -> str:
    # Match storage's utc_now_iso format so cursor comparisons line up exactly.
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_storage(tmp_path: Path) -> SignalStorage:
    storage = SignalStorage(tmp_path / "signal_stream.db")
    storage.init()
    return storage


def _insert_agent_run(storage: SignalStorage, run_id: str, status: str, started_at: datetime) -> None:
    # Direct insert so we can control status + started_at precisely for the
    # cursor-resolution tests (the public API only sets one or the other).
    with storage.connect() as conn:
        conn.execute(
            "insert into agent_runs (id, goal, status, started_at) values (?, ?, ?, ?)",
            (run_id, "test goal", status, _iso_at(started_at)),
        )


def _write_json_source(tmp_path: Path, filename: str, articles: list[dict]) -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(articles), encoding="utf-8")
    return path


class CursorResolutionTests(unittest.TestCase):
    def test_first_run_uses_per_source_limit_20(self) -> None:
        # On a clean db, the resolved cursor is None and fetch_source returns
        # the loader's source.limit slice — no cursor capping.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            storage = _new_storage(tmp_path)

            self.assertIsNone(storage.latest_complete_agent_run_started_at())
            self.assertIsNone(_resolve_cursor(storage))

            # 30 entries in the file, source.limit=20 → loader gives us 20.
            articles = [
                {
                    "source": "Fixture",
                    "title": f"Item {i}",
                    "url": f"https://example.com/items/{i}",
                    "published_at": "2026-05-01T12:00:00Z",
                    "body": f"Body for item {i}.",
                }
                for i in range(30)
            ]
            path = _write_json_source(tmp_path, "fallback.json", articles)
            source = SourceConfig(name="Fixture", kind="sample", path=str(path), limit=20)

            result = fetch_source(source, published_after=None)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["articles"]), 20)
            self.assertNotIn("source_capped", result)

    def test_second_run_uses_cursor_minus_6h(self) -> None:
        # With a prior complete run, the cursor is started_at - 6h, and only
        # articles newer than that survive fetch_source.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            storage = _new_storage(tmp_path)

            last_run_started = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
            _insert_agent_run(storage, "run_prior", "complete", last_run_started)

            self.assertEqual(
                storage.latest_complete_agent_run_started_at(),
                _iso_at(last_run_started),
            )
            cursor = _resolve_cursor(storage)
            self.assertIsNotNone(cursor)
            assert cursor is not None  # mypy/runtime narrowing
            self.assertEqual(cursor, last_run_started - CURSOR_OVERLAP)

            # Two old (before cursor) + two new (after cursor) + one inside the
            # overlap window (after cursor - 6h but before started_at).
            articles = [
                {"source": "F", "title": "Old A", "url": "u1", "published_at": "2026-05-09T12:00:00Z", "body": "x"},  # 24h before run start → BEFORE cursor (cursor is 6h before)
                {"source": "F", "title": "Old B", "url": "u2", "published_at": "2026-05-09T05:00:00Z", "body": "x"},
                {"source": "F", "title": "Edge",  "url": "u3", "published_at": "2026-05-10T07:00:00Z", "body": "x"},  # inside overlap window
                {"source": "F", "title": "Fresh A", "url": "u4", "published_at": "2026-05-10T13:00:00Z", "body": "x"},
                {"source": "F", "title": "Fresh B", "url": "u5", "published_at": "2026-05-10T18:00:00Z", "body": "x"},
            ]
            path = _write_json_source(tmp_path, "cursor.json", articles)
            source = SourceConfig(name="Fixture", kind="sample", path=str(path), limit=20)

            result = fetch_source(source, published_after=cursor)
            urls = {item["url"] for item in result["articles"]}
            # Cursor is 06:00 on 2026-05-10. "Old A" (12:00 on 5-09) and
            # "Old B" (05:00 on 5-09) are both before the cursor; the rest survive.
            self.assertEqual(urls, {"u3", "u4", "u5"})

    def test_failed_run_does_not_advance_cursor(self) -> None:
        # A later failed run must not bump the cursor past a previous complete run.
        with tempfile.TemporaryDirectory() as tmp:
            storage = _new_storage(Path(tmp))
            earlier = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
            later = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
            _insert_agent_run(storage, "run_complete", "complete", earlier)
            _insert_agent_run(storage, "run_failed", "failed", later)

            self.assertEqual(
                storage.latest_complete_agent_run_started_at(),
                _iso_at(earlier),
            )

    def test_interrupted_run_does_not_advance_cursor(self) -> None:
        # "interrupted" is the new status for max_iterations exhaustion and
        # must be treated the same way as "failed" for cursor purposes.
        with tempfile.TemporaryDirectory() as tmp:
            storage = _new_storage(Path(tmp))
            earlier = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
            later = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
            _insert_agent_run(storage, "run_complete", "complete", earlier)
            _insert_agent_run(storage, "run_interrupted", "interrupted", later)

            self.assertEqual(
                storage.latest_complete_agent_run_started_at(),
                _iso_at(earlier),
            )


class SeenFilterTests(unittest.TestCase):
    def test_seen_articles_dropped_before_scoring(self) -> None:
        # Pre-insert three articles, then hand analyze_articles a list of
        # five — the three overlapping by id and the two fresh ones. Only the
        # two fresh ones should reach scoring/signals.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = load_config("configs/demo.toml")
            config = replace(
                config,
                storage_path=str(tmp_path / "signals.db"),
                output_dir=str(tmp_path),
            )
            storage = SignalStorage(config.storage_path)
            storage.init()

            seen_articles = [
                Article.from_fields(
                    source="Fixture",
                    title=f"Seen {i}",
                    url=f"https://example.com/seen/{i}",
                    published_at="2026-05-09T12:00:00Z",
                    body=f"Body for seen item {i}.",
                )
                for i in range(3)
            ]
            # Pre-populate the articles table directly so the seen-check fires
            # without needing a full prior run.
            with storage.connect() as conn:
                for art in seen_articles:
                    conn.execute(
                        "insert into articles (id, source, title, url, published_at, body, fetched_at, raw_json) "
                        "values (?, ?, ?, ?, ?, ?, ?, ?)",
                        (art.id, art.source, art.title, art.url, art.published_at, art.body, art.fetched_at, "{}"),
                    )

            fresh_articles = [
                Article.from_fields(
                    source="Fixture",
                    title="Fresh OpenAI launch",
                    url="https://example.com/fresh/openai-launch",
                    published_at="2026-05-10T12:00:00Z",
                    body="OpenAI launched a new agent platform with enterprise tooling and developer APIs.",
                ),
                Article.from_fields(
                    source="Fixture",
                    title="Fresh NVIDIA chip",
                    url="https://example.com/fresh/nvidia-chip",
                    published_at="2026-05-10T13:00:00Z",
                    body="NVIDIA announced a new GPU aimed at inference latency for AI infrastructure customers.",
                ),
            ]
            articles_json = [
                {
                    "id": art.id,
                    "source": art.source,
                    "title": art.title,
                    "url": art.url,
                    "published_at": art.published_at,
                    "body": art.body,
                    "fetched_at": art.fetched_at,
                    "raw": art.raw,
                }
                for art in seen_articles + fresh_articles
            ]

            data = analyze_articles(
                config,
                storage,
                articles_json,
                analyst_mode="code",
                analyst_prompt="",
                scoring_rubric=None,
                behavior={"summary_mode": "short_only"},
            )

            self.assertEqual(data["article_count"], 2)
            signal_titles = {item["title"] for item in data["signals"]}
            self.assertIn("Fresh OpenAI launch", signal_titles)
            self.assertIn("Fresh NVIDIA chip", signal_titles)
            for already_seen in ("Seen 0", "Seen 1", "Seen 2"):
                self.assertNotIn(already_seen, signal_titles)


class _ProxyConn:
    """Wraps a real sqlite3 Connection so we can raise inside a transaction.

    sqlite3.Connection.execute is a read-only C attribute, so monkey-patching
    it directly fails. A thin proxy that forwards __enter__/__exit__ to the
    real connection (preserving SQLite's commit-on-success/rollback-on-exc
    semantics) but intercepts execute/executemany lets us simulate a mid-
    transaction failure without touching production code.
    """

    def __init__(self, real, trigger_sql_fragment: str):
        self._real = real
        self._trigger = trigger_sql_fragment.lower()

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, tb):
        return self._real.__exit__(exc_type, exc_val, tb)

    def execute(self, sql, *args, **kwargs):
        if self._trigger in sql.lower():
            raise RuntimeError("boom mid-transaction")
        return self._real.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        if self._trigger in sql.lower():
            raise RuntimeError("boom mid-transaction")
        return self._real.executemany(sql, *args, **kwargs)


class SaveRunAtomicTests(unittest.TestCase):
    def test_save_run_atomic_rolls_back_on_failure(self) -> None:
        # Force an exception mid-transaction (during the signals insert) and
        # confirm none of the run's articles or status changes survive.
        with tempfile.TemporaryDirectory() as tmp:
            storage = _new_storage(Path(tmp))
            run_id = storage.start_agent_run("rollback-test")

            article = Article(
                id="art_rollback",
                source="Fixture",
                title="Will not persist",
                url="https://example.com/rollback/1",
                published_at="2026-05-10T12:00:00Z",
                body="body",
                fetched_at=utc_now_iso(),
                raw={},
            )

            # Swap storage.connect() with one that returns a proxy. The articles
            # executemany runs first, then the proxy raises on the signals
            # statement, which exits the `with` block with an exception — that
            # is what triggers the real connection's rollback.
            original_connect = storage.connect

            def crashing_connect():
                return _ProxyConn(original_connect(), "into signals")

            storage.connect = crashing_connect  # type: ignore[method-assign]

            signal = Signal(
                id="sig_rollback",
                cluster_id="cl_x",
                article_id=article.id,
                title=article.title,
                url=article.url,
                source=article.source,
                published_at=article.published_at,
                score=50,
                urgency="medium",
                event_type="general_signal",
                summary="",
                why_it_matters="",
                next_steps=[],
                matched_priorities=[],
                entities={},
            )

            with self.assertRaises(RuntimeError):
                storage.save_run_atomic(
                    articles=[article],
                    signals=[signal],
                    cluster_count=1,
                    output_path="/tmp/digest.md",
                    started_at=utc_now_iso(),
                    run_id=run_id,
                    summary={"foo": "bar"},
                )

            # Restore the real connect so the post-failure assertions run
            # against actual storage rather than the proxy.
            storage.connect = original_connect  # type: ignore[method-assign]

            # The articles executemany ran before the failing signals insert;
            # the transaction's rollback must have undone it.
            self.assertFalse(storage.is_article_seen(article.id, article.url))
            # The agent_run row must NOT have flipped to 'complete' — that
            # transition is part of the same transaction.
            run = storage.latest_agent_run()
            self.assertIsNotNone(run)
            assert run is not None
            self.assertNotEqual(run["status"], "complete")


class SourceCapTests(unittest.TestCase):
    def test_source_cap_logged_when_over_20(self) -> None:
        # 25 fresh articles all newer than the cursor → fetch_source caps to
        # 20 and reports source_capped=25 so Scout can log it.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            articles = [
                {
                    "source": "Capped",
                    "title": f"Fresh {i}",
                    "url": f"https://example.com/capped/{i}",
                    "published_at": f"2026-05-10T{i:02d}:00:00Z",
                    "body": f"Body {i}.",
                }
                for i in range(25)
            ]
            path = _write_json_source(tmp_path, "capped.json", articles)
            # source.limit deliberately set higher than the fixture so the
            # loader gives all 25 entries — the cap should be applied by the
            # cursor filter, not the loader's slice.
            source = SourceConfig(name="Capped", kind="sample", path=str(path), limit=50)

            cursor = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
            result = fetch_source(source, published_after=cursor)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["articles"]), 20)
            self.assertEqual(result.get("source_capped"), 25)


class RunIdCollisionTests(unittest.TestCase):
    def test_back_to_back_runs_no_pk_conflict(self) -> None:
        # Two rapid consecutive runs with the same goal must not collide on PK.
        # Before the UUID fix, stable_id(goal, utc_now_iso()) produced the same
        # hash when both calls landed in the same second.
        with tempfile.TemporaryDirectory() as tmp:
            storage = _new_storage(Path(tmp))
            run_id_1 = storage.start_agent_run("same goal")
            run_id_2 = storage.start_agent_run("same goal")
            self.assertNotEqual(run_id_1, run_id_2)
            with storage.connect() as conn:
                count = conn.execute(
                    "select count(*) from agent_runs where goal = ?", ("same goal",)
                ).fetchone()[0]
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
