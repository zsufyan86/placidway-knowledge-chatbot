"""Append-only JSONL logs: every chat turn, every unanswered question (content
gaps) and every quote request (leads). Server logs never print the chat text
or the lead's details."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()


def _path(name: str):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    return config.LOG_DIR / f"{name}.jsonl"


def record(name: str, row: dict) -> None:
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
    with _lock, open(_path(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read(name: str, limit: int = 500) -> list[dict]:
    p = _path(name)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(l) for l in lines if l.strip()]


class RateLimiter:
    """Sliding window per client IP. In memory, which is fine for one instance."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
