"""Knowledge base: build it from the shared URLs, save it, load it, refresh it.

The knowledge base is a single JSON file (data/kb.json). A snapshot is
committed to the repo so the bot works even if a fresh crawl fails. A refresh
re-crawls the pages, and only replaces the live knowledge base if every page
came back with content, so a broken crawl can never empty the bot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone

from . import config
from .chunker import chunk_page
from .crawler import fetch_all
from .extract import extract_page
from .retrieve import Retriever
from .sources import SOURCES

log = logging.getLogger(__name__)


def build_kb(fetch=fetch_all) -> dict:
    urls = [s["url"] for s in SOURCES]
    results = fetch(urls)
    pages, chunks, errors = [], [], []
    for idx, (src, res) in enumerate(zip(SOURCES, results)):
        if not res.html:
            errors.append({"url": src["url"], "error": res.error or f"HTTP {res.status}"})
            continue
        page = extract_page(res.html, src["url"], src["type"])
        page_chunks = chunk_page(page, idx)
        chunks.extend(page_chunks)
        text = "\n".join(c["text"] for c in page_chunks)
        pages.append(
            {
                "url": src["url"],
                "type": src["type"],
                "title": page.title,
                "chunks": len(page_chunks),
                "chars": len(text),
                "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
            }
        )
    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
        "errors": errors,
        "chunks": chunks,
    }


def save_kb(kb: dict, path=None) -> None:
    path = path or config.KB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(kb, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # atomic swap


def load_kb(path=None) -> dict | None:
    path = path or config.KB_PATH
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def diff_pages(old: dict | None, new: dict) -> list[str]:
    before = {p["url"]: p["sha256"] for p in (old or {}).get("pages", [])}
    return [p["url"] for p in new["pages"] if before.get(p["url"]) != p["sha256"]]


class KnowledgeBase:
    """Holds the live knowledge base and swaps it safely on refresh."""

    def __init__(self):
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self.kb: dict | None = None
        self.retriever = Retriever([])
        self.refreshing = False
        self.last_refresh: dict = {}

    def set(self, kb: dict) -> None:
        retriever = Retriever(kb["chunks"])
        with self._lock:
            self.kb = kb
            self.retriever = retriever

    def load_or_build(self, retries: int = 5, wait: float = 120) -> None:
        kb = load_kb()
        if kb and kb.get("chunks"):
            self.set(kb)
            log.info("loaded %d chunks from %s", len(kb["chunks"]), config.KB_PATH)
            return
        for attempt in range(retries + 1):
            log.info("no knowledge base yet, crawling (attempt %d)", attempt + 1)
            self.refresh()
            if self.ready:
                return
            time.sleep(wait)

    def refresh(self, fetch=fetch_all) -> dict:
        # Non-blocking lock: two refresh requests at once can't both crawl.
        if not self._refresh_lock.acquire(blocking=False):
            return {"status": "already running"}
        self.refreshing = True
        try:
            new = build_kb(fetch)
            if new["errors"] or len(new["pages"]) < len(SOURCES):
                self.last_refresh = {
                    "status": "kept previous knowledge base",
                    "reason": new["errors"],
                    "at": new["built_at"],
                }
                if self.kb is None and new["chunks"]:
                    self.set(new)  # better partial than nothing on first boot
                return self.last_refresh
            changed = diff_pages(self.kb, new)
            save_kb(new)
            self.set(new)
            self.last_refresh = {
                "status": "updated",
                "at": new["built_at"],
                "chunks": len(new["chunks"]),
                "changed_pages": changed,
            }
            return self.last_refresh
        finally:
            self.refreshing = False
            self._refresh_lock.release()

    @property
    def ready(self) -> bool:
        return bool(self.kb and self.kb.get("chunks"))

    def stats(self) -> dict:
        kb = self.kb or {}
        return {
            "ready": self.ready,
            "built_at": kb.get("built_at"),
            "pages": kb.get("pages", []),
            "chunks": len(kb.get("chunks", [])),
            "refreshing": self.refreshing,
            "last_refresh": self.last_refresh,
        }
