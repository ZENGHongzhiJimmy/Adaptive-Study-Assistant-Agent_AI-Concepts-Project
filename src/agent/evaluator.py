"""Deterministic MCQ evaluator (P3).

No LLM, no rubric, no randomness: objective answers are compared by code, so
Learning Gain is protected from scoring noise (README 12.2). A ``Teach`` step
has no question and is recorded as ``skipped`` - it logs an event but does not
move mastery.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

CORRECT_TOKENS = frozenset({"+", "CORRECT", "RIGHT", "T", "TRUE", "YES", "Y"})
WRONG_TOKENS = frozenset({"-", "WRONG", "INCORRECT", "F", "FALSE", "NO", "N"})


@dataclass(frozen=True)
class Evaluation:
    skipped: bool
    correct: bool | None
    performance: float | None
    normalized_answer: str | None
    evaluation_result: str  # correct | incorrect | skipped
    question_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_raw_answer(raw_answer: str | None, question: dict[str, Any]) -> str:
    """Map CLI convenience tokens to a concrete option, preserving determinism.

    ``correct`` / ``wrong`` let a reproducible simulated student drive the loop
    without knowing which letter happens to be right for a given question.
    """
    token = (raw_answer or "").strip().upper()
    right = question["correct_option"]
    if token in CORRECT_TOKENS:
        return right
    if token in WRONG_TOKENS:
        return next(key for key in question["options"] if key != right)
    return token


def evaluate_step(
    question: dict[str, Any] | None,
    raw_answer: str | None,
) -> Evaluation:
    if question is None:
        return Evaluation(
            skipped=True,
            correct=None,
            performance=None,
            normalized_answer=None,
            evaluation_result="skipped",
            question_id=None,
        )
    chosen = resolve_raw_answer(raw_answer, question)
    correct = chosen in question["options"] and chosen == question["correct_option"]
    return Evaluation(
        skipped=False,
        correct=correct,
        performance=1.0 if correct else 0.0,
        normalized_answer=chosen,
        evaluation_result="correct" if correct else "incorrect",
        question_id=question["question_id"],
    )
