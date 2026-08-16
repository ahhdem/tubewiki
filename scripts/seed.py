#!/usr/bin/env python3
"""Bulk-seed the wiki from a list of YouTube URLs/IDs (spec §5 bulk-seeding exception:
ingest everything, assume good). Handy for the P1.11 value test.

Usage:
    python scripts/seed.py https://youtu.be/ID1 https://www.youtube.com/watch?v=ID2 ...
    python scripts/seed.py --backend http://localhost:8000 --file ids.txt

The backend fetches the transcript (extension not involved), so run it where the backend
can reach YouTube from a residential IP.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import json

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def to_id(s: str) -> str | None:
    s = s.strip()
    if not s or s.startswith("#"):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = _ID_RE.search(s)
    return m.group(1) if m else None


def ingest(backend: str, video_id: str) -> dict:
    body = json.dumps({"video_id": video_id, "title": video_id}).encode()
    req = urllib.request.Request(
        backend.rstrip("/") + "/ingest", data=body,
        headers={"content-type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--backend", default="http://localhost:8000")
    ap.add_argument("--file", help="file with one URL/ID per line")
    args = ap.parse_args()

    raw = list(args.urls)
    if args.file:
        raw += open(args.file).read().splitlines()
    ids = [i for i in (to_id(x) for x in raw) if i]
    if not ids:
        print("no valid video ids", file=sys.stderr)
        return 1

    for vid in ids:
        try:
            res = ingest(args.backend, vid)
            print(f"{vid}: {res['status']}"
                  + (f" → {res['concept_title']} (+{res['claims_added']})"
                     if res.get("concept_title") else ""))
        except Exception as e:  # noqa: BLE001
            print(f"{vid}: ERROR {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
