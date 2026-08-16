"""Ollama reachability + offline-mode resolution.

Embeddings use the NATIVE ``/api/embed`` endpoint; the chat LLM uses the
OpenAI-compatible ``/v1`` endpoint. (This split is a real gotcha on this box — see the
cognee configmap in the gitops repo.) If the box is unreachable and offline mode was
not explicitly requested, we fall back to the deterministic stub path so the pipeline
still runs.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("tubewiki.clients")


def ollama_reachable(timeout: float = 3.0) -> bool:
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        log.debug("ollama unreachable: %s", e)
        return False


def resolve_offline() -> bool:
    """Effective offline mode: explicit setting wins; otherwise probe the box."""
    if settings.offline:
        return True
    if not ollama_reachable():
        log.warning(
            "Ollama at %s is unreachable — falling back to OFFLINE stub mode "
            "(deterministic embedder + template LLM). Set real endpoints to go live.",
            settings.ollama_base_url,
        )
        return True
    return False
