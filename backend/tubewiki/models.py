"""Core domain models.

The atomic unit of provenance is the **Claim**: one assertion, attributed to exactly
one source video. A ConceptPage is a set of claims drawn from many sources, plus the
source registry needed to render references and to evict a source surgically later.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Source(BaseModel):
    """A contributing video."""

    id: str  # canonical source id, e.g. "yt:dQw4w9WgXcQ"
    title: str
    url: str
    channel: Optional[str] = None
    ingested_at: str = Field(default_factory=_now)


class Claim(BaseModel):
    """One assertion on a concept page, attributed to one source.

    `id` is a page-local block id (Obsidian block-ref, ``^c3``). It never changes once
    assigned, so eviction can find and remove exactly the blocks a source contributed.
    """

    id: str  # page-local, e.g. "c3"
    text: str
    source_id: str


class ConceptPage(BaseModel):
    """A distilled topic page. Serialized to markdown by ``provenance.py``."""

    title: str
    slug: str
    summary: Optional[str] = None
    claims: list[Claim] = Field(default_factory=list)
    sources: dict[str, Source] = Field(default_factory=dict)
    created: str = Field(default_factory=_now)
    updated: str = Field(default_factory=_now)

    def next_claim_id(self) -> str:
        n = 1 + max((int(c.id[1:]) for c in self.claims if c.id[1:].isdigit()), default=0)
        return f"c{n}"

    def source_ids(self) -> set[str]:
        return {c.source_id for c in self.claims}


# --- API payloads ----------------------------------------------------------


class IngestRequest(BaseModel):
    """Posted by the extension (transcript included) or the backend fallback path."""

    video_id: str
    title: str
    channel: Optional[str] = None
    url: Optional[str] = None
    # Transcript captured browser-side (preferred). If absent, backend fetches it.
    transcript: Optional[str] = None


class IngestResult(BaseModel):
    video_id: str
    status: str  # "ingested" | "skipped-seen" | "skipped-no-transcript"
    concept_slug: Optional[str] = None
    concept_title: Optional[str] = None
    claims_added: int = 0
    commit: Optional[str] = None
    detail: Optional[str] = None
