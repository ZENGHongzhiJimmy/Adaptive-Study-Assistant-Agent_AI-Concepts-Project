"""P4 LLM content generation: grounded Teach/Review + structured MCQ.

Design guarantees (README 12.1 / 12.2):

* **Objective scoring stays deterministic.** The LLM only *writes* MCQs;
  ``evaluator.py`` still scores them in code. ``subjective_rubric_enabled``
  remains false, so Learning Gain is not polluted by scoring noise.
* **Citations come from P1 metadata only.** The LLM is told never to write a
  source line; we append ``Source: Lecture 05, Page 14`` from the retrieved
  chunk after the call, and strip any source lines the model wrote anyway.
* **Every failure degrades safely.** Missing API key, empty retrieval, network
  error, retired model, or unparsable JSON all fall back to the P3 offline
  template / static question bank. The LangGraph loop never crashes.
* **Usage is recorded.** Every call accumulates latency + tokens + the model
  that actually answered, so the existing P2 ``events`` columns get real values.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import warnings
from dataclasses import dataclass
from typing import Any, Sequence

from ..utils.llm_client import LLMClient, key_setup_message
from ..utils.quiz_order import move_correct_to, seed_for
from .prompts.templates import (
    NO_EXCERPTS,
    QUESTION_RETRY,
    QUESTION_SYSTEM,
    QUESTION_USER,
    REVIEW_STYLE_NOTE,
    TEACH_SYSTEM,
    TEACH_USER,
    format_context,
    format_weak_points,
)
from .questions import MCQ

OPTION_KEYS = ("A", "B", "C", "D")
_SOURCE_LINE = re.compile(r"^\s*(source|reference|citation|sources)\s*:.*$", re.IGNORECASE)


class QuestionParseError(ValueError):
    """LLM output is not a valid, evaluator-compatible MCQ JSON object."""


@dataclass(frozen=True)
class TeachContent:
    text: str
    citation: str | None
    lecture: str | None
    page: int | None
    grounded: bool
    source: str            # llm | template
    usage: dict[str, Any]


@dataclass(frozen=True)
class QuestionResult:
    question: MCQ
    source: str            # llm | bank | fallback
    usage: dict[str, Any]
    note: str = ""


class _UsageRecorder:
    """Accumulates usage across calls in one action step (incl. retries)."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.latency_ms = 0.0
        self.calls = 0
        self.model: str | None = None

    def add(self, response: Any) -> None:
        self.prompt_tokens += int(getattr(response, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(response, "completion_tokens", 0) or 0)
        self.total_tokens += int(getattr(response, "total_tokens", 0) or 0)
        self.latency_ms += float(getattr(response, "latency_ms", 0) or 0)
        self.model = getattr(response, "model", None) or self.model
        self.calls += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            # :free models cost $0. P6 fills paid pricing from OpenRouter.
            "cost_usd": 0.0,
            "latency_ms": round(self.latency_ms, 2),
            "model": self.model,
            "llm_calls": self.calls,
        }


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise QuestionParseError("no JSON object found in the reply")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise QuestionParseError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise QuestionParseError("top-level JSON value must be an object")
    return payload


def parse_question_payload(
    raw: str,
    *,
    topic: str,
    difficulty: str,
    index: int,
) -> MCQ:
    """Turn one LLM reply into an MCQ that ``evaluator.py`` can score exactly.

    Accepts fenced JSON, ``question``/``stem``/``prompt`` aliases, options as a
    dict or a list, and ``correct_option`` as a letter, a lettered string, or
    the full option text. Anything ambiguous raises ``QuestionParseError`` so
    the caller can fall back to the static bank instead of scoring garbage.
    """
    payload = _extract_json(raw)

    stem = next(
        (
            str(payload[key]).strip()
            for key in ("question", "stem", "prompt", "question_text")
            if isinstance(payload.get(key), str) and payload[key].strip()
        ),
        "",
    )
    if not stem:
        raise QuestionParseError("missing non-empty 'question' field")

    raw_options = payload.get("options", payload.get("choices"))
    if isinstance(raw_options, dict):
        options = {
            str(key).strip().upper(): str(value).strip()
            for key, value in raw_options.items()
        }
    elif isinstance(raw_options, (list, tuple)) and len(raw_options) >= 3:
        options = {
            OPTION_KEYS[i]: (
                str(item.get("text", "")).strip()
                if isinstance(item, dict) else str(item).strip()
            )
            for i, item in enumerate(raw_options[:4])
        }
    else:
        raise QuestionParseError("'options' must be a dict or a list of >= 3 items")

    options = {key: value for key, value in options.items() if key in OPTION_KEYS and value}
    if len(options) < 3:
        raise QuestionParseError("need at least 3 non-empty A-D options")
    if len(set(options.values())) != len(options):
        raise QuestionParseError("option texts must be distinct")

    raw_correct = next(
        (
            payload[key]
            for key in ("correct_option", "answer", "correct_answer", "correct")
            if payload.get(key) is not None
        ),
        None,
    )
    if raw_correct is None:
        raise QuestionParseError("missing 'correct_option'")
    candidate = str(raw_correct).strip().upper()
    correct = candidate if candidate in options else ""
    if not correct and candidate and candidate[0] in options:
        correct = candidate[0]  # "B) ..." or "Option C"
    if not correct and len(str(raw_correct).strip()) > 1:
        # Accept the full option text as the answer, but never treat a single
        # stray letter like "Z" as option text - reject it instead.
        correct = next(
            (key for key, value in options.items()
             if value.strip().upper() == str(raw_correct).strip().upper()),
            "",
        )
    if not correct:
        raise QuestionParseError(f"correct_option {raw_correct!r} is not one of {list(options)}")

    digest = hashlib.sha1(
        "|".join((topic, difficulty, str(index), stem, json.dumps(options, sort_keys=True), correct))
        .encode("utf-8")
    ).hexdigest()[:12]
    return MCQ(
        question_id=f"llm-{digest}",
        prompt=stem,
        options=options,
        correct_option=correct,
    )


def _reorder_mcq_options(mcq: MCQ) -> MCQ:
    """Move the correct option off its usual first slot.

    Parsing stays a pure translation of the reply (tests rely on that); the
    option order is rebalanced here so a whole self-test does not read
    "A, A, A, B". The shuffle is seeded from the question id + stem, so the same
    question always renders identically while different questions land on
    different letters. ``question_id`` is deliberately unchanged - it identifies
    the question, not the layout.
    """
    keys = [key for key in OPTION_KEYS if key in mcq.options]
    if len(keys) < 2 or mcq.correct_option not in keys:
        return mcq
    values = [mcq.options[key] for key in keys]
    correct = keys.index(mcq.correct_option)
    rng = random.Random(seed_for(mcq.question_id, mcq.prompt))
    target = rng.randrange(len(values))
    new_values, new_index = move_correct_to(values, correct, target, rng)
    return MCQ(
        question_id=mcq.question_id,
        prompt=mcq.prompt,
        options=dict(zip(keys, new_values)),
        correct_option=keys[new_index],
    )


def _strip_citation_lines(text: str) -> str:
    kept = [line for line in text.splitlines() if not _SOURCE_LINE.match(line)]
    return "\n".join(kept).strip()


class LLMContentGenerator:
    """Injectable LLM-backed content provider with deterministic fallbacks."""

    def __init__(
        self,
        config: dict[str, Any],
        client: Any | None = None,
    ) -> None:
        self.config = config
        self.client = client if client is not None else LLMClient(config)
        agent = config.get("agent", {})
        self.max_json_retries = max(0, int(agent.get("max_json_retries", 2)))
        self.teach_max_chars = int(agent.get("teach_max_chars", 900))
        self.min_hits = int(agent.get("grounded_min_hits", 1))
        self.total_calls = 0
        self._warned_about_key = False

    @property
    def available(self) -> bool:
        return bool(getattr(self.client, "is_configured", False))

    def _chat(self, messages: list[dict[str, str]], max_tokens: int | None = None) -> Any:
        limit = max_tokens or min(int(getattr(self.client, "max_tokens", 600)), 600)
        response = self.client.chat(messages, max_tokens=limit)
        self.total_calls += 1
        return response

    def _warn_missing_key(self) -> None:
        if not self._warned_about_key:
            warnings.warn(key_setup_message(), stacklevel=2)
            self._warned_about_key = True

    # --- Teach / Review ------------------------------------------------------

    def _template_teach(
        self,
        topic: str,
        hits: Sequence[Any],
        style: str,
        recent_errors: Sequence[Any] | None,
    ) -> TeachContent:
        top = hits[0] if hits else None
        weak = format_weak_points(list(recent_errors or []))
        if top is not None:
            text = (
                f"[{topic}] {style}（离线模板：未启用 LLM）讲义要点："
                f"{str(getattr(top, 'text', ''))[:500]}"
            )
        else:
            text = (
                f"[{topic}] {style}（离线占位：未命中讲义页，也未启用 LLM；"
                "P4 的 grounded 讲解需先构建 P1 索引并配置 .env 中的 API Key）"
            )
        if style == "Review":
            text += f"\n重点纠正近期错题：{weak}。"
        citation = getattr(top, "source", None) if top is not None else None
        return TeachContent(
            text=text,
            citation=citation,
            lecture=getattr(top, "lecture", None) if top is not None else None,
            page=getattr(top, "page", None) if top is not None else None,
            grounded=top is not None,
            source="template",
            usage={},
        )

    def teach(
        self,
        topic: str,
        hits: Sequence[Any] | None,
        *,
        style: str = "Teach",
        recent_errors: Sequence[Any] | None = None,
    ) -> TeachContent:
        """Grounded explanation; degrades safely with no key or no retrieval."""
        hits = list(hits or [])
        if not self.available:
            self._warn_missing_key()
        if not self.available or len(hits) < self.min_hits:
            return self._template_teach(topic, hits, style, recent_errors)

        style_note = "" if style != "Review" else f"\n{REVIEW_STYLE_NOTE}"
        messages = [
            {"role": "system", "content": TEACH_SYSTEM},
            {"role": "user", "content": TEACH_USER.format(
                topic=topic,
                style=f"{style}{style_note}",
                weak_points=format_weak_points(list(recent_errors or [])),
                context=format_context(hits),
            )},
        ]
        usage = _UsageRecorder()
        try:
            response = self._chat(messages)
        except Exception as exc:
            warnings.warn(
                f"[P4] LLM {style} failed, degrading to template: "
                f"{type(exc).__name__}: {exc}",
                stacklevel=2,
            )
            return self._template_teach(topic, hits, style, recent_errors)
        usage.add(response)

        top = hits[0]
        citation = getattr(top, "source", None)
        body = _strip_citation_lines(response.text)[: self.teach_max_chars]
        if not body:
            warnings.warn("[P4] empty LLM explanation, degrading to template", stacklevel=2)
            return self._template_teach(topic, hits, style, recent_errors)
        text = f"{body}\n\n{citation}" if citation else body
        return TeachContent(
            text=text,
            citation=citation,
            lecture=getattr(top, "lecture", None),
            page=getattr(top, "page", None),
            grounded=bool(citation),
            source="llm",
            usage=usage.as_dict(),
        )

    # --- MCQ / Challenge -----------------------------------------------------

    def question(
        self,
        topic: str,
        hits: Sequence[Any] | None,
        *,
        difficulty: str,
        index: int,
        bank_question: MCQ,
    ) -> QuestionResult:
        """One structured MCQ; falls back to the static bank on any failure."""
        if not self.available:
            self._warn_missing_key()
            return QuestionResult(
                bank_question, "bank", {},
                "no API key configured; used the deterministic P3 question bank",
            )

        context = format_context(list(hits or []))
        if context == NO_EXCERPTS:
            warnings.warn(
                "[P4] no lecture excerpt retrieved; question generated from topic only",
                stacklevel=2,
            )
        system = {"role": "system", "content": QUESTION_SYSTEM}
        base_user = {"role": "user", "content": QUESTION_USER.format(
            topic=topic, difficulty=difficulty, index=index, context=context,
        )}
        usage = _UsageRecorder()
        last_error = ""
        previous = ""

        for attempt in range(self.max_json_retries + 1):
            messages = [system, base_user] if attempt == 0 else [
                system,
                {"role": "user", "content": QUESTION_RETRY.format(
                    error=last_error, previous=previous[:300],
                )},
            ]
            try:
                response = self._chat(messages)
                usage.add(response)
                previous = response.text
                mcq = parse_question_payload(
                    previous, topic=topic, difficulty=difficulty, index=index,
                )
                return QuestionResult(_reorder_mcq_options(mcq), "llm", usage.as_dict())
            except QuestionParseError as exc:
                last_error = str(exc)
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                break

        warnings.warn(
            f"[P4] question generation degraded to the static bank: {last_error}",
            stacklevel=2,
        )
        return QuestionResult(bank_question, "fallback", usage.as_dict(), last_error)
