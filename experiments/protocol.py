"""Frozen protocol constants.

One place, so the banking run and the replay run can never disagree about the
budget or the number of learners - a mismatch would show up as a missing
decision log entry rather than as a silently different experiment.
"""

from __future__ import annotations

#: Learners per (method, profile).  Eight gives 40 paired comparisons against
#: each baseline, which is what the bootstrap intervals are computed on.
SEEDS: int = 8

#: Activities per session, equal for every method.  With 8 topics this is 4
#: activities per topic on average.
BUDGET: int = 32

#: A topic counts as learned when the learner's *true* expected score on the
#: held-out probes reaches this.  Fixed in advance, not tuned afterwards.
LEARNED_AT: float = 0.75

#: Paired bootstrap over (profile, seed) pairs.
BOOTSTRAP: int = 2000
BOOTSTRAP_SEED: int = 20261116

__all__ = ["BOOTSTRAP", "BOOTSTRAP_SEED", "BUDGET", "LEARNED_AT", "SEEDS"]
