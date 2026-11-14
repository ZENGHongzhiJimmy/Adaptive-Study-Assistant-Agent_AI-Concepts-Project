"""The four tutors compared in E1/E2.

Each method is a pair: **where the explanation comes from** and **how the next
activity is chosen**. Splitting the two is what makes the proposal's three
baselines comparable instead of three unrelated numbers:

======================  =================  ==========================  ==========
method                  content            planner                     tests
======================  =================  ==========================  ==========
``llm_only``            ``llm_only``       one explain + one practice  the value of retrieval
``rag``                 ``rag``            one explain + one practice  + the value of a teaching loop
``fixed``               ``rag``            explain -> practice -> practice, frozen syllabus
``adaptive``            ``rag``            ``policy.plan_material()``  + the value of adapting
======================  =================  ==========================  ==========

Reading the table as a chain:

* ``llm_only`` vs ``rag`` changes only the content source (same planner), so the
  difference is the retrieval effect.
* ``rag`` vs ``fixed`` changes only the planner (same content), so the
  difference is what a designed teaching loop adds over "retrieve and answer".
* ``fixed`` vs ``adaptive`` changes only whether the learner state is consulted,
  which is the contribution the proposal claims.

Every planner walks the same frozen bank, spends the same budget and answers
through the same :class:`SimulatedStudent`, so the only source of difference is
the (content, planner) pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from src.agent import policy as P

EXPLAIN = P.EXPLAIN
PRACTICE = P.PRACTICE
REVIEW = P.REVIEW


class Planner(Protocol):
    def choose(
        self,
        topics: Sequence[P.TopicState],
        round_no: int,
        visited: Sequence[str],
    ) -> tuple[str, str] | None:
        """Return (topic_id, activity) or None when nothing is left to study."""


def candidates(topics: Sequence[P.TopicState]) -> list[P.TopicState]:
    """Topics that are assessed and not yet mastered, in dataset order."""
    return [t for t in topics if t.pretested and not P.is_mastered(t)]


class AdaptivePlanner:
    """The product policy, unmodified - not a copy of it."""

    def choose(self, topics, round_no, visited):
        plan = P.plan_material(topics, round_no=round_no, visited=visited)
        step = plan.get("next")
        if not step:
            return None
        return str(step["topic_id"]), str(step["activity"])


class CyclicPlanner:
    """A frozen activity pattern over the remaining topics, in dataset order.

    ``pattern=(explain, practice)`` is the "retrieve and answer" tutor: every
    topic gets one explanation and one quiz, then the cycle repeats. It has no
    notion of errors, no review, no re-ranking.

    ``pattern=(explain, practice, practice)`` is the fixed-strategy tutor: a
    designed lesson loop, but still identical for every learner.
    """

    def __init__(self, pattern: Sequence[str]) -> None:
        self.pattern = tuple(pattern)
        self.step = 0

    def choose(self, topics, round_no, visited):
        pool = candidates(topics)
        if not pool:
            return None
        topic = pool[(self.step // len(self.pattern)) % len(pool)]
        activity = self.pattern[self.step % len(self.pattern)]
        self.step += 1
        return topic.topic_id, activity


@dataclass(frozen=True)
class Method:
    name: str
    content: str
    planner: str

    def make_planner(self) -> Planner:
        if self.planner == "adaptive":
            return AdaptivePlanner()
        if self.planner == "qa":
            return CyclicPlanner((EXPLAIN, PRACTICE))
        if self.planner == "fixed":
            return CyclicPlanner((EXPLAIN, PRACTICE, PRACTICE))
        raise ValueError(f"unknown planner {self.planner!r}")


METHODS: dict[str, Method] = {
    "llm_only": Method("llm_only", content="llm_only", planner="qa"),
    "rag": Method("rag", content="rag", planner="qa"),
    "fixed": Method("fixed", content="rag", planner="fixed"),
    "adaptive": Method("adaptive", content="rag", planner="adaptive"),
}
METHOD_ORDER: tuple[str, ...] = ("llm_only", "rag", "fixed", "adaptive")

LABELS: dict[str, str] = {
    "llm_only": "LLM-only tutor",
    "rag": "RAG tutor",
    "fixed": "Fixed-strategy tutor",
    "adaptive": "Adaptive (ours)",
}


def make_method(name: str) -> Method:
    if name not in METHODS:
        raise ValueError(f"unknown method {name!r}")
    return METHODS[name]
