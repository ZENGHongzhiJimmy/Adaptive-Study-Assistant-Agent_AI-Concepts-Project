"""Rule-based Adaptive Planner (P3 vertical slice, extended in P4).

P3 (``extended=False``, the default) deliberately supports only ``Teach``,
``MCQ`` and ``Done`` so the first closed loop is fully reproducible without an
LLM. P4 (``extended=True``) adds the remaining two activities, ``Challenge``
and ``Review``:

* ``Review`` replaces re-teaching after repeated errors and focuses on the
  recent_errors window. Like Teach it is not scored (mastery unchanged).
* ``Challenge`` is a harder *objective* MCQ for the upper mastery band. It is
  scored by the same deterministic evaluator, so Learning Gain stays clean.

Non-scored activities are always followed by a scored one, which together with
``max_steps`` and ``target_mastery`` keeps the back-edge terminating. Thresholds
and the error window come from config.yaml - never hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NON_SCORED_ACTIONS = frozenset({"Teach", "Review"})


@dataclass(frozen=True)
class PlannerDecision:
    action: str  # Teach | MCQ | Challenge | Review | Done
    reason: str
    reached_target: bool = False


def choose_action(
    *,
    mastery: float,
    recent_errors: list[Any] | None,
    last_action: str | None,
    config: dict[str, Any],
    extended: bool = False,
    last_was_correct: bool | None = None,
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

    if extended:
        # A Teach/Review step does not change mastery; force a scored step next.
        if last_action in NON_SCORED_ACTIONS:
            if mastery >= float(thresholds["practice_below"]):
                return PlannerDecision(
                    "Challenge",
                    f"{last_action} was just shown; a scored Challenge is mandatory next",
                )
            return PlannerDecision(
                "MCQ", f"{last_action} was just shown; practice is mandatory next"
            )
        # recent_errors is a bounded window and is NOT cleared by a correct
        # answer (P2). Without this guard a student who once made 2 mistakes
        # would get a Review every other step forever, starving practice and
        # Challenge. Review is triggered by a *new* mistake, not by old ones.
        if errors >= trigger and last_was_correct is not True:
            return PlannerDecision(
                "Review",
                f"{errors} recent errors >= review_trigger_errors {trigger}: "
                "Review focuses on the recent_errors weak points",
            )
        if mastery < float(thresholds["teach_below"]):
            return PlannerDecision(
                "Teach", f"mastery {mastery:.3f} < teach_below {thresholds['teach_below']}"
            )
        if mastery >= float(thresholds["practice_below"]):
            return PlannerDecision(
                "Challenge",
                f"mastery {mastery:.3f} >= practice_below {thresholds['practice_below']}: "
                "harder objective question",
            )
        return PlannerDecision(
            "MCQ",
            f"mastery {mastery:.3f} < practice_below {thresholds['practice_below']}: "
            "standard practice",
        )

    # --- P3 behaviour, intentionally unchanged (46 offline tests depend on it)
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
