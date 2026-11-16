"""The four tutors compared in the equal-budget experiment.

Each method is a pair: **where the explanation comes from** and **how the next
activity is chosen**.  Splitting the two is what makes the three baselines
comparable instead of three unrelated numbers:

======================  =================  ==============================  ==========================
method                  content            planner                         isolates
======================  =================  ==============================  ==========================
``llm_only``            ``llm_only``       one explain + one practice      -
``rag``                 ``rag``            one explain + one practice      the value of retrieval
``fixed``               ``rag``            explain -> practice -> practice  the value of a teaching loop
``adaptive``            ``rag``            the LLM agent (``decisions.py``)  the value of letting the agent decide
======================  =================  ==============================  ==========================

Reading the table as a chain:

* ``llm_only`` vs ``rag`` changes only the content source (same planner), so the
  difference is the retrieval effect.
* ``rag`` vs ``fixed`` changes only the planner (same content), so the
  difference is what a designed teaching loop adds over "retrieve and answer".
* ``fixed`` vs ``adaptive`` changes only who picks the next step: a frozen
  three-activity cycle that never looks at the learner, versus the model that
  reads mastery, the error book and the last few steps and chooses among the
  legal candidates.  This is the contribution the project claims.

The adaptive arm is not a re-implementation of the product: it calls the same
``LLMPlanner`` the server calls, and its choices are replayed from the frozen
decision log (``experiments/decisions.py``) so the comparison stays paired.

Every planner walks the same frozen bank, spends the same budget and answers
through the same :class:`SimulatedStudent`, so the only source of difference is
the (content, planner) pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from src.agent import policy as P

from .decisions import DecisionLog

EXPLAIN = P.EXPLAIN
PRACTICE = P.PRACTICE
REVIEW = P.REVIEW


class Planner(Protocol):
    def choose(
        self,
        topics: Sequence[P.TopicState],
        round_no: int,
        visited: Sequence[str],
        recent: Sequence[dict[str, Any]] = (),
    ) -> tuple[str, str] | None:
        """Return (topic_id, activity) or None when nothing is left to study."""


def candidates(topics: Sequence[P.TopicState]) -> list[P.TopicState]:
    """Topics that are assessed and not yet mastered, in dataset order."""
    return [t for t in topics if t.pretested and not P.is_mastered(t)]


class LLMPlannerArm:
    """The product agent, deciding through the frozen decision log.

    ``DecisionLog.choose`` replays the model's recorded choice for this exact
    state, or (while banking) asks the model once and records it.  Returning
    ``None`` means the model gave no usable answer - the session stops there,
    as the product ships no next step, and the rate is reported.
    """

    def __init__(self, log: DecisionLog) -> None:
        self.log = log

    def choose(self, topics, round_no, visited, recent=()):
        decision = self.log.choose(topics, round_no, visited, recent)
        return decision.as_tuple() if decision else None


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

    def choose(self, topics, round_no, visited, recent=()):
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

    def make_planner(self, log: DecisionLog | None = None) -> Planner:
        if self.planner == "agent":
            if log is None:
                raise ValueError("the adaptive arm needs a decision log")
            return LLMPlannerArm(log)
        if self.planner == "qa":
            return CyclicPlanner((EXPLAIN, PRACTICE))
        if self.planner == "fixed":
            return CyclicPlanner((EXPLAIN, PRACTICE, PRACTICE))
        raise ValueError(f"unknown planner {self.planner!r}")


METHODS: dict[str, Method] = {
    "llm_only": Method("llm_only", content="llm_only", planner="qa"),
    "rag": Method("rag", content="rag", planner="qa"),
    "fixed": Method("fixed", content="rag", planner="fixed"),
    "adaptive": Method("adaptive", content="rag", planner="agent"),
}
METHOD_ORDER: tuple[str, ...] = ("llm_only", "rag", "fixed", "adaptive")

LABELS: dict[str, str] = {
    "llm_only": "LLM-only tutor",
    "rag": "RAG tutor",
    "fixed": "Fixed-strategy tutor",
    "adaptive": "Adaptive agent (ours)",
}

SHORT: dict[str, str] = {
    "llm_only": "LLM-only",
    "rag": "RAG",
    "fixed": "Fixed",
    "adaptive": "Agent",
}


def make_method(name: str) -> Method:
    if name not in METHODS:
        raise ValueError(f"unknown method {name!r}")
    return METHODS[name]
