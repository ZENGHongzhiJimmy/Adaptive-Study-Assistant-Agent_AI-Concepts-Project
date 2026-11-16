"""One simulated study session: library call plus a planner.

This is the only place where the learner, the bank and a planner meet.  Both
``collect_decisions`` (which asks the model) and ``run_experiment`` (which
replays the frozen answers) call this function, so the adaptive arm cannot
drift between the two stages - the banked decision and the replayed one are
produced by exactly the same loop.

What is held fixed for every method
-----------------------------------
* the learner (same profile, same seed, same hidden abilities),
* the pre-test (one probe item per topic seeds the agent's mastery),
* the bank (same practice items, same held-out probes),
* the budget (same number of activities).

What differs is only the (content, planner) pair, which is the point of the
comparison.
"""

from __future__ import annotations

import statistics
from typing import Any, Sequence

from src.agent import policy as P

from .bank import Bank
from .decisions import DecisionLog
from .methods import Method
from .protocol import LEARNED_AT
from .simulator import PROFILES, SimulatedStudent


def run_session(
    method: Method,
    profile_name: str,
    seed: int,
    bank: Bank,
    budget: int,
    quality: dict[str, dict[str, float]],
    log: DecisionLog | None = None,
) -> dict[str, Any]:
    """Study the whole material once, then measure the held-out probe score."""
    specs = bank.topics
    student = SimulatedStudent(specs, PROFILES[profile_name], seed)
    topics = [
        P.TopicState(
            topic_id=t.topic_id,
            name=t.name,
            explain_pages=t.explain_pages,
            quiz_count=t.quiz_count,
        )
        for t in specs
    ]
    for index, spec in enumerate(specs):
        correct = student.answer(spec.topic_id, spec.probe[0])
        topics[index] = P.apply_pre_test(topics[index], correct)
    pre = student.probe_score()

    planner = method.make_planner(log)
    round_no, visited = 1, []
    recent: list[dict[str, Any]] = []
    activities: list[str] = []
    plan_log: list[dict[str, Any]] = []
    stopped = "budget"

    for step in range(1, budget + 1):
        choice = planner.choose(topics, round_no, visited, recent)
        if choice is None:
            # Two very different reasons to stop, and they must not be confused:
            # nothing is left to study (the agent finished the material) versus
            # the model produced no usable answer.  Only the second one is a
            # failure, and only the second one belongs in the failure rate.
            ready = [
                t for t in topics
                if t.pretested and not P.is_mastered(t) and t.quiz_count > 0
            ]
            stopped = "finished" if not ready else "no_decision"
            break
        topic_id, activity = choice
        index = next(i for i, t in enumerate(topics) if t.topic_id == topic_id)
        state, spec = topics[index], next(s for s in specs if s.topic_id == topic_id)

        accuracy, quiz, outcome = 0.0, None, {}
        had_errors = bool(state.mistakes)
        if activity == P.PRACTICE:
            answers, accuracy = student.answer_round(spec)
            outcome = {
                "answers": answers,
                "total": len(answers),
                "correct": sum(1 for a in answers if a["ok"]),
            }
            quiz = [item.as_quiz() for item in spec.practice]

        content_quality = quality.get(topic_id, {}).get(method.content, 0.0)
        student.study(
            topic_id, activity,
            accuracy=accuracy, had_errors=had_errors, quality=content_quality,
        )
        before = state.mastery
        result = P.apply_result(state, activity, outcome, quiz=quiz)
        topics[index] = result.state

        # The last few steps go into the planner's context, exactly as the
        # product shows them: without this the model cannot tell what its own
        # previous choice produced.
        recent.insert(0, {
            "topic_id": topic_id,
            "activity": activity,
            "mastery_before": before,
            "mastery_after": result.state.mastery,
            "outcome": result.note,
        })
        del recent[4:]

        round_no, visited = P.advance_round(topics, round_no, visited, topic_id)
        activities.append(activity)
        plan_log.append({
            "step": step,
            "topic_id": topic_id,
            "activity": activity,
            "mastery_before": int(round(before)),
            "mastery_after": int(round(result.state.mastery)),
            "wrong": result.state.wrong_count,
            "note": result.note,
        })

    post = student.probe_score()
    truth = [student.probe_score(t.topic_id) for t in specs]
    learned = sum(1 for v in truth if v >= LEARNED_AT)
    agent = [t.mastery / 100.0 for t in topics]

    return {
        "method": method.name,
        "content": method.content,
        "planner": method.planner,
        "profile": profile_name,
        "seed": seed,
        "pre": round(pre, 4),
        "post": round(post, 4),
        "learning_gain": round(post - pre, 4),
        "normalized_gain": round((post - pre) / (1.0 - pre), 4) if pre < 0.999 else 0.0,
        "mastered_topics": learned,
        "mastered_rate": round(learned / len(specs), 4),
        "steps": len(activities),
        "efficiency": round(learned / len(activities), 4) if activities else 0.0,
        "estimate_mae": round(
            statistics.fmean([abs(a - b) for a, b in zip(agent, truth)]), 4
        ),
        "stop_reason": stopped,
        "mix": {a: activities.count(a) for a in (P.EXPLAIN, P.PRACTICE, P.REVIEW)},
        "activities": activities,
        "trace": plan_log,
    }


def method_rows(
    method: Method,
    profiles: Sequence[str],
    seeds: Sequence[int],
    bank: Bank,
    budget: int,
    quality: dict[str, dict[str, float]],
    log: DecisionLog | None = None,
) -> list[dict[str, Any]]:
    return [
        run_session(method, profile, seed, bank, budget, quality, log)
        for profile in profiles
        for seed in seeds
    ]


__all__ = ["LEARNED_AT", "method_rows", "run_session"]
