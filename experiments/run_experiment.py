"""E1: equal-budget comparison of the adaptive agent against three ablations.

Protocol
--------
* Dataset: ``experiments/data/bank.json`` - 8 knowledge points parsed from the
  COMP 5511 Lecture 1 slides, 4 practice items and 3 held-out probe items each.
* A **session** = one simulated learner studying the whole material.
* Pre-test: one probe item per topic seeds the agent's mastery (exactly what the
  product does), so the agent never starts from oracle knowledge.
* Then the arm chooses activities until the budget is spent or every topic is
  mastered.
* The post-test score is the learner's expected score on the same held-out
  probes. Because :class:`SimulatedStudent` has no item memory, reusing probes
  cannot inflate the post score (documented in ``simulator.py``).
* Budget is **equal for all arms** (default 32 activities = 4 per topic). This is
  deliberate: the policy's own stop rule of ``mastery >= 80`` is rarely satisfied
  within a session, so "activities to target" would be censored for every arm.
  Comparing gain at a fixed budget is the fair version.

Result files
------------
``experiments/results/e1_main.json`` - protocol, per-episode rows, aggregates,
paired bootstrap intervals.

Usage
-----
    python -m experiments.run_experiment
    python -m experiments.run_experiment --seeds 8 --budget 32
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

from src.agent import policy as P

from .bank import Bank, load_bank
from .baselines.policies import ARM_ORDER, make_arm
from .simulator import PROFILE_ORDER, PROFILES, SimulatedStudent

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUT = RESULTS_DIR / "e1_main.json"
CHECKPOINTS = (8, 16, 24, 32)
LEARNED_AT = 0.75          # probe score that counts as "learned" (outcome-based)
BOOTSTRAP = 2000
BOOTSTRAP_SEED = 20261114


# --------------------------------------------------------------------------
# one session
# --------------------------------------------------------------------------

def run_session(arm_name: str, profile_name: str, seed: int, bank: Bank, budget: int) -> dict[str, Any]:
    specs = bank.topics
    profile = PROFILES[profile_name]
    student = SimulatedStudent(specs, profile, seed)
    index_of = {t.topic_id: i for i, t in enumerate(specs)}
    spec_of = {t.topic_id: t for t in specs}

    topics: list[P.TopicState] = [
        P.TopicState(
            topic_id=t.topic_id,
            name=t.name,
            explain_pages=t.explain_pages,
            quiz_count=t.quiz_count,
        )
        for t in specs
    ]

    # Pre-test: one held-out item per topic sets the starting mastery.
    for i, spec in enumerate(specs):
        correct = student.answer(spec.topic_id, spec.probe[0])
        topics[i] = P.apply_pre_test(topics[i], correct)
    pre = student.probe_score()

    arm = make_arm(arm_name, seed)
    round_no, visited = 1, []
    activities: list[str] = []
    curve: dict[int, float] = {}
    stopped = "budget"

    for step in range(1, budget + 1):
        choice = arm.choose(topics, round_no, visited)
        if choice is None:
            stopped = "finished"
            break
        topic_id, activity = choice
        state = topics[index_of[topic_id]]
        spec = spec_of[topic_id]

        accuracy = 0.0
        had_errors = bool(state.mistakes)
        if activity == P.PRACTICE:
            answers, accuracy = student.answer_round(spec)
            outcome: dict[str, Any] = {
                "answers": answers,
                "total": len(answers),
                "correct": sum(1 for a in answers if a["ok"]),
            }
            quiz = [item.as_quiz() for item in spec.practice]
        else:
            outcome = {}
            quiz = None

        student.study(topic_id, activity, accuracy=accuracy, had_errors=had_errors)
        result = P.apply_result(state, activity, outcome, quiz=quiz)
        topics[index_of[topic_id]] = result.state
        round_no, visited = P.advance_round(topics, round_no, visited, topic_id)
        activities.append(activity)

        if step in CHECKPOINTS and step <= budget:
            curve[step] = round(student.probe_score(), 4)

    post = student.probe_score()
    # A session that stops early (all topics mastered) has no reading at the
    # later checkpoints. Leaving those cells empty would bias the curve: the
    # sessions missing from checkpoint 32 are exactly the fast finishers, so
    # the arm that finishes most often would look worst. Carry the final score
    # forward instead - at activity c the learner's standing is simply where
    # they stopped.
    if stopped == "finished":
        for cp in CHECKPOINTS:
            if cp <= budget and cp not in curve:
                curve[cp] = round(post, 4)
    truth = [student.probe_score(t.topic_id) for t in specs]
    agent = [t.mastery / 100.0 for t in topics]
    mae = sum(abs(a - b) for a, b in zip(agent, truth)) / len(truth)

    return {
        "arm": arm_name,
        "profile": profile_name,
        "seed": seed,
        "pre": round(pre, 4),
        "post": round(post, 4),
        "learning_gain": round(post - pre, 4),
        "normalized_gain": round((post - pre) / (1.0 - pre), 4) if pre < 0.999 else 0.0,
        "learned_topics": sum(1 for v in truth if v >= LEARNED_AT),
        "agent_mastery": round(statistics.fmean(agent), 4),
        "true_mastery": round(statistics.fmean(truth), 4),
        "estimate_mae": round(mae, 4),
        "steps": len(activities),
        "stop_reason": stopped,
        "rounds": round_no,
        "mix": {a: activities.count(a) for a in (P.EXPLAIN, P.PRACTICE, P.REVIEW)},
        "curve": curve,
        "activities": activities,
    }


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _sd(values: Sequence[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def aggregate(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for arm in ARM_ORDER:
        arm_rows = [r for r in rows if r["arm"] == arm]
        if not arm_rows:
            continue
        gains = [r["learning_gain"] for r in arm_rows]
        out.append({
            "arm": arm,
            "episodes": len(arm_rows),
            "pre": _mean([r["pre"] for r in arm_rows]),
            "post": _mean([r["post"] for r in arm_rows]),
            "learning_gain": _mean(gains),
            "learning_gain_sd": _sd(gains),
            "normalized_gain": _mean([r["normalized_gain"] for r in arm_rows]),
            "learned_topics": _mean([r["learned_topics"] for r in arm_rows]),
            "agent_mastery": _mean([r["agent_mastery"] for r in arm_rows]),
            "true_mastery": _mean([r["true_mastery"] for r in arm_rows]),
            "estimate_mae": _mean([r["estimate_mae"] for r in arm_rows]),
            "steps": _mean([r["steps"] for r in arm_rows]),
            "finished_rate": _mean([1.0 if r["stop_reason"] == "finished" else 0.0 for r in arm_rows]),
            "mix": {
                a: _mean([r["mix"][a] for r in arm_rows])
                for a in (P.EXPLAIN, P.PRACTICE, P.REVIEW)
            },
        })
    return out


def by_profile(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for arm in ARM_ORDER:
        for profile in PROFILE_ORDER:
            subset = [r for r in rows if r["arm"] == arm and r["profile"] == profile]
            if not subset:
                continue
            out.append({
                "arm": arm,
                "profile": profile,
                "learning_gain": _mean([r["learning_gain"] for r in subset]),
                "post": _mean([r["post"] for r in subset]),
                "learned_topics": _mean([r["learned_topics"] for r in subset]),
            })
    return out


def _bootstrap_ci(diffs: Sequence[float]) -> tuple[float, float]:
    if len(diffs) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(BOOTSTRAP_SEED)
    means = []
    for _ in range(BOOTSTRAP):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return (lo, hi)


def paired_vs_adaptive(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paired by (profile, seed): the same learner, the same dice, one difference."""
    key = lambda r: (r["profile"], r["seed"])
    base = {key(r): r["learning_gain"] for r in rows if r["arm"] == "adaptive"}
    out = []
    for arm in ARM_ORDER:
        if arm == "adaptive":
            continue
        other = {key(r): r["learning_gain"] for r in rows if r["arm"] == arm}
        common = sorted(set(base) & set(other))
        diffs = [base[k] - other[k] for k in common]
        if not diffs:
            continue
        lo, hi = _bootstrap_ci(diffs)
        out.append({
            "arm": arm,
            "pairs": len(diffs),
            "delta_gain": statistics.fmean(diffs),
            "ci_low": lo,
            "ci_high": hi,
            "wins": sum(1 for d in diffs if d > 0),
            "ties": sum(1 for d in diffs if d == 0),
        })
    return out


# --------------------------------------------------------------------------
# self-check + printing
# --------------------------------------------------------------------------

def self_check(totals: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> list[str]:
    """Fail loudly if the arms cannot be told apart.

    This is the regression guard for the original defect: when the learner's
    answers were replayed from a fixed list, every arm scored identically no
    matter what the agent chose.
    """
    problems: list[str] = []
    gains = [round(t["learning_gain"], 9) for t in totals]
    if len(set(gains)) == 1:
        problems.append(
            "all arms produced the same learning gain - the policy does not reach the outcome"
        )
    mixes = {tuple(sorted(t["mix"].items())) for t in totals}
    if len(mixes) == 1:
        problems.append("all arms used the same activity mix - the arms are not distinct")

    sequences: dict[str, set[tuple[str, ...]]] = {}
    for row in rows:
        sequences.setdefault(row["arm"], set()).add(tuple(row["activities"]))
    if len(sequences) > 1 and len({frozenset(v) for v in sequences.values()}) == 1:
        problems.append("all arms produced identical activity sequences")
    return problems


def print_report(rows, totals, profiles, pairs) -> None:
    print()
    print("== equal-budget comparison ==")
    header = f"{'arm':<12}{'pre':>7}{'post':>7}{'gain':>8}{'nGain':>8}{'learned':>9}{'steps':>7}{'mastery':>9}{'MAE':>7}"
    print(header)
    print("-" * len(header))
    for t in totals:
        print(
            f"{t['arm']:<12}{t['pre']:>7.3f}{t['post']:>7.3f}{t['learning_gain']:>8.3f}"
            f"{t['normalized_gain']:>8.3f}{t['learned_topics']:>9.1f}{t['steps']:>7.1f}"
            f"{t['agent_mastery']:>9.3f}{t['estimate_mae']:>7.3f}"
        )

    print()
    print("== activity mix (mean count per session) ==")
    print(f"{'arm':<12}{'explain':>9}{'practice':>10}{'review':>8}")
    for t in totals:
        print(
            f"{t['arm']:<12}{t['mix'][P.EXPLAIN]:>9.1f}"
            f"{t['mix'][P.PRACTICE]:>10.1f}{t['mix'][P.REVIEW]:>8.1f}"
        )

    print()
    print("== learning gain by profile ==")
    profiles_sorted = sorted({p["profile"] for p in profiles})
    print(f"{'arm':<12}" + "".join(f"{p:>12}" for p in profiles_sorted))
    for arm in ARM_ORDER:
        cells = []
        for p in profiles_sorted:
            row = next((x for x in profiles if x["arm"] == arm and x["profile"] == p), None)
            cells.append(f"{row['learning_gain']:>12.3f}" if row else f"{'-':>12}")
        print(f"{arm:<12}" + "".join(cells))

    if pairs:
        print()
        print("== paired difference vs adaptive (same profile + seed) ==")
        print(f"{'baseline':<12}{'delta':>8}{'95% CI':>18}{'wins':>7}")
        for p in pairs:
            ci = f"[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}]"
            print(f"{p['arm']:<12}{p['delta_gain']:>+8.3f}{ci:>18}{p['wins']:>4}/{p['pairs']}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E1 adaptive vs baselines")
    parser.add_argument("--seeds", type=int, default=5, help="learners per (arm, profile)")
    parser.add_argument("--budget", type=int, default=32, help="activities per session")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    bank = load_bank()
    rows: list[dict[str, Any]] = []
    for arm in ARM_ORDER:
        for profile in PROFILE_ORDER:
            for seed in range(1, args.seeds + 1):
                rows.append(run_session(arm, profile, seed, bank, args.budget))

    totals = aggregate(rows)
    profiles = by_profile(rows)
    pairs = paired_vs_adaptive(rows)
    print_report(rows, totals, profiles, pairs)

    problems = self_check(totals, rows)
    if problems:
        print()
        for problem in problems:
            print(f"[FAIL] {problem}")

    payload = {
        "protocol": {
            "experiment": "E1",
            "dataset": "experiments/data/bank.json",
            "topics": len(bank.topics),
            "items": bank.item_count,
            "budget": args.budget,
            "seeds": args.seeds,
            "learned_at": LEARNED_AT,
            "checkpoints": [c for c in CHECKPOINTS if c <= args.budget],
            "llm": "off (frozen bank, no network)",
            "profiles": {name: PROFILES[name].as_dict() for name in PROFILE_ORDER},
        },
        "totals": totals,
        "by_profile": profiles,
        "paired": pairs,
        "episodes": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"-> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
