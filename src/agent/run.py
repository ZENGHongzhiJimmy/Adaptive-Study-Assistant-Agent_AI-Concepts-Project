"""Command-line driver: run the adaptive study loop for N steps.

    # Default: OpenRouter free model (needs a real key in the local .env)
    python -m src.agent.run --answers correct,wrong,correct --max-steps 10

    # Only escape hatch: fully offline, no key, no network (tests / P6 baseline)
    python -m src.agent.run --offline --answers correct --max-steps 10

The platform connects to the LLM by default; there is no mode selector to
choose from. A missing or placeholder OPENROUTER_API_KEY fails fast with
.env setup instructions (exit code 2) rather than silently degrading, so a
run never looks "LLM-powered" when it actually used offline templates.
``--offline`` is an explicit, deliberate opt-out whose value is captured in
the events config snapshot, keeping the P6 freeze reproducible.

No UI, no subjective rubric, no baselines: those belong to P5 / E1.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..student_model.state import StudentStateStore
from ..utils.config import get_config
from ..utils.llm_client import LLMClient, MissingAPIKeyError, key_setup_message
from .actions import Retriever, build_retriever
from .graph import AgentRuntime, build_graph
from .llm_content import LLMContentGenerator
from .questions import questions_for

# Internal contract for the programmatic API and the --offline escape hatch.
# Not a user-facing mode selector: the CLI default always uses the LLM.
LLM_MODES = ("off", "on")


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
    llm_mode: str = "on"
    llm_model: str | None = None
    llm_calls: int = 0

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
            "llm_mode": self.llm_mode,
            "llm_model": self.llm_model,
            "llm_calls": self.llm_calls,
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
    llm_mode: str | None = None,
    llm_client: Any | None = None,
    responder: Any | None = None,
) -> SessionResult:
    """Execute one session and return its full event trace.

    By default the effective ``agent.llm_mode`` from config.yaml is used,
    which is ``on``: the platform always calls the LLM. Tests and the CLI
    ``--offline`` flag pass ``llm_mode="off"`` explicitly.
    """
    cfg = copy.deepcopy(config or get_config())
    mode = str(llm_mode or cfg.get("agent", {}).get("llm_mode", "on")).lower()
    if mode not in LLM_MODES:
        raise ValueError(f"llm_mode must be one of {LLM_MODES}, got {mode!r}")
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
    generator: LLMContentGenerator | None = None
    client: Any | None = llm_client
    try:
        if mode != "off":
            client = client or LLMClient(cfg)
            if not getattr(client, "is_configured", False):
                raise MissingAPIKeyError(
                    "OPENROUTER_API_KEY is missing or still a placeholder.\n"
                    + key_setup_message()
                )
            generator = LLMContentGenerator(cfg, client=client)

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
            generator=generator,
            extended=(mode != "off"),
            responder=responder,
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
            llm_mode=mode,
            llm_model=cfg["llm"]["model"] if generator else None,
            llm_calls=generator.total_calls if generator else 0,
        )
    finally:
        closer = getattr(active_retriever, "close", None)
        if callable(closer):
            closer()
        store.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Adaptive study loop (CLI, P3/P4)")
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
    parser.add_argument("--no-rag", action="store_true", help="skip P1 retrieval")
    parser.add_argument("--no-reset", action="store_true", help="keep existing student state")
    parser.add_argument(
        "--offline", action="store_true",
        help="explicit opt-out: offline templates + static bank, no key/network needed",
    )
    args = parser.parse_args(argv)

    cfg = get_config()
    mode = "off" if args.offline else str(cfg.get("agent", {}).get("llm_mode", "on"))

    if mode != "off" and not LLMClient(cfg).is_configured:
        print(key_setup_message(), file=sys.stderr)
        print(
            "[P4] 平台默认接入 LLM，不会静默降级。如需无 Key 离线运行，请显式加 --offline。",
            file=sys.stderr,
        )
        return 2

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
            llm_mode=mode,
        )
    except ValueError as exc:
        print(f"[P3/P4] {exc}")
        return 2
    except MissingAPIKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(result.summary(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
