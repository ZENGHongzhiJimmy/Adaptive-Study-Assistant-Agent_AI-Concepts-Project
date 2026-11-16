"""The ONE adaptive policy used by both the product and the experiments.

Before this module existed there were two planners:

* ``frontend/js/engine.js``  - what the demo actually ran (error book +
  fail-streak + rolling rounds, mastery on a 0-100 scale);
* a threshold planner in the retired CLI loop (mastery 0-1,
  ``Teach/MCQ/Challenge/Review``).

That split is indefensible in a report: the agent being demoed was not the
agent being evaluated, and the two disagreed on thresholds *and* on the
mastery update rule. This module is now the single source of truth:

* the product (``src/api`` -> ``src/agent/serve.py``) calls it through a
  LangGraph node, and
* the experiment harness (``experiments/methods.py`` /
  ``experiments/run_experiment.py``) calls the same functions.

The retired CLI loop was deleted on 2026-11-16.  The three experiment baselines
are (content, planner) pairs defined in ``experiments/methods.py``; the
fixed-strategy one is ``methods.CyclicPlanner``.  This module remains the
single policy shared by the product and the adaptive arm of the main experiment.

Design notes
------------
* Every function here is pure: state in -> state out, no I/O, no clock, no
  randomness.  Persistence and HTTP live in ``learner_store.py`` / ``serve.py``.
* Mastery is an integer percentage in [0, 100], exactly like the shipped UI,
  so the number a student sees is the number the experiment measures.
* ``MASTERED`` requires *both* mastery >= 80 and a clean error book, so a
  lucky streak cannot "finish" a topic that still has unresolved mistakes.
* ``EXPLAIN_CAP`` stops passive reading from reaching mastery: watching an
  explanation is worth at most one small step (+10 once, +2 on a re-watch).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

POLICY_ID = "adaptive-v1"

MASTERED = 80.0        # mastery needed to call a topic done (percent)
EXPLAIN_CAP = 55.0     # passive reading can never exceed this
PRACTICE_GAIN = 0.6    # how hard a practice score pulls mastery towards itself
DELTA_MIN, DELTA_MAX = -10.0, 25.0

# Lower is more urgent.  Used only for ordering the rolling plan.
STAGE_ORDER = {"help": 0, "review": 1, "explain": 2, "practice": 3}

EXPLAIN, PRACTICE, REVIEW = "explain", "practice", "review"


@dataclass
class TopicState:
    """Per-topic learner state + the content availability the policy needs.

    ``explain_pages`` / ``quiz_count`` are supplied by the client because the
    parsed material lives in the web layer; the policy only needs to know
    whether there is something to read and something to answer.
    """

    topic_id: str
    name: str = ""
    mastery: float = 30.0
    attempts: int = 0
    errors: int = 0
    mistakes: list[dict[str, Any]] = field(default_factory=list)
    explain_done: bool = False
    explain_runs: int = 0
    fail_streak: int = 0
    need_help: bool = False
    pretested: bool = False
    explain_pages: int = 0
    quiz_count: int = 0
    order: int = 0

    # ---------- derived ----------

    @property
    def wrong_count(self) -> int:
        return len(self.mistakes)

    @property
    def mastered(self) -> bool:
        return is_mastered(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "name": self.name,
            "mastery": _pct(self.mastery),
            "attempts": int(self.attempts),
            "errors": int(self.errors),
            "mistakes": list(self.mistakes),
            "explain_done": bool(self.explain_done),
            "explain_runs": int(self.explain_runs),
            "fail_streak": int(self.fail_streak),
            "need_help": bool(self.need_help),
            "pretested": bool(self.pretested),
            "explain_pages": int(self.explain_pages),
            "quiz_count": int(self.quiz_count),
            "order": int(self.order),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TopicState":
        return cls(
            topic_id=str(raw.get("topic_id") or raw.get("id") or ""),
            name=str(raw.get("name") or ""),
            mastery=float(raw.get("mastery", 30) or 0),
            attempts=int(raw.get("attempts") or 0),
            errors=int(raw.get("errors") or 0),
            mistakes=[m for m in (raw.get("mistakes") or []) if isinstance(m, dict)],
            explain_done=bool(raw.get("explain_done") or raw.get("explainDone")),
            explain_runs=int(raw.get("explain_runs") or raw.get("explainRuns") or 0),
            fail_streak=int(raw.get("fail_streak") or raw.get("failStreak") or 0),
            need_help=bool(raw.get("need_help") or raw.get("needHelp")),
            pretested=bool(raw.get("pretested")),
            explain_pages=int(raw.get("explain_pages") or 0),
            quiz_count=int(raw.get("quiz_count") or 0),
            order=int(raw.get("order") or 0),
        )


@dataclass(frozen=True)
class Decision:
    """What to do next for one topic.  ``activity is None`` means stop."""

    activity: str | None
    rule: str
    reason: str
    blocked: bool = False      # nothing to study (no explanation, no quiz)
    mastered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "rule": self.rule,
            "reason": self.reason,
            "blocked": self.blocked,
            "mastered": self.mastered,
        }


@dataclass(frozen=True)
class StepOutcome:
    """Result of one scored (or unscored) activity."""

    activity: str
    before: float
    after: float
    delta: float
    note: str
    state: TopicState

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "before": _pct(self.before),
            "after": _pct(self.after),
            "delta": _pct(self.delta),
            "note": self.note,
            "state": self.state.to_dict(),
        }


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def _pct(value: float) -> int:
    """Round to the integer percentage the UI displays."""
    return int(round(float(value)))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def is_mastered(topic: TopicState) -> bool:
    """Mastery is not enough: the error book must be empty too."""
    return topic.mastery >= MASTERED and topic.wrong_count == 0 and not (topic.errors > 0)


def decide(topic: TopicState) -> Decision:
    """Single-topic policy.  Order of the branches IS the policy.

    1. already mastered            -> stop
    2. two bad practices in a row  -> go back to the explanation (help)
    3. two unresolved mistakes     -> error-book review
    4. never read the explanation  -> explanation
    5. anything left to answer     -> practice
    6. nothing to study            -> blocked
    """
    wrong = topic.wrong_count

    if is_mastered(topic):
        return Decision(
            None,
            f"≥ {_pct(MASTERED)}% 且无错题",
            f"掌握度 {_pct(topic.mastery)}%，错题已全部纠正，这个知识点已经完成。",
            mastered=True,
        )

    # Two sub-half practices mean the explanation did not land.  Without this
    # branch the loop ping-pongs between practice and review forever.
    if topic.need_help and topic.explain_pages > 0:
        return Decision(
            EXPLAIN,
            f"连续 {max(topic.fail_streak, 2)} 次练习未达标",
            "最近几次练习正确率都不足一半，先回到讲解把概念重新过一遍，再回来做题。",
        )

    if wrong >= 2:
        return Decision(
            REVIEW,
            f"错题本还有 {wrong} 道题",
            f"练习中有 {wrong} 道题还没答对，先用错题复习纠正概念，再回到练习。",
        )

    if not topic.explain_done and topic.explain_pages > 0:
        return Decision(
            EXPLAIN,
            "尚未学习讲解",
            f"还没看过这个知识点的讲解（{topic.explain_pages} 页），先建立概念再练习。",
        )

    if topic.quiz_count > 0:
        return Decision(
            PRACTICE,
            f"掌握度 {_pct(topic.mastery)}% < {_pct(MASTERED)}%",
            f"掌握度 {_pct(topic.mastery)}%，用练习检验是否真的掌握：答对越多提升越快，答错会回落。",
        )

    return Decision(
        None,
        "没有可评估的内容",
        "解析结果里既没有讲解也没有测验题，这个知识点暂时无法学习。"
        if topic.explain_pages == 0
        else "这份资料只为它生成了讲解、没有测验题，读完讲解后无法继续评估掌握度。",
        blocked=True,
    )


def stage_of(topic: TopicState) -> str:
    """Where this topic sits right now: pretest / help / review / explain /
    practice / done / blocked.  Used for ordering and for the UI badge."""
    if not topic.pretested:
        return "pretest"
    decision = decide(topic)
    if decision.mastered or (decision.activity is None and not decision.blocked):
        return "done"
    if decision.blocked:
        return "blocked"
    if decision.activity == EXPLAIN and topic.need_help:
        return "help"
    return str(decision.activity)


# --------------------------------------------------------------------------
# state transitions
# --------------------------------------------------------------------------

def apply_pre_test(topic: TopicState, correct: bool | None) -> TopicState:
    """Pre-test only sets the starting point; it never counts as an error.

    ``correct is None`` means the topic had no assessment item and the student
    skipped it: the topic is marked assessed but mastery is untouched.  Treating
    a skipped item as "wrong" would drop every topic to 20% and pollute the
    learning-gain metric.
    """
    if correct is None:
        return replace(topic, pretested=True)
    return replace(
        topic,
        mastery=45.0 if correct else 20.0,
        attempts=topic.attempts + 1,
        errors=0,
        mistakes=[],
        pretested=True,
        # Pre-test is a fresh start: keep the reading/streak counters coherent.
        fail_streak=0,
        need_help=False,
    )


def _mistake_key(question: str) -> str:
    return str(question or "")[:160]


def apply_result(
    topic: TopicState,
    activity: str,
    outcome: dict[str, Any] | None = None,
    quiz: Sequence[dict[str, Any]] | None = None,
) -> StepOutcome:
    """Update mastery + error book after one activity.

    The score follows *performance*, not the number of clicks:

    * ``explain``  +10 on the first read (capped at EXPLAIN_CAP), +2 on a
      re-watch, 0 afterwards - otherwise clicking "done" would farm mastery;
    * ``practice`` ``delta = clamp(round((accuracy*100 - mastery) * 0.6))``
      so a perfect round climbs fast, a half-right round stalls, and a fully
      wrong round actually loses mastery;
    * ``review``   clears the error book but never adds mastery - flipping cards
      is not evidence of learning, the following practice decides that.
    """
    outcome = outcome or {}
    before = float(topic.mastery)
    delta = 0.0
    note = ""
    updated = topic

    if activity == EXPLAIN:
        if topic.explain_pages <= 0:
            delta, note = 0.0, "该知识点没有讲解内容，未计分"
        else:
            runs = topic.explain_runs if topic.explain_runs >= 0 else (1 if topic.explain_done else 0)
            if runs == 0:
                delta, note = 10.0, "完成首次讲解学习"
            elif runs == 1:
                delta, note = 2.0, "重看讲解，只做少量巩固"
            else:
                delta, note = 0.0, "再次重学讲解，不再计分"
            # Re-reading is a fresh start: the "two bad practices in a row"
            # branch counts *consecutive* failures, so the streak has to be
            # cleared here. Without this the next single failure re-triggers
            # help immediately and the loop degenerates into
            # practice -> explain -> practice -> explain, where every second
            # activity teaches nothing.
            updated = replace(
                topic,
                explain_runs=runs + 1,
                explain_done=True,
                need_help=False,
                fail_streak=0,
            )

    elif activity == PRACTICE:
        answers = outcome.get("answers")
        total = int(outcome.get("total") or (len(answers) if isinstance(answers, list) else topic.quiz_count))
        correct = int(outcome.get("correct") or 0) if not isinstance(answers, list) else sum(
            1 for a in answers if isinstance(a, dict) and a.get("ok")
        )
        if total <= 0:
            delta, note = 0.0, "没有可作答的题目，未计分"
        else:
            score = correct / total
            raw = round((score * 100.0 - before) * PRACTICE_GAIN)
            delta = clamp(raw, DELTA_MIN, DELTA_MAX)

            fail_streak, need_help = topic.fail_streak, topic.need_help
            if score >= 0.6:
                fail_streak, need_help = 0, False
            elif score < 0.5:
                fail_streak += 1
                if fail_streak >= 2:
                    need_help = True

            mistakes = _update_mistakes(topic.mistakes, answers, correct / total, quiz)
            updated = replace(
                topic,
                mistakes=mistakes,
                fail_streak=fail_streak,
                need_help=need_help,
            )
            note = f"答对 {correct} / {total} 题"

    elif activity == REVIEW:
        if not topic.mistakes:
            delta, note = 0.0, "没有待纠正的错题，未计分"
        else:
            delta = 0.0
            note = f"已重新讲解 {len(topic.mistakes)} 道错题，回到练习验证"
            updated = replace(topic, mistakes=[])

    else:
        delta, note = 0.0, "未知活动，未计分"

    # Reading has no check of its own, so it may never push mastery past the
    # cap - but it must not drag an already-mastered topic down either.
    if activity == EXPLAIN:
        after = max(before, min(EXPLAIN_CAP, before + delta))
    else:
        after = clamp(before + delta, 0.0, 100.0)

    updated = replace(
        updated,
        mastery=after,
        attempts=updated.attempts + 1,
        errors=len(updated.mistakes),
    )
    return StepOutcome(
        activity=activity,
        before=before,
        after=after,
        delta=after - before,
        note=note,
        state=updated,
    )


def _update_mistakes(
    current: Iterable[dict[str, Any]],
    answers: Any,
    score: float,
    quiz: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Correct answers leave the error book, wrong ones enter it.

    Keyed by the (truncated) question text so the same item is not stacked
    twice when a student fails it on two different days.
    """
    bucket = [dict(m) for m in (current or [])]

    if isinstance(answers, list) and quiz:
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            try:
                item = quiz[int(answer.get("i", -1))]
            except (IndexError, TypeError, ValueError):
                continue
            if not isinstance(item, dict) or not item.get("q"):
                continue
            key = _mistake_key(str(item.get("q")))
            at = next((n for n, m in enumerate(bucket) if m.get("q") == key), -1)
            if answer.get("ok"):
                if at != -1:
                    bucket.pop(at)
            elif at == -1:
                bucket.append({
                    "q": key,
                    "opts": list(item.get("opts") or []),
                    "a": item.get("a"),
                    "why": item.get("why") or "",
                })
        return bucket

    # Legacy callers send no per-question detail: keep the error signal alive
    # so the review activity still has cards to show.
    if score < 0.8 and quiz:
        known = {m.get("q") for m in bucket}
        for item in quiz:
            if not isinstance(item, dict) or not item.get("q"):
                continue
            key = _mistake_key(str(item.get("q")))
            if key not in known:
                bucket.append({
                    "q": key,
                    "opts": list(item.get("opts") or []),
                    "a": item.get("a"),
                    "why": item.get("why") or "",
                })
    elif score >= 0.8:
        return []
    return bucket


# --------------------------------------------------------------------------
# rolling plan (one material = one plan)
# --------------------------------------------------------------------------

def plan_material(
    topics: Sequence[TopicState],
    round_no: int = 1,
    visited: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Plan the next activity across a whole material.

    Every unmastered, pre-tested topic gets exactly one step per round.  The
    round ends when each candidate has been served once; then the round number
    increments and everything is re-ranked with fresh mastery values.  This
    "rolling" schedule is what stops the agent from grinding a single hard
    topic while three easy ones sit untouched.
    """
    visited = list(visited or [])
    pending_pretest = sum(1 for t in topics if not t.pretested)
    candidates = [t for t in topics if t.pretested and not is_mastered(t)]
    candidate_ids = {t.topic_id for t in candidates}

    steps: list[dict[str, Any]] = []
    for index, topic in enumerate(topics):
        # A topic that has not been pre-tested is not scheduled yet: the client
        # must run the Pre-test first, otherwise the "next step" card would point
        # at a topic whose starting mastery is still unknown.
        if topic.topic_id not in candidate_ids:
            continue
        decision = decide(topic)
        if decision.activity is None:
            continue
        stage = stage_of(topic)
        steps.append({
            "topic_id": topic.topic_id,
            "topic_name": topic.name,
            "activity": decision.activity,
            "rule": decision.rule,
            "reason": decision.reason,
            "stage": stage,
            "mastery": _pct(topic.mastery),
            "wrong": topic.wrong_count,
            "next_round": topic.topic_id in visited,
            "_order": (
                STAGE_ORDER.get(stage, 9),
                float(topic.mastery),
                -topic.wrong_count,
                index,
            ),
        })

    steps.sort(key=lambda s: (1 if s["next_round"] else 0, s["_order"]))
    for step in steps:
        step.pop("_order", None)

    by_topic = {t.topic_id: decide(t).to_dict() for t in topics}
    blocked = [t.topic_id for t in topics if stage_of(t) == "blocked"]
    visited_alive = sum(1 for tid in visited if any(t.topic_id == tid for t in candidates))

    return {
        "round": int(round_no),
        "total": len(topics),
        "mastered": sum(1 for t in topics if is_mastered(t)),
        "remaining": len(candidates),
        "pending_pretest": pending_pretest,
        "visited": visited_alive,
        "steps": steps,
        "next": steps[0] if steps else None,
        "last_round_done": bool(steps) and all(s["next_round"] for s in steps),
        "blocked": blocked,
        "done": bool(topics) and pending_pretest == 0 and not candidates,
        "by_topic": by_topic,
    }


# --------------------------------------------------------------------------
# facts only - the same envelope, without choosing anything
# --------------------------------------------------------------------------

PRETEST = "pretest"
READY = "ready"
DONE = "done"
BLOCKED = "blocked"


def fact_status(topic: TopicState) -> str:
    """Where a topic stands as a *fact*, without picking an activity.

    ``stage_of`` asks ``decide()`` what to do and turns the answer into a badge;
    this asks only what is objectively true: not assessed yet / finished /
    nothing to measure with / ready to be scheduled.  The distinction is what
    lets the model own the choice while the payload still reports status.
    """
    if not topic.pretested:
        return PRETEST
    if is_mastered(topic):
        return DONE
    if topic.quiz_count == 0:
        # Reading is possible but there is no way to measure the result, so no
        # activity would ever move this topic forward.
        return BLOCKED
    return READY


def plan_facts(
    topics: Sequence[TopicState],
    round_no: int = 1,
    visited: Sequence[str] | None = None,
) -> dict[str, Any]:
    """``plan_material`` minus the decision: counts, statuses, no ``next``.

    Used on the path where the model chooses.  It keeps every number the UI
    needs (round, mastered, remaining, thresholds arrive in the envelope) and
    every per-topic status, but ``steps`` is empty and ``next`` is ``None`` until
    ``apply_decision`` puts the model's choice in.  Nothing here calls
    ``decide()`` - that function only serves the explicit ``rule`` mode.
    """
    visited = list(visited or [])
    candidates = [t for t in topics if fact_status(t) == READY]

    by_topic: dict[str, dict[str, Any]] = {}
    for topic in topics:
        status = fact_status(topic)
        by_topic[topic.topic_id] = {
            "activity": None,
            "rule": "",
            "reason": "",
            "mastered": status == DONE,
            "blocked": status == BLOCKED,
            "status": status,
            "mastery": _pct(topic.mastery),
            "wrong": topic.wrong_count,
            "explain_pages": int(topic.explain_pages),
            "quiz_count": int(topic.quiz_count),
            "need_help": bool(topic.need_help),
        }

    return {
        "round": int(round_no),
        "total": len(topics),
        "mastered": sum(1 for t in topics if is_mastered(t)),
        "remaining": len(candidates),
        "pending_pretest": sum(1 for t in topics if not t.pretested),
        "visited": sum(1 for tid in visited if any(t.topic_id == tid for t in candidates)),
        "steps": [],
        "next": None,
        "last_round_done": bool(candidates) and all(t.topic_id in visited for t in candidates),
        "blocked": [t.topic_id for t in topics if fact_status(t) == BLOCKED],
        "done": bool(topics) and all(t.pretested for t in topics) and not candidates,
        "by_topic": by_topic,
        "decided_by_model": True,
    }


def advance_round(
    topics: Sequence[TopicState],
    round_no: int,
    visited: Sequence[str],
    finished_topic: str | None,
) -> tuple[int, list[str]]:
    """Book-keeping after one activity: mark the topic as served this round.

    A round closes when every *current* candidate has been served once.  If
    nothing is left to study the round simply stops advancing, so "round 12"
    is never reported for a material that is already finished.
    """
    visited = list(visited or [])
    if finished_topic and finished_topic not in visited:
        visited.append(finished_topic)

    candidates = [t.topic_id for t in topics if t.pretested and not is_mastered(t)]
    if not candidates:
        return int(round_no), []
    if all(tid in visited for tid in candidates):
        return int(round_no) + 1, []
    return int(round_no), [tid for tid in visited if tid in candidates]
