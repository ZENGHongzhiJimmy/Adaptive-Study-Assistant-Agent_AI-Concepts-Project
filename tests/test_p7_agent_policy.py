"""P7: the adaptive policy (``src/agent/policy.py``) is now the single planner.

These tests pin the behaviour that used to live in ``frontend/js/engine.js``.
They are the contract that keeps the web app and the experiment harness on the
same rules: if a number here changes, the report's method section changed too.
"""

from __future__ import annotations

import pytest

from src.agent.policy import (
    EXPLAIN_CAP,
    MASTERED,
    TopicState,
    advance_round,
    apply_pre_test,
    apply_result,
    decide,
    is_mastered,
    plan_material,
    stage_of,
)


def topic(**kwargs) -> TopicState:
    base = dict(topic_id="t1", name="Gradient Descent", explain_pages=2, quiz_count=2)
    base.update(kwargs)
    return TopicState(**base)


# --------------------------------------------------------------- decide


def test_mastered_topic_has_no_next_activity():
    t = topic(mastery=MASTERED, pretested=True)
    d = decide(t)
    assert d.activity is None and d.mastered and not d.blocked


def test_high_mastery_with_open_mistakes_is_not_mastered():
    t = topic(mastery=95, pretested=True, mistakes=[{"q": "a"}, {"q": "b"}])
    assert not is_mastered(t)
    # two open mistakes -> review beats "you are at 95%"
    assert decide(t).activity == "review"


def test_two_bad_practices_send_the_student_back_to_the_explanation():
    t = topic(mastery=60, pretested=True, explain_done=True, fail_streak=2, need_help=True)
    d = decide(t)
    assert d.activity == "explain"
    assert "连续" in d.rule


def test_explanation_first_when_never_read():
    d = decide(topic(mastery=30, pretested=True))
    assert d.activity == "explain" and d.rule == "尚未学习讲解"


def test_practice_once_explanation_is_read():
    d = decide(topic(mastery=30, pretested=True, explain_done=True))
    assert d.activity == "practice"


def test_topic_without_content_is_blocked_not_silently_practised():
    d = decide(topic(mastery=30, pretested=True, explain_pages=0, quiz_count=0))
    assert d.activity is None and d.blocked


def test_rule_order_review_before_explain():
    """A 30% topic with 2 open mistakes reviews first, then reads."""
    d = decide(topic(mastery=30, pretested=True, mistakes=[{"q": "a"}, {"q": "b"}]))
    assert d.activity == "review"


# --------------------------------------------------------- state update


def test_explanation_is_capped_and_only_pays_twice():
    t = topic(mastery=30, pretested=True)
    first = apply_result(t, "explain")
    assert (first.after, first.delta) == (40, 10)

    second = apply_result(first.state, "explain")
    assert second.after == 42 and second.state.explain_runs == 2

    third = apply_result(second.state, "explain")
    assert third.delta == 0 and third.after == 42


def test_explanation_cap_holds_when_mastery_is_already_high():
    """Re-reading a topic that practice already pushed to 70% cannot raise it."""
    t = topic(mastery=70, pretested=True, explain_done=True, explain_runs=1)
    out = apply_result(t, "explain")
    assert out.delta == 0 and out.after == 70


def test_explanation_never_lowers_an_already_high_mastery():
    t = topic(mastery=90, pretested=True, explain_done=True, explain_runs=3)
    out = apply_result(t, "explain")
    assert out.after == 90 and out.delta == 0


def test_perfect_practice_climbs_but_is_clamped():
    out = apply_result(topic(mastery=30, pretested=True, quiz_count=2), "practice",
                       {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]})
    # (100 - 30) * 0.6 = 42 -> clamped to +25
    assert out.after == 55 and out.delta == 25


def test_half_right_practice_stalls_near_the_score():
    out = apply_result(topic(mastery=30, pretested=True, quiz_count=2), "practice",
                       {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": False}]})
    assert out.after == 42 and out.delta == 12


def test_all_wrong_practice_actually_loses_mastery():
    out = apply_result(topic(mastery=60, pretested=True, quiz_count=2), "practice",
                       {"answers": [{"i": 0, "ok": False}, {"i": 1, "ok": False}]})
    assert out.delta == -10 and out.after == 50


def test_two_sub_half_practices_set_need_help():
    t = topic(mastery=60, pretested=True, explain_done=True, quiz_count=2)
    one = apply_result(t, "practice", {"answers": [{"i": 0, "ok": False}, {"i": 1, "ok": False}]})
    two = apply_result(one.state, "practice", {"answers": [{"i": 0, "ok": False}, {"i": 1, "ok": False}]})
    assert two.state.fail_streak == 2 and two.state.need_help
    assert decide(two.state).activity == "explain"


def test_error_book_fills_on_wrong_and_empties_on_right():
    quiz = [
        {"q": "What does gamma control?", "opts": ["a", "b"], "a": 0, "why": "future weight"},
        {"q": "What is the posterior?", "opts": ["a", "b"], "a": 1, "why": "P(H|D)"},
    ]
    t = topic(mastery=50, pretested=True, explain_done=True, quiz_count=2)
    wrong = apply_result(t, "practice", {"answers": [{"i": 0, "ok": False}]}, quiz)
    assert wrong.state.wrong_count == 1
    right = apply_result(wrong.state, "practice", {"answers": [{"i": 0, "ok": True}]}, quiz)
    assert right.state.wrong_count == 0


def test_review_clears_errors_but_never_adds_mastery():
    t = topic(mastery=55, pretested=True, explain_done=True,
              mistakes=[{"q": "a"}, {"q": "b"}], errors=2)
    out = apply_result(t, "review")
    assert out.delta == 0 and out.state.wrong_count == 0 and out.after == 55


def test_activity_without_content_scores_zero():
    out = apply_result(topic(mastery=30, explain_pages=0), "explain")
    assert out.delta == 0


def test_pre_test_sets_the_starting_line():
    assert apply_pre_test(topic(), True).mastery == 45
    assert apply_pre_test(topic(), False).mastery == 20


def test_skipped_pre_test_keeps_mastery_and_never_counts_as_error():
    t = topic(mastery=30)
    out = apply_pre_test(t, None)
    assert out.mastery == 30 and out.pretested and out.errors == 0


# ------------------------------------------------------------ rolling plan


def test_plan_only_covers_pretested_unmastered_topics():
    topics = [
        topic(topic_id="a", pretested=False),
        topic(topic_id="b", pretested=True, mastery=MASTERED),
        topic(topic_id="c", pretested=True, mastery=30, explain_done=True),
    ]
    plan = plan_material(topics)
    assert [s["topic_id"] for s in plan["steps"]] == ["c"]
    assert plan["pending_pretest"] == 1 and plan["mastered"] == 1


def test_one_step_per_topic_per_round():
    topics = [topic(topic_id=t, pretested=True, explain_done=True, mastery=40) for t in "abc"]
    plan = plan_material(topics)
    assert len(plan["steps"]) == 3
    assert {s["topic_id"] for s in plan["steps"]} == set("abc")


def test_help_and_review_are_served_before_plain_practice():
    topics = [
        topic(topic_id="practice", pretested=True, explain_done=True, mastery=60),
        topic(topic_id="help", pretested=True, explain_done=True, mastery=60,
              fail_streak=2, need_help=True),
        topic(topic_id="review", pretested=True, explain_done=True, mastery=60,
              mistakes=[{"q": "a"}, {"q": "b"}]),
    ]
    plan = plan_material(topics)
    assert [s["topic_id"] for s in plan["steps"]] == ["help", "review", "practice"]


def test_topics_already_served_this_round_go_last():
    topics = [topic(topic_id="a", pretested=True, explain_done=True, mastery=40),
              topic(topic_id="b", pretested=True, explain_done=True, mastery=40)]
    plan = plan_material(topics, round_no=1, visited=["a"])
    assert plan["next"]["topic_id"] == "b" and plan["steps"][-1]["topic_id"] == "a"


def test_round_advances_only_when_every_candidate_was_served():
    topics = [topic(topic_id="a", pretested=True, explain_done=True, mastery=40),
              topic(topic_id="b", pretested=True, explain_done=True, mastery=40)]
    assert advance_round(topics, 1, [], "a") == (1, ["a"])
    assert advance_round(topics, 1, ["a"], "b") == (2, [])


def test_finished_material_does_not_keep_counting_rounds():
    topics = [topic(topic_id="a", pretested=True, mastery=MASTERED)]
    assert advance_round(topics, 3, [], "a") == (3, [])


def test_plan_is_done_when_everything_is_mastered():
    topics = [topic(topic_id="a", pretested=True, mastery=MASTERED)]
    plan = plan_material(topics)
    assert plan["done"] and plan["next"] is None


def test_stage_helper_matches_the_decision():
    assert stage_of(topic(topic_id="a", pretested=False)) == "pretest"
    assert stage_of(topic(topic_id="b", pretested=True, explain_done=True,
                          fail_streak=2, need_help=True)) == "help"
    assert stage_of(topic(topic_id="c", pretested=True, mastery=MASTERED)) == "done"
