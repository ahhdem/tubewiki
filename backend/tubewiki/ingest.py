"""Ingestion pipeline (spec §4, P1.6): the orchestrator wiring capture → corpus →
merge → git, with the ledger providing dedup and eviction-stickiness.

Phase 1 auto-approves everything (§5 walking-skeleton rule). The seams for Phase 2
(the human gate) are already here: the ledger records state, and eviction stickiness is
enforced before any work happens.
"""
from __future__ import annotations

import logging
import threading

from .corpus import Corpus
from .ledger import Ledger
from .llm import LLM
from .merge import merge_video, source_id_for
from .models import IngestRequest, IngestResult
from .transcripts import get_transcript
from .vault import VaultStore

log = logging.getLogger("tubewiki.ingest")


class Pipeline:
    def __init__(self, corpus: Corpus, llm: LLM, vault: VaultStore, ledger: Ledger,
                 lock: "threading.Lock | None" = None):
        self.corpus = corpus
        self.llm = llm
        self.vault = vault
        self.ledger = ledger
        # Qdrant's local (embedded) mode and the JSON ledger are single-writer; FastAPI
        # runs this sync endpoint in a threadpool, so serialize the whole pipeline. Shared
        # with curation (eviction) so writes never interleave. One user is serial anyway.
        self._lock = lock or threading.Lock()

    def ingest(self, req: IngestRequest) -> IngestResult:
        with self._lock:
            return self._ingest(req)

    def _ingest(self, req: IngestRequest) -> IngestResult:
        vid = req.video_id

        # Eviction stickiness (§5.1, hard requirement): never silently re-ingest.
        if self.ledger.is_evicted(vid):
            log.info("refusing re-ingest of evicted video %s", vid)
            return IngestResult(video_id=vid, status="skipped-evicted",
                                detail="source was evicted; re-ingestion is blocked")

        # Dedup: already approved → nothing to do (re-watch is a no-op in Phase 1). This
        # also absorbs the duplicate POST when a reloaded tab's content script re-captures.
        if self.ledger.state(vid) == "approved":
            return IngestResult(video_id=vid, status="skipped-seen",
                                detail="already ingested")

        # Transcript: prefer the extension-supplied one, else backend fallback chain.
        transcript = req.transcript or get_transcript(vid)
        if not transcript:
            self.ledger.record(vid, "skipped", reason="no_transcript", title=req.title)
            return IngestResult(video_id=vid, status="skipped-no-transcript",
                                detail="no transcript obtainable; recorded for backfill")

        try:
            page, added, commit = merge_video(
                video_id=vid, title=req.title, channel=req.channel, url=req.url,
                transcript=transcript, corpus=self.corpus, llm=self.llm, vault=self.vault,
            )
        except Exception as e:  # noqa: BLE001 — one bad video must not 500 the request
            log.exception("ingest failed for %s", vid)
            self.ledger.record(vid, "error", reason=str(e)[:200], title=req.title)
            return IngestResult(video_id=vid, status="error", detail=str(e)[:200])

        self.ledger.record(vid, "approved", source_id=source_id_for(vid),
                           concept=page.slug, commit=commit)
        return IngestResult(
            video_id=vid, status="ingested", concept_slug=page.slug,
            concept_title=page.title, claims_added=added, commit=commit,
        )
