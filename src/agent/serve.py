"""The runtime the product actually calls: a LangGraph agent over ``policy.py``.

Before this module, ``src/agent/graph.py`` was a side artefact - only the CLI
ran it, while the web app decided everything in JavaScript.  A reviewer asking
"where is the agent, and is it the same logic as the page?" had no good answer.

This module closes that gap.  The decision the UI shows now comes from a
compiled LangGraph graph, and the policy that fills its nodes is the same
``policy.py`` the evaluation harness uses:

    observe ──► planner          (after one activity: Update → Re-plan)
                  │
                  └─► END         (plan-only request: just Re-plan)

``observe`` is the *Update* half of Plan–Act–Observe–Update–Re-plan (mastery and
error book), ``planner`` is *Re-plan*.  The *Act* half happens in the browser -
the student reads / answers - and comes back as an observation.  State lives in
SQLite (``learner_store``) so a page refresh, a second tab or an experiment rerun
see exactly the same trajectory.

Why not run ``graph.py`` here?  That graph renders content (Teach text, MCQ
generation through the LLM).  The web layer already has the parsed material and
would pay an LLM call per page render.  Same policy, different action surface -
documented in README §5.
"""

from __future__ import annotations

import time
from typing import Any, Sequence, TypedDict

from langgraph.graph import END, StateGraph

from .learner_store import LearnerStore
from .policy import (
    EXPLAIN_CAP,
    MASTERED,
    POLICY_ID,
    TopicState,
    advance_round,
    apply_pre_test,
    apply_result,
    decide,
    plan_material,
)


class AgentGraphState(TypedDict, total=False):
    """Graph state: the learner record plus the single observation to absorb."""

    material_id: str
    topics: list[TopicState]
    round: int
    visited: list[str]
    observation: dict[str, Any] | None
    result: dict[str, Any] | None
    plan: dict[str, Any]


class AgentService:
    """Ties the graph, the policy and the store together for the HTTP layer."""

    engine = "langgraph"
    policy = POLICY_ID

    def __init__(self, store: LearnerStore | None = None):
        self.store = store or LearnerStore()
        self._graph = self._build_graph()

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------- graph

    def _build_graph(self) -> Any:
        store = self.store

        def observe_node(state: AgentGraphState) -> dict[str, Any]:
            """Update: fold one finished activity into the learner state."""
            observation = state.get("observation") or {}
            topic_id = str(observation.get("topic_id") or "")
            activity = str(observation.get("activity") or "")
            material_id = state["material_id"]

            topic = next((t for t in state["topics"] if t.topic_id == topic_id), None)
            if topic is None:
                return {"result": None, "observation": None}

            outcome = apply_result(
                topic,
                activity,
                observation.get("outcome") or {},
                observation.get("quiz") or [],
            )
            store.save_topic(material_id, outcome.state)

            topics = [outcome.state if t.topic_id == topic_id else t for t in state["topics"]]
            round_no, visited = advance_round(
                topics, int(state.get("round", 1)), state.get("visited") or [], topic_id
            )
            store.save_roll(material_id, round_no, visited)
            return {
                "topics": topics,
                "round": round_no,
                "visited": visited,
                "result": outcome.to_dict(),
                "observation": None,
            }

        def planner_node(state: AgentGraphState) -> dict[str, Any]:
            """Re-plan: rank the whole material and pick the next activity."""
            return {
                "plan": plan_material(
                    state["topics"],
                    int(state.get("round", 1)),
                    state.get("visited") or [],
                )
            }

        def has_observation(state: AgentGraphState) -> str:
            return "observe" if state.get("observation") else "planner"

        builder = StateGraph(AgentGraphState)
        builder.add_node("observe", observe_node)
        builder.add_node("planner", planner_node)
        builder.set_conditional_entry_point(has_observation, {"observe": "observe", "planner": "planner"})
        builder.add_edge("observe", "planner")
        builder.add_edge("planner", END)
        return builder.compile()

    # ------------------------------------------------------------- helpers

    def _hydrate(self, material_id: str, payload: Sequence[dict[str, Any]] | None) -> list[TopicState]:
        """Seed from the client on first contact, then trust SQLite."""
        incoming = [TopicState.from_dict(item) for item in (payload or []) if isinstance(item, dict)]
        incoming = [t for t in incoming if t.topic_id]
        if incoming:
            return self.store.seed_topics(material_id, incoming)
        return self.store.load_topics(material_id)

    def _envelope(self, plan: dict[str, Any], topics: Sequence[TopicState]) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "agent": {"engine": self.engine, "nodes": ["planner"]},
            # Thresholds travel with every decision so the UI never needs its
            # own copy.  Before this, engine.js hardcoded 80 / 55 and would
            # silently disagree with the server the moment the policy changed.
            "mastered_at": MASTERED,
            "explain_cap": EXPLAIN_CAP,
            "round": plan["round"],
            "total": plan["total"],
            "mastered": plan["mastered"],
            "remaining": plan["remaining"],
            "pending_pretest": plan["pending_pretest"],
            "visited": plan["visited"],
            "steps": plan["steps"],
            "next": plan["next"],
            "blocked": plan["blocked"],
            "done": plan["done"],
            "last_round_done": plan["last_round_done"],
            "by_topic": plan["by_topic"],
            "topics": [t.to_dict() for t in topics],
        }

    # ------------------------------------------------------------- API

    def plan(self, material_id: str, topics_payload: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Re-plan only: what should the student do next?"""
        started = time.perf_counter()
        topics = self._hydrate(material_id, topics_payload)
        round_no, visited = self.store.load_roll(material_id)
        final = self._graph.invoke(
            {
                "material_id": material_id,
                "topics": topics,
                "round": round_no,
                "visited": visited,
                "observation": None,
            }
        )
        plan = final.get("plan") or plan_material(topics, round_no, visited)
        self._log_plan(material_id, plan, (time.perf_counter() - started) * 1000.0)
        return self._envelope(plan, final.get("topics") or topics)

    def observe(
        self,
        material_id: str,
        topic_id: str,
        activity: str,
        outcome: dict[str, Any] | None = None,
        quiz: Sequence[dict[str, Any]] | None = None,
        topics_payload: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Absorb one finished activity, update state, then re-plan."""
        started = time.perf_counter()
        topics = self._hydrate(material_id, topics_payload)
        round_no, visited = self.store.load_roll(material_id)
        final = self._graph.invoke(
            {
                "material_id": material_id,
                "topics": topics,
                "round": round_no,
                "visited": visited,
                "observation": {
                    "topic_id": topic_id,
                    "activity": activity,
                    "outcome": outcome or {},
                    "quiz": list(quiz or []),
                },
            }
        )
        plan = final.get("plan") or plan_material(topics, round_no, visited)
        latency = (time.perf_counter() - started) * 1000.0
        result = final.get("result") or {}
        self.store.log_step(
            material_id=material_id,
            topic_id=topic_id,
            activity=activity,
            rule=next((s["rule"] for s in plan["steps"] if s["topic_id"] == topic_id), ""),
            mastery_before=float(result.get("before", 0) or 0),
            mastery_after=float(result.get("after", 0) or 0),
            delta=float(result.get("delta", 0) or 0),
            outcome=str(result.get("note") or ""),
            policy=self.policy,
            source="web",
            latency_ms=latency,
        )
        payload = self._envelope(plan, final.get("topics") or topics)
        payload["result"] = result
        payload["latency_ms"] = round(latency, 3)
        return payload

    def pre_test(
        self,
        material_id: str,
        answers: Sequence[dict[str, Any]],
        topics_payload: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Set the starting line for each topic (Pre-test is not an error)."""
        started = time.perf_counter()
        topics = self._hydrate(material_id, topics_payload)
        by_id = {t.topic_id: t for t in topics}
        for answer in answers or []:
            if not isinstance(answer, dict):
                continue
            topic = by_id.get(str(answer.get("topic_id") or ""))
            if topic is None:
                continue
            raw = answer.get("correct", None)
            updated = apply_pre_test(topic, None if raw is None else bool(raw))
            by_id[topic.topic_id] = updated
            self.store.save_topic(material_id, updated)

        topics = [by_id[t.topic_id] for t in topics]
        round_no, visited = self.store.load_roll(material_id)
        final = self._graph.invoke(
            {
                "material_id": material_id,
                "topics": topics,
                "round": round_no,
                "visited": visited,
                "observation": None,
            }
        )
        plan = final.get("plan") or plan_material(topics, round_no, visited)
        payload = self._envelope(plan, final.get("topics") or topics)
        payload["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return payload

    # ------------------------------------------------------------- misc

    def _log_plan(self, material_id: str, plan: dict[str, Any], latency_ms: float) -> None:
        """Log the decision itself (``activity == "plan"``), not the whole plan.

        ``outcome`` carries what was chosen, so a trace reads
        ``plan -> practice -> plan -> explain ...`` and the ratio of decisions to
        finished activities is visible at a glance.
        """
        step = plan.get("next") or {}
        self.store.log_step(
            material_id=material_id,
            topic_id=str(step.get("topic_id") or ""),
            activity="plan",
            rule=str(step.get("rule") or ""),
            mastery_before=float(step.get("mastery") or 0),
            mastery_after=float(step.get("mastery") or 0),
            delta=0.0,
            outcome=str(step.get("activity") or "none"),
            policy=self.policy,
            source="web",
            latency_ms=latency_ms,
        )

    def state(self, material_id: str) -> dict[str, Any]:
        topics = self.store.load_topics(material_id)
        round_no, visited = self.store.load_roll(material_id)
        return {
            "material_id": material_id,
            "policy": self.policy,
            "round": round_no,
            "visited": visited,
            "topics": [t.to_dict() for t in topics],
            "steps": self.store.recent_steps(material_id, limit=20),
        }

    def reset(self, material_id: str | None = None) -> None:
        if material_id:
            self.store.clear_material(material_id)
        else:
            self.store.clear_all()


__all__ = ["AgentService", "AgentGraphState", "decide"]
