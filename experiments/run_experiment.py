"""Main experiment: equal-budget comparison of the four tutors.

Protocol
--------
* Dataset: ``experiments/data/bank.json`` - 8 knowledge points from the
  committed Lecture 1 PDF, 4 practice items and 3 held-out probes each.
* Content: ``experiments/data/materials.json`` - the LLM-only and RAG
  explanations, generated once by ``experiments.content`` and scored for
  coverage of the probe content. The simulator scales the gain of an
  explanation by that coverage: an explanation that never mentions what the
  post-test asks about cannot teach it.
* A session = one simulated learner (profile, seed) studying the whole
  material.
* Pre-test: one probe item per topic seeds the agent's mastery, as in the
  product, so no method starts from oracle knowledge.
* Then the method's planner chooses activities until the budget is spent or
  every topic is mastered.
* The post-test is the learner's expected score on the same held-out probes.
  ``SimulatedStudent`` has no item memory, so reusing probes cannot inflate the
  post score (see ``simulator.py``).
* Budget is equal for every method, which is what makes the comparison fair
  when methods stop at different times.

Methods (see ``methods.py``) form a chain, each step changing one thing:
``llm_only`` -> ``rag`` (retrieval) -> ``fixed`` (teaching loop) ->
``adaptive`` (the LLM agent that reads the learner state).

Offline
-------
This script never calls a model.  The adaptive arm's choices are replayed from
``experiments/data/decisions.json`` (see ``decisions.py``), which
``collect_decisions`` fills in once.  Re-running the analysis, changing a
metric or redrawing a figure therefore costs nothing.

Metrics (deliberately few)
--------------------------
Three numbers separate the methods, and nothing else is reported:

``learning_gain``      post - pre on the held-out probes: did the learner learn.
``mastered_topics``    how much of the material crossed the 0.75 probe threshold.
``mastered_topics``    how much of the material crossed the 0.75 probe threshold.

An ``activities_to_target`` variant was dropped: a strong learner can already be
above the threshold before the first activity, which scored as "target reached
at step 1" and made the mean meaningless.

Usage
-----
    python -m experiments.run_experiment
    python -m experiments.run_experiment --seeds 4 --budget 24
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

from .bank import load_bank
from .content import load_materials, quality_by_condition
from .decisions import DECISIONS_PATH, DecisionLog
from .methods import LABELS, METHOD_ORDER, METHODS
from .protocol import BOOTSTRAP, BOOTSTRAP_SEED, BUDGET, LEARNED_AT, SEEDS
from .session import run_session
from .simulator import PROFILE_ORDER, PROFILES

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUT = RESULTS_DIR / "e1_main.json"


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
        subset = [r for r in rows if r["method"] == method]
        if not subset:
            continue
        gains = [r["learning_gain"] for r in subset]
        out.append({
            "method": method,
            "label": LABELS[method],
            "content": METHODS[method].content,
            "planner": METHODS[method].planner,
            "episodes": len(subset),
            "pre": _mean([r["pre"] for r in subset]),
            "post": _mean([r["post"] for r in subset]),
            "learning_gain": _mean(gains),
            "learning_gain_sd": _sd(gains),
            "mastered_topics": _mean([r["mastered_topics"] for r in subset]),
            "mastered_rate": _mean([r["mastered_rate"] for r in subset]),
            "steps": _mean([r["steps"] for r in subset]),
            "stopped_early": sum(1 for r in subset if r["stop_reason"] != "budget"),
        })
    return out


def by_profile(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for method in METHOD_ORDER:
        for profile in PROFILE_ORDER:
            subset = [r for r in rows if r["method"] == method and r["profile"] == profile]
            if not subset:
                continue
            out.append({
                "method": method,
                "profile": profile,
                "learning_gain": _mean([r["learning_gain"] for r in subset]),
                "mastered_topics": _mean([r["mastered_topics"] for r in subset]),
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


def _paired(
    rows: Sequence[dict[str, Any]], field: str
) -> list[dict[str, Any]]:
    """Paired by (profile, seed): same learner, same dice, one difference."""
    key = lambda r: (r["profile"], r["seed"])  # noqa: E731
    base = {key(r): r[field] for r in rows if r["method"] == "adaptive"}
    out = []
    for method in METHOD_ORDER:
        if method == "adaptive":
            continue
        other = {key(r): r[field] for r in rows if r["method"] == method}
        common = sorted(set(base) & set(other))
        diffs = [base[k] - other[k] for k in common]
        if not diffs:
            continue
        lo, hi = _bootstrap_ci(diffs)
        out.append({
            "method": method,
            "field": field,
            "pairs": len(diffs),
            "delta": statistics.fmean(diffs),
            "ci_low": lo,
            "ci_high": hi,
            "wins": sum(1 for d in diffs if d > 0),
            "ties": sum(1 for d in diffs if d == 0),
        })
    return out


# --------------------------------------------------------------------------
# self-check + report
# --------------------------------------------------------------------------

def self_check(totals: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> list[str]:
    """Fail loudly if the methods cannot be told apart.

    When answers were replayed from a fixed list every method scored
    identically no matter what the agent chose; this guard is what caught it.
    """
    problems: list[str] = []
    gains = [round(t["learning_gain"], 9) for t in totals]
    if len(set(gains)) == 1:
        problems.append("all methods produced the same learning gain - the planner never reaches the outcome")

    sequences: dict[str, set[tuple[str, ...]]] = {}
    for row in rows:
        sequences.setdefault(row["method"], set()).add(tuple(row["activities"]))
    if len(sequences) > 1 and len({frozenset(v) for v in sequences.values()}) == 1:
        problems.append("all methods produced identical activity sequences")
    return problems


def print_report(totals, profiles, gains, mastered) -> None:
    print()
    print("== equal-budget comparison ==")
    header = (
        f"{'method':<12}{'pre':>7}{'post':>7}{'gain':>8}{'sd':>7}"
        f"{'mastered':>10}{'steps':>7}"
    )
    print(header)
    print("-" * len(header))
    for t in totals:
        print(
            f"{t['method']:<12}{t['pre']:>7.3f}{t['post']:>7.3f}"
            f"{t['learning_gain']:>+8.3f}{t['learning_gain_sd']:>7.3f}"
            f"{t['mastered_topics']:>10.2f}{t['steps']:>7.1f}"
        )

    print()
    print("== learning gain by profile ==")
    widths = "".join(f"{p:>11}" for p in PROFILE_ORDER)
    print(f"{'method':<12}{widths}")
    for method in METHOD_ORDER:
        cells = []
        for p in PROFILE_ORDER:
            row = next((x for x in profiles if x["method"] == method and x["profile"] == p), None)
            cells.append(f"{row['learning_gain']:>+11.3f}" if row else f"{'-':>11}")
        print(f"{method:<12}" + "".join(cells))

    for title, pairs in (("paired difference vs agent: learning gain", gains),
                         ("paired difference vs agent: master topics", mastered)):
        if not pairs:
            continue
        print()
        print(f"== {title} ==")
        print(f"{'baseline':<12}{'delta':>8}{'95% CI':>18}{'wins':>8}")
        for p in pairs:
            ci = f"[{p['ci_low']:+.3f}, {p['ci_high']:+.3f}]"
            print(f"{p['method']:<12}{p['delta']:>+8.3f}{ci:>18}{p['wins']:>5}/{p['pairs']}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="four tutors (equal budget)")
    parser.add_argument("--seeds", type=int, default=SEEDS, help="learners per (method, profile)")
    parser.add_argument("--budget", type=int, default=BUDGET, help="activities per session")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    bank = load_bank()
    materials = load_materials()
    quality = quality_by_condition(materials)
    log = DecisionLog(DECISIONS_PATH, read_only=True)

    rows: list[dict[str, Any]] = []
    for method in METHOD_ORDER:
        for profile in PROFILE_ORDER:
            for seed in range(1, args.seeds + 1):
                rows.append(
                    run_session(
                        METHODS[method], profile, seed, bank, args.budget, quality,
                        log if method == "adaptive" else None,
                    )
                )

    totals = aggregate(rows)
    profiles = by_profile(rows)
    gains = _paired(rows, "learning_gain")
    mastered = _paired(rows, "mastered_topics")
    print_report(totals, profiles, gains, mastered)

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
            "decisions": "experiments/data/decisions.json",
            "topics": len(bank.topics),
            "items": bank.item_count,
            "budget": args.budget,
            "seeds": args.seeds,
            "learned_at": LEARNED_AT,
            "content_scaling": "explain gain x coverage; practice gain unscaled",
            "decision_source": "LLM (frozen log); replayed offline",
            "profiles": {name: PROFILES[name].as_dict() for name in PROFILE_ORDER},
            "methods": {
                name: {"content": m.content, "planner": m.planner, "label": LABELS[name]}
                for name, m in METHODS.items()
            },
            "content_backend": materials.get("retrieval_backend"),
        },
        "decision_log": log.summary(),
        "content_quality": materials["scores"]["summary"],
        "totals": totals,
        "by_profile": profiles,
        "paired_gain": gains,
        "paired_mastered": mastered,
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
