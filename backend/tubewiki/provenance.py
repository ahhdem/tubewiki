"""Per-claim provenance: the load-bearing wall (spec §5.1).

A concept page is a markdown file that survives git *and* Obsidian:

    ---
    title: Agentic memory
    slug: agentic-memory
    type: concept
    created: 2026-08-16T00:00:00+00:00
    updated: 2026-08-16T00:00:00+00:00
    sources:
      yt:ID1: {title: ..., url: ..., channel: ..., ingested_at: ...}
    claim_sources:
      c1: yt:ID1
      c2: yt:ID2
    ---

    Agentic memory persists state across sessions. ^c1

    Vector stores give semantic recall. ^c2

    ## References

    1. [Title 1](https://youtu.be/ID1) — yt:ID1
    2. [Title 2](https://youtu.be/ID2) — yt:ID2

The ``^cN`` block-ref at the end of each paragraph is standard Obsidian syntax and
is the anchor that lets us remove exactly one source's contributions (``remove_source``)
without disturbing the rest of the page. Provenance is per-claim, not per-page — that
is what makes eviction (Phase 2) surgical rather than destructive.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import yaml

from .models import Claim, ConceptPage, Source

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_CLAIM_MARKER_RE = re.compile(r"\s*\^(c\d+)\s*$")
_REFERENCES_HEADING = "## References"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_page(page: ConceptPage) -> str:
    """Serialize a ConceptPage to its canonical markdown form."""
    fm = {
        "title": page.title,
        "slug": page.slug,
        "type": "concept",
        "category": list(page.category),
        "created": page.created,
        "updated": page.updated,
        "sources": {
            sid: {
                "title": s.title,
                "url": s.url,
                "channel": s.channel,
                "ingested_at": s.ingested_at,
            }
            for sid, s in page.sources.items()
        },
        "claim_sources": {c.id: c.source_id for c in page.claims},
    }
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()

    lines = [f"---\n{front}\n---", ""]
    if page.summary:
        lines += [page.summary.strip(), ""]
    for claim in page.claims:
        lines.append(f"{claim.text.strip()} ^{claim.id}")
        lines.append("")

    # References — one numbered entry per source, in first-seen order.
    lines.append(_REFERENCES_HEADING)
    lines.append("")
    for i, (sid, src) in enumerate(page.sources.items(), start=1):
        chan = f" · {src.channel}" if src.channel else ""
        lines.append(f"{i}. [{src.title}]({src.url}){chan} — `{sid}`")
    lines.append("")
    return "\n".join(lines)


def parse_page(md: str) -> ConceptPage:
    """Parse canonical markdown back into a ConceptPage (round-trips render_page)."""
    m = _FRONTMATTER_RE.match(md)
    if not m:
        raise ValueError("page is missing YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    sources: dict[str, Source] = {}
    for sid, sd in (fm.get("sources") or {}).items():
        sources[sid] = Source(
            id=sid,
            title=sd.get("title", sid),
            url=sd.get("url", ""),
            channel=sd.get("channel"),
            ingested_at=sd.get("ingested_at", _now()),
        )
    claim_sources: dict[str, str] = fm.get("claim_sources") or {}

    # Body claims come before the References heading.
    before_refs = body.split(_REFERENCES_HEADING, 1)[0]
    claims: list[Claim] = []
    summary_parts: list[str] = []
    for block in (b.strip() for b in before_refs.split("\n\n")):
        if not block:
            continue
        cm = _CLAIM_MARKER_RE.search(block)
        if cm:
            cid = cm.group(1)
            text = _CLAIM_MARKER_RE.sub("", block).strip()
            claims.append(Claim(id=cid, text=text, source_id=claim_sources.get(cid, "")))
        else:
            summary_parts.append(block)

    return ConceptPage(
        title=fm.get("title", ""),
        slug=fm.get("slug", ""),
        summary="\n\n".join(summary_parts) or None,
        category=list(fm.get("category") or []),
        claims=claims,
        sources=sources,
        created=fm.get("created", _now()),
        updated=fm.get("updated", _now()),
    )


def add_claims(page: ConceptPage, source: Source, texts: list[str]) -> int:
    """Append new claims from a source, registering the source. Returns count added."""
    page.sources.setdefault(source.id, source)
    added = 0
    existing = {c.text.strip().lower() for c in page.claims}
    for text in texts:
        norm = text.strip().lower()
        if not norm or norm in existing:
            continue  # cheap dedup; semantic dedup is a later concern
        page.claims.append(Claim(id=page.next_claim_id(), text=text.strip(), source_id=source.id))
        existing.add(norm)
        added += 1
    page.updated = _now()
    return added


def remove_source(page: ConceptPage, source_id: str) -> int:
    """Surgically evict one source: drop its claims and its registry entry.

    The rest of the page is untouched. Returns the number of claims removed. This is
    the operation the whole per-claim design exists to make cheap (spec §5.1).
    """
    before = len(page.claims)
    page.claims = [c for c in page.claims if c.source_id != source_id]
    page.sources.pop(source_id, None)
    page.updated = _now()
    return before - len(page.claims)
