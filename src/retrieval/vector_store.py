"""ChromaDB vector store with citation-preserving metadata (P1).

Vectors are always supplied explicitly (never computed by ChromaDB's default
ONNX embedding function), so building an index does not silently download a
model. A tiny offline embedding function is attached to the collection only to
satisfy ChromaDB's schema; it is never called on our code path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from ..ingestion.chunker import Chunk


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    lecture: str
    page: int
    topic: str
    source: str
    distance: float

    @property
    def score(self) -> float:
        """Cosine similarity derived from cosine distance."""
        return 1.0 - self.distance

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "score": self.score}


def _make_offline_embedding_function() -> Any:
    """A ChromaDB-compatible EF that never downloads anything."""
    from chromadb.api.types import EmbeddingFunction

    class OfflineEmbeddingFunction(EmbeddingFunction):
        def __init__(self) -> None:
            pass

        @staticmethod
        def name() -> str:
            return "offline_no_download"

        def __call__(self, input: Sequence[str]) -> list[list[float]]:
            return [[0.0] for _ in input]

        def is_legacy(self) -> bool:
            return False

        @staticmethod
        def build_from_config(config: dict[str, Any]) -> "OfflineEmbeddingFunction":
            return OfflineEmbeddingFunction()

        def get_config(self) -> dict[str, Any]:
            return {}

    return OfflineEmbeddingFunction()


class ChromaVectorStore:
    """Persistent Chroma collection of course chunks."""

    def __init__(
        self,
        persist_dir: str | Path,
        collection_name: str = "course_chunks",
        client: Any = None,
    ) -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self._owns_client = client is None
        self._closed = False
        if client is None:
            import chromadb

            client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._client = client
        self._collection = self._open_collection()

    def _open_collection(self) -> Any:
        return self._client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=_make_offline_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )

    def rebuild(self) -> None:
        """Drop and recreate the collection (idempotent full rebuild)."""
        try:
            self._client.get_collection(self.collection_name)
        except Exception:
            pass
        else:
            self._client.delete_collection(self.collection_name)
        self._collection = self._open_collection()

    def upsert_chunks(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return 0
        metadatas = []
        for chunk in chunks:
            meta = chunk.metadata()
            if extra_metadata:
                meta = {**meta, **extra_metadata}
            metadatas.append(meta)
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=metadatas,
            embeddings=[list(map(float, vector)) for vector in vectors],
        )
        return len(chunks)

    def count(self) -> int:
        return int(self._collection.count())

    def delete_by_ids(self, ids: Sequence[str]) -> int:
        """Delete chunks by id, returning the number of ids passed.

        Used to remove a single uploaded material's chunks from the index
        without touching unrelated chunks (idempotent: missing ids are fine).
        """
        ids = [str(i) for i in ids]
        if not ids:
            return 0
        self._collection.delete(ids=ids)
        return len(ids)

    def delete_where(self, where: dict[str, Any]) -> int:
        """Delete every chunk whose metadata matches ``where``.

        Returns the number of records actually removed. Unlike
        :meth:`delete_by_ids`, this needs no in-memory bookkeeping: the filter
        is evaluated against the persisted collection, so it still works after
        a backend restart wiped the material table.
        """
        if not where:
            return 0
        try:
            found = self._collection.get(where=where, include=[])
            ids = found.get("ids") or []
        except Exception:
            return 0
        if not ids:
            return 0
        self._collection.delete(ids=ids)
        return len(ids)

    def clear(self) -> int:
        """Delete every chunk in the collection, returning how many were removed.

        Used by ``MaterialService.reset()`` to purge the entire ``student_uploads``
        index. Unlike ``delete_by_ids`` (which relies on ids tracked in memory),
        this operates directly on the persisted collection, so it also removes
        chunks that were indexed before a backend restart — the reason stale
        ``Source: ...`` citations could survive a reset.
        """
        removed = self.count()
        if removed <= 0:
            return 0
        try:
            # ChromaDB supports deleting all records via an empty ``where`` filter.
            self._collection.delete(where={})
        except Exception:
            # Fallback for older ChromaDB: drop and recreate the collection.
            self.rebuild()
        return removed

    def search_by_vector(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 3,
        topic: str | None = None,
    ) -> list[RetrievedChunk]:
        total = self.count()
        if total == 0 or top_k <= 0:
            return []
        result = self._collection.query(
            query_embeddings=[list(map(float, vector))],
            n_results=min(top_k, total),
            where={"topic": topic} if topic else None,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[RetrievedChunk] = []
        for index, chunk_id in enumerate(ids):
            meta = metadatas[index] or {}
            hits.append(
                RetrievedChunk(
                    chunk_id=str(chunk_id),
                    text=(documents[index] or ""),
                    lecture=str(meta.get("lecture", "")),
                    page=int(meta.get("page", 0)),
                    topic=str(meta.get("topic", "")),
                    source=str(meta.get("source", "")),
                    distance=float(distances[index]) if index < len(distances) else 1.0,
                )
            )
        return hits

    def search(
        self,
        text: str,
        embedder: Any,
        *,
        top_k: int = 3,
        topic: str | None = None,
    ) -> list[RetrievedChunk]:
        return self.search_by_vector(embedder.embed([text])[0], top_k=top_k, topic=topic)

    def citations(self, hits: Sequence[RetrievedChunk]) -> list[str]:
        """Deduplicated ``Source: Lecture 05, Page 14`` strings, best hit first."""
        seen: list[str] = []
        for hit in hits:
            if hit.source and hit.source not in seen:
                seen.append(hit.source)
        return seen

    def close(self) -> None:
        """Release ChromaDB's SQLite handle (needed on Windows before cleanup).

        ChromaDB >= 1.x caches a shared System per path. Calling
        ``_system.stop()`` directly leaves that cache poisoned, so reopening the
        same index in the same process fails with "Could not connect to tenant".
        ``client.close()`` releases the refcount and removes the cache entry.
        """
        if not self._owns_client or self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        else:  # pragma: no cover - older chromadb releases
            system = getattr(self._client, "_system", None)
            if system is not None and hasattr(system, "stop"):
                system.stop()
