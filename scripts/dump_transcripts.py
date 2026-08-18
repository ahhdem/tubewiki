#!/usr/bin/env python3
"""Fetch transcripts + metadata and cache them to JSON — INCREMENTALLY (writes after
every hit) and RESUMABLY (skips anything already in --out). So an interrupt or an IP
block never loses progress: just re-run and it picks up where it left off.

Run with the project venv's Python (not conda base) from a residential IP:

    backend/.venv/bin/python scripts/dump_transcripts.py --file urls.txt --out cache.json --delay 4
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
    try:
        r = httpx.get("https://www.youtube.com/oembed",
                      params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
                      timeout=15)
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
    ap.add_argument("--delay", type=float, default=4.0)
    ap.add_argument("--stop-after-misses", type=int, default=5,
                    help="bail after this many consecutive MISSes (likely an IP block)")
    args = ap.parse_args()

    out_path = Path(args.out)
    # Resume: keep whatever's already cached so we never re-fetch it.
    hits: list[dict] = []
    if out_path.exists():
        try:
            hits = json.loads(out_path.read_text() or "[]")
        except Exception:  # noqa: BLE001
            hits = []
    done_ids = {h["video_id"] for h in hits}

    ids = list(dict.fromkeys(i for i in (to_id(x) for x in open(args.file).read().splitlines()) if i))
    todo = [i for i in ids if i not in done_ids]
    print(f"{len(ids)} ids · {len(done_ids)} already cached · {len(todo)} to fetch")

    def save() -> None:
        out_path.write_text(json.dumps(hits, indent=2))

    misses, consec = [], 0
    try:
        for i, vid in enumerate(todo, 1):
            title, channel = oembed(vid)
            transcript = _fetch_via_api(vid)
            if transcript:
                hits.append({"video_id": vid, "title": title, "channel": channel, "transcript": transcript})
                save()  # <-- write NOW, not at the end
                consec = 0
                print(f"{i:2}/{len(todo)} {vid} OK {len(transcript)}c  {title[:46]}")
            else:
                misses.append(vid)
                consec += 1
                print(f"{i:2}/{len(todo)} {vid} MISS  {title[:46]}")
                if consec >= args.stop_after_misses:
                    print(f"\n{consec} MISSes in a row — likely IP-rate-limited. Stopping "
                          f"(progress saved). Wait a while, then re-run the SAME command to resume.")
                    break
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\ninterrupted — progress saved.")
    finally:
        save()

    print(f"\ncached {len(hits)} transcripts total -> {out_path}")
    if misses:
        print(f"misses this run (no captions or blocked): {' '.join(misses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
