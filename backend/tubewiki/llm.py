"""Generation tier (spec §2, §5.4, P1.9).

Two responsibilities the merge step needs:
  1. choose_concept — which canonical concept page does this video belong to?
  2. extract_claims — distil the transcript into discrete, attributable assertions.

Real path uses host Ollama's OpenAI-compatible /v1/chat/completions. Offline path is a
deterministic template that produces a weak-but-real distillation (salient sentences),
so the full ingest→merge→browse loop runs without the GPU. Both are hidden behind ``LLM``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol

import httpx
from slugify import slugify

from .config import settings

log = logging.getLogger("tubewiki.llm")

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_MAX_CLAIMS = 8


class LLM(Protocol):
    def choose_concept(self, title: str, transcript: str, existing_titles: list[str]) -> str: ...
    def extract_claims(self, title: str, transcript: str) -> list[str]: ...


def _clean_concept(title: str) -> str:
    """Strip clickbait scaffolding from a video title to approximate a topic."""
    t = re.sub(r"\s*[\|\-–—:]\s*.*$", "", title)  # drop trailing " | channel"/subtitle
    t = re.sub(r"\b(20\d\d|tutorial|explained|guide|full course|part\s*\d+)\b", "", t, flags=re.I)
    t = re.sub(r"[^\w\s]", " ", t)
    t = " ".join(t.split())
    return t.title() if t else title.strip()


class TemplateLLM:
    """Offline, deterministic. Real plumbing, weak distillation."""

    def choose_concept(self, title: str, transcript: str, existing_titles: list[str]) -> str:
        concept = _clean_concept(title)
        # Reuse an existing concept if the slug matches (biases toward merging).
        cslug = slugify(concept)
        for et in existing_titles:
            if slugify(et) == cslug:
                return et
        return concept

    def extract_claims(self, title: str, transcript: str) -> list[str]:
        text = " ".join(transcript.split())
        sentences = [s.strip() for s in _SENT_RE.split(text) if len(s.strip()) > 40]
        # Prefer sentences that look declarative/informative; cap the count.
        picked, seen = [], set()
        for s in sentences:
            key = s.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            picked.append(s if s.endswith((".", "!", "?")) else s + ".")
            if len(picked) >= _MAX_CLAIMS:
                break
        return picked


class OllamaLLM:
    """Real generation via Ollama's OpenAI-compatible /v1 endpoint."""

    def __init__(self):
        self._url = f"{settings.ollama_base_url}/v1/chat/completions"
        self._model = settings.llm_model

    def _chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        payload = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        last_err = None
        for attempt in range(3):  # the box occasionally 500s / drops under load
            try:
                r = httpx.post(self._url, headers={"Authorization": "Bearer ollama"},
                               json=payload, timeout=180)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                # Ollama routes qwen3 "thinking" to a separate `reasoning` field, but strip
                # inline <think> blocks defensively so they never leak into output.
                return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as e:
                last_err = e
                log.info("ollama chat attempt %d/3 failed: %s", attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
        raise last_err

    def choose_concept(self, title: str, transcript: str, existing_titles: list[str]) -> str:
        existing = "\n".join(f"- {t}" for t in existing_titles) or "(none yet)"
        system = (
            "You organize a wiki. Given a video, name the single canonical CONCEPT it "
            "contributes to. Videos are the source, not the subject — prefer broad, "
            "reusable concept names, and REUSE an existing concept verbatim when the "
            "video fits one. Reply with only the concept name."
        )
        user = f"Existing concepts:\n{existing}\n\nVideo title: {title}\n\nExcerpt:\n{transcript[:2000]}"
        try:
            out = self._chat(system, user).splitlines()[0].strip(" #-*\"")
            return out or _clean_concept(title)
        except Exception as e:  # noqa: BLE001 — fall back to the heuristic, don't fail ingest
            log.warning("choose_concept fell back to heuristic for %r: %s", title, e)
            return _clean_concept(title)

    def extract_claims(self, title: str, transcript: str) -> list[str]:
        system = (
            "Distil the transcript into discrete, standalone factual claims a reader "
            "could learn without watching. Each claim: one sentence, self-contained, no "
            "fluff. Return a JSON array of strings, at most 8."
        )
        try:
            raw = self._chat(system, f"Title: {title}\n\nTranscript:\n{transcript[:8000]}")
            arr = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
            return [str(c).strip() for c in arr if str(c).strip()][:_MAX_CLAIMS]
        except Exception as e:  # noqa: BLE001
            log.warning("claim extraction fell back to template: %s", e)
            return TemplateLLM().extract_claims(title, transcript)


def make_llm(offline: bool) -> LLM:
    return TemplateLLM() if offline else OllamaLLM()
