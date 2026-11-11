"""P3 command-line driver: run the minimal closed loop for N steps.

    python -m src.agent.run --student-id demo --topic "Bayes Theorem" \
        --answers correct,wrong,correct --max-steps 10

Everything is deterministic: same initial state + same answers + same config
=> same mastery trajectory. No LLM, no UI, no subjective rubric, no baselines.
"""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..student_model.state import StudentStateStore
from ..utils.config import get_config
from .actions import Retriever, build_retriever
from .graph import AgentRuntime, build_graph
from .questions import questions_for


@dataclass
class SessionResult:
    student_id: str
    topic: str
    run_id: str
    steps: int
    stop_reason: str | None
    initial_mastery: float
    final_mastery: float
    actions: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    grounded_steps: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "topic": self.topic,
            "run_id": self.run_id,
            "steps": self.steps,
            "stop_reason": self.stop_reason,
            "initial_mastery": self.initial_mastery,
            "final_mastery": self.final_mastery,
            "actions": self.actions,
            "grounded_steps": self.grounded_steps,
            "event_count": len(self.events),
            "trace": [
                {
                    "step": e["step"],
                    "action": e["action"],
                    "reason": e["action_reason"],
                    "evaluation": e["evaluation_result"],
                    "mastery_before": e["mastery_before"],
                    "mastery_after": e["mastery_after"],
                    "citation": e["citation"],
                }
                for e in self.events
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_answers(answers: str | Sequence[str]) -> list[str]:
    values = answers.split(",") if isinstance(answers, str) else list(answers)
    cleaned = [value.strip() for value in values if value.strip()]
    if not cleaned:
        raise ValueError("--answers must contain at least one value")
    return cleaned


def run_cli_session(
    *,
    student_id: str = "demo",
    topic: str = "Bayes Theorem",
    answers: str | Sequence[str] = "correct",
    max_steps: int | None = None,
    target_mastery: float | None = None,
    db_path: str | Path | None = None,
    reset: bool = True,
    run_id: str | None = None,
    use_rag: bool = True,
    retriever: Retriever | None = None,
    config: dict[str, Any] | None = None,
) -> SessionResult:
    """Execute one session and return its full event trace."""
    cfg = copy.deepcopy(config or get_config())
    if max_steps is not None:
        cfg["planner"]["max_steps"] = int(max_steps)
    if target_mastery is not None:
        cfg["planner"]["target_mastery"] = float(target_mastery)
    steps_limit = int(cfg["planner"]["max_steps"])
    if steps_limit <= 0:
        raise ValueError("max_steps must be positive")

    answer_sequence = _parse_answers(answers)
    resolved_run_id = run_id or f"p3-{student_id}-{topic}"
    store = StudentStateStore(db_path, cfg)
    active_retriever: Retriever | None = retriever
    try:
        if reset:
            store.reset(student_id)
        initial = store.get_state(student_id, topic)
        if active_retriever is None and use_rag:
            active_retriever = build_retriever(cfg)

        runtime = AgentRuntime(
            store=store,
            config=cfg,
            questions=questions_for(topic),
            retriever=active_retriever,
        )
        graph = build_graph(runtime)
        initial_state: dict[str, Any] = {
            "student_id": student_id,
            "topic": topic,
            "run_id": resolved_run_id,
            "step": 0,
            "max_steps": steps_limit,
            "target_mastery": float(cfg["planner"]["target_mastery"]),
            "mastery": float(initial["mastery"]),
            "attempts": initial["attempts"],
            "recent_errors": initial["recent_errors"],
            "last_action": None,
            "mcq_index": 0,
            "answer_sequence": answer_sequence,
            "stop_reason": None,
        }
        # LangGraph's default recursion_limit (25) is too small for 4 nodes x N steps.
        final_state = graph.invoke(
            initial_state, config={"recursion_limit": max(25, steps_limit * 5 + 10)}
        )
        events = store.get_events(student_id, topic=topic)
        return SessionResult(
            student_id=student_id,
            topic=topic,
            run_id=resolved_run_id,
            steps=int(final_state.get("step", len(events))),
            stop_reason=final_state.get("stop_reason"),
            initial_mastery=float(initial["mastery"]),
            final_mastery=float(final_state.get("mastery", initial["mastery"])),
            actions=[str(e["action"]) for e in events],
            events=events,
            grounded_steps=sum(1 for e in events if e["citation"]),
        )
    finally:
        closer = getattr(active_retriever, "close", None)
        if callable(closer):
            closer()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3: minimal adaptive study loop (CLI)")
    parser.add_argument("--student-id", default="demo")
    parser.add_argument("--topic", default="Bayes Theorem")
    parser.add_argument(
        "--answers", default="correct",
        help="comma-separated simulated answers: correct/wrong or option letters A-D",
    )
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--target-mastery", type=float, default=None)
    parser.add_argument("--db", default=None, help="SQLite path (default: config.yaml)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--no-rag", action="store_true", help="skip P1 retrieval for Teach")
    parser.add_argument("--no-reset", action="store_true", help="keep existing student state")
    args = parser.parse_args(argv)

    try:
        result = run_cli_session(
            student_id=args.student_id,
            topic=args.topic,
            answers=args.answers,
            max_steps=args.max_steps,
            target_mastery=args.target_mastery,
            db_path=args.db,
            reset=not args.no_reset,
            run_id=args.run_id,
            use_rag=not args.no_rag,
        )
    except ValueError as exc:
        print(f"[P3] {exc}")
        return 2

    print(json.dumps(result.summary(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
