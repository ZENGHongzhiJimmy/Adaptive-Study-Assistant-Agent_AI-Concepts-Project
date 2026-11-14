"""Content preparation: the two content conditions, generated once and frozen.

Why a separate stage
--------------------
LLM-only and RAG differ in **where the explanation comes from**, not in how
activities are scheduled. Generating the explanations at experiment time would
make every run different (temperature, model drift, rate limits), so the text is
produced once, scored once, and committed as ``data/materials.json``. The
learning simulation then reads that frozen file, exactly like ``bank.json``.

Conditions
----------
``rag``       the product's Teach prompt with retrieved lecture excerpts
              (``TEACH_SYSTEM`` + ``format_context(hits)``).
``llm_only``  the same task with no excerpts and a system prompt that says so.
              The RAG prompt ("use ONLY the excerpts") cannot be reused here:
              with no excerpts the model has nothing to comply with, so the
              instruction is swapped for the honest one - answer from your own
              knowledge, do not cite.

Retrieval backend
-----------------
The product retriever (Chroma + hashing embeddings) is used when its index
exists. It usually does not in a fresh clone, so the fallback is a TF-IDF index
over the committed lecture PDF, chunked exactly like the product (800 chars,
100 overlap). Both are deterministic; the backend actually used is recorded in
the output so a run is never ambiguous.

Scoring
-------
Two offline metrics, no extra model calls:

``coverage``   fraction of the *probe item's* key terms (correct option +
               explanation) that appear in the explanation. Probes are the
               post-test, so this measures "does the explanation teach what is
               going to be measured".
``grounding``  TF-IDF cosine between the explanation and the retrieved lecture
               passages. High grounding means the text stays close to the
               course material rather than to generic knowledge.

Usage
-----
    python -m experiments.content              # generate what is missing
    python -m experiments.content --force      # regenerate everything
    python -m experiments.content --score-only # rescore the frozen text
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pymupdf
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.agent.prompts.templates import NO_EXCERPTS, TEACH_SYSTEM, TEACH_USER
from src.utils.config import get_config
from src.utils.llm_client import LLMClient

from .bank import Bank, load_bank

DATA_DIR = Path(__file__).resolve().parent / "data"
SOURCE_PDF = DATA_DIR / "source" / "Experiment(Lecture 1 Intro 2026).pdf"
MATERIALS_OUT = DATA_DIR / "materials.json"

CONDITIONS = ("llm_only", "rag")
TOP_K = 3
MAX_CHARS = 900          # keep the two conditions at comparable length
MIN_CHARS = 150          # below this a reply is treated as a refusal/truncation
CONTEXT_CHARS = 600      # per retrieved passage, same as templates.format_context

LLM_ONLY_SYSTEM = """You are a concise university-level tutor for an AI concepts course.
Rules:
- You have NO access to the course slides. Answer from your own general knowledge.
- Do NOT invent page numbers, lecture names, slide titles or "Source:" lines.
- Write 2-4 short paragraphs or bullets, in the same language as the topic text.
- End with one concrete takeaway the student should remember.
- Plain text only: no markdown fences, no JSON, no preamble about being an AI."""

STOPWORDS = set("""
a an the and or but if then than that this these those of in on at to for from by with
without into over under about as is are was were be been being it its their there here
which who whom whose what when where why how not no nor so such can could may might must
should would will shall do does did done have has had one two three also more most less
least other another each every both any some all only just very much many few own same
student question option answer correct wrong true false following statement best describes
""".split())


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

@dataclass
class Passage:
    source: str
    text: str


class ChunkIndex:
    """Chunk-level TF-IDF index over the committed lecture PDF.

    Chunks follow the product's retrieval config (800 chars, 100 overlap) so the
    RAG condition sees material of the same granularity as the app. Page-level
    chunks were tried first and failed on short queries: "What is AI" matched the
    course-roadmap slide rather than the definition slide, which turned the RAG
    explanation into a comment about the material instead of an explanation of
    the topic.
    """

    name = "tfidf-chunks"

    def __init__(self, pdf: Path, chunk_size: int = 800, overlap: int = 100) -> None:
        if not pdf.exists():
            raise SystemExit(f"lecture PDF not found: {pdf}")
        doc = pymupdf.open(pdf)
        pages: list[tuple[int, str]] = []
        for number, page in enumerate(doc, start=1):
            text = " ".join(page.get_text().split())
            if len(text) >= 40:
                pages.append((number, text))
        doc.close()
        if not pages:
            raise SystemExit(f"no extractable text in {pdf}")

        chunks: list[Passage] = []
        buffer, start_page = "", pages[0][0]
        for number, text in pages:
            if not buffer:
                start_page = number
            buffer = f"{buffer} {text}".strip()
            while len(buffer) >= chunk_size:
                piece, buffer = buffer[:chunk_size], buffer[chunk_size - overlap:]
                chunks.append(Passage(source=f"Lecture 1, Page {start_page}", text=piece))
                start_page = number
        if len(buffer) >= 120:
            chunks.append(Passage(source=f"Lecture 1, Page {start_page}", text=buffer))

        self.passages = chunks
        self._vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self._matrix = self._vectorizer.fit_transform([p.text for p in chunks])

    def retrieve(self, query: str, k: int = TOP_K) -> list[Passage]:
        vector = self._vectorizer.transform([query])
        scores = cosine_similarity(vector, self._matrix)[0]
        order = scores.argsort()[::-1][:k]
        return [self.passages[i] for i in order if scores[i] > 0]

    def __call__(self, query: str, k: int = TOP_K) -> list[Passage]:
        """Same call signature as the product retriever."""
        return self.retrieve(query, k)

    def close(self) -> None:  # parity with the product retriever
        return None


def build_index(config: dict[str, Any]) -> tuple[Any, str]:
    """Prefer the product retriever; fall back to the deterministic chunk index."""
    try:
        from src.agent.actions import build_retriever

        retriever = build_retriever(config)
        if retriever is not None:
            return retriever, "product-chroma"
    except Exception:
        pass
    index = ChunkIndex(SOURCE_PDF)
    return index, index.name


def _as_passages(hits: Sequence[Any]) -> list[Passage]:
    out = []
    for hit in hits:
        out.append(Passage(
            source=str(getattr(hit, "source", None) or "lecture"),
            text=str(getattr(hit, "text", "")),
        ))
    return out


def render_context(passages: Sequence[Passage]) -> str:
    if not passages:
        return NO_EXCERPTS
    blocks = [
        f"[{i}] {p.source}\n{p.text.strip()[:CONTEXT_CHARS]}"
        for i, p in enumerate(passages, start=1)
    ]
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def _chat(client: LLMClient, system: str, user: str) -> dict[str, Any]:
    response = client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=420,
    )
    text = response.text.strip()
    # The tutor prompt asks for 2-4 short paragraphs; enforce a comparable
    # length across conditions instead of letting verbosity pass for quality.
    if len(text) > MAX_CHARS:
        cut = text[:MAX_CHARS]
        text = cut[:cut.rfind(". ") + 1] if ". " in cut else cut
    return {
        "text": text,
        "model": response.model,
        "latency_ms": response.latency_ms,
        "tokens": response.total_tokens,
    }


def _chat_checked(client: LLMClient, system: str, user: str, attempts: int = 3) -> dict[str, Any]:
    """Regenerate on an implausibly short reply (refusal or truncation).

    Free endpoints occasionally answer "the excerpts do not cover this" in one
    sentence. Keeping that as the RAG condition would compare a refusal against
    a full explanation, which is not the comparison the experiment claims to
    make. The attempt count is recorded either way.
    """
    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        last = _chat(client, system, user)
        if len(last["text"]) >= MIN_CHARS:
            last["attempts"] = attempt
            return last
        time.sleep(1.0)
    assert last is not None
    last["attempts"] = attempts
    return last


def _query_for(topic: Any) -> str:
    """Retrieval query: the topic plus the vocabulary it is tested on."""
    parts = [topic.name]
    parts += [item.prompt for item in topic.practice[:2]]
    parts += [item.prompt for item in topic.probe[:1]]
    return " ".join(parts)


def generate(bank: Bank, *, force: bool = False) -> dict[str, Any]:
    cfg = get_config()
    client = LLMClient(cfg)
    if not client.is_configured:
        raise SystemExit("LLM not configured: cannot generate the content conditions")

    existing = {}
    if MATERIALS_OUT.exists() and not force:
        existing = json.loads(MATERIALS_OUT.read_text(encoding="utf-8"))
    done = {t["topic_id"]: t for t in existing.get("topics", [])}

    retriever, backend = build_index(cfg)
    print(f"retrieval backend: {backend}")
    rows: list[dict[str, Any]] = []
    try:
        for index, topic in enumerate(bank.topics, start=1):
            cached = done.get(topic.topic_id)
            if cached and not force and all(c in cached for c in CONDITIONS):
                rows.append(cached)
                print(f"[{index}/{len(bank.topics)}] {topic.topic_id} cached")
                continue

            query = _query_for(topic)
            hits = _as_passages(retriever(query))
            row: dict[str, Any] = {"topic_id": topic.topic_id, "name": topic.name,
                                   "query": query,
                                   "retrieved": [h.source for h in hits]}
            for condition in CONDITIONS:
                context = render_context(hits) if condition == "rag" else NO_EXCERPTS
                system = TEACH_SYSTEM if condition == "rag" else LLM_ONLY_SYSTEM
                user = TEACH_USER.format(
                    topic=topic.name, style="Teach", weak_points="none", context=context
                )
                started = time.perf_counter()
                payload = _chat_checked(client, system, user)
                payload["wall_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
                payload["context_chars"] = len(context)
                row[condition] = payload
                print(f"[{index}/{len(bank.topics)}] {topic.topic_id} {condition}: "
                      f"{len(payload['text'])} chars, {payload['latency_ms']:.0f} ms, "
                      f"attempts={payload['attempts']}")
            rows.append(row)
            # Flush after every topic: a crash during scoring must not throw away
            # paid API calls.
            _write(rows, client, backend, scores=None)
    finally:
        if hasattr(retriever, "close"):
            retriever.close()

    payload = _write(rows, client, backend, scores=score_all(bank, rows))
    print(f"-> {MATERIALS_OUT}")
    return payload


def _write(
    rows: Sequence[dict[str, Any]],
    client: LLMClient,
    backend: str,
    *,
    scores: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": client.model,
        "temperature": 0.0,
        "retrieval_backend": backend,
        "top_k": TOP_K,
        "conditions": list(CONDITIONS),
        "topics": list(rows),
    }
    if scores is not None:
        payload["scores"] = scores
    MATERIALS_OUT.parent.mkdir(parents=True, exist_ok=True)
    MATERIALS_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def key_terms(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z\-]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 3}


def coverage(explanation: str, probe_terms: Sequence[set[str]]) -> float:
    """Fraction of each probe item's key terms that the explanation mentions."""
    seen = key_terms(explanation)
    if not probe_terms:
        return 0.0
    recalls = []
    for terms in probe_terms:
        if not terms:
            continue
        recalls.append(len(terms & seen) / len(terms))
    return statistics.fmean(recalls) if recalls else 0.0


def grounding(explanation: str, passages: Sequence[str]) -> float:
    """TF-IDF cosine to the closest retrieved passage (0 when nothing retrieved)."""
    if not explanation.strip() or not passages:
        return 0.0
    vectorizer = TfidfVectorizer(stop_words="english", sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform([explanation, *passages])
    except ValueError:  # empty vocabulary
        return 0.0
    sims = cosine_similarity(matrix[0:1], matrix[1:])[0]
    return float(max(sims)) if len(sims) else 0.0


def probe_terms(bank: Bank) -> dict[str, list[set[str]]]:
    out: dict[str, list[set[str]]] = {}
    for topic in bank.topics:
        terms = []
        for item in topic.probe:
            terms.append(key_terms(f"{item.correct_option} {item.why}"))
        out[topic.topic_id] = terms
    return out


def score_all(bank: Bank, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    index = ChunkIndex(SOURCE_PDF)
    terms = probe_terms(bank)
    per_condition: dict[str, dict[str, float]] = {c: {} for c in CONDITIONS}
    rows_out = []
    for row in rows:
        topic_id = row["topic_id"]
        hits = index.retrieve(str(row.get("query") or row["name"]))
        passages = [h.text for h in hits]
        entry: dict[str, Any] = {"topic_id": topic_id, "name": row["name"]}
        for condition in CONDITIONS:
            block = row.get(condition) or {}
            text = str(block.get("text", ""))
            cov = coverage(text, terms.get(topic_id, []))
            grd = grounding(text, passages)
            entry[condition] = {"coverage": round(cov, 4), "grounding": round(grd, 4)}
            per_condition[condition][topic_id] = cov
        rows_out.append(entry)

    summary = {}
    for condition in CONDITIONS:
        values = list(per_condition[condition].values())
        summary[condition] = {
            "coverage_mean": round(statistics.fmean(values), 4) if values else 0.0,
            "coverage_sd": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        }
    # also average grounding per condition
    for condition in CONDITIONS:
        grounds = []
        for entry in rows_out:
            grounds.append(entry[condition]["grounding"])
        summary[condition]["grounding_mean"] = round(statistics.fmean(grounds), 4) if grounds else 0.0
    return {"per_topic": rows_out, "summary": summary}


def load_materials() -> dict[str, Any]:
    if not MATERIALS_OUT.exists():
        raise SystemExit(
            f"missing {MATERIALS_OUT}\nrun `python -m experiments.content` first"
        )
    return json.loads(MATERIALS_OUT.read_text(encoding="utf-8"))


def quality_by_condition(materials: dict[str, Any]) -> dict[str, dict[str, float]]:
    """topic_id -> condition -> coverage, the number the simulator consumes."""
    out: dict[str, dict[str, float]] = {}
    for entry in materials["scores"]["per_topic"]:
        out[entry["topic_id"]] = {
            condition: float(entry[condition]["coverage"]) for condition in CONDITIONS
        }
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="content conditions (frozen)")
    parser.add_argument("--force", action="store_true", help="regenerate every topic")
    parser.add_argument("--score-only", action="store_true", help="rescore frozen text")
    args = parser.parse_args(argv)

    bank = load_bank()
    if args.score_only:
        payload = load_materials()
        payload["scores"] = score_all(bank, payload["topics"])
        MATERIALS_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        payload = generate(bank, force=args.force)

    print()
    print("== content quality ==")
    print(f"{'condition':<10}{'coverage':>10}{'grounding':>11}")
    for condition in CONDITIONS:
        s = payload["scores"]["summary"][condition]
        print(f"{condition:<10}{s['coverage_mean']:>10.3f}{s['grounding_mean']:>11.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
