"""A capability-driven simulated learner.

The problem this replaces
-------------------------
The old harness replayed a fixed answer list indexed by question number, so the
learner's answers were independent of what the agent chose to teach. Every
policy then produced the same score, and the comparison was meaningless.

Here the learner owns a **private latent ability** ``theta`` per topic. The agent
never sees it; it only observes right/wrong. Whether an item is answered
correctly depends on the gap between that ability and the *item's* difficulty,
plus a slip and a guess term:

    p_raw   = sigmoid(SCALE * (theta[ topic ] - item.difficulty))
    p_true  = p_raw * (1 - SLIP) + (1 - p_raw) * GUESS
    correct ~ Bernoulli(p_true)

Activities move ``theta`` (not the agent's mastery estimate), which is what makes
the policy matter: a policy that picks a reading activity first raises ``theta``
before the first quiz, and the following quiz is then answered better.

Modelling assumptions (explicit, not empirical findings)
--------------------------------------------------------
1. **Reading teaches, with diminishing returns.** The first explanation of a
   topic gives the largest gain; each re-read gives 45% of the previous one. A
   hard cut-off was rejected because it would make the simulator - not the
   policy - determine whether re-reading is useless.
2. **Practice teaches through successful retrieval.** Gain is proportional to
   the success rate of the round, so drilling without instruction pays off only
   once the learner can already answer. This is the expertise-reversal pattern:
   novices need instruction first, and the more advanced the learner the more
   they gain from practice. It also means item difficulty matters even though the
   policy does not select difficulty - it sets the success rate.
3. **Review teaches only if there are errors to review.** Reviewing an empty
   error book changes nothing; that is what makes "review" a wasted step for a
   policy that fires it blindly.
4. **No forgetting, no item memory.** Ability never decays and the learner does
   not remember individual items. The second assumption is what lets the same
   probe items be reused for pre- and post-test without inflating the post score:
   the score is a function of ability, not of item exposure. A real learner would
   need disjoint pre/post forms.

These are stated up front because they are the model's premises. The experiment
tests *policies under this model*, not human learning rates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .bank import Item, Topic

SCALE = 3.0            # logistic slope: ability units -> log-odds
SLIP = 0.08            # answers wrong despite knowing
GUESS = 0.20           # answers right despite not knowing (4-option item)
THETA_MIN = -1.5

RATE_EXPLAIN = 0.55
RATE_PRACTICE = 0.70
RATE_REVIEW = 0.30
# Re-reading keeps helping a little, with sharply diminishing returns. A hard
# cut-off would make the model itself punish any policy that re-reads, which is
# an artefact of the simulator, not a property of learning.
EXPLAIN_DECAY = 0.45


@dataclass(frozen=True)
class Profile:
    """A learner archetype. ``theta_max`` is the ceiling ability on a topic."""

    name: str
    theta: float
    theta_max: float
    spread: float = 0.10

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "theta": round(self.theta, 3),
            "theta_max": round(self.theta_max, 3),
            "spread": round(self.spread, 3),
        }


PROFILES: dict[str, Profile] = {
    "struggling": Profile("struggling", theta=-0.70, theta_max=1.10),
    "weak": Profile("weak", theta=-0.20, theta_max=1.20),
    "medium": Profile("medium", theta=0.35, theta_max=1.60),
    "strong": Profile("strong", theta=0.90, theta_max=2.00),
    # Not a level but a *shape*: the same average learner who is strong on some
    # topics and lost on others. A policy that ranks topics by mastery has
    # something to exploit here; with a uniform learner it does not, so this
    # profile is what actually tests the rolling-round scheduling.
    "mixed": Profile("mixed", theta=0.35, theta_max=1.70, spread=0.95),
}
PROFILE_ORDER: tuple[str, ...] = ("struggling", "weak", "medium", "strong", "mixed")


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class SimulatedStudent:
    """Owns the truth. Answers items and grows ability; exposes probes."""

    def __init__(self, topics: Sequence[Topic], profile: Profile, seed: int) -> None:
        self.profile = profile
        self.seed = seed
        # Topic-level ability: same archetype, slightly different starting point
        # per topic, derived deterministically so a seed always replays.
        self.theta: dict[str, float] = {}
        for index, topic in enumerate(topics):
            jitter = (((seed * 7919 + index * 104729) % 1000) / 1000.0 - 0.5) * 2.0
            self.theta[topic.topic_id] = profile.theta + jitter * profile.spread
        self.explain_runs: dict[str, int] = {t.topic_id: 0 for t in topics}
        self._topics: dict[str, Topic] = {t.topic_id: t for t in topics}
        self._rng = _Random(seed)
        self.activities = 0

    # ---------- answering ----------

    def p_correct(self, topic_id: str, difficulty: float) -> float:
        theta = self.theta.get(topic_id, self.profile.theta)
        raw = _sigmoid(SCALE * (theta - float(difficulty)))
        return raw * (1.0 - SLIP) + (1.0 - raw) * GUESS

    def answer(self, topic_id: str, item: Item) -> bool:
        return self._rng.random() < self.p_correct(topic_id, item.difficulty)

    def answer_round(self, topic: Topic) -> tuple[list[dict[str, Any]], float]:
        """Answer every practice item once. Returns (per-item results, accuracy)."""
        results: list[dict[str, Any]] = []
        for index, item in enumerate(topic.practice):
            results.append({"i": index, "ok": self.answer(topic.topic_id, item)})
        correct = sum(1 for r in results if r["ok"])
        accuracy = correct / len(results) if results else 0.0
        return results, accuracy

    def probe_score(self, topic_id: str | None = None) -> float:
        """Expected score on held-out probe items, in [0, 1].

        Expected rather than sampled: the metric should not move because a coin
        flip went the other way, and ability is the only input.
        """
        if topic_id is not None:
            topic = self._topics[topic_id]
            values = [self.p_correct(topic_id, item.difficulty) for item in topic.probe]
            return sum(values) / len(values) if values else 0.0
        scores = [self.probe_score(tid) for tid in self._topics]
        return sum(scores) / len(scores) if scores else 0.0

    def true_mastery(self, topic_id: str) -> float:
        """Ability expressed on the same 0-100 scale as the agent's mastery."""
        return 100.0 * self.probe_score(topic_id)

    # ---------- learning ----------

    def _headroom(self, topic_id: str) -> float:
        ceiling = self.profile.theta_max
        theta = self.theta.get(topic_id, self.profile.theta)
        if ceiling <= THETA_MIN:
            return 0.0
        return max(0.0, (ceiling - theta) / (ceiling - THETA_MIN))

    def _grow(self, topic_id: str, rate: float) -> float:
        if rate <= 0:
            return 0.0
        gained = rate * self._headroom(topic_id)
        self.theta[topic_id] = min(self.profile.theta_max, self.theta.get(topic_id, 0.0) + gained)
        return gained

    def study(self, topic_id: str, activity: str, accuracy: float = 0.0, had_errors: bool = False) -> float:
        """Apply one activity's learning effect and return the theta gain."""
        gain = 0.0
        if activity == "explain":
            runs = self.explain_runs.get(topic_id, 0)
            factor = EXPLAIN_DECAY ** runs
            self.explain_runs[topic_id] = runs + 1
            gain = self._grow(topic_id, RATE_EXPLAIN * factor)
        elif activity == "practice":
            # Learning by successful retrieval: an attempt that succeeds teaches,
            # an attempt that fails mostly does not. The alternative (gain peaks
            # at 50% success and is *zero* at 0%) was rejected because it implies
            # a learner who answers everything wrong learns as much as one who
            # answers half right - it rewards failure. The ceiling is handled by
            # the headroom factor instead.
            gain = self._grow(topic_id, RATE_PRACTICE * accuracy)
        elif activity == "review":
            gain = self._grow(topic_id, RATE_REVIEW) if had_errors else 0.0
        self.activities += 1
        return gain

    def snapshot(self) -> dict[str, Any]:
        return {
            "profile": self.profile.name,
            "seed": self.seed,
            "theta": {k: round(v, 4) for k, v in self.theta.items()},
            "probe": {k: round(self.probe_score(k), 4) for k in self.theta},
        }


class _Random:
    """Tiny deterministic PRNG (xorshift32) so replays never depend on Python."""

    def __init__(self, seed: int) -> None:
        self._state = (int(seed) * 2654435761 + 0x9E3779B9) & 0xFFFFFFFF or 0x12345678

    def random(self) -> float:
        x = self._state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self._state = x & 0xFFFFFFFF
        return self._state / 0x100000000

    def choice(self, items: Sequence[Any]) -> Any:
        if not items:
            raise IndexError("choice from empty sequence")
        return items[int(self.random() * len(items)) % len(items)]


def total_items(topics: Iterable[Topic]) -> int:
    return sum(len(t.practice) + len(t.probe) for t in topics)
