"""TubeWiki transcript service — run this on the GPU box (.78).

Why it exists: YouTube blocks the caption endpoint from datacenter/cloud IPs, so the
TubeWiki backend (which may run anywhere) can't reliably fetch transcripts itself. This
service runs on the *residential* LAN, so caption fetches succeed, and it has the GPU for
a Whisper fallback when a video has no captions. The backend calls it by URL — same
host-service-over-LAN pattern as Ollama.

Endpoints:
  GET  /healthz
  POST /transcript   {"video_id": "..."}  ->  {"video_id","transcript","source"}

Env:
  PORT            (default 8090)
  ENABLE_WHISPER  (default "1"; set "0" to disable the GPU fallback)
  WHISPER_MODEL   (default "large-v3")
  WHISPER_DEVICE  (default "cuda")
  WHISPER_COMPUTE (default "int8_float16")
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("transcribe")

ENABLE_WHISPER = os.environ.get("ENABLE_WHISPER", "1") == "1"
_LANGS = ["en", "en-US", "en-GB"]
_whisper_model = None  # lazy-loaded on first use


class Req(BaseModel):
    video_id: str


app = FastAPI(title="TubeWiki transcribe", version="0.1.0")


def _snippets_to_text(fetched) -> str:
    rows = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    parts = [r.get("text", "") if isinstance(r, dict) else (getattr(r, "text", "") or "") for r in rows]
    return " ".join(p for p in parts if p).replace("\n", " ").strip()


def fetch_captions(video_id: str) -> str | None:
    """youtube-transcript-api 1.x, residential IP. Manual > generated > translated-en."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        ytt = YouTubeTranscriptApi()
        tl = ytt.list(video_id)
        transcript = None
        for finder in ("find_manually_created_transcript", "find_generated_transcript"):
            try:
                transcript = getattr(tl, finder)(_LANGS)
                break
            except Exception:  # noqa: BLE001
                continue
        if transcript is None:
            for t in tl:
                transcript = t.translate("en") if getattr(t, "is_translatable", False) else t
                break
        if transcript is not None:
            return _snippets_to_text(transcript.fetch()) or None
    except Exception as e:  # noqa: BLE001
        log.info("captions unavailable for %s: %s", video_id, e)
    return None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(
            os.environ.get("WHISPER_MODEL", "large-v3"),
            device=os.environ.get("WHISPER_DEVICE", "cuda"),
            compute_type=os.environ.get("WHISPER_COMPUTE", "int8_float16"),
        )
    return _whisper_model


def transcribe_whisper(video_id: str) -> str | None:
    """yt-dlp bestaudio -> faster-whisper on the GPU."""
    if not ENABLE_WHISPER:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "a.%(ext)s")
        try:
            subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "wav", "-o", out,
                 f"https://www.youtube.com/watch?v={video_id}"],
                check=True, capture_output=True, timeout=600,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("yt-dlp failed for %s: %s", video_id, e)
            return None
        wavs = list(Path(tmp).glob("*.wav"))
        if not wavs:
            return None
        try:
            segments, _ = _get_whisper().transcribe(str(wavs[0]))
            text = " ".join(s.text for s in segments).strip()
            return text or None
        except Exception as e:  # noqa: BLE001
            log.warning("whisper failed for %s: %s", video_id, e)
            return None


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "whisper": ENABLE_WHISPER}


@app.post("/transcript")
def transcript(req: Req) -> dict:
    text = fetch_captions(req.video_id)
    if text:
        return {"video_id": req.video_id, "transcript": text, "source": "captions"}
    text = transcribe_whisper(req.video_id)
    if text:
        return {"video_id": req.video_id, "transcript": text, "source": "whisper"}
    return {"video_id": req.video_id, "transcript": None, "source": None}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")))


if __name__ == "__main__":
    main()
