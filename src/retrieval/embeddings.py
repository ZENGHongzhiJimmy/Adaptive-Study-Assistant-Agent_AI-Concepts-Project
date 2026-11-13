"""Deterministic text embeddings (P1).

The platform uses a dependency-free signed-hashing bag-of-words embedder with
L2 normalization for cosine similarity. It is deterministic and offline, so
index rebuilds and topic routing are reproducible without network access or
large model downloads.

``embedding.backend`` is frozen to ``hashing`` in config.yaml (P6). ``auto``
is accepted as a compatibility alias and resolves to the same backend.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol, Sequence

STOPWORDS = frozenset(
    "a an and are as at be by for from has have in into is it of on or that the "
    "this to was were what when where which why with will you your we our their "
    "can may should than then these those such".split()
)
TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingBackendError(RuntimeError):
    """Raised when a requested embedding backend is unsupported."""


class HashingEmbedder:
    """Deterministic signed-hashing count vectors, L2-normalized for cosine."""

    name = "hashing"

    def __init__(self, dimension: int = 256) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in TOKEN_RE.findall(text.lower()):
            if token in STOPWORDS or len(token) < 2:
                continue
            digest = int.from_bytes(
                hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
            )
            index = digest % self.dimension
            sign = 1.0 if (digest // self.dimension) % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


def get_embedder(
    backend: str | None = None,
    config: dict[str, Any] | None = None,
) -> Embedder:
    """Resolve the deterministic hashing embedder from config (or a CLI override)."""
    from ..utils.config import get_config

    cfg = config or get_config()
    embedding_cfg = cfg.get("embedding", {})
    selected = (backend or embedding_cfg.get("backend", "hashing")).lower()

    # ``auto`` is kept as a compatibility alias for older CLI calls; both
    # resolve to the same deterministic 256-dimension hashing backend.
    if selected in ("hashing", "auto"):
        return HashingEmbedder(int(embedding_cfg.get("hashing_dimension", 256)))
    raise EmbeddingBackendError(
        f"unsupported embedding backend {selected!r}; "
        "use 'hashing' (--backend hashing)"
    )
