"""P3 acceptance tests: the first runnable closed loop (CLI, no LLM, no UI).

Covers: rule Planner routing, deterministic MCQ scoring, LangGraph back-edge
with both exits, mastery changing with answers, one event per step, state
persisted into SQLite, reproducibility from the same initial state, and RAG
citation reaching the events table.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.actions import render_teach
from src.agent.evaluator import evaluate_step
from src.agent.planner import choose_action
from src.agent.questions import questions_for
from src.agent.run import main, run_cli_session
from src.retrieval.vector_store import RetrievedChunk
from src.student_model.state import StudentStateStore
from src.utils.config import get_config


def _run(tmp_path: Path, name: str, **kwargs):
    defaults = dict(
        student_id="s1",
        topic="Bayes Theorem",
        answers="correct",
        use_rag=False,
        db_path=tmp_path / f"{name}.db",
    )
    defaults.update(kwargs)
    return run_cli_session(**defaults)


# --- planner / evaluator ----------------------------------------------------

def test_rule_planner_thresholds_and_transitions():
    cfg = get_config()

    def decide(mastery, errors=None, last=None):
        return choose_action(
            mastery=mastery, recent_errors=errors or [],
            last_action=last, config=cfg,
        )

    assert decide(0.0).action == "Teach"
    assert "teach_below" in decide(0.0).reason
    assert decide(0.5, last="Teach").action == "MCQ"  # no infinite Teach loop
    assert decide(0.5).action == "MCQ" and "practice" in decide(0.5).reason
    assert decide(0.75).action == "MCQ" and "challenge" in decide(0.75).reason
    assert decide(0.9).action == "Done" and decide(0.9).reached_target
    repeated = decide(0.5, errors=[{"question_id": "q1"}, {"question_id": "q2"}])
    assert repeated.action == "Teach" and "Review activity arrives in P4" in repeated.reason
    assert decide(0.5) == decide(0.5)  # pure / deterministic


def test_deterministic_evaluator():
    question = questions_for("Bayes Theorem")[0].as_dict()  # correct option C
    assert evaluate_step(question, "correct").correct is True
    assert evaluate_step(question, "correct").performance == 1.0
    assert evaluate_step(question, "wrong").correct is False
    assert evaluate_step(question, " C ").correct is True
    assert evaluate_step(question, "A").correct is False
    assert evaluate_step(question, "Z").correct is False  # invalid option is wrong
    skipped = evaluate_step(None, "correct")
    assert skipped.skipped and skipped.performance is None
    assert skipped.evaluation_result == "skipped"


def test_teach_falls_back_when_retrieval_is_empty():
    result = render_teach("Bayes Theorem", [])
    assert result.grounded is False
    assert result.citation is None
    assert "离线占位" in result.content


# --- closed loop ------------------------------------------------------------

def test_cli_runs_n_steps_with_one_event_each_and_max_steps_exit(tmp_path):
    result = _run(tmp_path, "maxsteps", answers="wrong", max_steps=6)
    assert result.steps == 6
    assert result.stop_reason == "max_steps"
    assert len(result.events) == 6
    assert result.actions[0] == "Teach" and result.actions[1] == "MCQ"
    assert result.final_mastery == 0.0  # all wrong, no LLM rubric noise

    for index, event in enumerate(result.events, start=1):
        assert event["step"] == index
        assert event["action"] in ("Teach", "MCQ")
        assert event["action_reason"]
        assert event["evaluation_result"] in ("skipped", "correct", "incorrect")
        assert event["mastery_before"] is not None
        assert event["mastery_after"] is not None
        assert event["config_snapshot"]
        assert event["latency_ms"] >= 0
        assert event["total_tokens"] == 0 and event["cost_usd"] == 0.0  # P3 has no LLM
        assert event["run_id"] == "p3-s1-Bayes Theorem"

    # Events are persisted by P2, not held only in memory.
    with StudentStateStore(tmp_path / "maxsteps.db") as reopened:
        assert len(reopened.get_events("s1")) == 6
        assert reopened.get_state("s1", "Bayes Theorem")["attempts"] == 3


def test_mastery_changes_with_answers_and_target_exit(tmp_path):
    result = _run(tmp_path, "target", answers="correct", max_steps=20)
    assert result.stop_reason == "target_mastery"
    assert result.steps < 20
    assert result.final_mastery >= get_config()["planner"]["target_mastery"]
    assert result.final_mastery > result.initial_mastery

    mcq_steps = [e for e in result.events if e["action"] == "MCQ"]
    teach_steps = [e for e in result.events if e["action"] == "Teach"]
    assert mcq_steps and teach_steps
    assert all(e["mastery_after"] > e["mastery_before"] for e in mcq_steps)
    assert all(e["mastery_after"] == e["mastery_before"] for e in teach_steps)
    assert all(e["evaluation_result"] == "skipped" for e in teach_steps)


def test_mixed_answers_drive_mastery_up_and_down(tmp_path):
    result = _run(tmp_path, "mixed", answers="correct,wrong,correct,wrong", max_steps=12)
    mcq = [e for e in result.events if e["action"] == "MCQ"]
    assert any(e["mastery_after"] > e["mastery_before"] for e in mcq)
    assert any(e["mastery_after"] < e["mastery_before"] for e in mcq)
    assert any(e["is_error"] == 1 for e in result.events)


def test_same_initial_state_reproduces_same_trajectory(tmp_path):
    first = _run(tmp_path, "repro1", answers="correct,wrong,correct", max_steps=12, run_id="fixed")
    second = _run(tmp_path, "repro2", answers="correct,wrong,correct", max_steps=12, run_id="fixed")
    assert first.actions == second.actions
    assert first.steps == second.steps
    assert first.stop_reason == second.stop_reason
    assert [e["mastery_after"] for e in first.events] == [
        e["mastery_after"] for e in second.events
    ]
    assert [e["evaluation_result"] for e in first.events] == [
        e["evaluation_result"] for e in second.events
    ]


def test_rag_citation_reaches_events(tmp_path):
    hit = RetrievedChunk(
        chunk_id="c1",
        text="The posterior combines prior and likelihood.",
        lecture="Lecture 05",
        page=14,
        topic="Bayes Theorem",
        source="Source: Lecture 05, Page 14",
        distance=0.1,
    )

    def retriever(topic: str):
        return [hit] if topic == "Bayes Theorem" else []

    result = _run(
        tmp_path, "rag", answers="correct", max_steps=3,
        retriever=retriever,
    )
    teach_events = [e for e in result.events if e["action"] == "Teach"]
    assert teach_events
    assert teach_events[0]["citation"] == "Source: Lecture 05, Page 14"
    assert teach_events[0]["lecture"] == "Lecture 05"
    assert teach_events[0]["page"] == 14
    assert result.grounded_steps >= 1


def test_empty_retrieval_does_not_break_the_loop(tmp_path):
    result = _run(
        tmp_path, "emptyrag", answers="correct", max_steps=4,
        retriever=lambda topic: [],
    )
    assert result.steps == 4
    assert result.grounded_steps == 0
    assert all(e["citation"] is None for e in result.events)


# --- CLI --------------------------------------------------------------------

def test_cli_main_prints_json_summary(tmp_path, capsys):
    code = main([
        "--student-id", "cli", "--topic", "MDP",
        "--answers", "correct,wrong", "--max-steps", "5",
        "--db", str(tmp_path / "cli.db"), "--no-rag",
        "--run-id", "cli-test",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"] == 5
    assert payload["event_count"] == 5
    assert len(payload["trace"]) == 5
    assert payload["run_id"] == "cli-test"


def test_cli_rejects_empty_answers(tmp_path):
    assert main([
        "--answers", "", "--db", str(tmp_path / "bad.db"), "--no-rag",
    ]) == 2
