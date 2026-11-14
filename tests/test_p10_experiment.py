"""Regression tests for the E1 harness.

The defect these guard against: the learner used to replay a fixed answer list
indexed by question number, so every policy produced the same result. Three
properties have to hold for the comparison to mean anything:

* different policies -> different trajectories (not just different step counts);
* the same learner + same seed -> byte-identical replay;
* a live responder actually reaches the graph, and reaches it *instead of* the
  replayed sequence.
"""

from __future__ import annotations

import copy

import pytest

from experiments.bank import load_bank
from experiments.baselines.policies import ARM_ORDER, make_arm
from experiments.run_experiment import run_session, self_check
from experiments.simulator import PROFILES, SimulatedStudent
from src.agent.run import run_cli_session


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------

def test_bank_is_valid_and_disjoint():
    bank = load_bank()
    assert len(bank.topics) == 8
    assert bank.item_count == 56
    for topic in bank.topics:
        practice = {item.item_id for item in topic.practice}
        probe = {item.item_id for item in topic.probe}
        assert len(topic.practice) == 4
        assert len(topic.probe) == 3
        assert not practice & probe
        # Held-out probes must not repeat a practised stem.
        assert not {i.prompt for i in topic.practice} & {i.prompt for i in topic.probe}


def test_bank_item_answers_point_inside_options():
    bank = load_bank()
    for topic in bank.topics:
        for item in (*topic.practice, *topic.probe):
            assert 0 <= item.answer < len(item.options)


# --------------------------------------------------------------------------
# simulator
# --------------------------------------------------------------------------

def test_same_seed_replays_identical_answers():
    bank = load_bank()
    topic = bank.topics[0]
    a = SimulatedStudent(bank.topics, PROFILES["medium"], seed=7)
    b = SimulatedStudent(bank.topics, PROFILES["medium"], seed=7)
    first, _ = a.answer_round(topic)
    second, _ = b.answer_round(topic)
    assert first == second


def test_ability_ordering_matches_profiles():
    bank = load_bank()
    scores = [
        SimulatedStudent(bank.topics, PROFILES[name], seed=1).probe_score()
        for name in ("struggling", "weak", "medium", "strong")
    ]
    assert scores == sorted(scores)
    assert scores[0] < scores[-1]


def test_reading_raises_ability_with_diminishing_returns():
    bank = load_bank()
    student = SimulatedStudent(bank.topics, PROFILES["weak"], seed=1)
    topic_id = bank.topics[0].topic_id
    before = student.theta[topic_id]
    first = student.study(topic_id, "explain")
    second = student.study(topic_id, "explain")
    assert first > 0 and second > 0
    assert second < first


def test_practice_with_no_success_teaches_nothing():
    bank = load_bank()
    student = SimulatedStudent(bank.topics, PROFILES["weak"], seed=1)
    topic_id = bank.topics[0].topic_id
    before = student.theta[topic_id]
    assert student.study(topic_id, "practice", accuracy=0.0) == 0.0
    assert student.theta[topic_id] == before


def test_review_without_errors_teaches_nothing():
    bank = load_bank()
    student = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    topic_id = bank.topics[0].topic_id
    assert student.study(topic_id, "review", had_errors=False) == 0.0
    assert student.study(topic_id, "review", had_errors=True) > 0.0


def test_probe_score_ignores_agent_state():
    """The post-test must be a function of ability, not of what was practised."""
    bank = load_bank()
    student = SimulatedStudent(bank.topics, PROFILES["medium"], seed=3)
    topic_id = bank.topics[0].topic_id
    before = student.probe_score(topic_id)
    student.answer_round(bank.topics[0])          # practice does not change theta
    assert student.probe_score(topic_id) == before


# --------------------------------------------------------------------------
# arms
# --------------------------------------------------------------------------

def test_arms_are_constructible_and_ordered():
    for arm in ARM_ORDER:
        assert make_arm(arm, seed=1).name == arm


def test_policies_produce_different_trajectories():
    bank = load_bank()
    adaptive = run_session("adaptive", "medium", 1, bank, budget=12)
    drill = run_session("always_mcq", "medium", 1, bank, budget=12)
    assert adaptive["activities"][:6] != drill["activities"][:6]
    assert drill["mix"]["explain"] == 0


def test_sessions_are_deterministic():
    bank = load_bank()
    a = run_session("random", "mixed", 4, bank, budget=10)
    b = run_session("random", "mixed", 4, bank, budget=10)
    assert a == b


def test_seed_changes_the_learner():
    bank = load_bank()
    a = run_session("adaptive", "medium", 1, bank, budget=10)
    b = run_session("adaptive", "medium", 2, bank, budget=10)
    assert a["pre"] != b["pre"] or a["post"] != b["post"]


def test_self_check_flags_identical_arms():
    """The guard that would have caught the original defect."""
    totals = [
        {"arm": arm, "learning_gain": 0.2, "mix": {"explain": 1, "practice": 1, "review": 1}}
        for arm in ARM_ORDER
    ]
    rows = [{"arm": arm, "activities": ["practice"] * 5} for arm in ARM_ORDER]
    problems = self_check(totals, rows)
    assert any("same learning gain" in p for p in problems)
    assert any("same activity mix" in p for p in problems)
    assert any("identical activity sequences" in p for p in problems)


# --------------------------------------------------------------------------
# the graph fix itself
# --------------------------------------------------------------------------

def test_responder_overrides_the_replayed_answer_sequence(tmp_path):
    """A live responder must reach evaluate_node and beat the scripted list."""
    db = tmp_path / "responder.db"
    scripted = run_cli_session(
        student_id="scripted",
        topic="Bayes Theorem",
        answers="correct",
        max_steps=4,
        db_path=db,
        llm_mode="off",
        use_rag=False,
        run_id="scripted",
    )
    live = run_cli_session(
        student_id="live",
        topic="Bayes Theorem",
        answers="correct",              # ignored: the responder answers instead
        max_steps=4,
        db_path=db,
        llm_mode="off",
        use_rag=False,
        run_id="live",
        responder=lambda question: "A",
    )
    assert scripted.events and live.events
    live_evaluations = [e for e in live.events if e.get("evaluation_result") in {"correct", "incorrect"}]
    assert live_evaluations, "responder path produced no graded answers"
    assert all(e["evaluation_result"] == "incorrect" for e in live_evaluations)
    assert live.final_mastery < scripted.final_mastery


def test_responder_receives_the_question_not_a_number(tmp_path):
    seen: list[dict] = []

    def responder(question):
        seen.append(copy.deepcopy(question))
        return "A"

    run_cli_session(
        student_id="shape",
        topic="Bayes Theorem",
        answers="correct",
        max_steps=3,
        db_path=tmp_path / "shape.db",
        llm_mode="off",
        use_rag=False,
        run_id="shape",
        responder=responder,
    )
    assert seen, "responder was never called"
    assert isinstance(seen[0], dict)
    assert "options" in seen[0] and "correct_option" in seen[0]


def test_without_responder_the_old_replay_path_is_unchanged(tmp_path):
    kwargs = dict(
        student_id="legacy",
        topic="Bayes Theorem",
        answers="correct,wrong,correct",
        max_steps=5,
        db_path=tmp_path / "legacy.db",
        llm_mode="off",
        use_rag=False,
        run_id="legacy",
    )
    first = run_cli_session(**kwargs)
    second = run_cli_session(**kwargs)
    assert first.summary()["trace"] == second.summary()["trace"]
    graded = [e for e in first.events if e.get("evaluation_result") in {"correct", "incorrect"}]
    assert graded, "replay path produced no graded answers"
    assert graded[0]["evaluation_result"] == "correct"
