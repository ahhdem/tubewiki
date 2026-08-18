"""Concept-page merge + generation with references — the core value (spec §2, P1.9).

Content from multiple videos merges into one evolving concept page; each page carries
references back to the contributing videos. NOT one page per video. Every claim is
written with its source (per-claim provenance), so the References section and future
eviction both fall out of the same data.

OQ#4 (merge aggressiveness) lives here: today the concept is chosen by the LLM and
pages merge when the concept slug matches. The knob to tune is ``choose_concept``'s
willingness to reuse an existing concept — evaluate against the P1.11 worked-example set.
"""
from __future__ import annotations

from slugify import slugify

from .corpus import Corpus
from .llm import LLM
from .models import ConceptPage, Source
from .provenance import add_claims
from .vault import VaultStore


def source_id_for(video_id: str) -> str:
    return f"yt:{video_id}"


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def merge_video(
    *,
    video_id: str,
    title: str,
    channel: str | None,
    url: str | None,
    transcript: str,
    corpus: Corpus,
    llm: LLM,
    vault: VaultStore,
) -> tuple[ConceptPage, int, str]:
    """Distil one video into the wiki. Returns (page, claims_added, commit_sha)."""
    sid = source_id_for(video_id)

    # 1. Land the raw transcript in the corpus (retrieval substrate).
    corpus.add_video(sid, transcript, {"title": title, "channel": channel or ""})

    # 2. Pick the canonical concept, reusing an existing one where it fits (§7 preview).
    pages = vault.list_pages()
    existing_titles = [p.title for p in pages]
    concept = llm.choose_concept(title, transcript, existing_titles)
    slug = slugify(concept)

    # 3. Load-or-create the concept page.
    page = vault.read_page(slug) or ConceptPage(title=concept, slug=slug)

    # 3b. New page → place it in the category tree, reusing existing paths (§7.1).
    if not page.category:
        existing_paths = sorted({" / ".join(p.category) for p in pages if p.category})
        page.category = llm.categorize(concept, transcript, existing_paths)

    # 4. Distil into claims and merge them with provenance.
    claim_texts = llm.extract_claims(title, transcript)
    source = Source(id=sid, title=title, url=url or watch_url(video_id), channel=channel)
    added = add_claims(page, source, claim_texts)

    # 5. Commit to the git-backed vault.
    msg = f"ingest({video_id}): +{added} claims → {concept}"
    commit = vault.commit_page(page, msg)
    return page, added, commit
