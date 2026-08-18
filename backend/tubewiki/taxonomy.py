"""Category canonicalization (spec §7.2, OQ#3).

Goal: stop category drift ("AI" one session, "Artificial Intelligence" the next). The
primary mechanism is the LLM being shown existing categories and told to reuse them
verbatim (see llm.categorize). This module is a **backstop**: after the LLM proposes a
label, snap it to an existing category if it's a slug match or embeds close enough.

Empirical caveat (measured on nomic-embed-text, 2026-08): bare-label cosine does NOT
cleanly separate synonyms from siblings — abbreviations ("ML"↔"Machine Learning" 0.63)
score *below* unrelated-but-adjacent pairs ("AI"↔"Machine Learning" 0.63), and
parent/child ("Solar Power"↔"Renewable Energy" 0.71) scores high. So the threshold is set
CONSERVATIVELY (0.80): it catches obvious casing/synonym variants without wrongly merging
siblings. Embeddings are a safety net here, not the source of truth.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from slugify import slugify

from .corpus import Embedder

log = logging.getLogger("tubewiki.taxonomy")


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class Taxonomy:
    def __init__(self, embedder: Embedder, path: Path, threshold: float = 0.80):
        self.embedder = embedder
        self.path = Path(path)
        self.threshold = threshold
        self.labels: list[str] = []
        if self.path.exists():
            self.labels = json.loads(self.path.read_text() or "[]")
        # Recompute embeddings on load (few labels; robust to an embedder change).
        self._vecs: dict[str, list[float]] = {}
        if self.labels:
            for label, vec in zip(self.labels, self.embedder.embed(self.labels)):
                self._vecs[label] = _normalize(vec)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.labels, indent=2))

    def _register(self, label: str) -> str:
        self.labels.append(label)
        self._vecs[label] = _normalize(self.embedder.embed([label])[0])
        self._save()
        return label

    def canonicalize(self, label: str) -> str:
        label = (label or "").strip()
        if not label:
            return label
        # 1. Exact / slug match — free and certain.
        for known in self.labels:
            if slugify(known) == slugify(label):
                return known
        # 2. Embedding backstop — snap to the nearest existing label above threshold.
        if self.labels:
            v = _normalize(self.embedder.embed([label])[0])
            best_label, best_sim = None, -1.0
            for known, kv in self._vecs.items():
                sim = sum(a * b for a, b in zip(v, kv))
                if sim > best_sim:
                    best_label, best_sim = known, sim
            if best_label is not None and best_sim >= self.threshold:
                log.info("canonicalized %r -> %r (sim %.3f)", label, best_label, best_sim)
                return best_label
        # 3. Genuinely new category.
        return self._register(label)

    def canonicalize_path(self, path: list[str]) -> list[str]:
        return [self.canonicalize(seg) for seg in path if seg and seg.strip()]
