"""Re-run the product's own extraction on a lecture PDF and freeze the result.

Why this exists
---------------
The experiment must not call an LLM at run time: free tiers rate-limit, and
generated text is not reproducible. So the lecture is parsed **once**, the raw
topics are written to ``experiments/data/parse_raw.json``, and every later step
reads that file. ``experiments/data/bank.json`` is the curated, frozen dataset
built from this output.

Parsing is **chunked by page range**. A free-tier model truncates a single
16k-char completion mid-string (observed: "Unterminated string" at char 9987),
so the lecture is split into slices small enough that each answer fits, and the
per-slice topic lists are merged by normalised name.

The prompt is imported from ``src/api/services.py`` on purpose: the dataset
should come from the same extraction the product uses, not a second
implementation that could drift.

Usage
-----
    python -m experiments.build_bank                    # uses the committed PDF
    python -m experiments.build_bank --pdf "C:/path/other.pdf"

The lecture under experiment is committed at
``experiments/data/source/Experiment(Lecture 1 Intro 2026).pdf``, so the parse is
reproducible from a fresh clone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pymupdf

from src.api.services import EXTRACT_SYSTEM, EXTRACT_USER, _extract_json
from src.utils.config import get_config
from src.utils.llm_client import LLMClient

DATA_DIR = Path(__file__).resolve().parent / "data"
SOURCE_PDF = DATA_DIR / "source" / "Experiment(Lecture 1 Intro 2026).pdf"
DEFAULT_OUT = DATA_DIR / "parse_raw.json"

# The product asks for 1-2 quizzes per topic; the experiment needs a bank big
# enough to cover practice *and* held-out probes, so the ask is raised here.
BANK_SUFFIX = (
    "\n\nAdditional requirement for an experiment dataset: give each knowledge "
    "point 3 to 5 quizzes instead of 1-2, and make them test different facts "
    "rather than rephrasings of one fact."
)


def page_texts(pdf: Path) -> list[str]:
    doc = pymupdf.open(pdf)
    out = []
    for index in range(doc.page_count):
        text = " ".join(doc[index].get_text().split())
        if text:
            out.append(f"[p.{index + 1}] {text}")
    return out


def chunks(pages: list[str], budget: int) -> list[str]:
    """Group pages into slices of at most ``budget`` characters."""
    out: list[str] = []
    current: list[str] = []
    size = 0
    for page in pages:
        if current and size + len(page) > budget:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(page)
        size += len(page)
    if current:
        out.append("\n".join(current))
    return out


def extract_slice(client: LLMClient, name: str, text: str, *, attempts: int = 2) -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": EXTRACT_USER.format(name=name, text=text) + BANK_SUFFIX},
    ]
    last = ""
    for _ in range(attempts):
        response = client.chat(messages, max_tokens=8000)
        raw = (response.text or "").strip()
        if not raw:
            last = "empty response"
            continue
        try:
            payload = _extract_json(raw)
        except ValueError as exc:
            last = str(exc)
            continue
        topics = payload.get("topics")
        if isinstance(topics, list) and topics:
            return [t for t in topics if isinstance(t, dict)]
        last = "no topics"
    raise ValueError(last or "extraction failed")


def merge(all_topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by normalised name, keeping the first (richest) occurrence."""
    seen: dict[str, dict[str, Any]] = {}
    for topic in all_topics:
        name = str(topic.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            # Keep whichever occurrence carries more quizzes.
            if len(topic.get("quiz") or []) > len(seen[key].get("quiz") or []):
                seen[key] = {**topic, "name": name}
            continue
        seen[key] = {**topic, "name": name}
    return list(seen.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="parse a lecture PDF into topics")
    parser.add_argument(
        "--pdf", default=str(SOURCE_PDF),
        help="lecture PDF (defaults to the copy committed under experiments/data/source/)",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--budget", type=int, default=3200, help="characters per slice")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf)
    if not pdf.exists():
        raise SystemExit(f"PDF not found: {pdf}")
    pages = page_texts(pdf)
    if not pages:
        raise SystemExit(f"no extractable text in {pdf} (scanned slides?)")

    cfg = get_config()
    client = LLMClient(cfg)
    if not client.is_configured:
        raise SystemExit("LLM not configured: cannot run the one-off parse")

    slices = chunks(pages, args.budget)
    collected: list[dict[str, Any]] = []
    failures: list[int] = []
    for index, text in enumerate(slices, start=1):
        try:
            topics = extract_slice(client, f"{pdf.name} (slice {index}/{len(slices)})", text)
        except ValueError as exc:
            failures.append(index)
            print(f"  slice {index}/{len(slices)}: FAILED ({exc})", file=sys.stderr)
            continue
        collected.extend(topics)
        print(f"  slice {index}/{len(slices)}: {len(topics)} topics", file=sys.stderr)

    topics = merge(collected)
    payload = {
        "summary": "",
        "topics": topics,
        "_meta": {
            "source_pdf": pdf.name,
            "pages": len(pages),
            "chars": sum(len(p) for p in pages),
            "slices": len(slices),
            "failed_slices": failures,
            "model": cfg["llm"]["model"],
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    quiz_total = sum(len(t.get("quiz") or []) for t in topics)
    print(f"pages={len(pages)} slices={len(slices)} failed={failures}")
    print(f"topics={len(topics)} quizzes={quiz_total} -> {out}")
    for t in topics:
        print(f"  - {t.get('name')} | explain={len(t.get('explain') or [])} quiz={len(t.get('quiz') or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
