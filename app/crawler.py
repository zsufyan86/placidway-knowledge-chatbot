"""Polite fetcher for the shared URLs.

- Reads robots.txt once and skips any URL it disallows.
- Waits between requests: the larger of robots.txt Crawl-delay and our own
  minimum (PlacidWay sets Crawl-delay: 10, so a full crawl takes ~70 seconds).
- Sends a clear User-Agent.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from . import config

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    error: str = ""


def _load_robots(base: str, client: httpx.Client) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    robots_url = f"{base}/robots.txt"
    try:
        resp = client.get(robots_url)
        if resp.status_code >= 400:
            # No robots.txt (or not readable) means no restrictions.
            rp.parse([])
        else:
            rp.parse(resp.text.splitlines())
    except httpx.HTTPError as exc:
        log.warning("robots.txt not reachable (%s); continuing with defaults", exc)
        rp.parse([])
    return rp


def fetch_all(urls: list[str], sleep=time.sleep, transport: httpx.BaseTransport | None = None) -> list[FetchResult]:
    """Fetch every URL in order. Never raises for a single bad page."""
    results: list[FetchResult] = []
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "en"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True, transport=transport) as client:
        robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        for i, url in enumerate(urls):
            parts = urlparse(url)
            base = f"{parts.scheme}://{parts.netloc}"
            if base not in robots_cache:
                robots_cache[base] = _load_robots(base, client)
            rp = robots_cache[base]

            if not rp.can_fetch(config.USER_AGENT, url):
                log.warning("robots.txt disallows %s, skipping", url)
                results.append(FetchResult(url, 0, "", "disallowed by robots.txt"))
                continue

            delay = max(rp.crawl_delay(config.USER_AGENT) or 0, config.MIN_DELAY_SECONDS)
            if i > 0:
                sleep(delay)

            try:
                resp = client.get(url)
                if resp.status_code != 200:
                    results.append(FetchResult(url, resp.status_code, "", f"HTTP {resp.status_code}"))
                else:
                    results.append(FetchResult(url, 200, resp.text))
                log.info("fetched %s -> %s", url, resp.status_code)
            except httpx.HTTPError as exc:
                results.append(FetchResult(url, 0, "", str(exc)))
                log.warning("failed %s: %s", url, exc)
    return results
