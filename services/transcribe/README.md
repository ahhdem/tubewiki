# TubeWiki transcript service (run on the GPU box, `.78`)

A tiny HTTP service that turns a `video_id` into a transcript. It exists because
**YouTube blocks the caption endpoint from datacenter/cloud IPs** — so the backend can't
fetch transcripts from just anywhere. This service runs on the **residential LAN** (where
caption fetches succeed) and has the **GPU** for a Whisper fallback when a video has no
captions. Same "host service over the LAN" shape as Ollama.

```
backend  ──POST /transcript {video_id}──▶  .78:8090
                                            ├─ captions (youtube-transcript-api, residential IP)
                                            └─ fallback: yt-dlp audio → faster-whisper (GPU)
```

## Run it

Anywhere on the residential LAN with the GPU — natively is simplest:

```bash
cd services/transcribe
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# captions + Whisper (GPU):
.venv/bin/python app.py
# captions only (no GPU / skip heavy deps): set ENABLE_WHISPER=0 and you can drop
# faster-whisper + yt-dlp from requirements.
```

Listens on `:8090`. Whisper (`faster-whisper`) needs CUDA; on the dual-GPU box the
defaults (`large-v3`, `device=cuda`, `int8_float16`, ~2.5 GB VRAM) coexist with Ollama.

### Env

| Var | Default | Notes |
|---|---|---|
| `PORT` | `8090` | |
| `ENABLE_WHISPER` | `1` | `0` = captions-only, no GPU deps needed |
| `WHISPER_MODEL` | `large-v3` | `distil-large-v3` for more throughput |
| `WHISPER_DEVICE` | `cuda` | `cpu` works but is slow |
| `WHISPER_COMPUTE` | `int8_float16` | |

## Point the backend at it

Set on the backend (see `.env.example`):

```
TUBEWIKI_TRANSCRIPT_URL=http://192.168.86.78:8090
```

When set, the backend calls this service first and skips its own (blockable) fetch. The
extension path still takes precedence when it supplies a transcript.

## Check it

```bash
curl -s localhost:8090/healthz
curl -s -X POST localhost:8090/transcript -H 'content-type: application/json' \
  -d '{"video_id":"arj7oStGLkU"}' | head -c 300
```
