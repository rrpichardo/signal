from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import uuid
from typing import Any

from .models import Article, Signal, ToolCall, stable_id, utc_now_iso


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    existing = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"alter table {table} add column {column} {column_type}")


class SignalStorage:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists articles (
                    id text primary key,
                    source text not null,
                    title text not null,
                    url text,
                    published_at text,
                    body text,
                    fetched_at text,
                    raw_json text
                );

                create table if not exists signals (
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
                    short_summary text,
                    expanded_summary text,
                    why_it_matters text,
                    next_steps_json text,
                    score_breakdown_json text,
                    matched_priorities_json text,
                    entities_json text,
                    image_url text,
                    icon_key text,
                    scout_note text,
                    relevance_label text,
                    duplicate_count integer default 0,
                    created_at text not null
                );

                create table if not exists feedback (
                    id integer primary key autoincrement,
                    signal_id text not null,
                    label text not null,
                    note text,
                    created_at text not null
                );

                create table if not exists runs (
                    id integer primary key autoincrement,
                    started_at text not null,
                    completed_at text not null,
                    article_count integer not null,
                    cluster_count integer not null,
                    signal_count integer not null,
                    output_path text
                );

                create table if not exists agent_runs (
                    id text primary key,
                    goal text not null,
                    status text not null,
                    started_at text not null,
                    completed_at text,
                    summary_json text
                );

                create table if not exists agent_events (
                    id integer primary key autoincrement,
                    run_id text not null,
                    agent text not null,
                    event_type text not null,
                    message text not null,
                    payload_json text,
                    created_at text not null
                );

                create table if not exists tool_calls (
                    id text primary key,
                    run_id text not null,
                    agent text not null,
                    tool text not null,
                    status text not null,
                    input_json text,
                    output_json text,
                    error text,
                    confidence real,
                    created_at text not null
                );

                create table if not exists memory_items (
                    id text primary key,
                    topic text not null,
                    title text not null,
                    url text,
                    summary text,
                    signal_id text,
                    created_at text not null
                );
                """
            )
            _ensure_column(conn, "signals", "score_breakdown_json", "text")
            _ensure_column(conn, "signals", "short_summary", "text")
            _ensure_column(conn, "signals", "expanded_summary", "text")
            _ensure_column(conn, "signals", "image_url", "text")
            _ensure_column(conn, "signals", "icon_key", "text")
            _ensure_column(conn, "signals", "scout_note", "text")
            _ensure_column(conn, "signals", "relevance_label", "text")
            # Per-signal analyst artifact (mechanism, key_actors, evidence, confidence, truncation meta).
            _ensure_column(conn, "signals", "analyst_artifact_json", "text")
            # Per-signal Groq review tracking (Phase 3).
            # analyst_status terminal values: success | failed | skipped.
            # Transient within a run only: pending | pending_retry.
            # analyst_attempt_count counts analyst-level review attempts, NOT
            # BrainClient's internal HTTP retries inside a single chat_json call.
            _ensure_column(conn, "signals", "analyst_status", "text")
            _ensure_column(conn, "signals", "analyst_error_type", "text")
            _ensure_column(conn, "signals", "analyst_error_message", "text")
            _ensure_column(conn, "signals", "analyst_attempt_count", "integer")
            _ensure_column(conn, "signals", "analyst_last_attempt_at", "text")
            # One-shot backfill: set analyst_status for existing rows that have the
            # new column but still hold the SQLite default (NULL).
            # Idempotency guard: skip entirely if any row already has a non-NULL status,
            # so this never runs twice (safe to re-execute on every startup).
            guard = conn.execute(
                "select 1 from signals where analyst_status is not null limit 1"
            ).fetchone()
            if not guard:
                # artifact present → success; artifact NULL → pending (conservative —
                # we can't prove old NULL rows were selected for Groq review).
                conn.execute(
                    "update signals set analyst_status = 'success' "
                    "where analyst_artifact_json is not null"
                )
                conn.execute(
                    "update signals set analyst_status = 'pending' "
                    "where analyst_artifact_json is null"
                )
            # Orphan sweep: signals left in transient states by a killed or crashed run
            # never had the terminal-finalization pass run. Flip them to 'failed' so no
            # row sits in pending/pending_retry indefinitely after the run is gone.
            conn.execute(
                """
                update signals
                set analyst_status = 'failed',
                    analyst_error_message = 'run interrupted before analyst finalization'
                where analyst_status in ('pending', 'pending_retry')
                  and article_id in (
                      select s2.article_id from signals s2
                      inner join agent_runs ar on ar.completed_at >= s2.created_at
                      where ar.status != 'complete'
                  )
                """
            )
            # Editor briefing columns on agent_runs.
            _ensure_column(conn, "agent_runs", "briefing_json", "text")
            _ensure_column(conn, "agent_runs", "briefing_status", "text")
            _ensure_column(conn, "agent_runs", "briefing_error", "text")

    def save_run(self, articles: list[Article], signals: list[Signal], cluster_count: int, output_path: str, started_at: str) -> None:
        completed_at = utc_now_iso()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into articles (id, source, title, url, published_at, body, fetched_at, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    source=excluded.source,
                    title=excluded.title,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    body=excluded.body,
                    fetched_at=excluded.fetched_at,
                    raw_json=excluded.raw_json
                """,
                [
                    (
                        article.id,
                        article.source,
                        article.title,
                        article.url,
                        article.published_at,
                        article.body,
                        article.fetched_at,
                        json.dumps(article.raw, sort_keys=True),
                    )
                    for article in articles
                ],
            )
            conn.executemany(
                """
                insert into signals (
                    id, cluster_id, article_id, title, url, source, published_at, score, urgency, event_type,
                    summary, short_summary, expanded_summary, why_it_matters, next_steps_json, score_breakdown_json,
                    matched_priorities_json, entities_json, image_url, icon_key, scout_note, relevance_label,
                    duplicate_count, analyst_artifact_json, analyst_status, analyst_error_type,
                    analyst_error_message, analyst_attempt_count, analyst_last_attempt_at, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    score=excluded.score,
                    urgency=excluded.urgency,
                    summary=excluded.summary,
                    short_summary=excluded.short_summary,
                    expanded_summary=excluded.expanded_summary,
                    why_it_matters=excluded.why_it_matters,
                    next_steps_json=excluded.next_steps_json,
                    score_breakdown_json=excluded.score_breakdown_json,
                    matched_priorities_json=excluded.matched_priorities_json,
                    entities_json=excluded.entities_json,
                    image_url=excluded.image_url,
                    icon_key=excluded.icon_key,
                    scout_note=excluded.scout_note,
                    relevance_label=excluded.relevance_label,
                    duplicate_count=excluded.duplicate_count,
                    analyst_artifact_json=excluded.analyst_artifact_json,
                    analyst_status=excluded.analyst_status,
                    analyst_error_type=excluded.analyst_error_type,
                    analyst_error_message=excluded.analyst_error_message,
                    analyst_attempt_count=excluded.analyst_attempt_count,
                    analyst_last_attempt_at=excluded.analyst_last_attempt_at,
                    created_at=excluded.created_at
                """,
                [
                    (
                        signal.id,
                        signal.cluster_id,
                        signal.article_id,
                        signal.title,
                        signal.url,
                        signal.source,
                        signal.published_at,
                        signal.score,
                        signal.urgency,
                        signal.event_type,
                        signal.summary,
                        signal.short_summary or signal.summary,
                        signal.expanded_summary or signal.summary,
                        signal.why_it_matters,
                        json.dumps(signal.next_steps),
                        json.dumps(signal.score_breakdown, sort_keys=True),
                        json.dumps(signal.matched_priorities, sort_keys=True),
                        json.dumps(signal.entities, sort_keys=True),
                        signal.image_url,
                        signal.icon_key,
                        signal.scout_note,
                        signal.relevance_label,
                        signal.duplicate_count,
                        # Serialize artifact to JSON if present; null otherwise so old
                        # consumers that don't read this column stay unaffected.
                        json.dumps(signal.analyst_artifact, sort_keys=True) if signal.analyst_artifact else None,
                        signal.analyst_status,
                        signal.analyst_error_type,
                        signal.analyst_error_message,
                        signal.analyst_attempt_count,
                        signal.analyst_last_attempt_at,
                        completed_at,
                    )
                    for signal in signals
                ],
            )
            conn.execute(
                """
                insert into runs (started_at, completed_at, article_count, cluster_count, signal_count, output_path)
                values (?, ?, ?, ?, ?, ?)
                """,
                (started_at, completed_at, len(articles), cluster_count, len(signals), output_path),
            )

    def save_run_atomic(
        self,
        articles: list[Article],
        signals: list[Signal],
        cluster_count: int,
        output_path: str,
        started_at: str,
        run_id: str,
        summary: dict[str, Any] | None = None,
        *,
        briefing_json: str | None = None,
        briefing_status: str | None = None,
        briefing_error: str | None = None,
    ) -> None:
        """Persist a successful run as one atomic transaction.

        Plain English: this replaces save_run() for the agentic path. Articles
        and signals are inserted, the legacy `runs` row is added, and the
        matching `agent_runs` row is flipped to status='complete' — all in a
        single SQLite transaction. If any step raises, the whole thing rolls
        back so a half-finished run never leaves articles marked "seen" by
        accident. That guarantee is what the Wave 2 cursor relies on.
        """
        # Computed once so the runs/agent_runs rows share the same completion
        # timestamp — keeps the dashboard timeline coherent.
        completed_at = utc_now_iso()
        summary_json = json.dumps(summary or {}, sort_keys=True)
        # The connection context manager auto-commits on a clean exit and
        # auto-rollbacks if any statement raises — that's exactly the atomic
        # semantics we need without an explicit BEGIN/COMMIT.
        with self.connect() as conn:
            conn.executemany(
                """
                insert into articles (id, source, title, url, published_at, body, fetched_at, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    source=excluded.source,
                    title=excluded.title,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    body=excluded.body,
                    fetched_at=excluded.fetched_at,
                    raw_json=excluded.raw_json
                """,
                [
                    (
                        article.id,
                        article.source,
                        article.title,
                        article.url,
                        article.published_at,
                        article.body,
                        article.fetched_at,
                        json.dumps(article.raw, sort_keys=True),
                    )
                    for article in articles
                ],
            )
            conn.executemany(
                """
                insert into signals (
                    id, cluster_id, article_id, title, url, source, published_at, score, urgency, event_type,
                    summary, short_summary, expanded_summary, why_it_matters, next_steps_json, score_breakdown_json,
                    matched_priorities_json, entities_json, image_url, icon_key, scout_note, relevance_label,
                    duplicate_count, analyst_artifact_json, analyst_status, analyst_error_type,
                    analyst_error_message, analyst_attempt_count, analyst_last_attempt_at, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    score=excluded.score,
                    urgency=excluded.urgency,
                    summary=excluded.summary,
                    short_summary=excluded.short_summary,
                    expanded_summary=excluded.expanded_summary,
                    why_it_matters=excluded.why_it_matters,
                    next_steps_json=excluded.next_steps_json,
                    score_breakdown_json=excluded.score_breakdown_json,
                    matched_priorities_json=excluded.matched_priorities_json,
                    entities_json=excluded.entities_json,
                    image_url=excluded.image_url,
                    icon_key=excluded.icon_key,
                    scout_note=excluded.scout_note,
                    relevance_label=excluded.relevance_label,
                    duplicate_count=excluded.duplicate_count,
                    analyst_artifact_json=excluded.analyst_artifact_json,
                    analyst_status=excluded.analyst_status,
                    analyst_error_type=excluded.analyst_error_type,
                    analyst_error_message=excluded.analyst_error_message,
                    analyst_attempt_count=excluded.analyst_attempt_count,
                    analyst_last_attempt_at=excluded.analyst_last_attempt_at,
                    created_at=excluded.created_at
                """,
                [
                    (
                        signal.id,
                        signal.cluster_id,
                        signal.article_id,
                        signal.title,
                        signal.url,
                        signal.source,
                        signal.published_at,
                        signal.score,
                        signal.urgency,
                        signal.event_type,
                        signal.summary,
                        signal.short_summary or signal.summary,
                        signal.expanded_summary or signal.summary,
                        signal.why_it_matters,
                        json.dumps(signal.next_steps),
                        json.dumps(signal.score_breakdown, sort_keys=True),
                        json.dumps(signal.matched_priorities, sort_keys=True),
                        json.dumps(signal.entities, sort_keys=True),
                        signal.image_url,
                        signal.icon_key,
                        signal.scout_note,
                        signal.relevance_label,
                        signal.duplicate_count,
                        # Persist artifact as JSON or null; the read path tolerates absence.
                        json.dumps(signal.analyst_artifact, sort_keys=True) if signal.analyst_artifact else None,
                        signal.analyst_status,
                        signal.analyst_error_type,
                        signal.analyst_error_message,
                        signal.analyst_attempt_count,
                        signal.analyst_last_attempt_at,
                        completed_at,
                    )
                    for signal in signals
                ],
            )
            # Flip agent_runs.status to 'complete' inside the same transaction
            # — this is the contract the cursor depends on. If anything above
            # raised, this update never lands and the cursor stays put.
            conn.execute(
                "update agent_runs set status = ?, completed_at = ?, summary_json = ?, "
                "briefing_json = ?, briefing_status = ?, briefing_error = ? where id = ?",
                ("complete", completed_at, summary_json, briefing_json, briefing_status, briefing_error, run_id),
            )

    def latest_complete_agent_run_started_at(self) -> str | None:
        """ISO timestamp of the most recent agent_runs row with status='complete'.

        Plain English: this is the run cursor. Scout uses it to know which
        articles count as "new." A failed or interrupted run never advances
        it, so a partial run can't accidentally hide tomorrow's fresh stories.
        Returns None on the very first run (no complete runs yet).
        """
        with self.connect() as conn:
            row = conn.execute(
                "select started_at from agent_runs where status = 'complete' "
                "order by started_at desc limit 1"
            ).fetchone()
        return row["started_at"] if row else None

    def is_article_seen(self, article_id: str, url: str) -> bool:
        """True if this article was persisted by a prior complete run.

        Plain English: the analyst checks this before scoring so we don't
        re-rank the same article we already shipped. Match is by stable
        article_id first, then by URL (case-insensitive) as a backstop in
        case the id-hash changed because the source/title shifted.
        """
        if not article_id and not url:
            return False
        with self.connect() as conn:
            if article_id:
                row = conn.execute(
                    "select 1 from articles where id = ? limit 1", (article_id,)
                ).fetchone()
                if row:
                    return True
            if url:
                # Case-insensitive URL match guards against trivial casing
                # differences between sources without doing heavy normalization.
                row = conn.execute(
                    "select 1 from articles where lower(url) = lower(?) limit 1",
                    (url,),
                ).fetchone()
                if row:
                    return True
        return False

    def add_feedback(self, signal_id: str, label: str, note: str = "") -> None:
        allowed = {"useful", "not_useful", "critical", "irrelevant"}
        if label not in allowed:
            raise ValueError(f"label must be one of: {', '.join(sorted(allowed))}")
        with self.connect() as conn:
            conn.execute(
                "insert into feedback (signal_id, label, note, created_at) values (?, ?, ?, ?)",
                (signal_id, label, note, utc_now_iso()),
            )

    def list_signals(self, limit: int = 10) -> list[dict[str, Any]]:
        # Legacy entry point kept for the CLI. New callers should use list_signals_paged().
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id, title, score, urgency, event_type, source, published_at, summary, short_summary,
                       expanded_summary, why_it_matters, url, score_breakdown_json, entities_json, image_url,
                       icon_key, scout_note, relevance_label, analyst_artifact_json, analyst_status,
                       analyst_error_type, analyst_error_message, analyst_attempt_count,
                       analyst_last_attempt_at, created_at
                from signals
                order by created_at desc, score desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [self._hydrate_signal_row(row) for row in rows]

    def count_signals(self, run_started_at: str | None = None) -> int:
        # When run_started_at is provided, only count signals produced in that run or later.
        # The on-conflict upsert in save_run() refreshes created_at on every run, so this
        # reliably identifies "signals produced in the latest run" without a schema change.
        query = "select count(*) as n from signals"
        params: list[Any] = []
        if run_started_at:
            query += " where created_at >= ?"
            params.append(run_started_at)
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
        return int(row["n"]) if row else 0

    def list_signals_paged(
        self,
        run_started_at: str | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        # Paged signal listing. When run_started_at is set, restricts to signals
        # produced in that run window (created_at >= run_started_at).
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))  # clamp page size to a safe range
        offset = (page - 1) * page_size

        total = self.count_signals(run_started_at=run_started_at)
        total_pages = (total + page_size - 1) // page_size if total else 0

        # Build the data query with optional WHERE clause for the run window
        query = """
            select id, title, score, urgency, event_type, source, published_at, summary, short_summary,
                   expanded_summary, why_it_matters, url, score_breakdown_json, entities_json, image_url,
                   icon_key, scout_note, relevance_label, analyst_artifact_json, created_at
            from signals
        """
        params: list[Any] = []
        if run_started_at:
            query += " where created_at >= ?"
            params.append(run_started_at)
        query += " order by created_at desc, score desc limit ? offset ?"
        params.extend([page_size, offset])

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return {
            # slim=True omits score_breakdown from list response — detail endpoint serves it.
            "items": [self._hydrate_signal_row(row, slim=True) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        """Return a single signal by ID with score_breakdown and analyst_artifact included."""
        with self.connect() as conn:
            row = conn.execute(
                """
                select id, title, score, urgency, event_type, source, published_at, summary, short_summary,
                       expanded_summary, why_it_matters, url, score_breakdown_json, entities_json, image_url,
                       icon_key, scout_note, relevance_label, analyst_artifact_json, analyst_status,
                       analyst_error_type, analyst_error_message, analyst_attempt_count,
                       analyst_last_attempt_at, created_at
                from signals where id = ?
                """,
                (signal_id,),
            ).fetchone()
        return self._hydrate_signal_row(row) if row else None

    def list_signals_executive(
        self,
        limit: int = 12,
        run_started_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the top `limit` signals by score from the latest run window.

        Used by the /api/signals/executive endpoint to power the digest exec
        summary block. Signals are slim (no score_breakdown) — the executive
        view only needs headline metadata.
        """
        query = """
            select id, title, score, urgency, event_type, source, published_at, summary, short_summary,
                   expanded_summary, why_it_matters, url, score_breakdown_json, entities_json, image_url,
                   icon_key, scout_note, relevance_label, analyst_artifact_json, created_at
            from signals
        """
        params: list[Any] = []
        if run_started_at:
            query += " where created_at >= ?"
            params.append(run_started_at)
        query += " order by score desc, created_at desc limit ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._hydrate_signal_row(row, slim=True) for row in rows]

    def latest_run(self) -> dict[str, Any] | None:
        # Most recent complete run from agent_runs. Used by the dashboard to scope
        # the digest page to the latest run's articles.
        with self.connect() as conn:
            row = conn.execute(
                "select id, started_at, completed_at, summary_json "
                "from agent_runs where status = 'complete' "
                "order by started_at desc, completed_at desc, id desc limit 1"
            ).fetchone()
        if not row:
            return None
        summary = json.loads(row["summary_json"] or "{}")
        return {
            "id": row["id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "article_count": summary.get("articles", 0),
            "cluster_count": summary.get("cluster_count", 0),
            "signal_count": summary.get("signals", 0),
            "output_path": summary.get("output_path", ""),
        }

    def get_latest_briefing(self) -> dict[str, Any]:
        """Return the executive briefing for the most recent complete run.

        Returns a dict with briefing, briefing_status, generated_at,
        source_signal_ids, and run_id. When no complete run exists or the run
        has no briefing yet, briefing is null and status is 'skipped'.
        """
        with self.connect() as conn:
            row = conn.execute(
                "select id, briefing_json, briefing_status, briefing_error "
                "from agent_runs where status = 'complete' "
                "order by started_at desc, completed_at desc, id desc limit 1"
            ).fetchone()
        if not row:
            return {"briefing": None, "briefing_status": "skipped", "generated_at": None, "source_signal_ids": [], "run_id": None}
        try:
            briefing = json.loads(row["briefing_json"] or "null")
        except json.JSONDecodeError:
            briefing = None
        return {
            "briefing": briefing,
            "briefing_status": row["briefing_status"] or "skipped",
            "generated_at": briefing.get("generated_at") if briefing else None,
            "source_signal_ids": briefing.get("source_signal_ids", []) if briefing else [],
            "run_id": row["id"],
        }

    def _hydrate_signal_row(self, row: Any, *, slim: bool = False) -> dict[str, Any]:
        # Shared post-processing for signal rows: parse JSON fields and apply UI fallbacks.
        # slim=True omits score_breakdown — used by the list endpoint to reduce payload size.
        item = dict(row)
        if slim:
            # Drop the (potentially large) breakdown from list responses.
            item.pop("score_breakdown_json", None)
        else:
            try:
                item["score_breakdown"] = json.loads(item.pop("score_breakdown_json") or "[]")
            except json.JSONDecodeError:
                item["score_breakdown"] = []
        try:
            item["entities"] = json.loads(item.pop("entities_json") or "{}")
        except json.JSONDecodeError:
            item["entities"] = {}
        # analyst_artifact_json is only selected by get_signal (detail endpoint).
        # Parse it when present; old signals without the column return null.
        if "analyst_artifact_json" in item:
            try:
                item["analyst_artifact"] = json.loads(item.pop("analyst_artifact_json") or "null")
            except json.JSONDecodeError:
                item.pop("analyst_artifact_json", None)
                item["analyst_artifact"] = None
        item["short_summary"] = item.get("short_summary") or item.get("summary") or ""
        item["expanded_summary"] = item.get("expanded_summary") or item.get("short_summary") or ""
        item["icon_key"] = item.get("icon_key") or item.get("event_type") or "signal"
        item["image_url"] = item.get("image_url") or ""
        item["scout_note"] = item.get("scout_note") or ""
        item["relevance_label"] = item.get("relevance_label") or "keep"
        # Analyst review status fields: safe defaults for rows created before this
        # migration (legacy rows have NULL in these columns from the DB).
        item["analyst_status"] = item.get("analyst_status") or "pending"
        item["analyst_error_type"] = item.get("analyst_error_type")
        item["analyst_error_message"] = item.get("analyst_error_message")
        item["analyst_attempt_count"] = item.get("analyst_attempt_count") or 0
        item["analyst_last_attempt_at"] = item.get("analyst_last_attempt_at")
        return item

    def update_signal_artifact(self, signal_id: str, artifact: dict[str, Any]) -> None:
        """Persist a refreshed analyst artifact for one signal.

        Called by the Editor fallback after it re-fetches and re-reviews a signal.
        Does NOT touch other signal fields — only analyst_artifact_json is updated.
        """
        with self.connect() as conn:
            conn.execute(
                "update signals set analyst_artifact_json = ? where id = ?",
                (json.dumps(artifact, sort_keys=True), signal_id),
            )

    def get_signal_artifacts(self, signal_ids: list[str]) -> dict[str, dict[str, Any] | None]:
        """Load analyst_artifact_json for a batch of signal IDs.

        Returns a mapping of signal_id -> parsed artifact dict (or None when absent).
        Used by the Editor fallback to check which signals need re-fetching.
        """
        if not signal_ids:
            return {}
        placeholders = ",".join("?" * len(signal_ids))
        with self.connect() as conn:
            rows = conn.execute(
                f"select id, analyst_artifact_json from signals where id in ({placeholders})",
                signal_ids,
            ).fetchall()
        result: dict[str, dict[str, Any] | None] = {}
        for row in rows:
            raw = row["analyst_artifact_json"]
            try:
                result[row["id"]] = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                result[row["id"]] = None
        # Fill in None for any IDs not found in the DB
        for sid in signal_ids:
            if sid not in result:
                result[sid] = None
        return result

    def load_priority_adjustments(self) -> dict[str, float]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select s.matched_priorities_json, f.label
                from feedback f
                join signals s on s.id = f.signal_id
                order by f.created_at desc
                limit 100
                """
            ).fetchall()

        adjustments: dict[str, float] = {}
        label_weight = {"critical": 0.35, "useful": 0.18, "not_useful": -0.15, "irrelevant": -0.25}
        for row in rows:
            try:
                priorities = json.loads(row["matched_priorities_json"] or "[]")
            except json.JSONDecodeError:
                continue
            for priority in priorities:
                name = priority.get("name")
                if name:
                    adjustments[name] = adjustments.get(name, 0.0) + label_weight.get(row["label"], 0.0)
        return adjustments

    def start_agent_run(self, goal: str) -> str:
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        with self.connect() as conn:
            conn.execute(
                "insert into agent_runs (id, goal, status, started_at) values (?, ?, ?, ?)",
                (run_id, goal, "running", utc_now_iso()),
            )
        return run_id

    def finish_agent_run(self, run_id: str, status: str, summary: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "update agent_runs set status = ?, completed_at = ?, summary_json = ? where id = ?",
                (status, utc_now_iso(), json.dumps(summary or {}, sort_keys=True), run_id),
            )

    def mark_stale_runs_failed(self) -> int:
        """Mark any runs still in 'running' state as failed. Returns count updated.

        Used at dashboard shutdown so a SIGTERM never leaves rows orphaned. We
        also stamp summary_json with a reason so the UI can explain to the user
        why an old run is showing as failed instead of just a red badge.
        """
        reason_json = json.dumps({"reason": "dashboard shutdown swept stale run"}, sort_keys=True)
        with self.connect() as conn:
            cur = conn.execute(
                "update agent_runs set status = 'failed', completed_at = ?, "
                "summary_json = coalesce(summary_json, ?) where status = 'running'",
                (utc_now_iso(), reason_json),
            )
            return cur.rowcount

    def mark_runs_failed_if_idle(self, max_idle_seconds: int) -> list[str]:
        """Sweep 'running' rows whose timeline has been silent too long.

        Plain English: every minute the dashboard calls this. If a run claims
        to be running but its last agent_event is older than `max_idle_seconds`
        (or it never logged any events at all), we mark it failed with a clear
        reason. This unsticks orphaned runs without restarting the dashboard.
        Returns the list of run_ids that were swept.
        """
        # Idle cutoff in ISO so it slots straight into the WHERE clause.
        # Same format as utc_now_iso() (no microseconds, trailing 'Z') so the
        # string comparison against created_at/started_at columns is correct.
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=max_idle_seconds)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.connect() as conn:
            # A row is considered idle when EITHER the most-recent event is
            # older than the cutoff OR there are no events at all and the
            # run itself started before the cutoff.
            rows = conn.execute(
                """
                select r.id
                from agent_runs r
                left join (
                    select run_id, max(created_at) as last_event
                    from agent_events
                    group by run_id
                ) e on e.run_id = r.id
                where r.status = 'running'
                  and coalesce(e.last_event, r.started_at) < ?
                """,
                (cutoff,),
            ).fetchall()
            stale_ids = [row["id"] for row in rows]
            if not stale_ids:
                return []
            reason_json = json.dumps(
                {"reason": f"stale: no events for {max_idle_seconds}s"}, sort_keys=True
            )
            now = utc_now_iso()
            placeholders = ",".join("?" * len(stale_ids))
            conn.execute(
                f"update agent_runs set status = 'failed', completed_at = ?, "
                f"summary_json = coalesce(summary_json, ?) where id in ({placeholders})",
                (now, reason_json, *stale_ids),
            )
            return stale_ids

    def save_agent_event(self, run_id: str, agent: str, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into agent_events (run_id, agent, event_type, message, payload_json, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (run_id, agent, event_type, message, json.dumps(payload or {}, sort_keys=True), utc_now_iso()),
            )

    def save_tool_call(self, call: ToolCall) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert or replace into tool_calls
                (id, run_id, agent, tool, status, input_json, output_json, error, confidence, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.id,
                    call.run_id,
                    call.agent,
                    call.tool,
                    call.status,
                    json.dumps(call.input, sort_keys=True),
                    json.dumps(call.output, sort_keys=True),
                    call.error,
                    call.confidence,
                    call.created_at,
                ),
            )

    def save_memory_for_signal(self, signal: Signal) -> None:
        topic = signal.event_type or "general"
        memory_id = stable_id(topic, signal.title, signal.url, prefix="mem")
        with self.connect() as conn:
            conn.execute(
                """
                insert or replace into memory_items
                (id, topic, title, url, summary, signal_id, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (memory_id, topic, signal.title, signal.url, signal.summary, signal.id, utc_now_iso()),
            )

    def memory_matches(self, text: str, limit: int = 8) -> list[dict[str, Any]]:
        words = {word.lower() for word in text.split() if len(word) > 4}
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id, topic, title, url, summary, signal_id, created_at
                from memory_items
                order by created_at desc
                limit 80
                """
            ).fetchall()
        matches = []
        for row in rows:
            haystack = f"{row['title']} {row['summary']}".lower()
            overlap = sum(1 for word in words if word in haystack)
            if overlap >= 2:
                item = dict(row)
                item["overlap"] = overlap
                matches.append(item)
        matches.sort(key=lambda item: (item["overlap"], item["created_at"]), reverse=True)
        return matches[:limit]

    def latest_agent_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "select * from agent_runs order by started_at desc limit 1"
            ).fetchone()
        return dict(row) if row else None

    def agent_events(self, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "select * from agent_events"
        params: list[Any] = []
        if run_id:
            query += " where run_id = ?"
            params.append(run_id)
        query += " order by created_at asc, id asc limit ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def tool_calls(self, run_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        query = "select * from tool_calls"
        params: list[Any] = []
        if run_id:
            query += " where run_id = ?"
            params.append(run_id)
        query += " order by created_at asc limit ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_memory(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id, topic, title, url, summary, signal_id, created_at
                from memory_items
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
