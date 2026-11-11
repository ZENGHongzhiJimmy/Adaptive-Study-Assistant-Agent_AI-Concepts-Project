"""Pluggable text embeddings (P1).

Production backend: sentence-transformers (semantic retrieval, README 6).
Offline/deterministic backend: hashing bag-of-words, used by tests and by
machines that have not downloaded a model yet. It is deterministic and
dependency-free, so index rebuilds and topic routing can be verified without
network access or a multi-hundred-MB torch install.

``backend: auto`` in config.yaml prefers sentence-transformers and falls back
to hashing with an explicit warning. P6 must freeze the backend explicitly.
"""

from __future__ import annotations

import hashlib
import math
import re
import warnings
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
    """Raised when a requested embedding backend is unavailable."""


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


class SentenceTransformerEmbedder:
    """Lazy wrapper around sentence-transformers; model is loaded once."""

    name = "sentence_transformers"

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingBackendError(
                "sentence-transformers is not installed. Run `pip install -r "
                "requirements.txt`, or use `--backend hashing` for an offline "
                "deterministic index."
            ) from exc
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # model download / cache / network failures
            raise EmbeddingBackendError(
                f"could not load embedding model {model_name!r}: {exc}. "
                "Use `--backend hashing` for an offline index."
            ) from exc
        self.model_name = model_name
        self.dimension = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )
        return [list(map(float, row)) for row in vectors]


def get_embedder(
    backend: str | None = None,
    config: dict[str, Any] | None = None,
) -> Embedder:
    """Resolve an embedder from config (or an explicit CLI override)."""
    from ..utils.config import get_config

    cfg = config or get_config()
    embedding_cfg = cfg.get("embedding", {})
    selected = (backend or embedding_cfg.get("backend", "auto")).lower()

    if selected == "hashing":
        return HashingEmbedder(int(embedding_cfg.get("hashing_dimension", 256)))
    if selected == "sentence_transformers":
        return SentenceTransformerEmbedder(
            embedding_cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2")
        )
    if selected == "auto":
        try:
            return SentenceTransformerEmbedder(
                embedding_cfg.get("model", "sentence-transformers/all-MiniLM-L6-v2")
            )
        except EmbeddingBackendError as exc:
            warnings.warn(f"[P1] falling back to hashing embeddings: {exc}", stacklevel=2)
            return HashingEmbedder(int(embedding_cfg.get("hashing_dimension", 256)))
    raise EmbeddingBackendError(
        f"unknown embedding backend {selected!r}; choose hashing / sentence_transformers / auto"
    )
