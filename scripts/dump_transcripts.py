#!/usr/bin/env python3
"""Fetch transcripts + basic metadata ONCE and cache them to JSON, so repeated
ingests (tuning runs) never hit YouTube again — which is what trips 429/IP bans.

Run this from a residential IP for best hit-rate. Output feeds `ingest_cache.py`.

Usage:
    python scripts/dump_transcripts.py --file urls.txt --out cache.json --delay 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from tubewiki.transcripts import _fetch_via_api  # noqa: E402

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def to_id(s: str) -> str | None:
    s = s.strip()
    if not s or s.startswith("#"):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = _ID_RE.search(s)
    return m.group(1) if m else None


def oembed(video_id: str) -> tuple[str, str | None]:
    """Title + channel via the lightweight oEmbed endpoint (rarely rate-limited)."""
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=15,
        )
        if r.status_code == 200:
            d = r.json()
            return d.get("title", video_id), d.get("author_name")
    except Exception:  # noqa: BLE001
        pass
    return video_id, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=3.0)
    args = ap.parse_args()

    ids = [i for i in (to_id(x) for x in open(args.file).read().splitlines()) if i]
    seen, hits, misses = set(), [], []
    for i, vid in enumerate(ids, 1):
        if vid in seen:
            continue
        seen.add(vid)
        title, channel = oembed(vid)
        transcript = None
        for attempt in range(3):
            try:
                transcript = _fetch_via_api(vid)
                break
            except Exception as e:  # noqa: BLE001 — likely rate limit; back off
                wait = args.delay * (attempt + 2)
                print(f"  {vid}: retrying in {wait:.0f}s ({e})", file=sys.stderr)
                time.sleep(wait)
        if transcript:
            hits.append({"video_id": vid, "title": title, "channel": channel,
                         "transcript": transcript})
            print(f"{i:2}/{len(ids)} {vid} OK {len(transcript)}c  {title[:50]}")
        else:
            misses.append(vid)
            print(f"{i:2}/{len(ids)} {vid} MISS  {title[:50]}")
        time.sleep(args.delay)

    Path(args.out).write_text(json.dumps(hits, indent=2))
    print(f"\ncached {len(hits)} transcripts -> {args.out}")
    if misses:
        print(f"misses (no transcript; whisper-backfill set): {' '.join(misses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
