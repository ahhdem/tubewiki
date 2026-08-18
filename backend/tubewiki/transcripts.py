"""Transcript acquisition + fallback policy (spec §4, OQ#5).

Ordered decision branch (decided by the P1.5 spike):

    1. Extension-supplied transcript (logged-in session, residential IP)  -- handled upstream
    2. Backend fetch via youtube-transcript-api (run from the HOMELAB's residential IP,
       never a cloud IP — YouTube blocks datacenter IPs from the timedtext endpoint)
    3. Local Whisper on the GPU box (faster-whisper large-v3 INT8) -- Phase-1 target,
       optional install; hook below
    4. Skip-and-log (terminal) -- record video_id + reason for later backfill

``get_transcript`` returns the transcript text or None (→ caller records skip-and-log).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from .config import settings

log = logging.getLogger("tubewiki.transcripts")


def _fetch_via_service(video_id: str) -> Optional[str]:
    """Residential transcript service (services/transcribe on .78). Handles captions +
    Whisper on the far side, so the backend never fetches from a blockable IP itself."""
    if not settings.transcript_url:
        return None
    try:
        r = httpx.post(settings.transcript_url.rstrip("/") + "/transcript",
                       json={"video_id": video_id}, timeout=650)
        r.raise_for_status()
        return r.json().get("transcript")
    except Exception as e:  # noqa: BLE001
        log.info("transcript service failed for %s: %s", video_id, e)
        return None


_LANGS = ["en", "en-US", "en-GB"]


def _snippets_to_text(fetched) -> str:
    """Flatten a fetched transcript to plain text, across library versions.
    1.x returns a FetchedTranscript (has to_raw_data()); older returns list[dict]."""
    rows = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    parts = []
    for r in rows:
        parts.append(r.get("text", "") if isinstance(r, dict) else (getattr(r, "text", "") or ""))
    return " ".join(p for p in parts if p).replace("\n", " ").strip()


def _fetch_via_api(video_id: str) -> Optional[str]:
    """youtube-transcript-api. Prefers manual captions over auto-generated, translates to
    English when needed. Handles the 1.x instance API and the legacy static API."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    # 1.x instance API: list tracks, prefer manual → generated → any (translated).
    try:
        ytt = YouTubeTranscriptApi()
        if hasattr(ytt, "list"):
            tl = ytt.list(video_id)
            transcript = None
            for finder in ("find_manually_created_transcript", "find_generated_transcript"):
                try:
                    transcript = getattr(tl, finder)(_LANGS)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if transcript is None:
                for t in tl:  # any language; translate to en if the track allows it
                    try:
                        transcript = t.translate("en") if getattr(t, "is_translatable", False) else t
                    except Exception:  # noqa: BLE001
                        transcript = t
                    break
            if transcript is not None:
                text = _snippets_to_text(transcript.fetch())
                if text:
                    return text
        elif hasattr(ytt, "fetch"):
            text = _snippets_to_text(ytt.fetch(video_id, languages=_LANGS))
            if text:
                return text
    except Exception as e:  # noqa: BLE001
        log.info("transcript-api (instance) failed for %s: %s", video_id, e)

    # Legacy static API (< 1.0)
    try:
        seg = YouTubeTranscriptApi.get_transcript(video_id, languages=_LANGS)  # type: ignore[attr-defined]
        return " ".join(s["text"] for s in seg).strip() or None
    except Exception as e:  # noqa: BLE001
        log.info("transcript-api (legacy) failed for %s: %s", video_id, e)
        return None


def _fetch_via_whisper(video_id: str) -> Optional[str]:
    """Phase-1 fallback: yt-dlp audio → faster-whisper on the GPU box.

    Heavy deps (``pip install .[whisper]``) and only sensible where the GPU lives, so
    this is best-effort: if the deps aren't present we return None and let the caller
    skip-and-log. Left as an explicit hook rather than run here (this runner has no GPU).
    """
    try:
        import faster_whisper  # noqa: F401
        import yt_dlp  # noqa: F401
    except ImportError:
        log.info("whisper fallback unavailable (install .[whisper] on the GPU host)")
        return None
    # TODO(P1.5): yt-dlp bestaudio -> wav -> faster-whisper large-v3 INT8 -> text.
    # Deliberately not implemented on this runner; the interface is the contract.
    log.warning("whisper deps present but transcription not wired on this host yet")
    return None


def get_transcript(video_id: str) -> Optional[str]:
    """Backend fallback chain (extension path is handled before this is called).
    Residential service first (captions + whisper), then local fetch, then local whisper."""
    for fetch in (_fetch_via_service, _fetch_via_api, _fetch_via_whisper):
        text = fetch(video_id)
        if text:
            return text
    log.warning("skip-and-log: no transcript obtainable for %s", video_id)
    return None
