"""SQLite-backed student state + append-only events (P2).

Design rules:
  * the ``events`` table exists from P2 onwards, so P5 Agent Trace and E-phase
    metrics never need retroactive instrumentation;
  * ``record_answer`` is idempotent when given an ``event_key`` - replaying the
    same step never double-counts an attempt or moves mastery twice;
  * every event carries the frozen config snapshot plus latency/token/cost
    columns, so a P6 run is fully reproducible from its log alone.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils.config import config_for_log, get_config, resolve_path
from .mastery import is_error, performance_from_answer, update_mastery

EVENT_COLUMNS = (
    "event_key", "ts", "run_id", "student_id", "topic", "step", "action",
    "action_reason", "mastery_before", "mastery_after", "performance",
    "evaluation_result", "question_id", "is_error", "recent_errors",
    "lecture", "page", "citation", "latency_ms", "prompt_tokens",
    "completion_tokens", "total_tokens", "cost_usd", "model",
    "platform_version", "mastery_alpha", "seed", "config_snapshot",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StudentStateStore:
    """One SQLite file per platform instance (see config.yaml paths.sqlite_path)."""

    def __init__(self, db_path: str | Path | None = None, config: dict[str, Any] | None = None):
        cfg = config or get_config()
        self.config = cfg
        self.db_path = (
            Path(db_path) if db_path is not None else resolve_path(cfg["paths"]["sqlite_path"])
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.alpha = float(cfg["mastery"]["alpha"])
        self.initial_mastery = float(cfg["mastery"].get("initial", 0.0))
        self.window = int(cfg["mastery"].get("recent_errors_window", 3))
        self._snapshot = config_for_log(cfg)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self._conn.executescript(schema)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StudentStateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- state ---------------------------------------------------------------

    def get_state(self, student_id: str, topic: str) -> dict[str, Any]:
        """Current knowledge state for one (student, topic), safe for Planner."""
        row = self._conn.execute(
            "SELECT * FROM student_topic_state WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        ).fetchone()
        if row is None:
            return {
                "student_id": student_id,
                "topic": topic,
                "mastery": self.initial_mastery,
                "attempts": 0,
                "correct_count": 0,
                "incorrect_count": 0,
                "recent_errors": [],
                "last_studied": None,
            }
        state = dict(row)
        state["recent_errors"] = json.loads(state["recent_errors"] or "[]")
        return state

    def state_snapshot(self, student_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT topic FROM student_topic_state WHERE student_id = ? ORDER BY topic",
            (student_id,),
        ).fetchall()
        return [self.get_state(student_id, row["topic"]) for row in rows]

    # --- events --------------------------------------------------------------

    def _insert_event(self, fields: dict[str, Any]) -> dict[str, Any]:
        columns = [name for name in EVENT_COLUMNS if name in fields]
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})"
        cursor = self._conn.execute(sql, [fields[name] for name in columns])
        event = dict(fields)
        event["id"] = cursor.lastrowid
        return event

    def _base_fields(self, student_id: str, topic: str) -> dict[str, Any]:
        return {
            "ts": _now(),
            "student_id": student_id,
            "topic": topic,
            "model": self._snapshot["model"],
            "platform_version": self._snapshot["platform_version"],
            "mastery_alpha": self.alpha,
            "seed": self._snapshot["seed"],
            "config_snapshot": json.dumps(self._snapshot, ensure_ascii=False),
        }

    def log_event(
        self,
        student_id: str,
        topic: str,
        *,
        event_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Append a non-answer event (planner decision, retrieval, etc.).

        Unknown keyword names are ignored so P3-P6 can extend the trace without
        schema churn for every new field.
        """
        if event_key:
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "idempotent": True}
        payload = {**self._base_fields(student_id, topic), **fields, "event_key": event_key}
        payload = {k: v for k, v in payload.items() if k in EVENT_COLUMNS}
        with self._conn:
            return self._insert_event(payload)

    def record_answer(
        self,
        student_id: str,
        topic: str,
        answer: bool | int | float,
        *,
        event_key: str | None = None,
        question_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        action: str = "MCQ",
        action_reason: str | None = None,
        citation: str | None = None,
        lecture: str | None = None,
        page: int | None = None,
        latency_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate one objective answer, update state, append exactly one event."""
        if event_key:
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing is not None:
                return {**dict(existing), "idempotent": True}

        performance = performance_from_answer(answer)
        wrong = is_error(performance)
        state = self.get_state(student_id, topic)
        mastery_before = float(state["mastery"])
        mastery_after = update_mastery(mastery_before, performance, self.alpha)

        recent = list(state["recent_errors"])
        if wrong:
            recent.append({"question_id": question_id, "topic": topic, "ts": _now()})
        recent = recent[-self.window:] if self.window > 0 else []

        ts = _now()
        fields: dict[str, Any] = {
            **self._base_fields(student_id, topic),
            "event_key": event_key,
            "ts": ts,
            "run_id": run_id,
            "step": step,
            "action": action,
            "action_reason": action_reason,
            "mastery_before": mastery_before,
            "mastery_after": mastery_after,
            "performance": performance,
            "evaluation_result": "correct" if not wrong else "incorrect",
            "question_id": question_id,
            "is_error": int(wrong),
            "recent_errors": json.dumps(recent, ensure_ascii=False),
            "lecture": lecture,
            "page": page,
            "citation": citation,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
        }
        if model:
            # P4: log the model that actually answered, not just the configured one.
            fields["model"] = model

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO student_topic_state
                    (student_id, topic, mastery, attempts, correct_count,
                     incorrect_count, recent_errors, last_studied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(student_id, topic) DO UPDATE SET
                    mastery = excluded.mastery,
                    attempts = excluded.attempts,
                    correct_count = excluded.correct_count,
                    incorrect_count = excluded.incorrect_count,
                    recent_errors = excluded.recent_errors,
                    last_studied = excluded.last_studied
                """,
                (
                    student_id,
                    topic,
                    mastery_after,
                    state["attempts"] + 1,
                    state["correct_count"] + (0 if wrong else 1),
                    state["incorrect_count"] + (1 if wrong else 0),
                    json.dumps(recent, ensure_ascii=False),
                    ts,
                ),
            )
            event = self._insert_event(fields)
        return {**event, "idempotent": False}

    def get_events(
        self,
        student_id: str,
        *,
        topic: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE student_id = ?"
        params: list[Any] = [student_id]
        if topic is not None:
            sql += " AND topic = ?"
            params.append(topic)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def reset(self, student_id: str | None = None) -> None:
        """Clear state/events for reproducible experiment runs (E2)."""
        with self._conn:
            if student_id is None:
                self._conn.execute("DELETE FROM events")
                self._conn.execute("DELETE FROM student_topic_state")
            else:
                self._conn.execute("DELETE FROM events WHERE student_id = ?", (student_id,))
                self._conn.execute(
                    "DELETE FROM student_topic_state WHERE student_id = ?", (student_id,)
                )
