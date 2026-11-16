"""Regression tests for the experiment harness.

Three things have to hold for the comparison to mean anything:

* the four tutors differ only in (content, planner), so each pair of adjacent
  methods isolates one effect;
* different methods produce different trajectories, and the same method with the
  same seed replays byte-identically (the original defect was the opposite: the
  learner replayed a fixed answer list, so every policy scored the same);
* the adaptive arm is driven by the model's decisions, so it is replayed from a
  frozen decision log and never falls back to ``policy.decide()``.

Everything here runs offline: the decision log is stubbed, no model is called.
"""

from __future__ import annotations

import pytest

from experiments.bank import load_bank
from experiments.content import (
    coverage,
    grounding,
    key_terms,
    quality_by_condition,
)
from experiments.decisions import DECISIONS_PATH, DecisionLog, LoggedDecision, state_key
from experiments.methods import (
    METHOD_ORDER,
    METHODS,
    CyclicPlanner,
    LLMPlannerArm,
    make_method,
)
from experiments.run_experiment import aggregate, self_check
from experiments.session import run_session
from experiments.simulator import PROFILES, SimulatedStudent
from src.agent import policy as P
from src.agent.planner_llm import legal_candidates


def _quality(bank) -> dict[str, dict[str, float]]:
    return {t.topic_id: {"llm_only": 0.30, "rag": 0.55} for t in bank.topics}


class _StubLog:
    """A decision log that always answers the same way, in dataset order."""

    def __init__(self, activity: str = "practice") -> None:
        self.activity = activity
        self.calls = 0

    def choose(self, topics, round_no, visited, recent):
        self.calls += 1
        due = [
            t for t in topics
            if t.pretested and not P.is_mastered(t) and t.topic_id not in set(visited)
        ]
        if not due:
            due = [t for t in topics if t.pretested and not P.is_mastered(t)]
        if not due:
            return None
        topic = due[0]
        activity = self.activity
        if activity == "practice" and topic.quiz_count == 0:
            activity = "explain"
        return LoggedDecision(topic.topic_id, activity, "stub", "stub/model")


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
    first = student.study(topic_id, "explain", quality=1.0)
    second = student.study(topic_id, "explain", quality=1.0)
    assert first > 0 and second > 0
    assert second < first


def test_content_quality_scales_the_explain_gain():
    """A thin explanation must teach less than a complete one."""
    bank = load_bank()
    topic_id = bank.topics[0].topic_id
    full = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    thin = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    assert full.study(topic_id, "explain", quality=1.0) > thin.study(topic_id, "explain", quality=0.3)
    zero = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    assert zero.study(topic_id, "explain", quality=0.0) == 0.0


def test_practice_without_instruction_teaches_less():
    """The degenerate optimum that was removed: practice alone must not win.

    A learner who was never taught a topic cannot consolidate it, so practice on
    an unexplained topic is worth only the residual feedback effect.
    """
    bank = load_bank()
    topic_id = bank.topics[0].topic_id
    naive = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    taught = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    taught.study(topic_id, "explain", quality=0.9)
    gain_naive = naive.study(topic_id, "practice", accuracy=0.6)
    gain_taught = taught.study(topic_id, "practice", accuracy=0.6)
    assert gain_taught > gain_naive > 0


def test_practice_ignores_the_quality_argument():
    """Practice items come from the frozen bank, so quality only affects explain."""
    bank = load_bank()
    topic_id = bank.topics[0].topic_id
    a = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    b = SimulatedStudent(bank.topics, PROFILES["medium"], seed=1)
    assert a.study(topic_id, "practice", accuracy=0.5, quality=1.0) == pytest.approx(
        b.study(topic_id, "practice", accuracy=0.5, quality=0.1)
    )


def test_practice_with_no_success_teaches_nothing():
    bank = load_bank()
    student = SimulatedStudent(bank.topics, PROFILES["weak"], seed=1)
    topic_id = bank.topics[0].topic_id
    student.study(topic_id, "explain", quality=0.9)
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
    student.answer_round(bank.topics[0])
    assert student.probe_score(topic_id) == before


# --------------------------------------------------------------------------
# methods
# --------------------------------------------------------------------------

def test_methods_pair_content_with_planner():
    assert METHODS["llm_only"].content == "llm_only"
    assert METHODS["llm_only"].planner == "qa"
    assert METHODS["rag"].content == "rag"
    assert METHODS["rag"].planner == "qa"
    assert METHODS["fixed"].content == "rag"
    assert METHODS["fixed"].planner == "fixed"
    assert METHODS["adaptive"].content == "rag"
    assert METHODS["adaptive"].planner == "agent"
    for name in METHOD_ORDER:
        assert make_method(name).name == name


def test_adjacent_methods_differ_in_exactly_one_component():
    """The chain llm_only -> rag -> fixed -> adaptive is the experiment design."""
    pairs = [("llm_only", "rag"), ("rag", "fixed"), ("fixed", "adaptive")]
    for left, right in pairs:
        a, b = METHODS[left], METHODS[right]
        assert (a.content == b.content) + (a.planner == b.planner) == 1


def test_cyclic_planner_patterns():
    qa = CyclicPlanner(("explain", "practice"))
    fixed = CyclicPlanner(("explain", "practice", "practice"))
    assert [qa.pattern[i % 2] for i in range(6)] == [
        "explain", "practice", "explain", "practice", "explain", "practice"
    ]
    assert fixed.pattern == ("explain", "practice", "practice")


def test_adaptive_arm_needs_a_decision_log():
    """The adaptive arm is the model; without a log there is nothing to replay."""
    with pytest.raises(ValueError):
        METHODS["adaptive"].make_planner(None)


def test_adaptive_arm_passes_the_log_decision_through():
    arm = LLMPlannerArm(_StubLog(activity="explain"))
    topics = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2)]
    assert arm.choose(topics, 1, []) == ("a", "explain")


def test_round_coverage_hides_topics_already_served():
    """One round serves every ready topic once; the model picks among the due."""
    topics = [
        P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2),
        P.TopicState(topic_id="b", pretested=True, explain_pages=1, quiz_count=2),
        P.TopicState(topic_id="c", pretested=True, explain_pages=1, quiz_count=2),
    ]
    first = legal_candidates(topics, 30, [])
    assert {c.topic_id for c in first} == {"a", "b", "c"}

    second = legal_candidates(topics, 30, ["a"])
    assert {c.topic_id for c in second} == {"b", "c"}

    # everyone served -> the round is over, the whole set is legal again
    third = legal_candidates(topics, 30, ["a", "b", "c"])
    assert {c.topic_id for c in third} == {"a", "b", "c"}


def test_round_coverage_still_hides_mastered_topics():
    topics = [
        P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2),
        P.TopicState(topic_id="b", pretested=True, explain_pages=1, quiz_count=2,
                     mastery=95.0),
    ]
    assert {c.topic_id for c in legal_candidates(topics, 30, [])} == {"a"}


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def test_methods_produce_different_trajectories():
    bank = load_bank()
    quality = _quality(bank)
    adaptive = run_session(
        METHODS["adaptive"], "medium", 1, bank, budget=12, quality=quality,
        log=_StubLog(activity="explain"),
    )
    rag = run_session(METHODS["rag"], "medium", 1, bank, budget=12, quality=quality)
    assert adaptive["activities"][:6] != rag["activities"][:6]
    assert rag["mix"]["review"] == 0  # the RAG tutor has no error handling


def test_sessions_are_deterministic():
    bank = load_bank()
    quality = _quality(bank)
    a = run_session(METHODS["rag"], "mixed", 4, bank, budget=10, quality=quality)
    b = run_session(METHODS["rag"], "mixed", 4, bank, budget=10, quality=quality)
    assert a == b


def test_seed_changes_the_learner():
    bank = load_bank()
    quality = _quality(bank)
    a = run_session(METHODS["rag"], "medium", 1, bank, budget=10, quality=quality)
    b = run_session(METHODS["rag"], "medium", 2, bank, budget=10, quality=quality)
    assert a["pre"] != b["pre"] or a["post"] != b["post"]


def test_every_method_respects_the_budget():
    bank = load_bank()
    quality = _quality(bank)
    for method in METHOD_ORDER:
        row = run_session(
            METHODS[method], "medium", 2, bank, budget=9, quality=quality,
            log=_StubLog() if method == "adaptive" else None,
        )
        assert row["steps"] <= 9
        assert sum(row["mix"].values()) == row["steps"]
        assert 0.0 <= row["efficiency"] <= 1.0
        assert 0.0 <= row["mastered_rate"] <= 1.0


def test_row_reports_the_components_it_used():
    bank = load_bank()
    row = run_session(METHODS["llm_only"], "weak", 1, bank, budget=6, quality=_quality(bank))
    assert row["content"] == "llm_only"
    assert row["planner"] == "qa"
    assert row["learning_gain"] == pytest.approx(row["post"] - row["pre"], abs=1e-3)


def test_a_missing_decision_ends_the_session_instead_of_inventing_one():
    class _Empty:
        def choose(self, *args, **kwargs):
            return None

    bank = load_bank()
    row = run_session(
        METHODS["adaptive"], "medium", 1, bank, budget=10,
        quality=_quality(bank), log=_Empty(),
    )
    assert row["steps"] == 0
    assert row["stop_reason"] == "no_decision"


def test_aggregate_keeps_every_method():
    bank = load_bank()
    quality = _quality(bank)
    rows = []
    for method in METHOD_ORDER:
        for profile in ("medium", "weak"):
            for seed in (1, 2):
                rows.append(
                    run_session(
                        METHODS[method], profile, seed, bank, budget=8, quality=quality,
                        log=_StubLog() if method == "adaptive" else None,
                    )
                )
    totals = aggregate(rows)
    assert [t["method"] for t in totals] == list(METHOD_ORDER)
    assert all(t["episodes"] == 4 for t in totals)
    for total in totals:
        assert {"learning_gain", "mastered_topics", "steps"} <= set(total)


def test_self_check_flags_identical_methods():
    """The guard that would have caught the original defect."""
    totals = [
        {"method": m, "learning_gain": 0.2, "mix": {"explain": 1, "practice": 1, "review": 0}}
        for m in METHOD_ORDER
    ]
    rows = [{"method": m, "activities": ["practice"] * 5} for m in METHOD_ORDER]
    problems = self_check(totals, rows)
    assert any("same learning gain" in p for p in problems)
    assert any("identical activity sequences" in p for p in problems)


# --------------------------------------------------------------------------
# decision log
# --------------------------------------------------------------------------

def test_state_key_separates_states_the_model_can_tell_apart():
    base = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2)]
    same = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2)]
    other = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2,
                          mastery=70.0)]
    assert state_key(base, 1, [], []) == state_key(same, 1, [], [])
    assert state_key(base, 1, [], []) != state_key(other, 1, [], [])
    assert state_key(base, 1, [], []) != state_key(base, 2, [], [])
    assert state_key(base, 1, [], [], model="x") != state_key(base, 1, [], [], model="y")


def test_decision_log_round_trips_and_replays(tmp_path):
    topics = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2)]
    path = tmp_path / "decisions.json"
    log = DecisionLog(path, planner=None, read_only=False)
    log.records[state_key(topics, 1, [], [], "")] = LoggedDecision(
        "a", "practice", "reason", "m"
    )
    log.save()

    replay = DecisionLog(path, read_only=True)
    assert replay.size == 1
    decision = replay.choose(topics, 1, [], [])
    assert decision is not None and decision.as_tuple() == ("a", "practice")


def test_replay_fails_loudly_on_an_unknown_state(tmp_path):
    log = DecisionLog(tmp_path / "empty.json", read_only=True)
    topics = [P.TopicState(topic_id="a", pretested=True, explain_pages=1, quiz_count=2)]
    with pytest.raises(KeyError):
        log.choose(topics, 1, [], [])


def test_banked_log_covers_the_committed_protocol():
    """The committed decision log must be replayable for the published protocol."""
    log = DecisionLog(DECISIONS_PATH, read_only=True)
    assert log.size > 0, "run `python -m experiments.collect_decisions` first"
    assert log.meta.get("model"), "log must record which model decided"


# --------------------------------------------------------------------------
# content scoring
# --------------------------------------------------------------------------

def test_key_terms_drops_stopwords_and_short_words():
    terms = key_terms("The AI is a program that learns from data and data")
    assert "data" in terms
    assert "the" not in terms
    assert all(len(t) >= 3 for t in terms)


def test_coverage_is_one_when_every_term_appears():
    terms = [{"machine", "learning"}]
    assert coverage("machine learning is a field", terms) == pytest.approx(1.0)
    assert coverage("nothing relevant here", terms) == 0.0


def test_grounding_prefers_the_matching_passage():
    passages = ["neural networks stack layers of units", "search algorithms explore a graph"]
    assert grounding(
        "search algorithms explore a graph", passages
    ) > grounding("neural networks stack layers of units", ["search algorithms explore a graph"])


def test_frozen_materials_cover_every_topic():
    bank = load_bank()
    quality = quality_by_condition(
        __import__("json").loads(
            (DECISIONS_PATH.parent / "materials.json").read_text(encoding="utf-8")
        )
    )
    assert set(quality) == {t.topic_id for t in bank.topics}
    for row in quality.values():
        assert set(row) == {"llm_only", "rag"}
        assert all(0.0 <= v <= 1.0 for v in row.values())
