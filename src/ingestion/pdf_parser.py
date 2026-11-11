"""Course PDF -> page-level text with citation metadata (P1).

Every page keeps ``lecture / page / topic`` so retrieval results can always be
rendered as ``Source: Lecture 05, Page 14`` (README 7.4 / 10.1).

The original lecture PDFs are NOT committed to this repository (copyright);
tests generate small synthetic PDFs at runtime instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PageText:
    lecture: str
    page: int  # 1-based, matching the page number printed on the slide
    topic: str
    text: str
    source_path: str


def lecture_name(path: str | Path) -> str:
    """Normalize a filename into a citation-friendly lecture label.

    ``Lecture_05.pdf`` / ``lecture5.pdf`` -> ``Lecture 05``; anything else keeps
    a readable version of the stem.
    """
    stem = Path(path).stem.strip()
    match = re.match(r"^lecture[\s_\-]*0*(\d+)$", stem, flags=re.IGNORECASE)
    if match:
        return f"Lecture {int(match.group(1)):02d}"
    return re.sub(r"[_\-]+", " ", stem).strip() or "Lecture"


def format_source(lecture: str, page: int) -> str:
    """The single place that produces citations; P4 and P5 must reuse it."""
    return f"Source: {lecture}, Page {page}"


def parse_pdf(path: str | Path, *, topic: str | None = None) -> list[PageText]:
    """Extract text page by page. Empty/blank pages are skipped."""
    # PyMuPDF >= 1.24 exposes `pymupdf`; the legacy `fitz` alias still works on
    # older releases but emits a deprecation warning on 2.x.
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover - compatibility with older PyMuPDF
        import fitz

    pdf_path = Path(path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"course material not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"expected a .pdf file: {pdf_path}")

    lecture = lecture_name(pdf_path)
    resolved_topic = topic or lecture
    pages: list[PageText] = []
    with fitz.open(pdf_path) as doc:
        for index, page in enumerate(doc):
            text = page.get_text("text").strip()
            if not text:
                continue
            pages.append(
                PageText(
                    lecture=lecture,
                    page=index + 1,
                    topic=resolved_topic,
                    text=text,
                    source_path=str(pdf_path.resolve()),
                )
            )
    return pages


def parse_directory(
    input_dir: str | Path,
    *,
    topic: str | None = None,
    topic_map: dict[str, str] | None = None,
) -> list[PageText]:
    """Parse all PDFs in a directory, in deterministic filename order.

    ``topic_map`` optionally maps a filename (e.g. ``Lecture 05.pdf``) to a
    topic, so a lecture can be indexed under a knowledge-point label.
    """
    directory = Path(input_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"course materials directory not found: {directory}")
    paths: Iterable[Path] = sorted(directory.glob("*.pdf"), key=lambda p: p.name.lower())
    result: list[PageText] = []
    for path in paths:
        mapped = (topic_map or {}).get(path.name)
        result.extend(parse_pdf(path, topic=mapped or topic))
    return result
