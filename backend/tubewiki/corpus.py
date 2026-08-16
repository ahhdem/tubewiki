"""Corpus tier — the RAG retrieval substrate (spec §3, §3.1).

Decision (from the P1.2 spike, OQ#2): **plain Qdrant + host-Ollama embeddings**, NOT
KAITO. KAITO's remote-embedding client is hardcoded to HuggingFace TEI wire format and
can't talk to Ollama; it's also a pre-1.0 sandbox CRD. Qdrant runs in local (embedded)
mode here — no server process — and swaps to a client-server URL for production.

Embeddings come from host Ollama's native ``/api/embed`` (``qwen3-embedding``, 2560-dim).
In offline mode a deterministic hash embedder stands in so the pipeline is testable
without the GPU. Everything sits behind ``Corpus`` so LlamaIndex could replace the guts
later without touching callers.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from typing import Protocol

import httpx
from qdrant_client import QdrantClient, models as qm

from .config import settings

log = logging.getLogger("tubewiki.corpus")


# --- Embedders -------------------------------------------------------------


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Real embeddings via Ollama's native /api/embed."""

    def __init__(self):
        self.dim = settings.embedding_dim
        self._url = f"{settings.ollama_base_url}/api/embed"
        self._model = settings.embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(self._url, json={"model": self._model, "input": texts}, timeout=120)
        r.raise_for_status()
        embeddings = r.json()["embeddings"]
        if embeddings and len(embeddings[0]) != self.dim:
            log.warning(
                "embedding dim %d != configured %d for model %s — update settings.embedding_dim",
                len(embeddings[0]), self.dim, self._model,
            )
            self.dim = len(embeddings[0])
        return embeddings


class HashEmbedder:
    """Deterministic offline embedder. Not semantic, but stable and dependency-free —
    enough to exercise the store/retrieve plumbing end-to-end in tests and dev."""

    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in text.lower().split():
                h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "big")
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def make_embedder(offline: bool) -> Embedder:
    return HashEmbedder() if offline else OllamaEmbedder()


# --- Corpus ----------------------------------------------------------------


@dataclass
class Retrieved:
    text: str
    source_id: str
    score: float


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Simple char-window chunker with overlap, snapping to whitespace."""
    text = " ".join(text.split())
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            sp = text.rfind(" ", start + size - overlap, end)
            if sp > start:
                end = sp
        chunks.append(text[start:end].strip())
        start = max(end - overlap, end) if end < len(text) else end
    return [c for c in chunks if c]


class Corpus:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.client = QdrantClient(path=str(settings.qdrant_path))
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if settings.collection_name not in existing:
            self.client.create_collection(
                collection_name=settings.collection_name,
                vectors_config=qm.VectorParams(size=self.embedder.dim, distance=qm.Distance.COSINE),
            )

    def add_video(self, source_id: str, text: str, metadata: dict) -> int:
        """Chunk, embed, and store one video's transcript. Returns chunk count."""
        chunks = chunk_text(text, settings.chunk_chars, settings.chunk_overlap)
        if not chunks:
            return 0
        vectors = self.embedder.embed(chunks)
        points = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            pid = int.from_bytes(hashlib.md5(f"{source_id}:{i}".encode()).digest()[:8], "big")
            points.append(qm.PointStruct(
                id=pid, vector=vec,
                payload={"source_id": source_id, "text": chunk, **metadata},
            ))
        self.client.upsert(collection_name=settings.collection_name, points=points)
        return len(chunks)

    def query(self, text: str, top_k: int | None = None) -> list[Retrieved]:
        vec = self.embedder.embed([text])[0]
        res = self.client.query_points(
            collection_name=settings.collection_name,
            query=vec, limit=top_k or settings.retrieval_top_k, with_payload=True,
        ).points
        return [Retrieved(p.payload.get("text", ""), p.payload.get("source_id", ""), p.score) for p in res]

    def remove_source(self, source_id: str) -> None:
        """Drop a source's chunks from the corpus (used by Phase-2 eviction)."""
        self.client.delete(
            collection_name=settings.collection_name,
            points_selector=qm.FilterSelector(filter=qm.Filter(
                must=[qm.FieldCondition(key="source_id", match=qm.MatchValue(value=source_id))]
            )),
        )
