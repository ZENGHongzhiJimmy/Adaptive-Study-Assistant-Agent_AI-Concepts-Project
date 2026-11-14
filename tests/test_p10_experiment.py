"""Regression tests for the experiment harness.

Two things have to hold for the comparison to mean anything:

* the four tutors differ only in (content, planner), so each pair of adjacent
  methods isolates one effect;
* different methods produce different trajectories, and the same method with the
  same seed replays byte-identically. The original defect was the opposite: the
  learner replayed a fixed answer list, so every policy scored the same.
"""

from __future__ import annotations

import copy

import pytest

from experiments.bank import load_bank
from experiments.content import (
    MATERIALS_OUT,
    coverage,
    grounding,
    key_terms,
    quality_by_condition,
)
from experiments.methods import (
    METHOD_ORDER,
    METHODS,
    CyclicPlanner,
    make_method,
)
from experiments.run_experiment import aggregate, run_session, self_check
from experiments.simulator import PROFILES, SimulatedStudent
from src.agent.run import run_cli_session

QUALITY = {
    topic_id: {"llm_only": 0.30, "rag": 0.55}
    for topic_id in ("t01_what_is_ai",)
}


def _quality(bank) -> dict[str, dict[str, float]]:
    return {t.topic_id: {"llm_only": 0.30, "rag": 0.55} for t in bank.topics}


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


def test_practice_is_not_scaled_by_content_quality():
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
    assert METHODS["adaptive"].planner == "adaptive"
    for name in METHOD_ORDER:
        assert make_method(name).name == name


def test_adjacent_methods_differ_in_exactly_one_component():
    """The chain llm_only -> rag -> fixed -> adaptive is the experiment design."""
    pairs = [("llm_only", "rag"), ("rag", "fixed"), ("fixed", "adaptive")]
    for left, right in pairs:
        a, b = METHODS[left], METHODS[right]
        assert a.content != b.content or a.planner != b.planner
        # exactly one of the two components changes
        assert (a.content == b.content) + (a.planner == b.planner) == 1


def test_cyclic_planner_patterns():
    qa = CyclicPlanner(("explain", "practice"))
    fixed = CyclicPlanner(("explain", "practice", "practice"))
    assert [qa.pattern[i % 2] for i in range(6)] == [
        "explain", "practice", "explain", "practice", "explain", "practice"
    ]
    assert fixed.pattern == ("explain", "practice", "practice")


def test_adaptive_planner_delegates_to_the_product_policy(monkeypatch):
    """The method under test must be the shipped policy, not a copy."""
    import experiments.methods as M
    from src.agent import policy as P

    calls: list[dict] = []
    original = P.plan_material

    def spy(topics, round_no=1, visited=None):
        calls.append({"round_no": round_no})
        return original(topics, round_no=round_no, visited=visited)

    monkeypatch.setattr(P, "plan_material", spy)
    bank = load_bank()
    run_session("adaptive", "medium", 1, bank, budget=4, quality=_quality(bank))
    assert calls, "adaptive method never called policy.plan_material"
    assert M.METHODS["adaptive"].planner == "adaptive"


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def test_methods_produce_different_trajectories():
    bank = load_bank()
    quality = _quality(bank)
    adaptive = run_session("adaptive", "medium", 1, bank, budget=12, quality=quality)
    rag = run_session("rag", "medium", 1, bank, budget=12, quality=quality)
    assert adaptive["activities"][:6] != rag["activities"][:6]
    assert rag["mix"]["review"] == 0  # the RAG tutor has no error handling


def test_sessions_are_deterministic():
    bank = load_bank()
    quality = _quality(bank)
    a = run_session("rag", "mixed", 4, bank, budget=10, quality=quality)
    b = run_session("rag", "mixed", 4, bank, budget=10, quality=quality)
    assert a == b


def test_seed_changes_the_learner():
    bank = load_bank()
    quality = _quality(bank)
    a = run_session("adaptive", "medium", 1, bank, budget=10, quality=quality)
    b = run_session("adaptive", "medium", 2, bank, budget=10, quality=quality)
    assert a["pre"] != b["pre"] or a["post"] != b["post"]


def test_every_method_respects_the_budget():
    bank = load_bank()
    quality = _quality(bank)
    for method in METHOD_ORDER:
        row = run_session(method, "medium", 2, bank, budget=9, quality=quality)
        assert row["steps"] <= 9
        assert sum(row["mix"].values()) == row["steps"]
        assert 0.0 <= row["efficiency"] <= 1.0


def test_row_reports_the_components_it_used():
    bank = load_bank()
    row = run_session("llm_only", "weak", 1, bank, budget=6, quality=_quality(bank))
    assert row["content"] == "llm_only"
    assert row["planner"] == "qa"
    assert row["content_quality"] == pytest.approx(0.30, abs=1e-3)


def test_aggregate_and_paired_keep_every_method():
    bank = load_bank()
    quality = _quality(bank)
    rows = [
        run_session(m, p, s, bank, budget=8, quality=quality)
        for m in METHOD_ORDER for p in ("medium", "weak") for s in (1, 2)
    ]
    totals = aggregate(rows)
    assert [t["method"] for t in totals] == list(METHOD_ORDER)
    assert all(t["episodes"] == 4 for t in totals)


def test_self_check_flags_identical_methods():
    """The guard that would have caught the original defect."""
    totals = [
        {"method": m, "learning_gain": 0.2,
         "mix": {"explain": 1, "practice": 1, "review": 1}}
        for m in METHOD_ORDER
    ]
    rows = [{"method": m, "activities": ["practice"] * 5} for m in METHOD_ORDER]
    problems = self_check(totals, rows)
    assert any("same learning gain" in p for p in problems)
    assert any("same activity mix" in p for p in problems)
    assert any("identical activity sequences" in p for p in problems)


# --------------------------------------------------------------------------
# content scoring
# --------------------------------------------------------------------------

def test_key_terms_drops_stopwords_and_short_words():
    terms = key_terms("AI is the ability of a computer program to think and learn")
    assert "program" in terms and "learn" in terms
    assert "the" not in terms and "is" not in terms


def test_coverage_is_one_when_every_term_appears():
    text = "A program or machine able to think and learn"
    terms = [key_terms(text)]
    assert coverage(text, terms) == pytest.approx(1.0)
    assert coverage("unrelated sentence about databases", terms) < 0.2


def test_grounding_prefers_the_matching_passage():
    explanation = "Big data is described by volume, velocity, variety and veracity."
    close = "Big data has four V characteristics: volume, velocity, variety, veracity."
    far = "Regression predicts a continuous value; classification predicts a label."
    assert grounding(explanation, [close, far]) > grounding(explanation, [far])
    assert grounding(explanation, []) == 0.0


@pytest.mark.skipif(not MATERIALS_OUT.exists(), reason="content not generated yet")
def test_frozen_materials_cover_every_topic():
    import json

    payload = json.loads(MATERIALS_OUT.read_text(encoding="utf-8"))
    bank = load_bank()
    quality = quality_by_condition(payload)
    assert set(quality) == {t.topic_id for t in bank.topics}
    for topic_id, by_condition in quality.items():
        for condition in ("llm_only", "rag"):
            assert 0.0 <= by_condition[condition] <= 1.0
    assert payload["scores"]["summary"]["rag"]["coverage_mean"] >= 0.0


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
