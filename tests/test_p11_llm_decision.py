"""P11: the LLM decision module (``src/agent/planner_llm.py``).

What is pinned here, and why it matters
---------------------------------------
The decision moved from ``policy.decide()`` to the model, so the properties that
used to be guaranteed by a pure function now have to be guaranteed by tests:

* **legality** - the model may only pick a pair that actually exists in the
  material and is actually possible right now (no quiz -> no practice);
* **determinism** - the same state must produce the same decision, otherwise an
  8-seed rerun is not comparable (the cache is what buys this);
* **no substitution** - when the model cannot answer there is no fallback to
  ``policy.decide()``.  The response ships no ``next`` and says why
  (``unavailable_reason``), because a rule-made step would misrepresent who
  decided;
* **measurement is untouched** - ``apply_result`` still decides mastery, so the
  learning-gain numbers keep a ground truth.

Every test here runs offline with a stub client: no key, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.learner_store import LearnerStore
from src.agent.planner_llm import (
    LLMPlanner,
    PlannerDecision,
    apply_decision,
    build_user_message,
    legal_candidates,
    make_planner,
    validate_choice,
)
from src.agent.policy import TopicState, plan_facts
from src.agent.serve import AgentService


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _topics() -> list[TopicState]:
    return [
        # weak, three unresolved mistakes, already read the explanation
        TopicState(
            topic_id="t_gd", name="Gradient Descent", mastery=57, pretested=True,
            mistakes=[{"q": "a"}, {"q": "b"}, {"q": "c"}], errors=3,
            explain_pages=3, explain_done=True, explain_runs=2,
            quiz_count=4, fail_streak=1,
        ),
        # healthy but not mastered yet
        TopicState(
            topic_id="t_bayes", name="Bayes Theorem", mastery=71, pretested=True,
            explain_pages=2, explain_done=True, explain_runs=1, quiz_count=4,
        ),
        # already mastered -> never a candidate
        TopicState(
            topic_id="t_over", name="Overfitting", mastery=85, pretested=True,
            explain_pages=2, explain_done=True, explain_runs=1, quiz_count=4,
        ),
        # never pre-tested -> not scheduled yet
        TopicState(
            topic_id="t_null", name="Not pre-tested", mastery=30, pretested=False,
            explain_pages=1, quiz_count=1,
        ),
    ]


class _Stub:
    """Minimal stand-in for LLMClient.chat."""

    is_configured = True

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["{}"]
        self.calls = 0

    def chat(self, messages, *, max_tokens=None, model=None):
        self.calls += 1
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]

        class _R:
            pass

        response = _R()
        response.text = text
        response.model = "stub/model"
        response.latency_ms = 42.0
        response.total_tokens = 111
        return response


def _planner(stub: _Stub, **agent_cfg) -> LLMPlanner:
    cfg = {"agent": {"planner": "llm", "planner_cache": False, **agent_cfg}}
    return LLMPlanner(cfg, client=stub)


class _Resp:
    def __init__(self, text: str, model: str) -> None:
        self.text = text
        self.model = model
        self.latency_ms = 42.0
        self.total_tokens = 111


class _RoutedStub:
    """Answers per model, so the free -> paid escalation is observable offline."""

    is_configured = True
    model = "free/model"

    def __init__(self, per_model: dict[str, list[str]]) -> None:
        self.per_model = per_model
        self.calls: list[tuple[str, int | None]] = []

    def chat(self, messages, *, max_tokens=None, model=None):
        key = model or self.model
        self.calls.append((key, max_tokens))
        queue = self.per_model.get(key) or ["{}"]
        seen = sum(1 for k, _ in self.calls if k == key) - 1
        return _Resp(queue[min(seen, len(queue) - 1)], key)

    @property
    def models(self) -> list[str]:
        return [k for k, _ in self.calls]


class _PrimaryDownStub:
    """The free model raises (429), the paid one answers."""

    is_configured = True
    model = "free/model"

    def __init__(self, paid_reply: str) -> None:
        self.paid_reply = paid_reply
        self.calls: list[str] = []

    def chat(self, messages, *, max_tokens=None, model=None):
        key = model or self.model
        self.calls.append(key)
        if key == self.model:
            raise RuntimeError("429 rate limit exceeded")
        return _Resp(self.paid_reply, key)


def _client_with_fallback(stub, fallback: tuple[str, ...] = ("paid/model",)) -> LLMPlanner:
    cfg = {
        "agent": {"planner": "llm", "planner_cache": False},
        "llm": {"fallback_models": list(fallback)},
    }
    return LLMPlanner(cfg, client=stub)


def _reply(topic_id: str, activity: str, reason: str = "掌握度 57%，错题 3 道") -> str:
    return json.dumps(
        {"topic_id": topic_id, "activity": activity, "reason": reason, "confidence": 0.8},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# 1. legality: code owns the candidate set
# ---------------------------------------------------------------------------

def test_candidates_only_include_possible_actions() -> None:
    keys = {c.key for c in legal_candidates(_topics())}
    assert "t_gd|explain" in keys
    assert "t_gd|practice" in keys
    assert "t_gd|review" in keys       # three mistakes to clear
    assert "t_bayes|practice" in keys
    assert "t_bayes|review" not in keys  # nothing to review
    assert not any(k.startswith("t_over") for k in keys)   # mastered
    assert not any(k.startswith("t_null") for k in keys)   # not pre-tested


def test_candidates_respect_missing_content() -> None:
    no_quiz = [TopicState(topic_id="x", mastery=40, pretested=True, explain_pages=2)]
    assert {c.activity for c in legal_candidates(no_quiz)} == {"explain"}
    no_pages = [TopicState(topic_id="y", mastery=40, pretested=True, quiz_count=3)]
    assert {c.activity for c in legal_candidates(no_pages)} == {"practice"}


def test_context_carries_numbers_and_forbids_prose() -> None:
    topics = _topics()
    message = build_user_message(topics, legal_candidates(topics), 2, ["t_gd"], [])
    assert "t_gd" in message and "掌握度 57%" in message
    assert "C1 |" in message
    # the trailing nudge is what stops thinking models from writing an essay
    assert message.rstrip().endswith("不要写任何分析、步骤、思考或解释文字。")


# ---------------------------------------------------------------------------
# 2. validation: reject what the material cannot support
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "choice, fragment",
    [
        ({"topic_id": "t_ghost", "activity": "review"}, "不在候选清单里"),
        ({"topic_id": "t_bayes", "activity": "review"}, "现在不能选 review"),
        ({"topic_id": "t_gd", "activity": "teach"}, "activity 必须是"),
        ({"activity": "review"}, "缺少 topic_id"),
    ],
)
def test_validation_rejects_illegal_choices(choice, fragment) -> None:
    error = validate_choice(choice, legal_candidates(_topics()))
    assert fragment in error


def test_validation_accepts_a_legal_choice() -> None:
    assert validate_choice({"topic_id": "t_gd", "activity": "review"}, legal_candidates(_topics())) == ""


def test_validation_blocks_a_verbatim_repeat() -> None:
    last = {"topic_id": "t_gd", "activity": "practice"}
    error = validate_choice(
        {"topic_id": "t_gd", "activity": "practice"}, legal_candidates(_topics()), last
    )
    assert "与上一步完全相同" in error


# ---------------------------------------------------------------------------
# 3. decision + degradation
# ---------------------------------------------------------------------------

def test_choice_becomes_a_decision_with_the_model_reason() -> None:
    stub = _Stub(_reply("t_gd", "review", "错题本还有 3 道，先清错题再练"))
    decision = _planner(stub).choose(_topics(), 1, [], [])
    assert decision is not None
    assert (decision.topic_id, decision.activity) == ("t_gd", "review")
    assert decision.source == "llm"
    assert "3 道" in decision.reason
    assert stub.calls == 1


def test_unusable_replies_yield_no_decision() -> None:
    """No decision is the *only* acceptable outcome - never a rule substitute."""
    stub = _Stub("我觉得应该先复习错题。", "还是先复习吧。")
    planner = _planner(stub)
    assert planner.choose(_topics(), 1, [], []) is None
    assert planner.calls == 2          # retried once, then gave up
    assert planner.failures == 1
    assert planner.unavailable_reason == "bad_output"


def test_a_bad_first_reply_is_recovered_by_the_retry() -> None:
    stub = _Stub("先分析一下：t_gd 只有 57%……", _reply("t_bayes", "practice"))
    decision = _planner(stub).choose(_topics(), 1, [], [])
    assert decision is not None and decision.topic_id == "t_bayes"
    assert stub.calls == 2


def test_cache_makes_the_same_state_stable_and_free() -> None:
    stub = _Stub(_reply("t_gd", "review"))
    planner = _planner(stub, planner_cache=True)
    first = planner.choose(_topics(), 1, [], [])
    second = planner.choose(_topics(), 1, [], [])
    assert first == second
    assert stub.calls == 1          # second decision came from the cache


def test_rule_mode_builds_no_planner() -> None:
    assert make_planner({"agent": {"planner": "rule"}}) is None
    assert make_planner({"agent": {}}) is None


def test_llm_mode_without_a_key_still_builds_a_planner(monkeypatch) -> None:
    """A missing key must be *reported*, not silently replaced by the rule policy."""
    from src.utils.config import get_config

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    planner = make_planner(get_config())
    assert planner is not None
    assert planner.available is False
    assert planner.choose(_topics(), 1, [], []) is None
    assert planner.unavailable_reason == "no_api_key"
    assert planner.calls == 0          # never hit the network


def test_no_candidates_is_not_a_failure() -> None:
    """Everything mastered: there is nothing to decide, not an outage."""
    done = [TopicState(topic_id="x", mastery=90, pretested=True, quiz_count=2)]
    planner = _planner(_Stub(_reply("x", "practice")))
    assert planner.choose(done, 1, [], []) is None
    assert planner.unavailable_reason == "no_candidates"
    assert planner.failures == 0
    assert planner.calls == 0


# ---------------------------------------------------------------------------
# 4. escalation: a free model that answers with junk must not lose the decision
# ---------------------------------------------------------------------------

def test_unusable_free_reply_is_re_decided_by_the_paid_model() -> None:
    """HTTP 200 with a truncated/prose body is a content failure, not a 429.

    The transport layer only switches models on 429/404/network errors, so the
    decision module has to escalate itself - otherwise a weak model silently
    leaves low-mastery learners with no next step.
    """
    stub = _RoutedStub({
        "free/model": ["先分析一下：t_gd 只有 57%……", "我觉得还是先复习错题。"],
        "paid/model": [_reply("t_gd", "review", "错题本还有 3 道，先清错题")],
    })
    planner = _client_with_fallback(stub)
    decision = planner.choose(_topics(), 1, [], [])

    assert decision is not None
    assert (decision.topic_id, decision.activity) == ("t_gd", "review")
    assert decision.escalated is True
    assert planner.escalated is True
    assert stub.models == ["free/model", "free/model", "paid/model"]
    # the strong model gets a bigger budget: truncation is a common cause
    assert stub.calls[-1][1] == 1200
    # still a model decision, never a rule one
    assert decision.source == "llm" and decision.rule == "LLM 决策"


def test_escalation_records_both_failures_when_no_model_can_decide() -> None:
    stub = _RoutedStub({
        "free/model": ["我觉得应该先复习。", "还是先复习吧。"],
        "paid/model": ["先讲解 t_gd。", "还是先讲解。"],
    })
    planner = _client_with_fallback(stub)
    assert planner.choose(_topics(), 1, [], []) is None
    assert planner.unavailable_reason == "bad_output"
    assert planner.escalated is False
    # the message names every model that was asked, so the UI can say so
    assert "free/model" in planner.last_error and "paid/model" in planner.last_error
    # one record per model that was asked (the retries inside a model are
    # already folded into that model's single verdict)
    assert [(a["model"], a["reason"]) for a in planner.attempt_log] == [
        ("free/model", "bad_output"), ("paid/model", "bad_output")
    ]


def test_primary_success_never_touches_the_paid_model() -> None:
    stub = _RoutedStub({
        "free/model": [_reply("t_bayes", "practice")],
        "paid/model": [_reply("t_gd", "review")],
    })
    decision = _client_with_fallback(stub).choose(_topics(), 1, [], [])
    assert decision is not None and decision.topic_id == "t_bayes"
    assert decision.escalated is False
    assert stub.models == ["free/model"]


def test_escalation_defaults_to_the_transport_fallback_list() -> None:
    planner = _client_with_fallback(_RoutedStub({}), fallback=("a/paid", "b/paid"))
    assert planner.escalation_models == ["a/paid", "b/paid"]
    assert planner.escalation_max_tokens == 1200


def test_escalation_can_be_disabled_explicitly() -> None:
    stub = _RoutedStub({"free/model": ["没有 JSON"], "paid/model": [_reply("t_gd", "review")]})
    cfg = {
        "agent": {"planner": "llm", "planner_cache": False, "planner_escalation_models": []},
        "llm": {"fallback_models": ["paid/model"]},
    }
    planner = LLMPlanner(cfg, client=stub)
    assert planner.escalation_models == []
    assert planner.choose(_topics(), 1, [], []) is None
    assert stub.models == ["free/model", "free/model"]


def test_a_throttled_free_model_still_escalates() -> None:
    stub = _PrimaryDownStub(_reply("t_gd", "explain", "掌握度 57%，错题 3 道"))
    planner = _client_with_fallback(stub)
    decision = planner.choose(_topics(), 1, [], [])
    assert decision is not None and decision.activity == "explain"
    assert decision.escalated is True
    assert stub.calls == ["free/model", "paid/model"]


def test_confirmation_after_an_escalated_decision_still_ships_one_step(tmp_path: Path) -> None:
    """The escalated step reaches the plan like any other model decision."""
    topics = _topics()
    plan = plan_facts(topics, 1, [])
    decision = PlannerDecision(
        topic_id="t_gd", activity="review", reason="错题 3 道", escalated=True
    )
    updated = apply_decision(plan, decision, topics)
    assert updated["next"]["escalated"] is True
    assert updated["decision"]["escalated"] is True
    assert len(updated["steps"]) == 1


def test_candidates_cover_every_topic_when_the_list_is_truncated() -> None:
    """Flat truncation used to hide every topic after the 10th.

    22 topics x 3 activities = 66 pairs with a limit of 30: the old code kept
    the first 30, so topics 11-22 never appeared in the prompt at all.  Filling
    round-robin guarantees every topic is visible, without ranking anything.
    """
    topics = [
        TopicState(topic_id=f"t{i}", name=f"Topic {i}", mastery=20, pretested=True,
                   explain_pages=1, quiz_count=1, mistakes=[{"q": "x"}], errors=1)
        for i in range(22)
    ]
    picked = legal_candidates(topics, limit=30)
    assert len(picked) == 30
    assert {c.topic_id for c in picked} == {t.topic_id for t in topics}
    # first pass is one activity per topic, in dataset order
    assert [c.topic_id for c in picked[:22]] == [t.topic_id for t in topics]


# ---------------------------------------------------------------------------
# 5. split observe: state first, decision second
# ---------------------------------------------------------------------------

def test_observe_without_decide_reports_pending_and_keeps_the_numbers(tmp_path: Path) -> None:
    """The result page must be able to paint before the model answers."""
    class _Exploding:
        available = True

        def choose(self, topics, round_no=1, visited=(), recent=()):
            raise AssertionError("decide=False must not ask the model")

    store = LearnerStore(tmp_path / "p11e.db")
    try:
        svc = AgentService(store=store, planner=_Exploding())
        payload = [{"topic_id": "a", "name": "A", "mastery": 30, "pretested": True,
                    "explain_pages": 1, "quiz_count": 2}]
        plan = svc.observe(
            "m5", "a", "practice",
            outcome={"total": 2, "correct": 2},
            quiz=[], topics_payload=payload, decide=False,
        )
        assert plan["decision_pending"] is True
        assert plan["agent"]["decided_by"] == "pending"
        assert plan["next"] is None and plan["steps"] == []
        assert plan["agent_unavailable"] is False
        # the measurement is already there: that is the part the page shows first
        assert plan["result"]["after"] > plan["result"]["before"]
        assert plan["topics"][0]["mastery"] == plan["result"]["after"]

        # second half of the round trip: the decision lands on its own
        class _Later:
            available = True

            def choose(self, topics, round_no=1, visited=(), recent=()):
                return PlannerDecision(topic_id="a", activity="practice", reason="x")

        after = AgentService(store=store, planner=_Later()).plan("m5")
        assert after["decision_pending"] is False
        assert after["next"] is not None
    finally:
        store.close()


def test_full_observe_still_decides_in_one_call(tmp_path: Path) -> None:
    class _FixedPlanner:
        available = True

        def choose(self, topics, round_no=1, visited=(), recent=()):
            return PlannerDecision(topic_id="a", activity="practice", reason="x")

    store = LearnerStore(tmp_path / "p11f.db")
    try:
        svc = AgentService(store=store, planner=_FixedPlanner())
        plan = svc.observe(
            "m6", "a", "explain", outcome={"read": True},
            quiz=[], topics_payload=[{"topic_id": "a", "name": "A", "mastery": 30,
                                      "pretested": True, "explain_pages": 1, "quiz_count": 2}],
        )
        assert plan["decision_pending"] is False
        assert plan["agent"]["decided_by"] == "llm"
        assert plan["next"]["activity"] == "practice"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# 6. the decision reaches the plan and the envelope
# ---------------------------------------------------------------------------

def test_plan_facts_contains_no_activity_at_all() -> None:
    """The facts view must not smuggle a rule decision into ``by_topic``."""
    facts = plan_facts(_topics(), 1, [])
    assert facts["steps"] == [] and facts["next"] is None
    assert all(v["activity"] is None for v in facts["by_topic"].values())
    statuses = {tid: v["status"] for tid, v in facts["by_topic"].items()}
    assert statuses == {
        "t_gd": "ready", "t_bayes": "ready", "t_over": "done", "t_null": "pretest"
    }
    assert facts["remaining"] == 2 and facts["mastered"] == 1 and facts["pending_pretest"] == 1


def test_apply_decision_puts_only_the_model_step_in_the_plan() -> None:
    topics = _topics()
    plan = plan_facts(topics, 1, [])
    decision = PlannerDecision(topic_id="t_gd", activity="review", reason="错题 3 道")
    updated = apply_decision(plan, decision, topics)

    assert updated["next"]["topic_id"] == "t_gd"
    assert updated["next"]["activity"] == "review"
    # exactly one step: the model's.  Code adds no second, rule-made step.
    assert [s["topic_id"] for s in updated["steps"]] == ["t_gd"]
    assert updated["by_topic"]["t_gd"]["reason"] == "错题 3 道"
    assert updated["by_topic"]["t_bayes"]["activity"] is None
    assert updated["decision"]["source"] == "llm"
    # facts stay the policy's: the mastered topic is still marked done
    assert updated["by_topic"]["t_over"]["status"] == "done"


def test_apply_decision_never_invents_a_step() -> None:
    topics = _topics()
    plan = plan_facts(topics, 1, [])
    ghost = PlannerDecision(topic_id="t_ghost", activity="review", reason="x")
    assert apply_decision(plan, ghost, topics) is plan


def test_envelope_flags_an_escalated_decision(tmp_path: Path) -> None:
    """A step that only appeared on the paid model is labelled as such."""
    class _EscalatedPlanner:
        escalated = True
        available = True

        def choose(self, topics, round_no=1, visited=(), recent=()):
            return PlannerDecision(topic_id="a", activity="practice", reason="x", escalated=True)

    store = LearnerStore(tmp_path / "p11d.db")
    try:
        svc = AgentService(store=store, planner=_EscalatedPlanner())
        plan = svc.plan("m4", [{"topic_id": "a", "name": "A", "mastery": 20,
                                "pretested": True, "explain_pages": 1, "quiz_count": 2}])
        assert plan["agent"]["escalated"] is True
        assert plan["next"]["escalated"] is True
        assert plan["decision"]["escalated"] is True
        assert plan["agent"]["decided_by"] == "llm"
    finally:
        store.close()


def test_service_reports_who_decided(tmp_path: Path) -> None:
    class _FixedPlanner:
        def choose(self, topics, round_no=1, visited=(), recent=()):
            return PlannerDecision(topic_id="t_gd", activity="review", reason="错题 3 道")

    store = LearnerStore(tmp_path / "p11.db")
    try:
        payload = [
            {"topic_id": "t_gd", "name": "Gradient Descent", "mastery": 57, "pretested": True,
             "mistakes": [{"q": "a"}, {"q": "b"}, {"q": "c"}], "errors": 3,
             "explain_pages": 3, "explain_done": True, "explain_runs": 2, "quiz_count": 4},
            {"topic_id": "t_bayes", "name": "Bayes Theorem", "mastery": 71, "pretested": True,
             "explain_pages": 2, "explain_done": True, "quiz_count": 4},
        ]
        with_llm = AgentService(store=store, planner=_FixedPlanner())
        plan = with_llm.plan("m1", payload)
        assert plan["agent"]["decided_by"] == "llm"
        assert plan["next"]["topic_id"] == "t_gd"
        assert plan["next"]["activity"] == "review"
        assert plan["decision"]["reason"] == "错题 3 道"
        # the injected planner has no escalation state -> not escalated
        assert plan["agent"]["escalated"] is False

        # Same store, rule path: verdicts unchanged, only the chooser differs.
        rule_only = AgentService(store=store, planner=None)
        rule_plan = rule_only.plan("m1", payload)
        assert rule_plan["agent"]["decided_by"] == "rule"
        assert rule_plan["mastered_at"] == 80 and rule_plan["explain_cap"] == 55
        assert {t["topic_id"]: t["mastery"] for t in rule_plan["topics"]} == {
            t["topic_id"]: t["mastery"] for t in plan["topics"]
        }
    finally:
        store.close()


def test_missing_key_is_reported_as_not_connected(tmp_path: Path) -> None:
    """No key -> "no model connected", which is not the same as a rule plan."""
    class _NoKey:
        is_configured = False

    planner = LLMPlanner({"agent": {"planner": "llm"}}, client=_NoKey())
    store = LearnerStore(tmp_path / "p11c.db")
    try:
        svc = AgentService(store=store, planner=planner)
        plan = svc.plan("m3", [{"topic_id": "a", "name": "A", "mastery": 30,
                                "pretested": True, "explain_pages": 1, "quiz_count": 2}])
        assert plan["agent"]["decided_by"] == "unavailable"
        assert plan["agent"]["model_connected"] is False
        assert plan["unavailable_reason"] == "no_api_key"
        assert plan["next"] is None
    finally:
        store.close()


def test_service_ships_no_step_when_the_model_gives_nothing(tmp_path: Path) -> None:
    """The failure path must not substitute a rule-made next step."""
    class _BrokenPlanner:
        available = True
        unavailable_reason = "bad_output"
        last_error = "回复里没有 JSON 对象"

        def choose(self, topics, round_no=1, visited=(), recent=()):
            self.unavailable_reason = "bad_output"
            return None

    store = LearnerStore(tmp_path / "p11b.db")
    try:
        svc = AgentService(store=store, planner=_BrokenPlanner())
        plan = svc.plan("m2", [{"topic_id": "a", "name": "A", "mastery": 30,
                                "pretested": True, "explain_pages": 1, "quiz_count": 1}])
        assert plan["agent"]["decided_by"] == "unavailable"
        assert plan["agent_unavailable"] is True
        assert plan["unavailable_reason"] == "bad_output"
        assert plan["next"] is None and plan["steps"] == []
        # facts are still reported, so the page can explain the situation
        assert plan["remaining"] == 1 and plan["mastered"] == 0
    finally:
        store.close()