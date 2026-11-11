"""Build / rebuild the course vector index (P1 CLI entry point).

    python -m src.ingestion.indexer --input data/course_materials
    python -m src.ingestion.indexer --input data/course_materials --rebuild --backend hashing

Running without ``--rebuild`` is idempotent: chunk ids are content hashes and
ChromaDB uses upsert, so repeated runs never duplicate vectors. ``--rebuild``
drops and recreates the collection first.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from ..retrieval.embeddings import get_embedder
from ..retrieval.vector_store import ChromaVectorStore
from ..utils.config import get_config, resolve_path
from .chunker import chunk_pages
from .pdf_parser import parse_directory


@dataclass
class IndexStats:
    pdf_count: int
    page_count: int
    chunk_count: int
    backend: str
    collection: str
    rebuild: bool
    sources: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_topic_map(path: str | Path | None) -> dict[str, str] | None:
    """Optional JSON mapping ``filename -> topic`` for knowledge-point labels."""
    if path is None:
        return None
    topic_path = Path(path)
    if not topic_path.is_file():
        raise FileNotFoundError(f"topic map not found: {topic_path}")
    data = json.loads(topic_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("topic map must be a JSON object: {filename: topic}")
    return {str(k): str(v) for k, v in data.items()}


def build_index(
    input_dir: str | Path,
    store: ChromaVectorStore,
    embedder: Any,
    *,
    topic: str | None = None,
    topic_map: dict[str, str] | None = None,
    rebuild: bool = False,
    config: dict[str, Any] | None = None,
) -> IndexStats:
    """Parse -> chunk -> embed -> upsert. Empty input is a safe no-op."""
    cfg = config or get_config()
    retrieval_cfg = cfg.get("retrieval", {})
    pages = parse_directory(input_dir, topic=topic, topic_map=topic_map)
    chunks = chunk_pages(
        pages,
        chunk_size=int(retrieval_cfg.get("chunk_size", 800)),
        chunk_overlap=int(retrieval_cfg.get("chunk_overlap", 100)),
    )

    if rebuild:
        store.rebuild()
    if chunks:
        vectors = embedder.embed([chunk.text for chunk in chunks])
        store.upsert_chunks(chunks, vectors)

    return IndexStats(
        pdf_count=len({page.source_path for page in pages}),
        page_count=len(pages),
        chunk_count=len(chunks),
        backend=embedder.name,
        collection=store.collection_name,
        rebuild=rebuild,
        sources=sorted({page.lecture for page in pages}),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1: index course PDFs into ChromaDB")
    parser.add_argument("--input", default="./data/course_materials")
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate collection")
    parser.add_argument("--backend", choices=("hashing", "sentence_transformers", "auto"))
    parser.add_argument("--topic", default=None, help="topic label applied to every PDF")
    parser.add_argument("--topic-map", default=None, help="JSON file: {filename: topic}")
    parser.add_argument("--persist-dir", default=None)
    args = parser.parse_args(argv)

    cfg = get_config()
    embedder = get_embedder(args.backend, cfg)
    persist_dir = (
        Path(args.persist_dir) if args.persist_dir else resolve_path(cfg["paths"]["chroma_persist_dir"])
    )
    store = ChromaVectorStore(
        persist_dir,
        collection_name=cfg.get("retrieval", {}).get("collection_name", "course_chunks"),
    )
    try:
        stats = build_index(
            args.input,
            store,
            embedder,
            topic=args.topic,
            topic_map=load_topic_map(args.topic_map),
            rebuild=args.rebuild,
            config=cfg,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"[P1 index] {exc}")
        return 2

    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    if stats.chunk_count == 0:
        print("[P1 index] no PDF content found; put lecture PDFs into --input and retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
