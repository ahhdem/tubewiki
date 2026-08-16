"""State ledger — the fast index over *what* (spec §6).

Phase 1 uses a small JSON file; Phase 2 swaps this for Postgres (same interface). Git
stays the source of truth for *why*; this is just the fast pre-ingestion lookup and the
home of **eviction stickiness** — an evicted video must never be silently re-ingested on
re-watch (§5.1, a hard requirement). We record that foundation now even though the
eviction *UI* is Phase 2.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text() or "{}")

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True))

    def get(self, video_id: str) -> Optional[dict]:
        return self._data.get(video_id)

    def state(self, video_id: str) -> Optional[str]:
        rec = self._data.get(video_id)
        return rec["state"] if rec else None

    def is_evicted(self, video_id: str) -> bool:
        return self.state(video_id) == "evicted"

    def record(self, video_id: str, state: str, **extra) -> None:
        self._data[video_id] = {
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **extra,
        }
        self._flush()
