"""P2 acceptance tests: mastery boundaries, idempotency, error window, events."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.student_model.mastery import (
    clamp01,
    is_error,
    performance_from_answer,
    update_mastery,
)
from src.student_model.state import StudentStateStore


@pytest.fixture
def store(tmp_path: Path):
    with StudentStateStore(tmp_path / "student_state.db") as state:
        yield state


# --- mastery formula --------------------------------------------------------

def test_mastery_formula_boundaries():
    assert update_mastery(0.0, 1.0, 0.7) == pytest.approx(0.3)
    assert update_mastery(0.5, 1.0, 0.0) == 1.0  # alpha=0: only performance
    assert update_mastery(0.5, 0.0, 1.0) == 0.5  # alpha=1: only history
    assert update_mastery(1.0, 1.0, 0.7) == 1.0
    assert update_mastery(0.0, 0.0, 0.7) == 0.0


def test_mastery_clamps_and_rejects_non_finite():
    assert update_mastery(-1.0, 2.0, 0.5) == 0.5
    assert update_mastery(1.5, -2.0, 1.5) == 1.0  # alpha clamps to 1 -> history only
    assert update_mastery(1.5, -2.0, 0.0) == 0.0  # alpha 0 -> performance only
    assert clamp01(0.499) == 0.499
    for bad in (float("nan"), float("inf"), "n/a", None, object()):
        with pytest.raises(ValueError):
            update_mastery(bad, 1.0)


def test_performance_normalization_and_error_flag():
    assert performance_from_answer(True) == 1.0
    assert performance_from_answer(False) == 0.0
    assert performance_from_answer(0.25) == 0.25
    assert is_error(0.0) and not is_error(1.0)
    assert is_error(0.49) and not is_error(0.5)


# --- state updates ----------------------------------------------------------

def test_get_state_defaults_and_mastery_moves_with_answers(store):
    initial = store.get_state("s1", "Bayes Theorem")
    assert initial["mastery"] == 0.0
    assert initial["attempts"] == 0
    assert initial["recent_errors"] == []

    e1 = store.record_answer("s1", "Bayes Theorem", True, event_key="r1:1")
    assert e1["mastery_after"] == pytest.approx(0.3)
    assert e1["mastery_before"] == 0.0
    assert not e1["idempotent"]

    e2 = store.record_answer("s1", "Bayes Theorem", True, event_key="r1:2")
    assert e2["mastery_after"] == pytest.approx(0.51)
    assert e2["mastery_after"] > e1["mastery_after"]

    e3 = store.record_answer("s1", "Bayes Theorem", False, event_key="r1:3")
    assert e3["mastery_after"] < e2["mastery_after"]

    state = store.get_state("s1", "Bayes Theorem")
    assert state["attempts"] == 3
    assert state["correct_count"] == 2
    assert state["incorrect_count"] == 1
    assert state["last_studied"] is not None
    assert len(store.get_events("s1")) == 3


def test_repeated_answer_is_idempotent(store):
    first = store.record_answer(
        "s1", "Bayes Theorem", True, event_key="run42:step1", question_id="q1"
    )
    replay = store.record_answer(
        "s1", "Bayes Theorem", True, event_key="run42:step1", question_id="q1"
    )
    assert replay["idempotent"] is True
    assert replay["id"] == first["id"]
    assert store.get_state("s1", "Bayes Theorem")["attempts"] == 1
    assert store.get_state("s1", "Bayes Theorem")["mastery"] == pytest.approx(0.3)
    assert len(store.get_events("s1")) == 1

    # A different key is a genuinely new attempt, even for the same question.
    store.record_answer(
        "s1", "Bayes Theorem", True, event_key="run42:step2", question_id="q1"
    )
    assert store.get_state("s1", "Bayes Theorem")["attempts"] == 2


def test_recent_errors_window_keeps_latest_only(store):
    for step in range(1, 5):
        store.record_answer(
            "s1", "Bayes Theorem", False,
            event_key=f"run:step{step}", question_id=f"q{step}",
        )
    state = store.get_state("s1", "Bayes Theorem")
    assert store.window == 3
    assert len(state["recent_errors"]) == 3
    assert [e["question_id"] for e in state["recent_errors"]] == ["q2", "q3", "q4"]
    assert all(e["topic"] == "Bayes Theorem" for e in state["recent_errors"])

    # A correct answer does not append and does not drop existing errors.
    store.record_answer("s1", "Bayes Theorem", True, event_key="run:step5")
    assert [e["question_id"] for e in store.get_state("s1", "Bayes Theorem")["recent_errors"]] == [
        "q2", "q3", "q4"
    ]


# --- events table -----------------------------------------------------------

def test_events_table_has_trace_latency_and_cost_columns(store):
    columns = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    required = {
        "event_key", "ts", "student_id", "topic", "step", "action",
        "action_reason", "mastery_before", "mastery_after", "performance",
        "evaluation_result", "question_id", "is_error", "recent_errors",
        "citation", "lecture", "page", "latency_ms", "prompt_tokens",
        "completion_tokens", "total_tokens", "cost_usd", "model",
        "platform_version", "mastery_alpha", "seed", "config_snapshot", "run_id",
    }
    assert required <= columns

    event = store.record_answer(
        "s1", "Bayes Theorem", True,
        event_key="trace:1", question_id="q1", run_id="run42", step=1,
        action="MCQ", action_reason="mastery < 0.4",
        citation="Source: Lecture 05, Page 14", lecture="Lecture 05", page=14,
        latency_ms=123.4, prompt_tokens=7, completion_tokens=3,
        total_tokens=10, cost_usd=0.00012,
    )
    row = store.get_events("s1")[0]
    assert row["id"] == event["id"]
    assert row["latency_ms"] == 123.4
    assert row["total_tokens"] == 10
    assert row["cost_usd"] == 0.00012
    assert row["citation"] == "Source: Lecture 05, Page 14"
    snapshot = json.loads(row["config_snapshot"])
    assert {"model", "mastery_alpha", "seed", "platform_version"} <= set(snapshot)
    assert row["mastery_alpha"] == 0.7
    assert row["seed"] == 42


def test_log_event_is_idempotent_and_ignores_unknown_fields(store):
    store.log_event(
        "s1", "Bayes Theorem", event_key="plan:1",
        step=1, action="Teach", action_reason="mastery below 0.4",
        totally_new_field="ignored",
    )
    duplicate = store.log_event(
        "s1", "Bayes Theorem", event_key="plan:1", action="Teach"
    )
    assert duplicate["idempotent"] is True
    events = store.get_events("s1")
    assert len(events) == 1
    assert events[0]["action"] == "Teach"


def test_same_initial_state_reproduces_same_trajectory(tmp_path):
    answers = [True, False, True, True, False]
    trajectories = []
    for index in range(2):
        with StudentStateStore(tmp_path / f"state_{index}.db") as state:
            for step, answer in enumerate(answers, start=1):
                state.record_answer(
                    "profile_a", "Bayes Theorem", answer,
                    event_key=f"run:{step}", step=step,
                )
            trajectories.append(
                [e["mastery_after"] for e in state.get_events("profile_a")]
            )
    assert trajectories[0] == trajectories[1]
    assert all(math.isfinite(value) for value in trajectories[0])

    # reset() returns a store to its initial condition for a fresh experiment run.
    with StudentStateStore(tmp_path / "state_0.db") as state:
        state.reset("profile_a")
        assert state.get_state("profile_a", "Bayes Theorem")["attempts"] == 0
        assert state.get_events("profile_a") == []


def test_state_is_persisted_across_connections(tmp_path):
    db = tmp_path / "persisted.db"
    with StudentStateStore(db) as first:
        first.record_answer("s1", "MDP", True, event_key="a:1")
    with StudentStateStore(db) as second:
        assert second.get_state("s1", "MDP")["mastery"] == pytest.approx(0.3)
        assert len(second.get_events("s1")) == 1
