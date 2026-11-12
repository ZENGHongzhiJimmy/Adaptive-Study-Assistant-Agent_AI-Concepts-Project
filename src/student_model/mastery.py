"""Momentum-style mastery update (P2).

    new_mastery = alpha * old_mastery + (1 - alpha) * current_performance

All three quantities live in [0, 1]. Out-of-range values are clamped rather
than silently propagated; NaN / non-numeric values are rejected, because a
corrupted mastery would poison every downstream Planner decision and the
Learning Gain metric.
"""

from __future__ import annotations

import math


def clamp01(value: float) -> float:
    """Clamp a finite number into [0, 1]."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"expected a finite number, got {value!r}")
    return min(1.0, max(0.0, number))


def update_mastery(old_mastery: float, performance: float, alpha: float = 0.7) -> float:
    """Return the updated mastery; does not mutate any persisted state."""
    old = clamp01(old_mastery)
    perf = clamp01(performance)
    a = clamp01(alpha)
    return clamp01(a * old + (1.0 - a) * perf)


def performance_from_answer(correct: bool | int | float) -> float:
    """Normalize an objective answer into ``current_performance`` in [0, 1].

    P2/P3 use MCQ only: bool -> 1.0 / 0.0. A numeric 0..1 score is accepted
    for later phases, but subjective LLM rubric scoring stays disabled.
    """
    if isinstance(correct, bool):
        return 1.0 if correct else 0.0
    return clamp01(correct)


def is_error(performance: float) -> bool:
    """An answer counts as an error below the halfway mark (deterministic)."""
    return clamp01(performance) < 0.5
