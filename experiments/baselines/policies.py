"""The four arms compared in the experiment.

Only one of them is the product. ``adaptive`` delegates straight to
``src.agent.policy``, so the thing being measured here is literally the thing the
web app runs - not a copy of it. The other three are ablations designed to rule
out cheap explanations for any gain:

======================  ==========================================  =================================
arm                     what it does                                what it rules out
======================  ==========================================  =================================
``adaptive``            policy.plan_material(): reads the learner   (the method)
                        state, picks activity per topic, rolls
                        rounds when every candidate is served
``fixed``               explain -> practice -> practice, repeating, "just following a course plan works"
                        ignoring mastery / errors / streaks
``random``              uniform random activity on a random topic   "any variety of activities works"
``always_mcq``          practice only, never explains or reviews    "more quiz practice is all you need"
======================  ==========================================  =================================

Every arm is stopped by the same two conditions (all topics mastered, or the
activity budget is spent), and every arm answers through the same
``SimulatedStudent``, so differences can only come from the activity policy.
"""

from __future__ import annotations

import random
from typing import Any, Protocol, Sequence

from src.agent import policy as P

EXPLAIN = P.EXPLAIN
PRACTICE = P.PRACTICE
REVIEW = P.REVIEW


class Arm(Protocol):
    name: str

    def choose(
        self,
        topics: Sequence[P.TopicState],
        round_no: int,
        visited: Sequence[str],
    ) -> tuple[str, str] | None:
        """Return (topic_id, activity) or None when the material is finished."""


def candidates(topics: Sequence[P.TopicState]) -> list[P.TopicState]:
    """Topics that are assessed and not yet mastered, in dataset order."""
    return [t for t in topics if t.pretested and not P.is_mastered(t)]


class AdaptiveArm:
    """The product policy, unmodified."""

    name = "adaptive"

    def choose(
        self,
        topics: Sequence[P.TopicState],
        round_no: int,
        visited: Sequence[str],
    ) -> tuple[str, str] | None:
        plan = P.plan_material(topics, round_no=round_no, visited=visited)
        step = plan.get("next")
        if not step:
            return None
        return str(step["topic_id"]), str(step["activity"])


class FixedArm:
    """A frozen syllabus: explain, practice, practice - forever, in dataset order."""

    name = "fixed"
    PATTERN = (EXPLAIN, PRACTICE, PRACTICE)

    def __init__(self) -> None:
        self.step = 0

    def choose(self, topics, round_no, visited):
        pool = candidates(topics)
        if not pool:
            return None
        topic = pool[self.step % len(pool)]
        activity = self.PATTERN[self.step % len(self.PATTERN)]
        self.step += 1
        return topic.topic_id, activity


class RandomArm:
    """Uniform random topic and activity - the lower bound."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed * 31 + 17)

    def choose(self, topics, round_no, visited):
        pool = candidates(topics)
        if not pool:
            return None
        topic = self.rng_choice(pool)
        activity = self.rng_choice((EXPLAIN, PRACTICE, REVIEW))
        return topic.topic_id, activity

    def rng_choice(self, seq: Sequence[Any]) -> Any:
        return seq[self._rng.randrange(len(seq))]


class AlwaysMcqArm:
    """Drill only: no explanations, no error review."""

    name = "always_mcq"

    def __init__(self) -> None:
        self.step = 0

    def choose(self, topics, round_no, visited):
        pool = candidates(topics)
        if not pool:
            return None
        topic = pool[self.step % len(pool)]
        self.step += 1
        return topic.topic_id, PRACTICE


def make_arm(name: str, seed: int = 0) -> Arm:
    if name == "adaptive":
        return AdaptiveArm()
    if name == "fixed":
        return FixedArm()
    if name == "random":
        return RandomArm(seed)
    if name == "always_mcq":
        return AlwaysMcqArm()
    raise ValueError(f"unknown arm {name!r}")


ARM_ORDER: tuple[str, ...] = ("adaptive", "fixed", "random", "always_mcq")
