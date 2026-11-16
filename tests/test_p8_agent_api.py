"""P8: the web app must get its decisions from the LangGraph agent.

These tests would have failed before the refactor: the endpoints did not exist
and the browser computed the next activity itself.  They now pin the contract
between ``frontend/js/engine.js`` and ``/api/agent/*``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.agent.learner_store import LearnerStore
from src.agent.serve import AgentService
from src.api import agent as agent_module
from src.api.app import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Fresh in-memory-ish agent (temporary SQLite) per test.

    ``planner=None`` pins the rule path: these tests assert the HTTP contract,
    not who picked the activity, and they must never need an API key or network.
    """
    service = AgentService(LearnerStore(db_path=tmp_path / "learner.db"), planner=None)
    monkeypatch.setattr(agent_module, "_agent", service)
    yield TestClient(app)
    service.close()


def topics_payload(*, pretested=True, explain_done=True, quiz=2, pages=2):
    return [
        {
            "topic_id": "a",
            "name": "Gradient Descent",
            "mastery": 30,
            "pretested": pretested,
            "explain_done": explain_done,
            "quiz_count": quiz,
            "explain_pages": pages,
        },
        {
            "topic_id": "b",
            "name": "Backpropagation",
            "mastery": 30,
            "pretested": pretested,
            "explain_done": explain_done,
            "quiz_count": quiz,
            "explain_pages": pages,
        },
    ]


def test_policy_endpoint_reports_the_serving_engine(client):
    body = client.get("/api/agent/policy").json()
    assert body["policy"] == "adaptive-v1"
    assert body["engine"] == "langgraph"
    assert body["nodes"] == ["observe", "planner"]


def test_progress_is_idle_when_nothing_is_being_decided(client):
    body = client.get("/api/agent/progress/m1").json()
    assert body["busy"] is False
    assert body["attempts"] == []


def test_progress_shows_a_retry_while_the_decision_is_running(tmp_path):
    """等待期间页面要能知道「已经重问了」——这一点只能在 choose() 进行中读到。

    旧实现把重试信息只放在响应里，学生只能事后看到；这个测试钉住的是实时面：
    第一次尝试失败、第二次还在跑的时候，progress() 已经带着失败记录。
    """
    seen: dict = {}
    service = None

    class _RetryingPlanner:
        available = True
        escalated = False
        cycles_used = 1
        unavailable_reason = "bad_output"
        last_error = "not json"
        attempt_log: list = []

        def choose(self, topics, round_no=1, visited=(), recent=()):  # noqa: ANN001
            self.attempt_log = [{"model": "primary", "reason": "bad_output", "round": 1}]
            seen.update(service.progress())
            return None

    service = AgentService(
        store=LearnerStore(db_path=tmp_path / "learner.db"),
        planner=_RetryingPlanner(),
    )
    try:
        service.plan("m1", topics_payload())
        assert seen["busy"] is True
        assert seen["material_id"] == "m1"
        assert [a["reason"] for a in seen["attempts"]] == ["bad_output"]
        assert service.progress()["busy"] is False, "决策结束后不应再报忙碌"
    finally:
        service.close()


def test_plan_returns_the_agents_decision(client):
    body = client.post("/api/agent/plan", json={
        "material_id": "m1", "topics": topics_payload(),
    }).json()

    assert body["agent"]["engine"] == "langgraph"
    assert body["next"]["topic_id"] == "a"
    assert body["next"]["activity"] == "practice"
    assert body["remaining"] == 2 and body["round"] == 1
    # Per-topic decisions travel with the plan so the UI can badge each card.
    assert set(body["by_topic"]) == {"a", "b"}


def test_unread_topic_is_sent_to_the_explanation_first(client):
    body = client.post("/api/agent/plan", json={
        "material_id": "m1", "topics": topics_payload(explain_done=False),
    }).json()
    assert body["next"]["activity"] == "explain"
    assert body["next"]["rule"] == "尚未学习讲解"


def test_observe_updates_mastery_and_replans(client):
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})

    body = client.post("/api/agent/observe", json={
        "material_id": "m1",
        "topic_id": "a",
        "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
        "quiz": [
            {"q": "q1", "opts": ["x", "y"], "a": 0, "why": ""},
            {"q": "q2", "opts": ["x", "y"], "a": 1, "why": ""},
        ],
    }).json()

    assert body["result"]["before"] == 30
    assert body["result"]["after"] == 55          # (100-30)*0.6 clamped to +25
    assert body["result"]["state"]["attempts"] == 1
    # 55% is still below 80%, so the same topic may come back - but a is now
    # "visited" this round, so the other topic is served first.
    assert body["next"]["topic_id"] == "b"


def test_two_perfect_rounds_finish_a_topic(client):
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})
    for _ in range(2):
        body = client.post("/api/agent/observe", json={
            "material_id": "m1",
            "topic_id": "a",
            "activity": "practice",
            "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
        }).json()

    topic_a = next(t for t in body["topics"] if t["topic_id"] == "a")
    assert topic_a["mastery"] == 80 and topic_a["mistakes"] == []
    assert body["mastered"] == 1


def test_error_book_travels_with_the_state(client):
    quiz = [
        {"q": "q1", "opts": ["x", "y"], "a": 0, "why": "because"},
        {"q": "q2", "opts": ["x", "y"], "a": 1, "why": ""},
    ]
    body = client.post("/api/agent/observe", json={
        "material_id": "m1",
        "topic_id": "a",
        "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": False}, {"i": 1, "ok": False}]},
        "quiz": quiz,
        "topics": topics_payload(),
    }).json()

    topic_a = next(t for t in body["topics"] if t["topic_id"] == "a")
    assert len(topic_a["mistakes"]) == 2
    # Two open mistakes means the review activity must be offered next.
    assert body["by_topic"]["a"]["activity"] == "review"


def test_pre_test_sets_starting_mastery_and_skip_is_neutral(client):
    body = client.post("/api/agent/pre-test", json={
        "material_id": "m1",
        "answers": [
            {"topic_id": "a", "correct": True},
            {"topic_id": "b", "correct": None},
        ],
        "topics": topics_payload(pretested=False),
    }).json()

    by_id = {t["topic_id"]: t for t in body["topics"]}
    assert by_id["a"]["mastery"] == 45 and by_id["a"]["pretested"]
    assert by_id["b"]["mastery"] == 30 and by_id["b"]["pretested"]
    assert body["pending_pretest"] == 0
    assert body["next"] is not None


def test_state_survives_a_second_request_without_a_client_payload(client):
    """SQLite is authoritative: after first contact the client may send nothing."""
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})
    client.post("/api/agent/observe", json={
        "material_id": "m1", "topic_id": "a", "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
    })

    body = client.post("/api/agent/plan", json={"material_id": "m1"}).json()
    topic_a = next(t for t in body["topics"] if t["topic_id"] == "a")
    assert topic_a["mastery"] == 55


def test_materials_are_isolated(client):
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})
    client.post("/api/agent/observe", json={
        "material_id": "m1", "topic_id": "a", "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
    })
    other = client.post("/api/agent/plan", json={
        "material_id": "m2", "topics": topics_payload(),
    }).json()
    assert all(t["mastery"] == 30 for t in other["topics"])


def test_reset_clears_one_material(client):
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})
    client.post("/api/agent/observe", json={
        "material_id": "m1", "topic_id": "a", "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
    })
    client.post("/api/agent/reset", json={"material_id": "m1"})
    body = client.post("/api/agent/plan", json={
        "material_id": "m1", "topics": topics_payload(),
    }).json()
    assert all(t["mastery"] == 30 for t in body["topics"])


def test_decision_trace_is_recorded_for_the_report(client):
    client.post("/api/agent/plan", json={"material_id": "m1", "topics": topics_payload()})
    client.post("/api/agent/observe", json={
        "material_id": "m1", "topic_id": "a", "activity": "practice",
        "outcome": {"answers": [{"i": 0, "ok": True}, {"i": 1, "ok": True}]},
    })

    state = client.get("/api/agent/state/m1").json()
    assert state["policy"] == "adaptive-v1"
    activities = [row["activity"] for row in state["steps"]]
    assert "practice" in activities and "plan" in activities


def test_observe_can_defer_the_decision_to_a_second_call(tmp_path, monkeypatch):
    """两段式：先落掌握度（快），再单独要下一步（慢）。

    前端靠这个把 2-3 秒的模型决策挡在结果页之外：点「完成」→ 看到分数 →
    骨架 → 下一步。HTTP 契约必须能表达中间态，否则前端只能干等。
    """
    from src.agent.planner_llm import PlannerDecision

    class _FixedPlanner:
        available = True

        def choose(self, topics, round_no=1, visited=(), recent=()):
            return PlannerDecision(topic_id="a", activity="practice", reason="x")

    service = AgentService(LearnerStore(db_path=tmp_path / "defer.db"), planner=_FixedPlanner())
    monkeypatch.setattr(agent_module, "_agent", service)
    client = TestClient(app)
    try:
        payload = topics_payload()
        body = client.post("/api/agent/observe", json={
            "material_id": "m1", "topic_id": "a", "activity": "explain",
            "outcome": {"read": True}, "topics": payload, "decide": False,
        }).json()
        assert body["decision_pending"] is True
        assert body["agent"]["decided_by"] == "pending"
        assert body["next"] is None
        assert body["result"]["after"] >= body["result"]["before"]

        after = client.post("/api/agent/plan", json={"material_id": "m1", "topics": []}).json()
        assert after["decision_pending"] is False
        assert after["agent"]["decided_by"] == "llm"
        assert after["next"] is not None
    finally:
        service.close()
