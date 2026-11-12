"""Page text -> stable, citation-carrying chunks (P1).

chunk_id is a hash of (lecture, page, ordinal, text): the same PDFs always
produce the same ids, which is what makes an index rebuild idempotent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

from .pdf_parser import PageText, format_source


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    lecture: str
    page: int
    topic: str
    text: str
    source: str
    ordinal: int

    def metadata(self) -> dict[str, str | int]:
        """ChromaDB metadata (flat scalar values only)."""
        return {
            "lecture": self.lecture,
            "page": self.page,
            "topic": self.topic,
            "source": self.source,
            "ordinal": self.ordinal,
        }

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def stable_chunk_id(lecture: str, page: int, ordinal: int, text: str) -> str:
    payload = f"{lecture}|{page}|{ordinal}|{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Character windows, extended to a whitespace/sentence boundary."""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    pieces: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            # Prefer breaking after a sentence, else at the last whitespace.
            window = normalized[start:end]
            boundary = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
            if boundary != -1 and boundary > chunk_size // 2:
                end = start + boundary + 2
            else:
                space = window.rfind(" ")
                if space > chunk_size // 2:
                    end = start + space + 1
        piece = normalized[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(normalized):
            break
        start = max(end - chunk_overlap, start + 1)
    return pieces


def chunk_pages(
    pages: list[PageText],
    *,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> list[Chunk]:
    if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("require chunk_size > 0 and 0 <= chunk_overlap < chunk_size")

    chunks: list[Chunk] = []
    for page in pages:
        for ordinal, piece in enumerate(_split_text(page.text, chunk_size, chunk_overlap)):
            chunks.append(
                Chunk(
                    chunk_id=stable_chunk_id(page.lecture, page.page, ordinal, piece),
                    lecture=page.lecture,
                    page=page.page,
                    topic=page.topic,
                    text=piece,
                    source=format_source(page.lecture, page.page),
                    ordinal=ordinal,
                )
            )
    return chunks
