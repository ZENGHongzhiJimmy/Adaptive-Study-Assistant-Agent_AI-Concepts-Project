"""Stage 2 of the experiment: bank the adaptive arm's decisions.

The adaptive arm's next step is chosen by the model, so it has to be *asked*
before it can be replayed.  This script walks every (profile, seed) session of
the adaptive arm once, writes each state -> choice mapping into
``experiments/data/decisions.json``, and prints the trace of every session so a
human can see what the agent actually did.

It is resumable: entries already in the file are free, so an interrupted run
continues where it stopped.  ``run_experiment`` then replays the file offline -
no model, no network, no drift.

Usage
-----
    python -m experiments.collect_decisions                 # fill in what is missing
    python -m experiments.collect_decisions --seed-from 1 --seeds 8
    python -m experiments.collect_decisions --workers 6
    python -m experiments.collect_decisions --dry-run       # report coverage only
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from src.agent.planner_llm import LLMPlanner
from src.utils.config import get_config

from .bank import load_bank
from .content import load_materials, quality_by_condition
from .decisions import DECISIONS_PATH, DecisionLog
from .methods import METHODS
from .protocol import BUDGET, SEEDS
from .session import run_session
from .simulator import PROFILE_ORDER

SAVE_EVERY = 25


def _planner(config: dict[str, Any]) -> LLMPlanner:
    """One planner per worker: the counters and the client are not shared."""
    agent = dict(config.get("agent") or {})
    # The log owns the cross-run cache; the in-memory one would only hide the
    # difference between "asked now" and "replayed from disk".
    agent["planner_cache"] = False
    return LLMPlanner({**config, "agent": agent})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bank the adaptive arm's decisions")
    parser.add_argument("--seeds", type=int, default=SEEDS)
    parser.add_argument("--seed-from", type=int, default=1)
    parser.add_argument("--budget", type=int, default=BUDGET)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument(
        "--profiles", default=",".join(PROFILE_ORDER),
        help="comma separated subset of " + ",".join(PROFILE_ORDER),
    )
    parser.add_argument("--out", default=str(DECISIONS_PATH))
    parser.add_argument("--dry-run", action="store_true", help="report coverage, ask nothing")
    args = parser.parse_args(argv)

    config = get_config()
    bank = load_bank()
    quality = quality_by_condition(load_materials())
    method = METHODS["adaptive"]
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    unknown = [p for p in profiles if p not in PROFILE_ORDER]
    if unknown:
        parser.error(f"unknown profile(s): {unknown}")
    seeds = list(range(args.seed_from, args.seed_from + args.seeds))

    if args.dry_run:
        log = DecisionLog(args.out, read_only=True)
        print(f"log entries: {log.size}")
        print(f"model: {config['llm']['model']}")
        print("replaying every adaptive session needs no model call:")
        failed = 0
        for profile in profiles:
            for seed in seeds:
                row = run_session(method, profile, seed, bank, args.budget, quality, log)
                failed += 1 if row["stop_reason"] == "no_decision" else 0
                print(
                    f"  {profile:<11} seed={seed}  gain={row['learning_gain']:+.4f} "
                    f"mastered={row['mastered_topics']} steps={row['steps']}"
                )
        print(f"replay ok, no_decision sessions: {failed}")
        return 0

    log = DecisionLog(args.out, planner=_planner(config), attempts=3)
    log.meta.update({
        "purpose": "adaptive arm decisions for experiments.run_experiment",
        "model": str(config["llm"]["model"]),
        "fallback_models": list(config["llm"].get("fallback_models") or []),
        "budget": args.budget,
    })
    print(f"model={config['llm']['model']}  entries already banked={log.size}", flush=True)

    lock = threading.Lock()
    done = 0
    total = len(profiles) * len(seeds)
    rows: list[dict[str, Any]] = []

    def work(job: tuple[str, int]) -> dict[str, Any]:
        nonlocal done
        profile, seed = job
        started = time.time()
        row = run_session(
            method, profile, seed, bank, args.budget, quality, log,
        )
        with lock:
            done += 1
            rows.append(row)
            print(
                f"[{done:>2}/{total}] {profile:<11} seed={seed}  "
                f"gain={row['learning_gain']:+.4f} mastered={row['mastered_topics']} "
                f"steps={row['steps']} {row['stop_reason']}  "
                f"{time.time() - started:.0f}s  (banked {log.size})",
                flush=True,
            )
            if log.size and done % 1 == 0:
                log.save()
        return row

    jobs = [(profile, seed) for profile in profiles for seed in seeds]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, jobs))
    log.save()

    rows.sort(key=lambda r: (PROFILE_ORDER.index(r["profile"]), r["seed"]))
    Path("experiments/results").mkdir(parents=True, exist_ok=True)
    (Path("experiments/results") / "adaptive_sessions.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    summary = log.summary()
    print()
    print(f"log: {summary}")
    print(f"sessions: {len(rows)}   no_decision: "
          f"{sum(1 for r in rows if r['stop_reason'] == 'no_decision')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
