"""End-to-end walking-skeleton test (offline stub path, no GPU needed).

Proves the two things Phase 1 must get right:
  1. Multiple videos on one topic MERGE into a single concept page with references
     (spec §2 — videos are the source, not the subject; NOT one page per video).
  2. Per-claim provenance makes eviction SURGICAL — pulling one source removes exactly
     its claims and leaves the rest intact (spec §5.1, the load-bearing wall).
"""
from __future__ import annotations

import pytest

from tubewiki.config import settings
from tubewiki.corpus import Corpus, make_embedder
from tubewiki.ledger import Ledger
from tubewiki.llm import make_llm
from tubewiki.models import IngestRequest
from tubewiki.provenance import parse_page, remove_source, render_page
from tubewiki.ingest import Pipeline
from tubewiki.vault import VaultStore

VID1, VID2 = "aaaaaaaaaaa", "bbbbbbbbbbb"
T1 = (
    "Agentic memory lets an autonomous agent persist important state across separate sessions. "
    "Without it, an agent forgets everything the moment a conversation ends. "
    "A vector database is commonly used to store and recall those memories semantically."
)
T2 = (
    "Long term agent memory can be split into episodic and semantic components for better recall. "
    "Retrieval quality depends heavily on how the memories are chunked before they are embedded. "
    "Evicting stale or wrong memories is as important as adding new ones over time."
)


@pytest.fixture
def stack(tmp_path):
    settings.offline = True
    settings.data_dir = tmp_path / "data"
    settings.qdrant_path = tmp_path / "qdrant"
    settings.vault_dir = tmp_path / "vault"
    settings.ledger_path = tmp_path / "data" / "ledger.json"
    settings.ensure_dirs()
    corpus = Corpus(make_embedder(True))
    vault = VaultStore(settings.vault_dir)
    ledger = Ledger(settings.ledger_path)
    pipe = Pipeline(corpus, make_llm(True), vault, ledger)
    return pipe, vault, corpus, ledger


def test_two_videos_merge_into_one_concept(stack):
    pipe, vault, corpus, _ = stack
    r1 = pipe.ingest(IngestRequest(video_id=VID1, title="Agentic Memory Explained", transcript=T1))
    r2 = pipe.ingest(IngestRequest(video_id=VID2, title="Agentic Memory - Deep Dive", transcript=T2))

    assert r1.status == "ingested" and r2.status == "ingested"
    # Both landed on the SAME concept page — the anti-goal (one page per video) avoided.
    assert r1.concept_slug == r2.concept_slug == "agentic-memory"
    pages = vault.list_pages()
    assert len(pages) == 1
    page = pages[0]

    # Two sources, claims from both, references render for both.
    assert set(page.sources) == {f"yt:{VID1}", f"yt:{VID2}"}
    assert page.source_ids() == {f"yt:{VID1}", f"yt:{VID2}"}
    md = render_page(page)
    assert "## References" in md and page.sources[f"yt:{VID1}"].url in md

    # Corpus retrieval works against the stored transcripts.
    hits = corpus.query("how are memories stored and recalled")
    assert hits and any(h.source_id in page.sources for h in hits)


def test_eviction_is_surgical(stack):
    pipe, vault, _, _ = stack
    pipe.ingest(IngestRequest(video_id=VID1, title="Agentic Memory Explained", transcript=T1))
    pipe.ingest(IngestRequest(video_id=VID2, title="Agentic Memory - Deep Dive", transcript=T2))
    page = vault.read_page("agentic-memory")

    v1_claims = [c for c in page.claims if c.source_id == f"yt:{VID1}"]
    v2_claims = [c for c in page.claims if c.source_id == f"yt:{VID2}"]
    assert v1_claims and v2_claims
    v2_ids_before = {c.id for c in v2_claims}

    removed = remove_source(page, f"yt:{VID1}")
    assert removed == len(v1_claims)
    # VID1 gone entirely; VID2 claims (and their block ids) untouched.
    assert all(c.source_id != f"yt:{VID1}" for c in page.claims)
    assert {c.id for c in page.claims} == v2_ids_before
    assert f"yt:{VID1}" not in page.sources and f"yt:{VID2}" in page.sources


def test_provenance_roundtrips(stack):
    pipe, vault, _, _ = stack
    pipe.ingest(IngestRequest(video_id=VID1, title="Agentic Memory Explained", transcript=T1))
    page = vault.read_page("agentic-memory")
    reparsed = parse_page(render_page(page))
    assert reparsed.slug == page.slug
    assert [(c.id, c.source_id) for c in reparsed.claims] == [(c.id, c.source_id) for c in page.claims]


def test_stickiness_and_dedup(stack):
    pipe, vault, _, ledger = stack
    pipe.ingest(IngestRequest(video_id=VID1, title="Agentic Memory Explained", transcript=T1))

    # Re-watch of an already-ingested video is a no-op in Phase 1.
    again = pipe.ingest(IngestRequest(video_id=VID1, title="Agentic Memory Explained", transcript=T1))
    assert again.status == "skipped-seen"

    # Evicted stickiness: once evicted, re-ingest is blocked (hard requirement §5.1).
    ledger.record(VID2, "evicted", reason="test")
    blocked = pipe.ingest(IngestRequest(video_id=VID2, title="Agentic Memory - Deep Dive", transcript=T2))
    assert blocked.status == "skipped-evicted"


def test_no_transcript_skips_and_logs(stack, monkeypatch):
    pipe, vault, _, ledger = stack
    # Simulate the backend fallback chain exhausting (no network dependence in the test).
    monkeypatch.setattr("tubewiki.ingest.get_transcript", lambda vid: None)
    res = pipe.ingest(IngestRequest(video_id="zzzzzzzzzzz", title="Nonexistent"))
    assert res.status == "skipped-no-transcript"
    assert ledger.state("zzzzzzzzzzz") == "skipped"
