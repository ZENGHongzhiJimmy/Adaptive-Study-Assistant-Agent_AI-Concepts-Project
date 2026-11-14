"""Main experiment: equal-budget comparison of the four tutors from the proposal.

Protocol
--------
* Dataset: ``experiments/data/bank.json`` - 8 knowledge points from the
  committed Lecture 1 PDF, 4 practice items and 3 held-out probes each.
* Content: ``experiments/data/materials.json`` - the LLM-only and RAG
  explanations, generated once by ``experiments.content`` and scored for
  coverage of the probe content. The simulator scales the gain of an
  explanation by that coverage: an explanation that never mentions what the
  post-test asks about cannot teach it.
* A session = one simulated learner studying the whole material.
* Pre-test: one probe item per topic seeds the agent's mastery (as in the
  product), so no method starts from oracle knowledge.
* Then the method's planner chooses activities until the budget is spent or
  every topic is mastered.
* The post-test is the learner's expected score on the same held-out probes.
  :class:`SimulatedStudent` has no item memory, so reusing probes cannot inflate
  the post score (see ``simulator.py``).
* Budget is equal for every method (default 32 activities = 4 per topic), which
  is what makes the gain comparison fair when methods stop at different times.

Methods (see ``methods.py``) form a chain, each step changing one thing:
``llm_only`` -> ``rag`` (retrieval) -> ``fixed`` (teaching loop) ->
``adaptive`` (learner state).

Result files
------------
``experiments/results/e1_main.json`` - protocol, per-episode rows, aggregates,
paired bootstrap intervals, content cost summary.

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
from .content import CONDITIONS, load_materials, quality_by_condition
from .methods import LABELS, METHOD_ORDER, METHODS, Method, make_method
from .simulator import PROFILE_ORDER, PROFILES, SimulatedStudent

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUT = RESULTS_DIR / "e1_main.json"
CHECKPOINTS = (8, 16, 24, 32)
LEARNED_AT = 0.75
BOOTSTRAP = 2000
BOOTSTRAP_SEED = 20261114


# --------------------------------------------------------------------------
# one session
# --------------------------------------------------------------------------

def run_session(
    method_name: str,
    profile_name: str,
    seed: int,
    bank: Bank,
    budget: int,
    quality: dict[str, dict[str, float]],
) -> dict[str, Any]:
    method: Method = make_method(method_name)
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

    for i, spec in enumerate(specs):
        correct = student.answer(spec.topic_id, spec.probe[0])
        topics[i] = P.apply_pre_test(topics[i], correct)
    pre = student.probe_score()

    planner = method.make_planner()
    round_no, visited = 1, []
    activities: list[str] = []
    curve: dict[int, float] = {}
    stopped = "budget"
    reached_all_at: int | None = None

    for step in range(1, budget + 1):
        choice = planner.choose(topics, round_no, visited)
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

        content_quality = quality.get(topic_id, {}).get(method.content, 0.0)
        student.study(
            topic_id, activity,
            accuracy=accuracy, had_errors=had_errors, quality=content_quality,
        )
        result = P.apply_result(state, activity, outcome, quiz=quiz)
        topics[index_of[topic_id]] = result.state
        round_no, visited = P.advance_round(topics, round_no, visited, topic_id)
        activities.append(activity)

        if step in CHECKPOINTS and step <= budget:
            curve[step] = round(student.probe_score(), 4)
        if reached_all_at is None and all(
            student.probe_score(t.topic_id) >= LEARNED_AT for t in specs
        ):
            reached_all_at = step

    post = student.probe_score()
    # Fast finishers have no reading at later checkpoints; carry the final score
    # forward so the curve does not reward being slow.
    if stopped == "finished":
        for cp in CHECKPOINTS:
            if cp <= budget and cp not in curve:
                curve[cp] = round(post, 4)

    truth = [student.probe_score(t.topic_id) for t in specs]
    agent = [t.mastery / 100.0 for t in topics]
    mae = sum(abs(a - b) for a, b in zip(agent, truth)) / len(truth)

    return {
        "method": method_name,
        "content": method.content,
        "planner": method.planner,
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
        "activities_to_target": reached_all_at if reached_all_at is not None else budget,
        # Learning efficiency: learned topics per activity spent. Unlike
        # activities_to_target (censored at the budget for every method), this
        # still separates methods that stop early from those that do not.
        "efficiency": round(
            sum(1 for v in truth if v >= LEARNED_AT) / len(activities), 4
        ) if activities else 0.0,
        "reached_all": reached_all_at is not None,
        "stop_reason": stopped,
        "rounds": round_no,
        "mix": {a: activities.count(a) for a in (P.EXPLAIN, P.PRACTICE, P.REVIEW)},
        "curve": curve,
        "content_quality": round(
            statistics.fmean([quality[t.topic_id][method.content] for t in specs]), 4
        ) if quality else 0.0,
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
    for method in METHOD_ORDER:
        method_rows = [r for r in rows if r["method"] == method]
        if not method_rows:
            continue
        gains = [r["learning_gain"] for r in method_rows]
        out.append({
            "method": method,
            "content": METHODS[method].content,
            "planner": METHODS[method].planner,
            "episodes": len(method_rows),
            "pre": _mean([r["pre"] for r in method_rows]),
            "post": _mean([r["post"] for r in method_rows]),
            "learning_gain": _mean(gains),
            "learning_gain_sd": _sd(gains),
            "normalized_gain": _mean([r["normalized_gain"] for r in method_rows]),
            "learned_topics": _mean([r["learned_topics"] for r in method_rows]),
            "agent_mastery": _mean([r["agent_mastery"] for r in method_rows]),
            "true_mastery": _mean([r["true_mastery"] for r in method_rows]),
            "estimate_mae": _mean([r["estimate_mae"] for r in method_rows]),
            "steps": _mean([r["steps"] for r in method_rows]),
            "activities_to_target": _mean([r["activities_to_target"] for r in method_rows]),
            "efficiency": _mean([r["efficiency"] for r in method_rows]),
            "target_rate": _mean([1.0 if r["reached_all"] else 0.0 for r in method_rows]),
            "finished_rate": _mean([1.0 if r["stop_reason"] == "finished" else 0.0 for r in method_rows]),
            "content_quality": _mean([r["content_quality"] for r in method_rows]),
            "mix": {
                a: _mean([r["mix"][a] for r in method_rows])
                for a in (P.EXPLAIN, P.PRACTICE, P.REVIEW)
            },
        })
    return out


def by_profile(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for method in METHOD_ORDER:
        for profile in PROFILE_ORDER:
            subset = [r for r in rows if r["method"] == method and r["profile"] == profile]
            if not subset:
                continue
            out.append({
                "method": method,
                "profile": profile,
                "learning_gain": _mean([r["learning_gain"] for r in subset]),
                "post": _mean([r["post"] for r in subset]),
                "learned_topics": _mean([r["learned_topics"] for r in subset]),
                "activities_to_target": _mean([r["activities_to_target"] for r in subset]),
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
    """Paired by (profile, seed): same learner, same dice, one difference."""
    key = lambda r: (r["profile"], r["seed"])
    base = {key(r): r["learning_gain"] for r in rows if r["method"] == "adaptive"}
    out = []
    for method in METHOD_ORDER:
        if method == "adaptive":
            continue
        other = {key(r): r["learning_gain"] for r in rows if r["method"] == method}
        common = sorted(set(base) & set(other))
        diffs = [base[k] - other[k] for k in common]
        if not diffs:
            continue
        lo, hi = _bootstrap_ci(diffs)
        out.append({
            "method": method,
            "pairs": len(diffs),
            "delta_gain": statistics.fmean(diffs),
            "ci_low": lo,
            "ci_high": hi,
            "wins": sum(1 for d in diffs if d > 0),
            "ties": sum(1 for d in diffs if d == 0),
        })
    return out


def cost_summary(materials: dict[str, Any]) -> dict[str, Any]:
    """Per-condition LLM cost of preparing the whole material."""
    out: dict[str, Any] = {}
    for condition in CONDITIONS:
        tokens, latency, calls = 0, [], 0
        for topic in materials["topics"]:
            block = topic.get(condition) or {}
            tokens += int(block.get("tokens") or 0)
            latency.append(float(block.get("latency_ms") or 0.0))
            calls += int(block.get("attempts") or 1)
        out[condition] = {
            "calls": calls,
            "tokens": tokens,
            "mean_latency_ms": round(statistics.fmean(latency), 1) if latency else 0.0,
            "tokens_per_topic": round(tokens / len(materials["topics"]), 1) if materials["topics"] else 0.0,
        }
    return out


# --------------------------------------------------------------------------
# self-check + printing
# --------------------------------------------------------------------------

def self_check(totals: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> list[str]:
    """Fail loudly if the methods cannot be told apart.

    Kept from the original harness: when answers were replayed from a fixed
    list, every method scored identically no matter what the agent chose.
    """
    problems: list[str] = []
    gains = [round(t["learning_gain"], 9) for t in totals]
    if len(set(gains)) == 1:
        problems.append("all methods produced the same learning gain - the planner does not reach the outcome")
    mixes = {tuple(sorted(t["mix"].items())) for t in totals}
    if len(mixes) == 1:
        problems.append("all methods used the same activity mix - the methods are not distinct")

    sequences: dict[str, set[tuple[str, ...]]] = {}
    for row in rows:
        sequences.setdefault(row["method"], set()).add(tuple(row["activities"]))
    if len(sequences) > 1 and len({frozenset(v) for v in sequences.values()}) == 1:
        problems.append("all methods produced identical activity sequences")
    return problems


def print_report(totals, profiles, pairs, costs) -> None:
    print()
    print("== equal-budget comparison ==")
    header = (f"{'method':<12}{'pre':>7}{'post':>7}{'gain':>8}{'nGain':>8}"
              f"{'learned':>9}{'steps':>7}{'eff':>7}{'toTarget':>10}{'cov':>7}{'MAE':>7}")
    print(header)
    print("-" * len(header))
    for t in totals:
        print(
            f"{t['method']:<12}{t['pre']:>7.3f}{t['post']:>7.3f}{t['learning_gain']:>8.3f}"
            f"{t['normalized_gain']:>8.3f}{t['learned_topics']:>9.1f}{t['steps']:>7.1f}"
            f"{t['efficiency']:>7.3f}{t['activities_to_target']:>10.1f}{t['content_quality']:>7.3f}"
            f"{t['estimate_mae']:>7.3f}"
        )

    print()
    print("== activity mix (mean count per session) ==")
    print(f"{'method':<12}{'explain':>9}{'practice':>10}{'review':>8}")
    for t in totals:
        print(
            f"{t['method']:<12}{t['mix'][P.EXPLAIN]:>9.1f}"
            f"{t['mix'][P.PRACTICE]:>10.1f}{t['mix'][P.REVIEW]:>8.1f}"
        )

    print()
    print("== learning gain by profile ==")
    profiles_sorted = sorted({p["profile"] for p in profiles})
    print(f"{'method':<12}" + "".join(f"{p:>12}" for p in profiles_sorted))
    for method in METHOD_ORDER:
        cells = []
        for p in profiles_sorted:
            row = next((x for x in profiles if x["method"] == method and x["profile"] == p), None)
            cells.append(f"{row['learning_gain']:>12.3f}" if row else f"{'-':>12}")
        print(f"{method:<12}" + "".join(cells))

    if pairs:
        print()
        print("== paired difference vs adaptive (same profile + seed) ==")
        print(f"{'baseline':<12}{'delta':>8}{'95% CI':>18}{'wins':>8}")
        for p in pairs:
            ci = f"[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}]"
            print(f"{p['method']:<12}{p['delta_gain']:>+8.3f}{ci:>18}{p['wins']:>5}/{p['pairs']}")

    print()
    print("== LLM cost of preparing the material ==")
    print(f"{'content':<10}{'calls':>7}{'tokens':>9}{'tok/topic':>11}{'mean ms':>9}")
    for condition, c in costs.items():
        print(f"{condition:<10}{c['calls']:>7}{c['tokens']:>9}"
              f"{c['tokens_per_topic']:>11.1f}{c['mean_latency_ms']:>9.0f}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="four tutors (equal budget)")
    parser.add_argument("--seeds", type=int, default=5, help="learners per (method, profile)")
    parser.add_argument("--budget", type=int, default=32, help="activities per session")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    bank = load_bank()
    materials = load_materials()
    quality = quality_by_condition(materials)
    costs = cost_summary(materials)

    rows: list[dict[str, Any]] = []
    for method in METHOD_ORDER:
        for profile in PROFILE_ORDER:
            for seed in range(1, args.seeds + 1):
                rows.append(run_session(method, profile, seed, bank, args.budget, quality))

    totals = aggregate(rows)
    profiles = by_profile(rows)
    pairs = paired_vs_adaptive(rows)
    print_report(totals, profiles, pairs, costs)

    problems = self_check(totals, rows)
    if problems:
        print()
        for problem in problems:
            print(f"[FAIL] {problem}")

    payload = {
        "protocol": {
            "experiment": "equal_budget_comparison",
            "dataset": "experiments/data/bank.json",
            "materials": "experiments/data/materials.json",
            "topics": len(bank.topics),
            "items": bank.item_count,
            "budget": args.budget,
            "seeds": args.seeds,
            "learned_at": LEARNED_AT,
            "checkpoints": [c for c in CHECKPOINTS if c <= args.budget],
            "content_scaling": "explain gain x coverage; practice gain unscaled",
            "llm": "offline during simulation; content generated once by experiments.content",
            "profiles": {name: PROFILES[name].as_dict() for name in PROFILE_ORDER},
            "methods": {name: {"content": m.content, "planner": m.planner, "label": LABELS[name]}
                        for name, m in METHODS.items()},
            "content_backend": materials.get("retrieval_backend"),
        },
        "content_quality": materials["scores"]["summary"],
        "content_cost": costs,
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
