"""HTTP surface of the adaptive agent (LangGraph + policy.py).

The browser never decides what to do next any more.  It posts the learner state
it knows about, and these endpoints return the agent's decision:

    GET  /api/agent/policy            which policy/engine is serving decisions
    POST /api/agent/plan              Re-plan: what is the next activity?
    POST /api/agent/observe           Update + Re-plan after one activity
    POST /api/agent/pre-test          set the starting mastery per topic
    GET  /api/agent/state/{material}  authoritative learner record + trace
    POST /api/agent/reset             clear one material (or everything)

Keeping this in its own router (instead of app.py) mirrors the module boundary:
``src/api/app.py`` handles materials/RAG/QA, ``src/api/agent.py`` handles the
agent loop.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.policy import (
    EXPLAIN_CAP,
    MASTERED,
    POLICY_ID,
    PRACTICE_GAIN,
    STAGE_ORDER,
)
from ..agent.serve import AgentService

router = APIRouter(prefix="/api/agent", tags=["agent"])

_agent: AgentService | None = None


def get_agent() -> AgentService:
    global _agent
    if _agent is None:
        _agent = AgentService()
    return _agent


def close_agent() -> None:
    global _agent
    if _agent is not None:
        _agent.close()
        _agent = None


class PlanRequest(BaseModel):
    material_id: str
    # Client-side view of each topic.  The server only uses it to seed a topic
    # it has never seen (first contact after an upgrade); afterwards SQLite wins.
    topics: list[dict[str, Any]] = Field(default_factory=list)


class ObserveRequest(BaseModel):
    material_id: str
    topic_id: str
    activity: Literal["explain", "practice", "review"]
    outcome: dict[str, Any] = Field(default_factory=dict)
    # The quiz bank of the scored topic: the policy needs the item text/options
    # to build the error book.  Only sent for `practice`.
    quiz: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)


class PreTestRequest(BaseModel):
    material_id: str
    answers: list[dict[str, Any]] = Field(default_factory=list)
    topics: list[dict[str, Any]] = Field(default_factory=list)


class ResetRequest(BaseModel):
    material_id: str | None = None


@router.get("/policy")
def policy_info() -> dict[str, Any]:
    """Describe the serving policy so the UI (and the report) can cite it."""
    return {
        "policy": POLICY_ID,
        "engine": AgentService.engine,
        "nodes": ["observe", "planner"],
        "loop": "Plan -> Act (browser) -> Observe -> Update -> Re-plan",
        "mastered_at": MASTERED,
        "explain_cap": EXPLAIN_CAP,
        "practice_gain": PRACTICE_GAIN,
        "stage_order": STAGE_ORDER,
    }


@router.post("/plan")
def plan(req: PlanRequest) -> dict[str, Any]:
    try:
        return get_agent().plan(req.material_id, req.topics)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"规划失败：{type(exc).__name__}: {exc}")


@router.post("/observe")
def observe(req: ObserveRequest) -> dict[str, Any]:
    try:
        return get_agent().observe(
            req.material_id,
            req.topic_id,
            req.activity,
            req.outcome,
            req.quiz,
            req.topics,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"状态更新失败：{type(exc).__name__}: {exc}")


@router.post("/pre-test")
def pre_test(req: PreTestRequest) -> dict[str, Any]:
    try:
        return get_agent().pre_test(req.material_id, req.answers, req.topics)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Pre-test 记录失败：{type(exc).__name__}: {exc}")


@router.get("/state/{material_id}")
def state(material_id: str) -> dict[str, Any]:
    return get_agent().state(material_id)


@router.post("/reset")
def reset(req: ResetRequest) -> dict[str, Any]:
    get_agent().reset(req.material_id)
    return {"ok": True, "material_id": req.material_id}
