"""P1 acceptance tests (offline).

Real lecture PDFs are not committed (copyright), so tests generate small
synthetic PDFs with PyMuPDF. They verify: physical page numbers in metadata,
topic-filtered retrieval landing on the right page, idempotent rebuild, and
`Source: Lecture 05, Page 14` citations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.chunker import chunk_pages, stable_chunk_id
from src.ingestion.indexer import build_index, load_topic_map
from src.ingestion.pdf_parser import format_source, lecture_name, parse_directory, parse_pdf
from src.retrieval.embeddings import (
    EmbeddingBackendError,
    HashingEmbedder,
    get_embedder,
)
from src.retrieval.vector_store import ChromaVectorStore

LECTURE_05_PAGES = [
    "Bayes theorem introduction. The prior probability and the likelihood are defined. "
    "priorconcept priorconcept priorconcept priorconcept",
    "",  # intentionally blank: physical page 2 must be skipped, not renumbered
    "The posterior formula combines the prior and likelihood into a posterior. "
    "posteriorformula posteriorformula posteriorformula posteriorformula",
    "Worked example of medical testing with Bayes rule. workedexample workedexample",
]
LECTURE_06_PAGES = [
    "Markov decision process states actions rewards transition probabilities. "
    "mdptoken mdptoken mdptoken",
    "Value iteration and the optimal policy with a discount factor. "
    "valueiteration valueiteration valueiteration",
]


def _write_pdf(path: Path, pages: list[str]) -> None:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if not text:
            continue
        y = 72.0
        for start in range(0, len(text), 80):
            page.insert_text((50.0, y), text[start : start + 80], fontsize=9)
            y += 12.0
    doc.save(path)
    doc.close()


@pytest.fixture
def materials(tmp_path: Path):
    directory = tmp_path / "course_materials"
    directory.mkdir()
    _write_pdf(directory / "Lecture 05.pdf", LECTURE_05_PAGES)
    _write_pdf(directory / "Lecture_06.pdf", LECTURE_06_PAGES)
    topic_map = {
        "Lecture 05.pdf": "Bayes Theorem",
        "Lecture_06.pdf": "MDP",
    }
    return directory, topic_map


@pytest.fixture
def rag(tmp_path: Path, materials):
    directory, topic_map = materials
    embedder = HashingEmbedder()
    store = ChromaVectorStore(tmp_path / "chroma_db", collection_name="course_chunks")
    yield directory, topic_map, embedder, store
    store.close()


# --- parsing / chunking -----------------------------------------------------

def test_lecture_name_normalization():
    assert lecture_name("Lecture 05.pdf") == "Lecture 05"
    assert lecture_name("lecture5.pdf") == "Lecture 05"
    assert lecture_name("Lecture_06.pdf") == "Lecture 06"
    assert format_source("Lecture 05", 14) == "Source: Lecture 05, Page 14"


def test_parse_keeps_physical_page_numbers_and_topic(materials):
    directory, topic_map = materials
    pages = parse_directory(directory, topic_map=topic_map)
    assert {p.lecture for p in pages} == {"Lecture 05", "Lecture 06"}
    lecture05 = [p for p in pages if p.lecture == "Lecture 05"]
    assert [p.page for p in lecture05] == [1, 3, 4]  # blank page 2 skipped
    assert all(p.topic == "Bayes Theorem" for p in lecture05)
    assert "posterior formula" in lecture05[1].text.lower()
    assert format_source(lecture05[1].lecture, lecture05[1].page) == "Source: Lecture 05, Page 3"


def test_chunk_ids_are_deterministic_and_carry_citation(materials):
    directory, topic_map = materials
    pages = parse_directory(directory, topic_map=topic_map)
    first = chunk_pages(pages, chunk_size=120, chunk_overlap=20)
    second = chunk_pages(pages, chunk_size=120, chunk_overlap=20)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)
    assert all(c.source.startswith("Source: Lecture") for c in first)
    assert all(set(c.metadata()) == {"lecture", "page", "topic", "source", "ordinal"} for c in first)
    assert stable_chunk_id("Lecture 05", 3, 0, "abc") == stable_chunk_id("Lecture 05", 3, 0, "abc")
    assert stable_chunk_id("Lecture 05", 3, 0, "abc") != stable_chunk_id("Lecture 05", 4, 0, "abc")
    with pytest.raises(ValueError):
        chunk_pages(pages, chunk_size=100, chunk_overlap=100)


def test_hashing_embedder_is_deterministic_and_normalized():
    embedder = HashingEmbedder()
    a = embedder.embed(["posteriorformula prior likelihood"])[0]
    b = embedder.embed(["posteriorformula prior likelihood"])[0]
    c = embedder.embed(["valueiteration discount policy"])[0]
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9
    assert sum(x * y for x, y in zip(a, c)) < 0.5


def test_unsupported_embedding_backend_errors_clearly():
    with pytest.raises(EmbeddingBackendError, match="--backend hashing"):
        get_embedder("unsupported_backend")


# --- index / retrieval ------------------------------------------------------

def test_topic_search_hits_the_correct_lecture_page(rag):
    directory, topic_map, embedder, store = rag
    stats = build_index(
        directory, store, embedder, topic_map=topic_map, rebuild=True
    )
    assert stats.pdf_count == 2
    assert stats.page_count == 5  # 3 non-blank pages + 2 pages
    assert stats.chunk_count > 0
    assert stats.backend == "hashing"
    assert store.count() == stats.chunk_count

    bayes_hits = store.search(
        "posteriorformula posterior prior likelihood", embedder,
        top_k=1, topic="Bayes Theorem",
    )
    assert len(bayes_hits) == 1
    assert bayes_hits[0].lecture == "Lecture 05"
    assert bayes_hits[0].page == 3
    assert bayes_hits[0].source == "Source: Lecture 05, Page 3"

    mdp_hits = store.search(
        "valueiteration discount policy", embedder, top_k=1, topic="MDP"
    )
    assert mdp_hits[0].lecture == "Lecture 06"
    assert mdp_hits[0].page == 2
    assert store.citations(mdp_hits) == ["Source: Lecture 06, Page 2"]


def test_index_rebuild_is_idempotent(rag):
    directory, topic_map, embedder, store = rag
    first = build_index(directory, store, embedder, topic_map=topic_map, rebuild=True)
    ids_after_first = store.count()

    # Repeated upsert must not duplicate vectors.
    second = build_index(directory, store, embedder, topic_map=topic_map)
    assert second.chunk_count == first.chunk_count
    assert store.count() == ids_after_first

    # Full rebuild must produce the same number of chunks and same ids.
    store.rebuild()
    third = build_index(directory, store, embedder, topic_map=topic_map)
    assert third.chunk_count == first.chunk_count
    assert store.count() == ids_after_first
    rebuild = build_index(directory, store, embedder, topic_map=topic_map, rebuild=True)
    assert rebuild.chunk_count == first.chunk_count
    assert store.count() == ids_after_first


def test_reopening_same_index_in_one_process_keeps_data(rag):
    """Regression: ChromaDB >= 1.x must be closed via client.close().

    P3 builds an index and then reopens it for Teach retrieval in the same
    process; stopping the shared System directly used to break this with
    "Could not connect to tenant default_tenant".
    """
    directory, topic_map, embedder, store = rag
    stats = build_index(directory, store, embedder, topic_map=topic_map, rebuild=True)
    store.close()

    reopened = ChromaVectorStore(store.persist_dir, collection_name=store.collection_name)
    try:
        assert reopened.count() == stats.chunk_count
        hits = reopened.search(
            "posteriorformula posterior", embedder, top_k=1, topic="Bayes Theorem"
        )
        assert hits and hits[0].lecture == "Lecture 05"
    finally:
        reopened.close()
    # The fixture will call store.close() again; closing twice must be harmless.


def test_empty_materials_is_a_safe_noop(tmp_path):
    empty = tmp_path / "empty_materials"
    empty.mkdir()
    store = ChromaVectorStore(tmp_path / "empty_chroma", collection_name="course_chunks")
    try:
        stats = build_index(empty, store, HashingEmbedder(), rebuild=True)
        assert stats.chunk_count == 0
        assert store.count() == 0
        assert store.search("anything", HashingEmbedder()) == []
    finally:
        store.close()


def test_topic_map_file_is_loaded(tmp_path, materials):
    directory, topic_map = materials
    map_path = tmp_path / "topics.json"
    map_path.write_text(
        __import__("json").dumps(topic_map), encoding="utf-8"
    )
    assert load_topic_map(map_path) == topic_map
    assert load_topic_map(None) is None
    with pytest.raises(FileNotFoundError):
        load_topic_map(tmp_path / "missing.json")


def test_parse_pdf_rejects_non_pdf(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.pdf"):
        parse_pdf(bad)
