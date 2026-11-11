"""Rule-based Adaptive Planner (P3 vertical slice).

P3 deliberately supports only three outcomes: ``Teach``, ``MCQ`` and ``Done``.
Challenge / Review / Hint are P4 activities; when the rules would choose one
of them we still emit ``Teach`` or ``MCQ`` and record the reason explicitly, so
the decision stays explainable without violating "no LLM, no four activity
types before the loop is verified".

Thresholds and the error window come from config.yaml - never hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlannerDecision:
    action: str  # Teach | MCQ | Done
    reason: str
    reached_target: bool = False


def choose_action(
    *,
    mastery: float,
    recent_errors: list[Any] | None,
    last_action: str | None,
    config: dict[str, Any],
) -> PlannerDecision:
    """Pure function: same state + same config => same decision."""
    planner_cfg = config["planner"]
    thresholds = planner_cfg["thresholds"]
    target = float(planner_cfg["target_mastery"])
    trigger = int(planner_cfg.get("review_trigger_errors", 2))
    errors = len(recent_errors or [])

    if mastery >= target:
        return PlannerDecision(
            "Done", f"mastery {mastery:.3f} >= target_mastery {target}", reached_target=True
        )

    # A Teach step does not change mastery; without this rule the loop would
    # keep re-teaching forever. P4 replaces this with a real activity sequence.
    if last_action == "Teach":
        return PlannerDecision("MCQ", "Teach was just shown; practice is mandatory next")

    if errors >= trigger:
        return PlannerDecision(
            "Teach",
            f"{errors} recent errors >= review_trigger_errors {trigger}: "
            "re-teach the weak point (Review activity arrives in P4)",
        )

    if mastery < float(thresholds["teach_below"]):
        return PlannerDecision(
            "Teach", f"mastery {mastery:.3f} < teach_below {thresholds['teach_below']}"
        )

    band = (
        "practice" if mastery < float(thresholds["practice_below"])
        else "challenge" if mastery < float(thresholds["challenge_below"])
        else "pre-target"
    )
    return PlannerDecision(
        "MCQ",
        f"mastery {mastery:.3f} in {band} band; MCQ practice "
        "(Challenge/Review activities arrive in P4)",
    )
