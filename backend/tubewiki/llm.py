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
    def categorize(self, concept: str, transcript: str, existing_paths: list[str]) -> list[str]: ...


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

    def categorize(self, concept: str, transcript: str, existing_paths: list[str]) -> list[str]:
        # Offline heuristic can't reason about domains; park everything under "General".
        return ["General"]


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
            "You maintain the topic index of a wiki. Output the single canonical TOPIC a "
            "video belongs under. Strict rules:\n"
            "- 1 to 4 words. A noun phrase naming a topic — NOT a description or sentence.\n"
            "- Title Case. No verbs, no 'How to', no colons, no clickbait, no year.\n"
            "- Prefer a broad topic many videos can share — but do NOT force unrelated "
            "videos together.\n"
            "- Reuse an existing topic VERBATIM only if the video is PRIMARILY about that "
            "same subject. If the subject is meaningfully different, create a NEW topic "
            "even when it is loosely related.\n"
            "Reply with ONLY the topic, nothing else.\n\n"
            "Examples:\n"
            "'I Built an AI Agent That Books My Flights (INSANE)' -> AI Agents\n"
            "'The Complete Guide to Kubernetes Networking in 2026' -> Kubernetes Networking\n"
            "existing=['AI Agent Memory']; 'MSI Edge AI PC for Running Local LLMs' -> "
            "Local AI Hardware   (different subject — do NOT reuse 'AI Agent Memory')"
        )
        user = f"Existing topics:\n{existing}\n\nVideo title: {title}\n\nTranscript excerpt:\n{transcript[:1500]}"
        try:
            out = self._chat(system, user, temperature=0.0).splitlines()[0]
            out = re.sub(r"^(topic|category)\s*[:\-]\s*", "", out, flags=re.I)  # strip "Topic:" prefix
            out = out.strip(" \t#*\"'`.").strip()
            if not out:
                return _clean_concept(title)
            # Snap casing/spacing variants onto an existing topic so pages actually merge.
            for et in existing_titles:
                if slugify(et) == slugify(out):
                    return et
            return out
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

    def categorize(self, concept: str, transcript: str, existing_paths: list[str]) -> list[str]:
        existing = "\n".join(f"- {p}" for p in existing_paths) or "(none yet)"
        system = (
            "You place a wiki topic into a shallow category tree. Output a path of 1-2 "
            "levels, broadest first, formatted 'Top / Sub'. Rules:\n"
            "- Each level is 1-2 words, Title Case, a BROAD domain (e.g. Technology, AI, "
            "DevOps, Finance, Health, Science, Business).\n"
            "- The top level must be very broad. Add a second level only if it groups "
            "usefully; otherwise give just the top level.\n"
            "- REUSE an existing path (or its prefix) VERBATIM whenever it fits.\n"
            "Reply with ONLY the path, e.g. 'Technology / AI'."
        )
        user = f"Existing category paths:\n{existing}\n\nTopic: {concept}\n\nExcerpt:\n{transcript[:800]}"
        try:
            out = self._chat(system, user, temperature=0.0).splitlines()[0]
            parts = [p.strip(" \t#*\"'`.") for p in out.split("/")]
            parts = [p for p in parts if p][:2]
            return parts or ["General"]
        except Exception as e:  # noqa: BLE001
            log.warning("categorize fell back to General for %r: %s", concept, e)
            return ["General"]


def make_llm(offline: bool) -> LLM:
    return TemplateLLM() if offline else OllamaLLM()
