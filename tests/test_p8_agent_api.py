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
