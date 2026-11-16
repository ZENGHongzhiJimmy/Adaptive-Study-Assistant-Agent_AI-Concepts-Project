"""SQLite persistence for the adaptive policy's learner state.

Storage is split from the policy on purpose: ``policy.py`` is pure and unit
testable, this module owns the schema and the read/write.

Why its own tables?  The retired CLI harness kept a 0-1 mastery with alpha
smoothing in a separate table set (deleted 2026-11-16).  The product tracks a
richer, per-material record: 0-100 mastery, error book, reading counters,
rolling round.  Two different models would have meant two different schemas;
now there is only this one, and every consumer reads the same rows.

Tables
------
``learner_topic``  one row per (material, topic): mastery + error book + counters
``learner_roll``   rolling-round book-keeping per material (round, visited[])
``learner_step``   append-only decision trace (what the agent chose and why)

``learner_step`` is the same idea as ``events``: every decision the agent makes
is logged with mastery before/after, the rule that fired and the latency, so the
evaluation can be computed from the log instead of from ad-hoc prints.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..utils.config import get_config, resolve_path
from .policy import TopicState

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner_topic (
    material_id   TEXT NOT NULL,
    topic_id      TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    mastery       REAL NOT NULL DEFAULT 30,
    attempts      INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    mistakes_json TEXT NOT NULL DEFAULT '[]',
    explain_done  INTEGER NOT NULL DEFAULT 0,
    explain_runs  INTEGER NOT NULL DEFAULT 0,
    fail_streak   INTEGER NOT NULL DEFAULT 0,
    need_help     INTEGER NOT NULL DEFAULT 0,
    pretested     INTEGER NOT NULL DEFAULT 0,
    explain_pages INTEGER NOT NULL DEFAULT 0,
    quiz_count    INTEGER NOT NULL DEFAULT 0,
    ord           INTEGER NOT NULL DEFAULT 0,
    updated_ts    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (material_id, topic_id)
);

CREATE TABLE IF NOT EXISTS learner_roll (
    material_id  TEXT PRIMARY KEY,
    round        INTEGER NOT NULL DEFAULT 1,
    visited_json TEXT NOT NULL DEFAULT '[]',
    updated_ts   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS learner_step (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    material_id    TEXT NOT NULL,
    topic_id       TEXT NOT NULL,
    activity       TEXT NOT NULL,
    rule           TEXT NOT NULL DEFAULT '',
    mastery_before REAL NOT NULL DEFAULT 0,
    mastery_after  REAL NOT NULL DEFAULT 0,
    delta          REAL NOT NULL DEFAULT 0,
    outcome        TEXT NOT NULL DEFAULT '',
    policy         TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    latency_ms     REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_learner_step_material
    ON learner_step (material_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LearnerStore:
    """Thin SQLite gateway; no policy logic lives here."""

    def __init__(self, db_path: str | Path | None = None, config: dict[str, Any] | None = None):
        cfg = config or get_config()
        self.db_path = (
            Path(db_path) if db_path is not None else resolve_path(cfg["paths"]["sqlite_path"])
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LearnerStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- topics

    def load_topics(self, material_id: str) -> list[TopicState]:
        rows = self._conn.execute(
            "SELECT * FROM learner_topic WHERE material_id = ? ORDER BY ord, topic_id",
            (material_id,),
        ).fetchall()
        return [_row_to_topic(row) for row in rows]

    def seed_topics(self, material_id: str, topics: Sequence[TopicState]) -> list[TopicState]:
        """Insert unknown topics, refresh content flags on known ones.

        Existing learning state is *never* overwritten by a client payload:
        otherwise a stale browser tab could roll a student's mastery back.  Only
        the content availability (name / explain pages / quiz count) is refreshed,
        because a material can be re-parsed and gain new questions.
        """
        known = {t.topic_id for t in self.load_topics(material_id)}
        for index, topic in enumerate(topics):
            if topic.topic_id in known:
                self._conn.execute(
                    "UPDATE learner_topic SET name = ?, explain_pages = ?, quiz_count = ?, ord = ?,"
                    " updated_ts = ? WHERE material_id = ? AND topic_id = ?",
                    (topic.name, topic.explain_pages, topic.quiz_count,
                     topic.order if topic.order else index, _now(), material_id, topic.topic_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO learner_topic (material_id, topic_id, name, mastery, attempts,"
                    " errors, mistakes_json, explain_done, explain_runs, fail_streak, need_help,"
                    " pretested, explain_pages, quiz_count, ord, updated_ts)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        material_id, topic.topic_id, topic.name, float(topic.mastery),
                        int(topic.attempts), int(topic.errors),
                        json.dumps(topic.mistakes, ensure_ascii=False),
                        int(bool(topic.explain_done)), int(topic.explain_runs),
                        int(topic.fail_streak), int(bool(topic.need_help)),
                        int(bool(topic.pretested)), int(topic.explain_pages),
                        int(topic.quiz_count), int(topic.order if topic.order else index), _now(),
                    ),
                )
        self._conn.commit()
        return self.load_topics(material_id)

    def save_topic(self, material_id: str, topic: TopicState) -> None:
        self._conn.execute(
            "UPDATE learner_topic SET name = ?, mastery = ?, attempts = ?, errors = ?,"
            " mistakes_json = ?, explain_done = ?, explain_runs = ?, fail_streak = ?,"
            " need_help = ?, pretested = ?, explain_pages = ?, quiz_count = ?, updated_ts = ?"
            " WHERE material_id = ? AND topic_id = ?",
            (
                topic.name, float(topic.mastery), int(topic.attempts), int(topic.errors),
                json.dumps(topic.mistakes, ensure_ascii=False),
                int(bool(topic.explain_done)), int(topic.explain_runs), int(topic.fail_streak),
                int(bool(topic.need_help)), int(bool(topic.pretested)),
                int(topic.explain_pages), int(topic.quiz_count), _now(),
                material_id, topic.topic_id,
            ),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ roll

    def load_roll(self, material_id: str) -> tuple[int, list[str]]:
        row = self._conn.execute(
            "SELECT round, visited_json FROM learner_roll WHERE material_id = ?",
            (material_id,),
        ).fetchone()
        if row is None:
            return 1, []
        try:
            visited = json.loads(row["visited_json"] or "[]")
        except json.JSONDecodeError:
            visited = []
        return int(row["round"] or 1), [str(v) for v in visited if v]

    def save_roll(self, material_id: str, round_no: int, visited: Iterable[str]) -> None:
        self._conn.execute(
            "INSERT INTO learner_roll (material_id, round, visited_json, updated_ts)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(material_id) DO UPDATE SET round = excluded.round,"
            " visited_json = excluded.visited_json, updated_ts = excluded.updated_ts",
            (material_id, int(round_no), json.dumps(list(visited)), _now()),
        )
        self._conn.commit()

    # ------------------------------------------------------------- trace

    def log_step(
        self,
        *,
        material_id: str,
        topic_id: str,
        activity: str,
        rule: str = "",
        mastery_before: float = 0.0,
        mastery_after: float = 0.0,
        delta: float = 0.0,
        outcome: str = "",
        policy: str = "",
        source: str = "web",
        latency_ms: float = 0.0,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO learner_step (ts, material_id, topic_id, activity, rule, mastery_before,"
            " mastery_after, delta, outcome, policy, source, latency_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(), material_id, topic_id, activity, rule, float(mastery_before),
                float(mastery_after), float(delta), outcome, policy, source,
                float(latency_ms),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def recent_steps(self, material_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if material_id:
            rows = self._conn.execute(
                "SELECT * FROM learner_step WHERE material_id = ? ORDER BY id DESC LIMIT ?",
                (material_id, int(limit)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM learner_step ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------- lifecycle

    def clear_material(self, material_id: str) -> None:
        self._conn.execute("DELETE FROM learner_topic WHERE material_id = ?", (material_id,))
        self._conn.execute("DELETE FROM learner_roll WHERE material_id = ?", (material_id,))
        self._conn.execute("DELETE FROM learner_step WHERE material_id = ?", (material_id,))
        self._conn.commit()

    def clear_all(self) -> None:
        for table in ("learner_topic", "learner_roll", "learner_step"):
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()


def _row_to_topic(row: sqlite3.Row) -> TopicState:
    try:
        mistakes = json.loads(row["mistakes_json"] or "[]")
    except json.JSONDecodeError:
        mistakes = []
    return TopicState(
        topic_id=row["topic_id"],
        name=row["name"],
        mastery=float(row["mastery"]),
        attempts=int(row["attempts"]),
        errors=int(row["errors"]),
        mistakes=mistakes if isinstance(mistakes, list) else [],
        explain_done=bool(row["explain_done"]),
        explain_runs=int(row["explain_runs"]),
        fail_streak=int(row["fail_streak"]),
        need_help=bool(row["need_help"]),
        pretested=bool(row["pretested"]),
        explain_pages=int(row["explain_pages"]),
        quiz_count=int(row["quiz_count"]),
        order=int(row["ord"]),
    )
