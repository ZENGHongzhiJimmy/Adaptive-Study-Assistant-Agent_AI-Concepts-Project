"""Teach / Review / MCQ / Challenge activities.

P3 used the offline templates + static question bank only (no LLM). P4 adds an
optional injectable ``LLMContentGenerator``; with ``generator=None`` the exact
P3 behaviour is preserved, which keeps the 46 offline regression tests and the
reproducible baseline intact.

``Teach`` optionally uses the P1 knowledge base: retrieval hits provide the
grounded excerpt and its ``Source: Lecture 05, Page 14`` citation. When the
index is empty or unavailable, or the API key / LLM call fails, the loop
degrades safely to an offline template instead of crashing.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Sequence

from ..retrieval.embeddings import HashingEmbedder
from ..utils.config import resolve_path
from .llm_content import LLMContentGenerator, TeachContent
from .questions import MCQ

Retriever = Callable[[str], Sequence[Any]]


@dataclass(frozen=True)
class ActionResult:
    action: str
    content: str
    citation: str | None
    lecture: str | None
    page: int | None
    grounded: bool
    question: dict[str, Any] | None
    mcq_index: int
    usage: dict[str, Any] = field(default_factory=dict)
    content_source: str = "template"

    def as_state_update(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "teach_text": self.content,
            "citation": self.citation,
            "lecture": self.lecture,
            "page": self.page,
            "grounded": self.grounded,
            "question": self.question,
            "mcq_index": self.mcq_index,
            "llm_usage": self.usage,
            "content_source": self.content_source,
        }


def build_retriever(
    config: dict[str, Any],
    embedder: Any | None = None,
) -> Retriever | None:
    """Return ``topic -> retrieved chunks`` if a usable P1 index exists.

    P3 indexes are built with the deterministic hashing backend, so that is the
    default query embedder. P6 must freeze a single backend for both sides.
    """
    persist = resolve_path(config["paths"]["chroma_persist_dir"])
    if not persist.is_dir():
        return None
    try:
        from ..retrieval.vector_store import ChromaVectorStore

        store = ChromaVectorStore(
            persist,
            collection_name=config.get("retrieval", {}).get("collection_name", "course_chunks"),
        )
    except Exception as exc:  # missing chromadb, locked/corrupt index, etc.
        warnings.warn(f"[P3] RAG index unavailable, Teach falls back: {exc}", stacklevel=2)
        return None

    if store.count() == 0:
        store.close()
        return None

    query_embedder = embedder or HashingEmbedder(
        int(config.get("embedding", {}).get("hashing_dimension", 256))
    )

    def retrieve(topic: str) -> Sequence[Any]:
        try:
            return store.search(
                topic, query_embedder,
                top_k=int(config.get("retrieval", {}).get("top_k", 3)),
                topic=topic,
            )
        except Exception as exc:
            warnings.warn(f"[P3] retrieval failed, Teach falls back: {exc}", stacklevel=2)
            return []

    retrieve.close = store.close  # type: ignore[attr-defined]
    return retrieve


def render_teach(topic: str, hits: Sequence[Any]) -> ActionResult:
    """P3 offline Teach template - kept byte-for-byte for reproducibility."""
    if hits:
        top = hits[0]
        return ActionResult(
            action="Teach",
            content=f"[{topic}] 讲义要点：{top.text[:500]}",
            citation=top.source,
            lecture=top.lecture,
            page=top.page,
            grounded=True,
            question=None,
            mcq_index=0,
        )
    return ActionResult(
        action="Teach",
        content=(
            f"[{topic}] 概念讲解（P3 离线占位：未命中讲义页；"
            "P4 由 LLM 基于检索结果生成讲解）"
        ),
        citation=None,
        lecture=None,
        page=None,
        grounded=False,
        question=None,
        mcq_index=0,
    )


def render_mcq(question: MCQ, mcq_index: int) -> ActionResult:
    options = "  ".join(f"{key}) {value}" for key, value in question.options.items())
    return ActionResult(
        action="MCQ",
        content=f"{question.prompt}  {options}",
        citation=None,
        lecture=None,
        page=None,
        grounded=False,
        question=question.as_dict(),
        mcq_index=mcq_index + 1,
    )


def _content_action(
    action: str,
    content: TeachContent,
    mcq_index: int,
) -> ActionResult:
    """Wrap a Teach/Review generation result with its verified citation."""
    clean = content.text.replace("\r", "")
    return ActionResult(
        action=action,
        content=clean,
        citation=content.citation,
        lecture=content.lecture,
        page=content.page,
        grounded=content.grounded,
        question=None,
        mcq_index=mcq_index,
        usage=content.usage,
        content_source=content.source,
    )


def _render_review_template(
    topic: str,
    mcq_index: int,
    recent_errors: Sequence[Any] | None,
    hits: Sequence[Any],
) -> ActionResult:
    weak = ", ".join(
        str(item.get("question_id", "?")) if isinstance(item, dict) else str(item)
        for item in (recent_errors or [])
    ) or "none recorded"
    base = render_teach(topic, hits)
    return replace(
        base,
        action="Review",
        content=f"{base.content}\n重点复习近期错题：{weak}（离线模板）",
        mcq_index=mcq_index,
    )


def render_action(
    *,
    action: str,
    topic: str,
    mcq_index: int,
    questions: Sequence[MCQ],
    retriever: Retriever | None,
    generator: LLMContentGenerator | None = None,
    extended: bool = False,
    recent_errors: Sequence[Any] | None = None,
) -> ActionResult:
    """Render one activity. Defaults reproduce P3 exactly (generator=None)."""
    # P4 question generation is grounded on the same excerpts as Teach;
    # P3 (generator=None) never retrieves for a question step.
    needs_retrieval = action in ("Teach", "Review") or (
        generator is not None and action in ("MCQ", "Challenge")
    )
    hits: Sequence[Any] = []
    if needs_retrieval and retriever is not None:
        try:
            hits = retriever(topic)
        except Exception as exc:
            warnings.warn(f"[P3] retriever error, Teach falls back: {exc}", stacklevel=2)

    if action in ("Teach", "Review"):
        if generator is None:
            if action == "Teach":
                return replace(render_teach(topic, hits), mcq_index=mcq_index)
            return _render_review_template(topic, mcq_index, recent_errors, hits)
        content = generator.teach(
            topic, hits,
            style="Review" if action == "Review" else "Teach",
            recent_errors=recent_errors,
        )
        return _content_action(action, content, mcq_index)

    if action in ("MCQ", "Challenge"):
        if not questions:
            raise ValueError("MCQ bank is empty; cannot render a practice step")
        bank_question = questions[mcq_index % len(questions)]
        if generator is not None:
            result = generator.question(
                topic, hits,
                difficulty="challenge" if action == "Challenge" else "mcq",
                index=mcq_index,
                bank_question=bank_question,
            )
            options = "  ".join(
                f"{key}) {value}" for key, value in result.question.options.items()
            )
            prefix = "【Challenge】" if action == "Challenge" else ""
            return ActionResult(
                action=action,
                content=f"{prefix}{result.question.prompt}  {options}",
                citation=hits[0].source if hits else None,
                lecture=hits[0].lecture if hits else None,
                page=hits[0].page if hits else None,
                grounded=bool(hits),
                question=result.question.as_dict(),
                mcq_index=mcq_index + 1,
                usage=result.usage,
                content_source=result.source,
            )
        base = render_mcq(bank_question, mcq_index)
        if action == "Challenge":
            return replace(base, action="Challenge", content=f"【Challenge】{base.content}")
        return base

    if action == "Done":
        raise ValueError("Done is a terminal decision, not a renderable activity")
    raise ValueError(f"unsupported action {action!r} (extended={extended})")
