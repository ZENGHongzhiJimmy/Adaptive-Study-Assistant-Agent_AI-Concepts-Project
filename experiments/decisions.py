"""Frozen decision log for the adaptive arm.

Why a log exists
----------------
The adaptive arm is the product agent, and in the product the next activity is
chosen by the model (``src/agent/planner_llm.py``).  Two properties of that
choice make it unusable for a paired experiment if it is called live every time:

* it is **slow** (2-3 s per step) and **paid**, and the protocol needs
  5 profiles x 8 seeds x up to 32 steps of it;
* it is **stochastic**, so re-running would move the numbers for reasons that
  have nothing to do with the learner.

So the decisions are collected once and written to
``experiments/data/decisions.json``, keyed by a canonical hash of the learner
state.  A run then replays the same state -> same choice mapping, exactly like
``materials.json`` freezes the explanations.  The adaptive arm is therefore
evaluated as the *policy the model produced*, which is the honest object of
study; nothing about legality, mastery or stopping is frozen here.

What is stored
--------------
Only what the product itself records: the chosen ``(topic_id, activity)``, the
one-line reason, which model answered, and whether a stronger model had to be
asked.  No prompts, no state snapshots - a state that is not in the log is
simply asked again.

Failure handling
----------------
``choose()`` returns ``None`` only when every model refused.  The session then
stops early (as the product would ship no next step), the failure is counted,
and ``run_experiment`` reports the rate instead of hiding it behind a rule
fallback.
"""

from __future__ import annotations

import hashlib
import json
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from src.agent.policy import TopicState
from src.agent.planner_llm import LLMPlanner, PlannerDecision, legal_candidates

DATA_DIR = Path(__file__).resolve().parent / "data"
DECISIONS_PATH = DATA_DIR / "decisions.json"


def state_key(
    topics: Sequence[TopicState],
    round_no: int,
    visited: Sequence[str],
    recent: Sequence[dict[str, Any]],
    model: str = "",
) -> str:
    """Canonical hash of everything the model sees.

    If two states render the same prompt they must hash the same, and if they
    differ in any field the prompt contains they must differ here - otherwise a
    replay would hand back a decision made from different information.  The
    model name is part of the key so switching models cannot silently reuse
    another model's choices.
    """
    payload = {
        "model": model,
        "round": int(round_no),
        "visited": [str(v) for v in visited],
        "recent": [
            [
                str(r.get("topic_id") or ""),
                str(r.get("activity") or ""),
                round(float(r.get("mastery_before") or 0.0), 1),
                round(float(r.get("mastery_after") or 0.0), 1),
            ]
            for r in recent
        ],
        "topics": [
            [
                t.topic_id,
                round(float(t.mastery), 1),
                int(t.wrong_count),
                int(t.explain_runs),
                int(t.fail_streak),
                bool(t.pretested),
                int(t.explain_pages),
                int(t.quiz_count),
            ]
            for t in topics
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoggedDecision:
    topic_id: str
    activity: str
    reason: str
    model: str
    escalated: bool = False

    def as_tuple(self) -> tuple[str, str]:
        return self.topic_id, self.activity

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "activity": self.activity,
            "reason": self.reason,
            "model": self.model,
            "escalated": bool(self.escalated),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LoggedDecision":
        return cls(
            topic_id=str(raw["topic_id"]),
            activity=str(raw["activity"]),
            reason=str(raw.get("reason") or ""),
            model=str(raw.get("model") or ""),
            escalated=bool(raw.get("escalated")),
        )


class DecisionLog:
    """State -> decision map backed by one JSON file, safe for a thread pool.

    Sessions run in parallel because each step costs a model call; the log is
    the only shared object, and it is guarded by a lock.  Two workers asking the
    same unseen state is possible but harmless: the state is genuinely the same,
    so either answer is a valid recording of it.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        planner: LLMPlanner | None = None,
        read_only: bool = False,
        attempts: int = 3,
    ) -> None:
        self.path = Path(path) if path else DECISIONS_PATH
        self.planner = planner
        self.read_only = read_only
        # A state whose first asks all failed is asked again: "the model had a
        # bad minute" is not a property of the state, and losing the step would
        # silently shorten the session by one activity of budget.
        self.attempts = max(1, int(attempts))
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.failures = 0
        self.records: dict[str, LoggedDecision] = {}
        #: States where the model produced nothing usable.  Recorded so the
        #: replay stops at exactly the same step instead of raising KeyError.
        self.failed_states: set[str] = set()
        self.meta: dict[str, Any] = {}
        self._load()

    # ---------- io ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.meta = dict(payload.get("meta") or {})
        self.failed_states = set(payload.get("no_decision") or [])
        for key, raw in (payload.get("decisions") or {}).items():
            self.records[key] = LoggedDecision.from_dict(raw)

    def save(self) -> None:
        if self.read_only:
            return
        with self._lock:
            payload = {
                "meta": {
                    **self.meta,
                    "model": self._model_name() or self.meta.get("model", ""),
                    "entries": len(self.records),
                },
                "decisions": {
                    key: value.as_dict()
                    for key, value in sorted(self.records.items())
                },
                "no_decision": sorted(self.failed_states),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            tmp.replace(self.path)

    # ---------- lookup ----------

    def _model_name(self) -> str:
        """Model identity for the cache key.

        A replay has no planner, so the name is read back from the file's meta
        block.  Without this the replay would hash ``model=""`` and miss every
        entry recorded under the real model name.
        """
        if self.planner is None:
            return str(self.meta.get("model") or "")
        return str(
            self.planner.model
            or getattr(self.planner.client, "model", "")
            or ""
        )

    def choose(
        self,
        topics: Sequence[TopicState],
        round_no: int,
        visited: Sequence[str],
        recent: Sequence[dict[str, Any]],
    ) -> LoggedDecision | None:
        """Replay the recorded decision, or ask the model once and record it."""
        model = self._model_name()
        key = state_key(topics, round_no, visited, recent, model)

        with self._lock:
            cached = self.records.get(key)
            failed_before = key in self.failed_states
        if cached is not None:
            self.hits += 1
            return cached
        if failed_before:
            self.hits += 1
            return None
        if not legal_candidates(topics, 0, visited):
            # Nothing is legal: the material is finished or unmeasurable.  That
            # is a fact about the state, not a decision, so it is never stored.
            return None

        if self.read_only or self.planner is None:
            # A replay that hits an unknown state means the banking step and the
            # replay disagree; say so loudly instead of inventing a step.
            raise KeyError(
                f"decision log has no entry for state {key[:12]} - rerun "
                "`python -m experiments.collect_decisions` before the experiment"
            )

        decision: PlannerDecision | None = None
        for attempt in range(self.attempts):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                decision = self.planner.choose(topics, round_no, visited, recent)
            if decision is not None:
                break
            # Same state, same question: a retry cannot change what is legal,
            # only whether the model managed to answer this time.
            if attempt + 1 < self.attempts:
                continue

        if decision is None:
            self.failures += 1
            with self._lock:
                self.failed_states.add(key)
            return None

        logged = LoggedDecision(
            topic_id=decision.topic_id,
            activity=decision.activity,
            reason=decision.reason,
            model=decision.model or model,
            escalated=bool(decision.escalated),
        )
        with self._lock:
            # A concurrent worker may have recorded the same state first; both
            # decisions describe the same state, so keep the first one.
            self.records.setdefault(key, logged)
            self.misses += 1
        return logged

    # ---------- reporting ----------

    @property
    def size(self) -> int:
        return len(self.records)

    def summary(self) -> dict[str, Any]:
        models: dict[str, int] = {}
        escalated = 0
        for record in self.records.values():
            models[record.model or "unknown"] = models.get(record.model or "unknown", 0) + 1
            escalated += 1 if record.escalated else 0
        total = len(self.records) or 1
        return {
            "entries": len(self.records),
            "models": dict(sorted(models.items(), key=lambda kv: -kv[1])),
            "escalated": escalated,
            "escalated_rate": round(escalated / total, 4),
            "replay_hits": self.hits,
            "new_calls": self.misses,
            "failures": self.failures,
        }


__all__ = [
    "DECISIONS_PATH",
    "DecisionLog",
    "LoggedDecision",
    "state_key",
]
