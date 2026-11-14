"""Minimal deterministic MCQ bank for the P3 self-test loop ONLY.

This is NOT the experiment question bank: the experiment harness builds the real knowledge points,
questions and predefined student profiles. P4 replaces these items with
LLM-generated, RAG-grounded questions. Keeping a tiny static bank here lets
the P3 closed loop run with zero LLM calls and be perfectly reproducible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MCQ:
    question_id: str
    prompt: str
    options: dict[str, str]
    correct_option: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


MCQ_BANK: dict[str, tuple[MCQ, ...]] = {
    "Bayes Theorem": (
        MCQ(
            "bayes-q1",
            "In Bayes' theorem, P(H|D) is called the ...",
            {"A": "prior", "B": "likelihood", "C": "posterior", "D": "evidence"},
            "C",
        ),
        MCQ(
            "bayes-q2",
            "Which term normalizes the numerator in Bayes' theorem?",
            {"A": "P(D|H)", "B": "P(H)", "C": "P(H|D)", "D": "P(D)"},
            "D",
        ),
        MCQ(
            "bayes-q3",
            "P(D|H) in Bayes' theorem is the ...",
            {"A": "posterior", "B": "likelihood", "C": "prior", "D": "marginal"},
            "B",
        ),
    ),
    "MDP": (
        MCQ(
            "mdp-q1",
            "Which quantity does value iteration compute?",
            {"A": "transition matrix", "B": "state-value function", "C": "reward table", "D": "policy gradient"},
            "B",
        ),
        MCQ(
            "mdp-q2",
            "The discount factor gamma controls ...",
            {"A": "state space size", "B": "immediate reward", "C": "weight of future rewards", "D": "action count"},
            "C",
        ),
    ),
    "__default__": (
        MCQ(
            "generic-q1",
            "A study loop updates its state after which step?",
            {"A": "Planning", "B": "Evaluation", "C": "Rendering", "D": "Logging"},
            "B",
        ),
        MCQ(
            "generic-q2",
            "Which component decides the next learning activity?",
            {"A": "Evaluator", "B": "Vector store", "C": "Planner", "D": "Event log"},
            "C",
        ),
    ),
}


def questions_for(topic: str, bank: dict[str, tuple[MCQ, ...]] | None = None) -> list[MCQ]:
    """Return topic questions, falling back to a topic-agnostic mini set."""
    source = MCQ_BANK if bank is None else bank
    return list(source.get(topic) or source["__default__"])
