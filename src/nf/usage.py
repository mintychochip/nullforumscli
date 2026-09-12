"""Append-only request ledger and windowed rollups.

This reports the *client's own consumption* -- requests made, cache hit rate,
bytes, and enforced spacing -- not forum-account statistics. Records hold a
coarse path class and never a full URL or any cookie material.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

MAX_LEDGER_BYTES = 10 * 1024 * 1024
_WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, state_dir: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(state_dir) / "ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utcnow

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_LEDGER_BYTES:
                self.path.replace(self.path.with_suffix(".jsonl.1"))
        except OSError:
            pass

    def record(self, path_class: str, status: int, nbytes: int, cache: str,
               attempt: int = 1) -> None:
        self._rotate_if_needed()
        entry = {
            "ts": self._clock().astimezone(timezone.utc).isoformat(timespec="seconds"),
            "pathClass": path_class,
            "status": int(status),
            "bytes": int(nbytes),
            "cache": cache,
            "attempt": int(attempt),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def record_refusal(self, path: str) -> None:
        """Record a path refused by robots. Not a request; never counted as one."""
        self._rotate_if_needed()
        entry = {
            "ts": self._clock().astimezone(timezone.utc).isoformat(timespec="seconds"),
            "pathClass": "refused",
            "refusal": path,
            "status": 0,
            "bytes": 0,
            "cache": "none",
            "attempt": 0,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _in_window(self, row: dict, window: str, now: datetime) -> bool:
        if window == "all":
            return True
        delta = _WINDOWS.get(window)
        if delta is None:
            return True
        try:
            ts = datetime.fromisoformat(row["ts"])
        except (KeyError, ValueError):
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return ts >= now - delta

    def rollup(self, window: str, limit_ms: int) -> dict:
        all_rows = self.entries()
        if window == "all":
            rows = all_rows
        else:
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            rows = [r for r in all_rows if self._in_window(r, window, now)]

        refused = [r for r in rows if r.get("refusal")]
        requests = [r for r in rows if not r.get("refusal")]
        by_class: dict[str, int] = {}
        hits = misses = 0
        total_bytes = 0
        block = login_wall = 0
        for row in requests:
            by_class[row.get("pathClass", "other")] = (
                by_class.get(row.get("pathClass", "other"), 0) + 1)
            cache = row.get("cache")
            if cache == "hit":
                hits += 1
            elif cache == "miss":
                misses += 1
            total_bytes += int(row.get("bytes", 0))
            status = int(row.get("status", 0))
            if status == 403:
                block += 1
            elif status == 401:
                login_wall += 1
        last = rows[-1]["ts"] if rows else None
        return {
            "window": window,
            "requests": {"total": len(requests), "byClass": dict(sorted(by_class.items()))},
            "cache": {"hits": hits, "misses": misses},
            "bytes": total_bytes,
            "rate": {"limitMs": int(limit_ms),
                     "budgetUsedMs": max(0, len(requests) - 1) * int(limit_ms)},
            "refusals": {"robots": len(refused), "block": block, "loginWall": login_wall},
            "lastRequestAt": last,
        }
