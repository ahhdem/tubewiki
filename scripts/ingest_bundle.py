#!/usr/bin/env python3
"""Ingest a `tubewiki-transcripts.json` bundle (from the extension's Export button) into
a running backend. Transcripts are already in the file, so this touches only the backend
— never YouTube.

Usage:
    python scripts/ingest_bundle.py tubewiki-transcripts.json --backend http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import urllib.request


def ingest(backend: str, item: dict) -> dict:
    body = json.dumps({k: item.get(k) for k in ("video_id", "title", "channel", "url", "transcript")}).encode()
    req = urllib.request.Request(backend.rstrip("/") + "/ingest", data=body,
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--backend", default="http://localhost:8000")
    args = ap.parse_args()

    items = json.load(open(args.bundle))
    have = sum(1 for it in items if it.get("transcript"))
    print(f"{len(items)} videos, {have} with transcripts")
    for it in items:
        if not it.get("transcript"):
            print(f"{it.get('video_id')}: skip (no transcript in bundle)")
            continue
        try:
            res = ingest(args.backend, it)
            extra = f" → {res['concept_title']} (+{res['claims_added']})" if res.get("concept_title") else ""
            print(f"{it['video_id']}: {res['status']}{extra}")
        except Exception as e:  # noqa: BLE001
            print(f"{it.get('video_id')}: ERROR {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
