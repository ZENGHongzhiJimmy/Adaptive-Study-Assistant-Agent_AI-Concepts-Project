"""The runtime the product actually calls: a LangGraph agent over ``policy.py``.

The decision the UI shows comes from a compiled LangGraph graph: ``observe``
folds the result into the learner state with ``policy.apply_result``
(measurement), ``planner`` asks the model which legal activity to do next
(``planner_llm``) and falls back to *no decision* - never to a rule-made one -
when the model cannot answer.

    observe ──► planner          (after one activity: Update → Re-plan)
                  │
                  └─► END         (plan-only request: just Re-plan)

``observe`` is the *Update* half of Plan–Act–Observe–Update–Re-plan (mastery and
error book), ``planner`` is *Re-plan*.  The *Act* half happens in the browser -
the student reads / answers - and comes back as an observation.  State lives in
SQLite (``learner_store``) so a page refresh, a second tab or an experiment rerun
see exactly the same trajectory.

``policy.decide()`` is not reachable from this module.  It survives only behind
the explicit ``agent.planner: "rule"`` switch (tests / offline demo), where
``policy.plan_material`` builds the step; the experiment's ``adaptive`` arm uses
the same ``LLMPlanner`` as this module, replayed from a frozen decision log.

Content (the explanation text the student reads, the quiz they answer) is
rendered by the web layer, which already holds the parsed material.  This graph
therefore plans only, and never pays an LLM call per page render.
"""

from __future__ import annotations

import time
from typing import Any, Sequence, TypedDict

from langgraph.graph import END, StateGraph

from .learner_store import LearnerStore
from .planner_llm import apply_decision, make_planner
from .policy import (
    EXPLAIN_CAP,
    MASTERED,
    POLICY_ID,
    TopicState,
    advance_round,
    apply_pre_test,
    apply_result,
    plan_facts,
    plan_material,
)

# ``planner=None`` is meaningful (force the rule path, e.g. in tests), so the
# "build it from config" case needs its own sentinel.
_UNSET = object()


class AgentGraphState(TypedDict, total=False):
    """Graph state: the learner record plus the single observation to absorb."""

    material_id: str
    topics: list[TopicState]
    round: int
    visited: list[str]
    observation: dict[str, Any] | None
    result: dict[str, Any] | None
    # The activity that was just absorbed, so the planner sees it *in the same
    # invoke*: ``learner_step`` is written after the graph returns, and without
    # this the model would be blind to its own last move and repeat it.
    last_activity: dict[str, Any] | None
    # Two-phase observe: ``False`` means "update the state now, decide later in
    # a separate call".  The browser uses it so the mastery change paints
    # immediately instead of waiting 3-4 s for the model.
    decide: bool
    plan: dict[str, Any]


class AgentService:
    """Ties the graph, the policy and the store together for the HTTP layer."""

    engine = "langgraph"
    policy = POLICY_ID

    def __init__(
        self,
        store: LearnerStore | None = None,
        planner: Any = _UNSET,
        config: dict[str, Any] | None = None,
    ):
        """``planner`` may be injected:

        * omitted  -> built from ``agent.planner`` in config (``llm`` or ``rule``)
        * ``None`` -> force the rule path (tests, offline runs, no network)
        * object   -> use it as-is (a stub in tests, or a shared cache)
        """
        self.store = store or LearnerStore()
        self.config = config
        self.planner = make_planner(config) if planner is _UNSET else planner
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
                "last_activity": {
                    "topic_id": topic_id,
                    "activity": activity,
                    "mastery_before": outcome.before,
                    "mastery_after": outcome.after,
                    "outcome": outcome.note,
                },
                "observation": None,
            }

        def planner_node(state: AgentGraphState) -> dict[str, Any]:
            """Re-plan: the model decides, code only reports what is true.

            Two modes, and they are not allowed to blur into each other:

            * ``planner is None`` - an **explicit** ``agent.planner: rule``.
              ``plan_material`` runs, as it did before, for the rule demo and
              for the tests that pin its behaviour.
            * ``planner is not None`` - the product path.  The plan is built by
              ``plan_facts`` (counts and statuses, no activity anywhere), the
              model picks one legal candidate, and ``apply_decision`` inserts
              that single step.  If the model returns nothing there is **no**
              rule substitution: ``next`` stays ``None`` and the payload says
              why, so a rule-made step can never be mistaken for a decision.
            """
            planner = self.planner
            round_no = int(state.get("round", 1))
            visited = state.get("visited") or []

            if planner is None:
                return {"plan": plan_material(state["topics"], round_no, visited)}

            if not state.get("decide", True):
                # The caller split Observe from Re-plan to keep the UI
                # responsive: hand back the facts with an explicit "decision not
                # computed yet" marker.  Not a failure, so no agent_unavailable.
                plan = plan_facts(state["topics"], round_no, visited)
                plan["decision_pending"] = True
                return {"plan": plan}

            plan = plan_facts(state["topics"], round_no, visited)
            recent = list(store.recent_steps(state["material_id"], limit=5))
            just_now = state.get("last_activity")
            if just_now:
                # Written to SQLite only after this invoke returns, so hand it
                # to the planner directly - otherwise the model cannot see that
                # it just did this step and happily repeats it.
                recent = [just_now, *recent]
            decision = planner.choose(
                state["topics"],
                round_no,
                visited,
                recent,
            )
            if decision is not None:
                return {"plan": apply_decision(plan, decision, state["topics"])}

            reason = str(getattr(planner, "unavailable_reason", "") or "no_decision")
            # "no_candidates" is not a failure: it means there was nothing left
            # to schedule (all mastered, or everything blocked).  Only the real
            # failures deserve an "agent unavailable" banner.
            if reason != "no_candidates" and plan.get("remaining"):
                plan["agent_unavailable"] = True
                plan["unavailable_reason"] = reason
            plan["decision"] = {
                "source": "unavailable" if plan.get("agent_unavailable") else "none",
                "reason": str(getattr(planner, "last_error", "") or ""),
                "unavailable_reason": reason,
                # Which models were asked and how each failed (model + reason
                # only, never prompt text), so the page can tell "rate limited"
                # apart from "the reply was not JSON" instead of guessing.
                "attempts": list(getattr(planner, "attempt_log", []) or []),
                # How many full passes over [primary, fallback] were made
                # before giving up.  The page says "已在主/备用模型间循环 N 轮"
                # instead of a vague "已重试".
                "rounds": int(getattr(planner, "cycles_used", 0) or 0),
            }
            return {"plan": plan}

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

    def _safety_plan(
        self,
        topics: Sequence[TopicState],
        round_no: int,
        visited: Sequence[str],
    ) -> dict[str, Any]:
        """Plan used only if the graph somehow returned nothing.

        In model mode this must not fall back to the rule plan - that would
        reintroduce exactly the substitution this design removes.  It returns
        the facts plus an explicit "no decision" marker instead.
        """
        if self.planner is None:
            return plan_material(topics, round_no, visited)
        plan = plan_facts(topics, round_no, visited)
        plan["agent_unavailable"] = True
        plan["unavailable_reason"] = "no_plan"
        return plan

    def _hydrate(self, material_id: str, payload: Sequence[dict[str, Any]] | None) -> list[TopicState]:
        """Seed from the client on first contact, then trust SQLite."""
        incoming = [TopicState.from_dict(item) for item in (payload or []) if isinstance(item, dict)]
        incoming = [t for t in incoming if t.topic_id]
        if incoming:
            return self.store.seed_topics(material_id, incoming)
        return self.store.load_topics(material_id)

    def _envelope(self, plan: dict[str, Any], topics: Sequence[TopicState]) -> dict[str, Any]:
        decision = plan.get("decision") or {}
        model_mode = self.planner is not None
        if plan.get("decision_pending"):
            # asked for the state only; the next step arrives in a second call
            decided_by = "pending"
        else:
            decided_by = str(decision.get("source") or ("unavailable" if model_mode else "rule"))
        return {
            "policy": self.policy,
            "agent": {
                "engine": self.engine,
                "nodes": ["planner"],
                # Who picked the next activity for THIS response:
                #   llm           - the model chose among legal candidates
                #   unavailable   - the model was asked and gave nothing usable;
                #                   no step is shipped, nothing is substituted
                #   rule          - explicit agent.planner="rule" (demo/tests)
                "planner": "llm" if model_mode else "rule",
                "decided_by": decided_by,
                "model_connected": bool(getattr(self.planner, "available", False)),
                # The free model produced nothing usable and the paid one made
                # the call.  Still a model decision, but the page must not
                # present it as a first-try answer.
                "escalated": bool(getattr(self.planner, "escalated", False)),
            },
            "decision": decision or None,
            # A failed decision is surfaced, never papered over: the UI needs to
            # tell "no API key" apart from "the model is thinking".
            "agent_unavailable": bool(plan.get("agent_unavailable")),
            "unavailable_reason": plan.get("unavailable_reason") or "",
            # True only for the split observe call: mastery is updated, the next
            # step is still being decided.  The page renders the numbers at once
            # and fills the "next step" card in when the second call lands.
            "decision_pending": bool(plan.get("decision_pending")),
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
        plan = final.get("plan") or self._safety_plan(topics, round_no, visited)
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
        decide: bool = True,
    ) -> dict[str, Any]:
        """Absorb one finished activity, update state, then re-plan.

        ``decide=False`` stops after the state update: the payload carries the
        mastery delta and ``decision_pending = True``, and the client asks
        ``plan()`` right after.  Two round-trips instead of one, but the first
        one costs ~10 ms, so the student sees "58% → 66%" instantly instead of
        staring at a frozen button for 3-4 s.
        """
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
                "decide": decide,
            }
        )
        plan = final.get("plan") or self._safety_plan(topics, round_no, visited)
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
        plan = final.get("plan") or self._safety_plan(topics, round_no, visited)
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


__all__ = ["AgentService", "AgentGraphState"]
