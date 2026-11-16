"""LLM decision module: the model chooses the next activity, code only defines legality.

Division of labour (see README §5.3)
------------------------------------
* ``legal_candidates()`` enumerates what is *possible* right now.  A topic with
  no quiz cannot be practised; a mastered topic is not a candidate.  These are
  facts about the material, not decisions, so they stay in code.
* ``LLMPlanner.choose()`` picks exactly one candidate and writes the reason.
  Nothing here hard-codes a preference between "rescue the weakest topic" and
  "push the overall progress" - that trade-off is the model's judgement, which
  is the whole point of moving the decision out of ``policy.decide()``.
* ``validate_choice()`` rejects a choice that violates a fact (hallucinated
  topic, no quiz, verbatim repeat of the previous step) and the reply is asked
  again with the error fed back.
* ``apply_decision()`` puts the chosen step into the plan (which is built from
  facts only, so the model's step is the only step in it).

What this module deliberately does NOT touch
--------------------------------------------
``policy.apply_result`` / ``apply_pre_test`` / ``is_mastered`` stay exactly as
they were.  Mastery is a *measurement*, not a decision: if the model scored its
own work, the learning-gain numbers in the report would have no ground truth.
``policy.decide()`` is no longer reachable from the product path at all - it
survives only as the ``adaptive`` baseline in ``experiments/methods.py`` and as
the explicit ``agent.planner: "rule"`` demo mode.  There is no automatic
fallback to it: when the model cannot answer, the response says
``decided_by = unavailable`` and ships no ``next``.

Escalation (P12): a free model sometimes answers with a truncated / off-format
reply, which is a *content* failure, not an HTTP failure - ``LLMClient.chat``
only switches models on 429/404/network errors, so the decision would simply be
lost.  ``choose()`` therefore asks once, and if no usable decision came back it
re-asks the same question on ``planner_escalation_models`` with a larger token
budget.  The model still decides; what changes is the model and/or the room it
gets.  Listing the primary's own id is legal - see ``_escalation_models``.

Boundary note: this module owns the decision contract end to end - the facts it
sends, the candidate list it allows, the JSON it parses and the validation that
follows.  Measurement stays in ``policy.py``; nothing here decides what counts
as mastery, and nothing here falls back to a rule when the model cannot answer.
"""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from dataclasses import dataclass
from typing import Any, Sequence

from ..utils.llm_client import LLMClient
from .policy import (
    EXPLAIN,
    EXPLAIN_CAP,
    MASTERED,
    PRACTICE,
    REVIEW,
    TopicState,
    is_mastered,
)
from .prompts.decision import (
    DECISION_MECHANICS,
    DECISION_RETRY,
    DECISION_SYSTEM,
    DECISION_USER,
)

ACTIVITIES = (EXPLAIN, PRACTICE, REVIEW)
ACTIVITY_LABEL = {EXPLAIN: "讲解", PRACTICE: "练习", REVIEW: "复习"}

# The UI's "判定依据" card reads ``rule``; keep it short and honest about who
# decided, so a trace can never mistake a model decision for a rule decision.
DECIDED_BY = "LLM 决策"
FALLBACK_RULE = "规则兜底"

SOURCE_LLM = "llm"


def _pct(value: float) -> int:
    return int(round(float(value)))


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One legal (topic, activity) pair the model may choose."""

    topic_id: str
    name: str
    activity: str
    mastery: int
    wrong: int
    explain_pages: int
    explain_runs: int
    quiz_count: int
    fail_streak: int

    @property
    def key(self) -> str:
        return f"{self.topic_id}|{self.activity}"

    def line(self, index: int) -> str:
        label = ACTIVITY_LABEL.get(self.activity, self.activity)
        # Mastery / mistake count deliberately omitted: the topic table above
        # already carries them, and repeating them here used to add ~600 prompt
        # tokens per decision (i.e. ~0.3 s of prefill) for zero new information.
        return f"C{index} | {self.topic_id} | {self.activity} ({label}) | {self.name}"


def _candidate(topic: TopicState, activity: str) -> Candidate:
    return Candidate(
        topic_id=topic.topic_id,
        name=topic.name or topic.topic_id,
        activity=activity,
        mastery=_pct(topic.mastery),
        wrong=topic.wrong_count,
        explain_pages=int(topic.explain_pages),
        explain_runs=int(topic.explain_runs),
        quiz_count=int(topic.quiz_count),
        fail_streak=int(topic.fail_streak),
    )


def legal_candidates(
    topics: Sequence[TopicState], limit: int = 30, visited: Sequence[str] = ()
) -> list[Candidate]:
    """Every activity that is objectively possible right now.

    Two facts decide what is legal, and neither is a preference:

    * **Availability** - a topic with no quiz cannot be practised, a mastered
      topic is finished, an un-pre-tested topic has no starting point.
    * **Round coverage** - within one round every ready topic is served once
      before any topic is served twice.  Without this the model can spend the
      whole session on the topic it finds interesting (measured: 15 of 32
      activities on two topics, three topics never read), which starves the
      material exactly like the pre-round scheduling did before the decision
      moved to the model.  The model still chooses *which* due topic and *which*
      activity; the round only says that everyone gets a turn.
      ``visited`` empty means a new round, so the filter is inactive.

    Ordering inside the due set is a plain enumeration (dataset order x
    explain/practice/review), deliberately *not* a ranking: sorting by mastery
    would smuggle the old rule back in through the prompt.

    When there are more possible pairs than ``limit``, the list is filled
    **round-robin across topics** instead of being truncated.  Flat truncation
    silently hid every topic after the 10th (22 topics x 3 activities = 66,
    limit 30), so the model could not even see most of the material.
    """
    served = set(visited or ())
    buckets: list[list[Candidate]] = []
    due: list[list[Candidate]] = []
    for topic in topics:
        if not topic.pretested or is_mastered(topic):
            continue
        row: list[Candidate] = []
        if topic.explain_pages > 0:
            row.append(_candidate(topic, EXPLAIN))
        if topic.quiz_count > 0:
            row.append(_candidate(topic, PRACTICE))
        if topic.wrong_count > 0:
            row.append(_candidate(topic, REVIEW))
        if row:
            buckets.append(row)
            if topic.topic_id not in served:
                due.append(row)

    pool = due or buckets

    if limit <= 0:
        return [c for row in pool for c in row]

    out: list[Candidate] = []
    depth = 0
    while len(out) < limit and any(len(row) > depth for row in pool):
        for row in pool:
            if len(row) > depth:
                out.append(row[depth])
                if len(out) >= limit:
                    break
        depth += 1
    return out


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

def _topic_line(topic: TopicState) -> str:
    read = f"已读 {topic.explain_runs} 次" if topic.explain_done else "未读"
    return (
        f"{topic.topic_id} | {topic.name or topic.topic_id}"
        f" | 掌握度 {_pct(topic.mastery)}% | 错题 {topic.wrong_count}"
        f" | 讲解{read} | 题库 {topic.quiz_count} 题"
        f" | 连续低分 {topic.fail_streak}"
    )


def _recent_lines(recent: Sequence[dict[str, Any]], limit: int = 4) -> list[str]:
    """Recent *activities*, newest first (``plan`` rows are bookkeeping)."""
    lines: list[str] = []
    for row in recent:
        activity = str(row.get("activity") or "")
        if not activity or activity == "plan":
            continue
        before = _pct(float(row.get("mastery_before") or 0))
        after = _pct(float(row.get("mastery_after") or 0))
        note = str(row.get("outcome") or "").strip()
        tail = f" | {note}" if note else ""
        lines.append(
            f"{len(lines) + 1}. {activity} | {row.get('topic_id') or '?'}"
            f" | 掌握度 {before}% → {after}%{tail}"
        )
        if len(lines) >= limit:
            break
    return lines


def build_user_message(
    topics: Sequence[TopicState],
    candidates: Sequence[Candidate],
    round_no: int = 1,
    visited: Sequence[str] = (),
    recent: Sequence[dict[str, Any]] = (),
) -> str:
    """Render the学情 report the model decides from."""
    total = len(topics)
    mastered = sum(1 for t in topics if is_mastered(t))
    pending_pretest = sum(1 for t in topics if not t.pretested)
    remaining = sum(1 for t in topics if t.pretested and not is_mastered(t))

    # Facts of the machinery, rendered with the live policy constants so the
    # prompt cannot drift from ``policy.py``.  Kept in ``prompts/decision.py``
    # because it is part of the decision prompt, and because its fingerprint
    # keys the decision log (see ``experiments/decisions.py::state_key``).
    goal = DECISION_MECHANICS.format(
        mastered=_pct(MASTERED), explain_cap=_pct(EXPLAIN_CAP)
    )
    served = [t.topic_id for t in topics if t.topic_id in set(visited)]
    progress = (
        f"进度：第 {int(round_no)} 轮；共 {total} 个知识点，已掌握 {mastered} 个，"
        f"待学 {remaining} 个，未前测 {pending_pretest} 个；"
        f"本轮已服务 {len(served)} 个"
        + (f"（{'、'.join(served)}）" if served else "")
        + "。本轮内每个知识点最多安排一次，候选清单里只有本轮还没轮到的知识点。"
    )
    topics_block = (
        "知识点现状（id | 名称 | 掌握度 | 错题 | 讲解 | 题库 | 连续低分）：\n"
        + "\n".join(_topic_line(t) for t in topics)
    )
    candidates_block = (
        "合法候选动作（只能从中选一个）：\n"
        + "\n".join(c.line(i + 1) for i, c in enumerate(candidates))
    )
    recents = _recent_lines(recent)
    recent_block = "最近动作（新 → 旧）：\n" + ("\n".join(recents) if recents else "（暂无历史动作）")

    message = DECISION_USER.format(
        goal=goal,
        progress=progress,
        topics_block=topics_block,
        candidates_block=candidates_block,
        recent_block=recent_block,
    )
    # The instruction is repeated *after* the data on purpose: some free
    # "reasoning" models start a prose analysis and get truncated before they
    # ever reach the JSON.  The last line the model reads is the one that wins.
    return message + (
        "\n\n现在直接输出那个 JSON 对象：第一个字符必须是 {，"
        "不要写任何分析、步骤、思考或解释文字。"
    )


# --------------------------------------------------------------------------
# reply parsing / validation
# --------------------------------------------------------------------------

def _extract_json(raw: str) -> dict[str, Any]:
    """First balanced JSON object in the reply (tolerates fences and prose)."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(
            "回复里没有 JSON 对象——不要写分析过程，直接输出 JSON"
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 非法：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须是对象")
    return payload


def validate_choice(
    choice: dict[str, Any],
    candidates: Sequence[Candidate],
    last_step: dict[str, Any] | None = None,
) -> str:
    """Return an error string, or ``""`` when the choice is usable."""
    activity = str(choice.get("activity") or "").strip().lower()
    topic_id = str(choice.get("topic_id") or "").strip()

    if activity not in ACTIVITIES:
        return f"activity 必须是 {list(ACTIVITIES)} 之一，收到 {activity!r}"
    if not topic_id:
        return "缺少 topic_id"

    keys = {c.key for c in candidates}
    if f"{topic_id}|{activity}" not in keys:
        same_topic = [c.activity for c in candidates if c.topic_id == topic_id]
        if not same_topic:
            return f"topic_id {topic_id!r} 不在候选清单里"
        return f"知识点 {topic_id!r} 现在不能选 {activity}，可选 {same_topic}"

    if last_step:
        last_topic = str(last_step.get("topic_id") or "")
        last_activity = str(last_step.get("activity") or "")
        if last_topic == topic_id and last_activity == activity:
            return f"与上一步完全相同（{activity} / {topic_id}），请换一步"

    return ""


# --------------------------------------------------------------------------
# decision object
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PlannerDecision:
    """One model decision, shaped like ``policy.Decision`` for the UI."""

    topic_id: str
    activity: str
    reason: str
    confidence: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    tokens: int = 0
    source: str = SOURCE_LLM
    rule: str = DECIDED_BY
    #: True when the primary (usually free) model produced nothing usable and a
    #: stronger model made the call instead.  Surfaced in the UI so a decision
    #: that only appeared after escalation is never mistaken for a first-try one.
    escalated: bool = False
    #: Which pass over the model chain produced this decision (1 = first pass).
    #: 2+ means the first pass was lost - usually to truncated reasoning - and
    #: the same question was asked again rather than shipping no step.
    attempt_round: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "activity": self.activity,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 3),
            "rule": self.rule,
            "source": self.source,
            "model": self.model,
            "latency_ms": round(float(self.latency_ms), 2),
            "tokens": int(self.tokens),
            "escalated": bool(self.escalated),
            # Passes over the model chain it took to get this decision. Same key
            # as the failure envelope, so the page reads one field either way.
            "rounds": int(self.attempt_round),
        }


def _last_activity(recent: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for row in recent:
        activity = str(row.get("activity") or "")
        if activity and activity != "plan":
            return row
    return None


def _as_float(value: Any, default: float = 0.0) -> float:
    """A model can put ``"high"`` in a numeric field; never crash over it."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _attempt_summary(log: Sequence[dict[str, Any]]) -> str:
    """One line listing every model that was asked and how it failed."""
    parts: list[str] = []
    for item in log:
        if item.get("reason") == "ok":
            continue
        detail = f" ({item['error']})" if item.get("error") else ""
        parts.append(f"{item.get('model')}: {item.get('reason')}{detail}")
    return " | ".join(parts) or "unknown"


# --------------------------------------------------------------------------
# the planner
# --------------------------------------------------------------------------

class LLMPlanner:
    """Asks the model to choose, and reports *why* when it cannot.

    There is no rule substitution behind this class.  ``choose()`` returns
    ``None`` only when the model produced no usable decision, and
    ``unavailable_reason`` records which failure it was, so the caller can say
    "no decision" instead of quietly showing a rule-made step as if the agent
    had picked it.
    """

    def __init__(self, config: dict[str, Any], client: Any | None = None) -> None:
        self.config = config
        agent_cfg = config.get("agent", {}) or {}
        self.max_candidates = int(agent_cfg.get("planner_max_candidates", 30))
        self.retries = max(0, int(agent_cfg.get("planner_retries", 1)))
        self.max_tokens = int(agent_cfg.get("planner_max_tokens", 700))
        self.use_cache = bool(agent_cfg.get("planner_cache", True))
        self.model = agent_cfg.get("planner_model") or None
        self.client = client if client is not None else LLMClient(config)
        self.escalation_models = self._escalation_models(config, agent_cfg)
        self.escalation_retries = max(
            0, int(agent_cfg.get("planner_escalation_retries", self.retries))
        )
        # How many full passes over [primary, *escalation_models] to make before
        # giving up. 1 = the old single-pass behaviour; 2 or more = re-ask the
        # same question on the same models, which is what recovers a round lost
        # to truncated reasoning.  Worst-case call count is
        # rounds * len(chain) * (retries + 1), so keep it small.
        self.rounds = max(1, int(agent_cfg.get("planner_rounds", 2)))
        # A stronger model usually needs more room for its reasoning tokens;
        # truncation is one of the reasons escalation happens in the first place.
        self.escalation_max_tokens = int(
            agent_cfg.get("planner_escalation_max_tokens") or max(self.max_tokens, 1200)
        )
        self._cache: dict[str, PlannerDecision] = {}
        self.calls = 0
        self.decisions = 0
        self.failures = 0
        # Why the last ``choose()`` produced nothing.  Empty means it succeeded.
        self.unavailable_reason = ""
        self.last_error = ""
        # True when the last successful decision came from an escalation model.
        self.escalated = False
        # How many passes over the chain the last ``choose()`` actually used.
        self.cycles_used = 0
        # Per-attempt record (model + reason), for the trace and the UI.  Never
        # contains prompt text - only which model answered and how it failed.
        self.attempt_log: list[dict[str, Any]] = []

    def _escalation_models(
        self, config: dict[str, Any], agent_cfg: dict[str, Any]
    ) -> list[str]:
        """Models to re-ask when the primary one produced nothing usable.

        Default source is ``llm.fallback_models`` - the same paid model the
        transport layer already uses for 429/404.  Reusing it means there is one
        list to maintain, not two that can drift.  ``agent.planner_escalation_models``
        overrides it (``[]`` disables escalation entirely).

        The primary's own id is allowed in this list on purpose.  Escalation is
        not only a change of model: it also re-asks with
        ``planner_escalation_max_tokens`` and the parse-error feedback from the
        failed attempt.  When the failure is "the model narrated its reasoning
        past the token cap", swapping vendors does not help - the same model
        with more room does.  Naming it here is explicit configuration, not a
        silent duplicate.
        """
        raw = agent_cfg.get("planner_escalation_models")
        if raw is None:
            raw = (config.get("llm", {}) or {}).get("fallback_models", [])
        out: list[str] = []
        for item in raw or []:
            name = str(item).strip()
            if name and name not in out:
                out.append(name)
        return out

    @property
    def model_chain(self) -> list[str | None]:
        """The models asked in one pass: primary first, then the fallbacks.

        ``None`` in slot 0 means "whatever ``llm.model`` says" - the client
        resolves it.  Duplicates are dropped so a pass never asks one model
        twice in a row, which would waste a call without changing the odds.
        """
        chain: list[str | None] = [self.model]
        for name in self.escalation_models:
            if name not in chain:
                chain.append(name)
        return chain

    @property
    def available(self) -> bool:
        return bool(getattr(self.client, "is_configured", False))

    def choose(
        self,
        topics: Sequence[TopicState],
        round_no: int = 1,
        visited: Sequence[str] = (),
        recent: Sequence[dict[str, Any]] = (),
    ) -> PlannerDecision | None:
        """Pick the next (topic, activity), or ``None`` when the model cannot.

        The chain is asked in cycles, and every shot is still the *model*
        deciding (the prompt and the token budget never change):

        1. the configured model, with ``planner_retries`` error-feedback retries;
        2. if that produced nothing usable, the same question again on
           ``escalation_models`` (the fallback) with its own token budget;
        3. if the whole chain still produced nothing, start over at step 1 -
           ``planner_rounds`` full passes in total.

        Step 3 exists because a thinking model can spend its entire budget on
        prose and never reach the JSON: that is a per-call accident, not a
        property of the state. One pass therefore loses the round whenever both
        models truncate on the same attempt (observed in practice), while the
        next pass usually succeeds with the identical prompt. Re-asking is
        cheaper and more honest than shipping no step, and "no step" stays
        reserved for a genuinely dead endpoint or a missing key.

        ``None`` is returned only after every pass refused; ``unavailable_reason``
        then names the last failure, ``last_error`` lists every attempt, and
        ``cycles_used`` reports how many passes were needed.
        """
        self.unavailable_reason = ""
        self.last_error = ""
        self.escalated = False
        self.attempt_log = []

        if not topics:
            return None
        if not self.available:
            # A missing key is a deployment fact, not a transient error: the
            # caller must say "no model connected" rather than fall back.
            self.unavailable_reason = "no_api_key"
            self.last_error = "未配置 OPENROUTER_API_KEY"
            return None

        candidates = legal_candidates(topics, self.max_candidates, visited)
        if not candidates:
            # Nothing legal to do: either everything is mastered or everything
            # is blocked.  Not a failure - the plan's own flags say which.
            self.unavailable_reason = "no_candidates"
            return None

        message = build_user_message(topics, candidates, round_no, visited, recent)
        # Deterministic replay: the same state always yields the same decision,
        # which is what makes an 8-seed rerun comparable at all.
        cache_key = hashlib.sha1(f"{self.model}|{message}".encode("utf-8")).hexdigest()
        if self.use_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            self.escalated = cached.escalated
            return cached

        last_step = _last_activity(recent)

        # One prompt, one budget, several shots: primary -> fallback ->
        # primary -> fallback ... up to ``planner_rounds`` passes.  Nothing
        # about the question changes between passes, so a pass that fails on
        # truncation is worth exactly one more try.
        chain = self.model_chain
        decision = None
        self.cycles_used = 1
        for cycle in range(1, self.rounds + 1):
            self.cycles_used = cycle
            for index, model in enumerate(chain):
                decision = self._attempt(
                    message,
                    candidates,
                    last_step,
                    model=model,
                    max_tokens=self.max_tokens if index == 0 else self.escalation_max_tokens,
                    retries=self.retries if index == 0 else self.escalation_retries,
                    # Only the very first shot of the very first pass is a
                    # "first try"; anything later is a re-judgement and the UI
                    # says so.
                    escalated=not (cycle == 1 and index == 0),
                    attempt_round=cycle,
                )
                if decision is not None:
                    break
            if decision is not None:
                break

        if decision is None:
            reasons = [a["reason"] for a in self.attempt_log if a["reason"] != "ok"]
            self.unavailable_reason = reasons[-1] if reasons else "bad_output"
            self.last_error = _attempt_summary(self.attempt_log)
            warnings.warn(
                "[decision] 所有模型都没有给出可用决策，本步没有决策："
                f"{self.last_error}",
                stacklevel=2,
            )
            return None

        self.escalated = decision.escalated
        if self.use_cache:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[cache_key] = decision
        return decision

    def _attempt(
        self,
        message: str,
        candidates: Sequence[Candidate],
        last_step: dict[str, Any] | None,
        *,
        model: str | None,
        max_tokens: int,
        retries: int,
        escalated: bool,
        attempt_round: int = 1,
    ) -> PlannerDecision | None:
        """One model's shot at the question (plus error-feedback retries).

        ``attempt_round`` is the pass number over the model chain, recorded on
        every attempt so the trace and the UI can say "第 2 轮重判" instead of
        just "escalated".
        """
        label = str(model or getattr(self.client, "model", "") or "primary")
        system = {"role": "system", "content": DECISION_SYSTEM}
        error = ""
        previous = ""

        for attempt in range(retries + 1):
            user_text = message if not attempt else (
                message + "\n\n" + DECISION_RETRY.format(error=error, previous=previous[:300])
            )
            try:
                response = self.client.chat(
                    [system, {"role": "user", "content": user_text}],
                    max_tokens=max_tokens,
                    model=model,
                )
            except Exception as exc:  # network / quota / auth: report, never crash
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.attempt_log.append(
                    {"model": label, "reason": "call_failed", "error": str(exc)[:200],
                     "round": attempt_round}
                )
                warnings.warn(
                    f"[decision] {label} 调用失败：{self.last_error}",
                    stacklevel=2,
                )
                return None

            self.calls += 1
            previous = getattr(response, "text", "") or ""
            try:
                choice = _extract_json(previous)
            except ValueError as exc:
                error = str(exc)
                continue

            error = validate_choice(choice, candidates, last_step)
            if error:
                continue

            decision = PlannerDecision(
                topic_id=str(choice["topic_id"]).strip(),
                activity=str(choice["activity"]).strip().lower(),
                reason=str(choice.get("reason") or "").strip() or "（模型未给出理由）",
                confidence=_as_float(choice.get("confidence")),
                model=str(getattr(response, "model", "") or ""),
                latency_ms=_as_float(getattr(response, "latency_ms", 0.0)),
                tokens=int(getattr(response, "total_tokens", 0) or 0),
                escalated=escalated,
                attempt_round=attempt_round,
            )
            self.decisions += 1
            self.attempt_log.append(
                {"model": decision.model or label, "reason": "ok", "round": attempt_round}
            )
            return decision

        self.failures += 1
        self.last_error = error or "unknown"
        self.attempt_log.append(
            {"model": label, "reason": "bad_output", "error": error[:200],
             "round": attempt_round}
        )
        warnings.warn(
            f"[decision] {label} 没有给出合法决策：{self.last_error}",
            stacklevel=2,
        )
        return None


def make_planner(config: dict[str, Any] | None = None) -> LLMPlanner | None:
    """Build the planner when ``agent.planner == "llm"``.

    A missing key still returns a planner.  Returning ``None`` would silently
    switch the product to the rule policy, which is exactly the substitution
    this design removes: the response has to be able to say "no model
    connected" (``decided_by = unavailable``) instead of presenting a rule-made
    step as an agent decision.

    ``mode == "rule"`` is different: it is an explicit configuration used by
    tests and by the rule-mode demo, so it returns ``None`` and ``serve.py``
    runs ``plan_material`` as before.
    """
    from ..utils.config import get_config

    cfg = config or get_config()
    mode = str((cfg.get("agent", {}) or {}).get("planner", "rule")).strip().lower()
    if mode != "llm":
        return None

    planner = LLMPlanner(cfg)
    if not planner.available:
        warnings.warn(
            "[decision] agent.planner=llm 但未配置 OPENROUTER_API_KEY："
            "不会退回规则策略，响应会标记 decided_by=unavailable",
            stacklevel=2,
        )
    return planner


# --------------------------------------------------------------------------
# apply the decision to a plan
# --------------------------------------------------------------------------

def apply_decision(
    plan: dict[str, Any],
    decision: PlannerDecision,
    topics: Sequence[TopicState],
) -> dict[str, Any]:
    """Put the model's choice into a facts-only plan.

    ``plan_facts`` deliberately contains no step, so this builds the one step
    that exists: the model's.  The rest of the payload (counts, statuses,
    blocked ids, thresholds in the envelope) stays exactly as computed from
    facts - the code never adds a second, rule-made step next to it.
    """
    topic = next((t for t in topics if t.topic_id == decision.topic_id), None)
    if topic is None:
        return plan

    stage = "help" if (decision.activity == EXPLAIN and topic.need_help) else decision.activity
    step = {
        "topic_id": decision.topic_id,
        "topic_name": topic.name,
        "activity": decision.activity,
        "rule": decision.rule,
        "reason": decision.reason,
        "stage": stage,
        "mastery": int(round(float(topic.mastery))),
        "wrong": topic.wrong_count,
        "next_round": False,
        # True when the first pass produced nothing: the UI says so, so a
        # decision that only appeared on a later pass is never read as a
        # first-try answer.
        "escalated": bool(decision.escalated),
        # Which pass over the model chain produced this step (1 = first try).
        "round": int(decision.attempt_round),
    }

    by_topic = dict(plan.get("by_topic") or {})
    facts = dict(by_topic.get(decision.topic_id) or {})
    facts.update({
        "activity": decision.activity,
        "rule": decision.rule,
        "reason": decision.reason,
        "blocked": False,
        "mastered": False,
    })
    by_topic[decision.topic_id] = facts

    updated = dict(plan)
    updated["steps"] = [step]
    updated["next"] = step
    updated["by_topic"] = by_topic
    updated["decision"] = decision.to_dict()
    updated["decided_by_model"] = True
    return updated


__all__ = [
    "ACTIVITIES",
    "Candidate",
    "LLMPlanner",
    "PlannerDecision",
    "apply_decision",
    "build_user_message",
    "legal_candidates",
    "make_planner",
    "validate_choice",
]
