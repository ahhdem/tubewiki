"""Curation actions — Phase 2's gate (spec §5).

Phase-1 auto-approves everything; the human control this adds is **eviction**: pull one
source out and take exactly its contributions with it, leaving the rest of every page
intact. This is the payoff of per-claim provenance (§5.1) — it's surgical, not a page
delete. Eviction is **sticky**: the ledger records "evicted", and the ingest pipeline
refuses to re-ingest an evicted video (already enforced in Pipeline).

Evicted claims are also written to the rejected-claims collection (§6) so the system can
later answer "have I rejected something like this before?".
"""
from __future__ import annotations

import logging
import threading

from .corpus import Corpus, RejectedClaims
from .ledger import Ledger
from .provenance import remove_source
from .vault import VaultStore

log = logging.getLogger("tubewiki.curation")


class Curation:
    def __init__(self, vault: VaultStore, corpus: Corpus, ledger: Ledger,
                 rejected: RejectedClaims, lock: threading.Lock):
        self.vault = vault
        self.corpus = corpus
        self.ledger = ledger
        self.rejected = rejected
        self._lock = lock  # shared with the ingest pipeline

    def evict_source(self, source_id: str) -> dict:
        """Remove a source's claims from every page + the corpus, mark it evicted."""
        with self._lock:
            removed: list[str] = []
            pages_touched = pages_deleted = 0
            for page in self.vault.list_pages():
                claims = [c.text for c in page.claims if c.source_id == source_id]
                if not claims:
                    continue
                n = remove_source(page, source_id)
                removed.extend(claims)
                pages_touched += 1
                if not page.claims:
                    self.vault.delete_page(page.slug, f"evict {source_id}: page emptied")
                    pages_deleted += 1
                else:
                    self.vault.commit_page(page, f"evict {source_id}: -{n} claims")

            self.corpus.remove_source(source_id)
            if removed:
                self.rejected.add(removed)

            vid = source_id.split(":", 1)[1] if ":" in source_id else source_id
            self.ledger.record(vid, "evicted", reason="manual",
                               claims_removed=len(removed))
            log.info("evicted %s: -%d claims across %d pages (%d emptied)",
                     source_id, len(removed), pages_touched, pages_deleted)
            return {
                "source_id": source_id, "claims_removed": len(removed),
                "pages_touched": pages_touched, "pages_deleted": pages_deleted,
            }
