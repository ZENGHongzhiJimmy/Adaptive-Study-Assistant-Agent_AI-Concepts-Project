"""LangGraph closed loop (P3): Planner -> Action -> Evaluate -> State Update.

The back-edge ``State Update -> Re-planning`` is what makes this a stateful
agent rather than a one-shot chain. It has TWO mandatory exits (README 12.2):
``step >= max_steps`` and ``mastery >= target_mastery``. A Teach step never
changes mastery, so the planner also forces an MCQ right after Teach to avoid
an infinite re-teaching loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..student_model.state import StudentStateStore
from .actions import Retriever, render_action
from .evaluator import evaluate_step
from .planner import choose_action
from .questions import MCQ, questions_for


class AgentState(TypedDict, total=False):
    student_id: str
    topic: str
    run_id: str
    step: int
    max_steps: int
    target_mastery: float
    mastery: float
    attempts: int
    recent_errors: list[Any]
    last_action: str | None
    action: str
    action_reason: str
    reached_target: bool
    teach_text: str
    citation: str | None
    lecture: str | None
    page: int | None
    grounded: bool
    question: dict[str, Any] | None
    mcq_index: int
    answer_sequence: list[str]
    student_answer: str
    evaluation: dict[str, Any]
    started_at: float
    event_id: int
    idempotent: bool
    stop_reason: str | None


@dataclass
class AgentRuntime:
    """Everything the nodes need; injectable so tests stay offline."""

    store: StudentStateStore
    config: dict[str, Any]
    questions: list[MCQ] = field(default_factory=list)
    retriever: Retriever | None = None


def build_graph(runtime: AgentRuntime) -> Any:
    """Compile the four-node graph with conditional routing."""
    cfg = runtime.config
    store = runtime.store
    max_steps = int(cfg["planner"]["max_steps"])
    target = float(cfg["planner"]["target_mastery"])

    def planner_node(state: AgentState) -> dict[str, Any]:
        decision = choose_action(
            mastery=float(state["mastery"]),
            recent_errors=state.get("recent_errors"),
            last_action=state.get("last_action"),
            config=cfg,
        )
        return {
            "action": decision.action,
            "action_reason": decision.reason,
            "reached_target": decision.reached_target,
            "stop_reason": "target_mastery" if decision.action == "Done" else None,
        }

    def action_node(state: AgentState) -> dict[str, Any]:
        result = render_action(
            action=state["action"],
            topic=state["topic"],
            mcq_index=int(state.get("mcq_index", 0)),
            questions=runtime.questions,
            retriever=runtime.retriever,
        )
        return {**result.as_state_update(), "started_at": time.perf_counter()}

    def evaluate_node(state: AgentState) -> dict[str, Any]:
        answers = state["answer_sequence"]
        # Index by question number, not loop step: Teach steps consume no answer.
        question_number = max(int(state.get("mcq_index", 0)) - 1, 0)
        raw = answers[question_number % len(answers)]
        evaluation = evaluate_step(state.get("question"), raw)
        return {"student_answer": raw, "evaluation": evaluation.as_dict()}

    def state_update_node(state: AgentState) -> dict[str, Any]:
        step = int(state["step"]) + 1
        evaluation = state["evaluation"]
        latency_ms = round((time.perf_counter() - float(state["started_at"])) * 1000.0, 3)
        common = {
            "run_id": state["run_id"],
            "step": step,
            "action": state["action"],
            "action_reason": state["action_reason"],
            "citation": state.get("citation"),
            "lecture": state.get("lecture"),
            "page": state.get("page"),
            "latency_ms": latency_ms,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        event_key = f"{state['run_id']}:{step}"

        if evaluation["skipped"]:
            # Teach: log a trace event but do NOT touch mastery or attempts.
            event = store.log_event(
                state["student_id"], state["topic"],
                event_key=event_key,
                mastery_before=float(state["mastery"]),
                mastery_after=float(state["mastery"]),
                evaluation_result="skipped",
                question_id=None,
                is_error=0,
                **common,
            )
            fresh = store.get_state(state["student_id"], state["topic"])
        else:
            event = store.record_answer(
                state["student_id"], state["topic"],
                float(evaluation["performance"]),
                event_key=event_key,
                question_id=evaluation["question_id"],
                **common,
            )
            fresh = store.get_state(state["student_id"], state["topic"])

        mastery = float(fresh["mastery"])
        # Reaching the learning goal is the primary exit; max_steps is the guard.
        if mastery >= target:
            stop_reason = "target_mastery"
        elif step >= max_steps:
            stop_reason = "max_steps"
        else:
            stop_reason = None

        return {
            "mastery": mastery,
            "attempts": fresh["attempts"],
            "recent_errors": fresh["recent_errors"],
            "step": step,
            "last_action": state["action"],
            "event_id": event["id"],
            "idempotent": bool(event.get("idempotent")),
            "stop_reason": stop_reason,
        }

    def route_from_planner(state: AgentState) -> str:
        return "end" if state["action"] == "Done" else "action"

    def route_after_update(state: AgentState) -> str:
        return "end" if state.get("stop_reason") else "planner"

    builder = StateGraph(AgentState)
    builder.add_node("planner", planner_node)
    builder.add_node("action", action_node)
    builder.add_node("evaluate", evaluate_node)
    builder.add_node("state_update", state_update_node)
    builder.set_entry_point("planner")
    builder.add_edge("action", "evaluate")
    builder.add_edge("evaluate", "state_update")
    builder.add_conditional_edges(
        "planner", route_from_planner, {"action": "action", "end": END}
    )
    builder.add_conditional_edges(
        "state_update", route_after_update, {"planner": "planner", "end": END}
    )
    return builder.compile()


__all__ = ["AgentRuntime", "AgentState", "build_graph", "questions_for"]
