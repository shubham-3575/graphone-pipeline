"""Async HTTP client: global + per-host concurrency limits, retries with exponential backoff + full jitter,
Retry-After support for 429/503. Every scraper in the project goes through this one client."""
from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from src import config

log = logging.getLogger(__name__)
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, url: str, status: Optional[int], msg: str = "", retry_after: Optional[float] = None):
        super().__init__(f"{url} -> {status} {msg}")
        self.url, self.status, self.retry_after = url, status, retry_after


def backoff_delay(attempt: int, base: float = None, cap: float = None, rng=random.random) -> float:
    """Full jitter: uniform(0, min(cap, base * 2**attempt))."""
    base = config.BACKOFF_BASE_S if base is None else base
    cap = config.BACKOFF_CAP_S if cap is None else cap
    return rng() * min(cap, base * (2 ** attempt))


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form: fall back to computed backoff


class AsyncFetcher:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._own_session = session is None
        self._global = asyncio.Semaphore(config.GLOBAL_CONCURRENCY)
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(config.PER_HOST_CONCURRENCY)
        )

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": config.USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_S),
            )
        return self

    async def __aexit__(self, *exc):
        if self._own_session and self._session:
            await self._session.close()

    async def request(self, method: str, url: str, *, as_json: bool = False,
                      max_retries: Optional[int] = None, extra_retry: frozenset = frozenset(), **kw: Any):
        retryable = RETRYABLE | set(extra_retry)
        retries = config.MAX_RETRIES if max_retries is None else max_retries
        host = urlparse(url).netloc
        last: Optional[Exception] = None
        retry_after: Optional[float] = None
        for attempt in range(retries + 1):
            try:
                async with self._global, self._hosts[host]:
                    async with self._session.request(method, url, **kw) as r:
                        if r.status in retryable:
                            ra = parse_retry_after(r.headers.get("Retry-After"))
                            raise FetchError(url, r.status, "retryable", retry_after=ra)
                        if r.status >= 400:
                            raise FetchError(url, r.status, "non-retryable")  # 4xx: don't hammer
                        return await (r.json(content_type=None) if as_json else r.text())
            except FetchError as e:
                if e.status not in retryable:
                    raise
                last, retry_after = e, e.retry_after
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last, retry_after = e, None
            # Server-provided Retry-After wins over our own computed backoff (capped for safety)
            if attempt >= retries:
                break
            delay = min(retry_after, config.BACKOFF_CAP_S) if retry_after is not None else backoff_delay(attempt)
            log.warning("retry %d/%d for %s in %.1fs (%s: %s)", attempt + 1, retries, url[:90], delay,
                        type(last).__name__, last)
            await asyncio.sleep(delay)
        raise FetchError(url, getattr(last, "status", None), f"gave up: {last}")

    async def get_text(self, url: str, **kw) -> str:
        return await self.request("GET", url, **kw)

    async def get_json(self, url: str, **kw):
        return await self.request("GET", url, as_json=True, **kw)
