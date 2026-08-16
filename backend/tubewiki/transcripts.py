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

log = logging.getLogger("tubewiki.transcripts")


def _fetch_via_api(video_id: str) -> Optional[str]:
    """youtube-transcript-api. Prefers manual captions, falls back to auto-generated.
    Handles both the classic and the newer instance API shapes."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        # Newer API (>=0.6.2): instance .fetch()
        if hasattr(YouTubeTranscriptApi, "list") or hasattr(YouTubeTranscriptApi(), "fetch"):
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            snippets = getattr(fetched, "snippets", fetched)
            return " ".join(getattr(s, "text", s.get("text", "")) for s in snippets).strip() or None
    except Exception as e:  # noqa: BLE001
        log.info("instance fetch failed for %s (%s); trying classic API", video_id, e)
    try:
        # Classic API: static get_transcript()
        segments = YouTubeTranscriptApi.get_transcript(video_id)  # type: ignore[attr-defined]
        return " ".join(s["text"] for s in segments).strip() or None
    except Exception as e:  # noqa: BLE001
        log.info("no transcript via API for %s: %s", video_id, e)
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
    """Backend fallback chain (extension path is handled before this is called)."""
    text = _fetch_via_api(video_id)
    if text:
        return text
    text = _fetch_via_whisper(video_id)
    if text:
        return text
    log.warning("skip-and-log: no transcript obtainable for %s", video_id)
    return None
