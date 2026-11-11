"""Teach / MCQ activities for the P3 loop (no LLM).

``Teach`` optionally uses the P1 knowledge base: retrieval hits provide the
grounded excerpt and its ``Source: Lecture 05, Page 14`` citation. When the
index is empty or unavailable the loop degrades safely to an offline template
instead of crashing - the closed loop must be verifiable before any LLM wiring.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from ..retrieval.embeddings import HashingEmbedder
from ..utils.config import resolve_path
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


def render_action(
    *,
    action: str,
    topic: str,
    mcq_index: int,
    questions: Sequence[MCQ],
    retriever: Retriever | None,
) -> ActionResult:
    if action == "Teach":
        hits: Sequence[Any] = []
        if retriever is not None:
            try:
                hits = retriever(topic)
            except Exception as exc:
                warnings.warn(f"[P3] retriever error, Teach falls back: {exc}", stacklevel=2)
        return replace(render_teach(topic, hits), mcq_index=mcq_index)
    if action == "MCQ":
        if not questions:
            raise ValueError("MCQ bank is empty; cannot render a practice step")
        return render_mcq(questions[mcq_index % len(questions)], mcq_index)
    raise ValueError(f"P3 supports Teach/MCQ only, got {action!r}")
