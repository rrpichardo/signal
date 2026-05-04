from __future__ import annotations

from pathlib import Path
import json
import sqlite3
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
                    duplicate_count, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        with self.connect() as conn:
            rows = conn.execute(
                """
                select id, title, score, urgency, event_type, source, published_at, summary, short_summary,
                       expanded_summary, why_it_matters, url, score_breakdown_json, entities_json, image_url,
                       icon_key, scout_note, relevance_label, created_at
                from signals
                order by created_at desc, score desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["score_breakdown"] = json.loads(item.pop("score_breakdown_json") or "[]")
            except json.JSONDecodeError:
                item["score_breakdown"] = []
            try:
                item["entities"] = json.loads(item.pop("entities_json") or "{}")
            except json.JSONDecodeError:
                item["entities"] = {}
            item["short_summary"] = item.get("short_summary") or item.get("summary") or ""
            item["expanded_summary"] = item.get("expanded_summary") or item.get("short_summary") or ""
            item["icon_key"] = item.get("icon_key") or item.get("event_type") or "signal"
            item["image_url"] = item.get("image_url") or ""
            item["scout_note"] = item.get("scout_note") or ""
            item["relevance_label"] = item.get("relevance_label") or "keep"
            items.append(item)
        return items

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
        run_id = stable_id(goal, utc_now_iso(), prefix="run")
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
