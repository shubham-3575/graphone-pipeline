"""Multi-tier LLM extraction engine.

Tier chain (built from config, tiers without an API key are skipped):
    Gemini (main) -> Gemini (lite) -> Groq (large) -> Groq (small)   [any OpenAI-compatible provider can be added as a Tier]

Handles:
  * 429  : honour Retry-After, else exponential backoff + full jitter; after repeated 429s the tier gets a
           cooldown so concurrent workers skip it immediately instead of piling on.
  * 413  : (and 400 "context too long") -> halve the input budget with semantic truncation and retry the same tier.
  * 5xx / network : bounded retries with backoff, then next tier.
  * bad JSON      : one re-ask, then next tier.
  * 401/403/404   : tier permanently disabled for this run (bad key / bad model name).
  * Grounding     : every extracted value must appear in the source text, else nulled / record rejected.
All tiers failing raises ExtractionFailed -> caller puts the item in the dead-letter queue (never fabricate).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from src import config
from src.grounding import ground_fields
from src.http_client import backoff_delay, parse_retry_after
from src.text_chunking import smart_truncate

log = logging.getLogger(__name__)


# ---- errors -----------------------------------------------------------------
class LLMError(Exception): ...
class RateLimited(LLMError):
    def __init__(self, retry_after: Optional[float] = None, msg: str = "429"):
        super().__init__(msg); self.retry_after = retry_after
class PayloadTooLarge(LLMError): ...
class ProviderError(LLMError): ...            # retryable 5xx / network
class TierDisabled(LLMError): ...             # auth / model-not-found: don't retry this tier
class BadOutput(LLMError): ...
class ExtractionFailed(Exception): ...


@dataclass
class Tier:
    name: str
    provider: str            # "gemini" | "openai"
    model: str
    api_key: str
    base_url: str = ""
    max_input_chars: int = 24000
    disabled: bool = False
    cooldown_until: float = 0.0
    rpm: float = 0.0            # max requests/minute for this tier (0 = unlimited); spaces calls to avoid 429 bursts
    next_slot: float = 0.0


@dataclass
class ExtractionResult:
    data: dict
    tier: str
    truncated: bool
    ungrounded: list[str] = field(default_factory=list)


def build_default_tiers() -> list[Tier]:
    env = os.getenv
    specs = [
        Tier("gemini-main", "gemini", env("GEMINI_MODEL_MAIN", "gemini-2.5-flash"), config.GEMINI_API_KEY,
             "https://generativelanguage.googleapis.com/v1beta", 60000, rpm=float(env("GEMINI_RPM", "8"))),
        Tier("gemini-lite", "gemini", env("GEMINI_MODEL_LITE", "gemini-2.5-flash-lite"), config.GEMINI_API_KEY,
             "https://generativelanguage.googleapis.com/v1beta", 60000, rpm=float(env("GEMINI_LITE_RPM", "12"))),
        Tier("groq-large", "openai", env("GROQ_MODEL_LARGE", "openai/gpt-oss-120b"), config.GROQ_API_KEY,
             "https://api.groq.com/openai/v1", 16000, rpm=float(env("GROQ_RPM", "10"))),  # small free TPM -> small payloads
        Tier("groq-small", "openai", env("GROQ_MODEL_SMALL", "openai/gpt-oss-20b"), config.GROQ_API_KEY,
             "https://api.groq.com/openai/v1", 12000, rpm=float(env("GROQ_RPM", "10"))),
    ]
    active = []
    for t in specs:
        if t.api_key:
            active.append(t)
        else:
            log.info("LLM tier %s skipped: no API key configured", t.name)
    return active


SYSTEM_PROMPT = (
    "You are a strict information-extraction engine. Extract ONLY facts that are explicitly stated in the "
    "provided text. Never guess, infer, or use outside knowledge. If a field is not stated, use null. "
    "Return one JSON object and nothing else."
)


def build_user_prompt(instruction: str, schema_hint: str, text: str) -> str:
    return f"{instruction}\n\nReturn JSON with these fields:\n{schema_hint}\n\nTEXT:\n<<<\n{text}\n>>>"


def parse_json_loose(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise BadOutput("no JSON object in output")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise BadOutput(str(e))
    if not isinstance(obj, dict):
        raise BadOutput("JSON output is not an object")
    return obj


def _classify(status: int, headers: Any, body_text: str) -> None:
    ra = parse_retry_after(headers.get("Retry-After") if headers else None)
    low = body_text.lower()
    if status == 429:
        raise RateLimited(ra, body_text[:200])
    if status == 413 or (status == 400 and any(k in low for k in
                         ("too large", "too long", "context length", "context_length", "maximum context",
                          "token limit", "exceeds the maximum", "input token"))):
        raise PayloadTooLarge(body_text[:200])
    if status in (401, 403, 404):
        raise TierDisabled(f"{status}: {body_text[:200]}")
    if status >= 500 or status in (408, 425):
        raise ProviderError(f"{status}: {body_text[:200]}")
    if status >= 400:
        raise ProviderError(f"{status}: {body_text[:200]}")


async def call_tier(session: aiohttp.ClientSession, tier: Tier, system: str, user: str) -> str:
    """One raw provider call. Returns model text or raises a typed LLMError."""
    if tier.provider == "gemini":
        url = f"{tier.base_url}/models/{tier.model}:generateContent"
        headers = {"x-goog-api-key": tier.api_key, "Content-Type": "application/json"}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    else:
        url = f"{tier.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {tier.api_key}", "Content-Type": "application/json"}
        body = {"model": tier.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        async with session.post(url, headers=headers, json=body,
                                timeout=aiohttp.ClientTimeout(total=90)) as r:
            text = await r.text()
            if r.status != 200:
                _classify(r.status, r.headers, text)
            data = json.loads(text)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ProviderError(f"network: {e}")
    except json.JSONDecodeError:
        raise ProviderError("provider returned non-JSON envelope")
    try:
        if tier.provider == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise BadOutput(f"unexpected response shape (blocked/empty?): {str(data)[:200]}")


Caller = Callable[[Tier, str, str], Awaitable[str]]


class ResultCache:
    """Persistent answer cache: an identical (instruction, schema, text) never costs a second LLM call."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    @staticmethod
    def key(instruction: str, schema_hint: str, fields: list[str], text: str) -> str:
        return hashlib.sha256("\x00".join([instruction, schema_hint, ",".join(fields), text]).encode()).hexdigest()

    def get(self, k: str):
        row = self.db.execute("SELECT v FROM c WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, k: str, v: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO c(k, v) VALUES (?, ?)", (k, json.dumps(v)))


class LLMOrchestrator:
    def __init__(self, tiers: Optional[list[Tier]] = None, caller: Optional[Caller] = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 max_429_retries: int = 3, max_5xx_retries: int = 2, concurrency: int = 4,
                 cache_path: Optional[str] = None):
        self.cache = ResultCache(cache_path) if cache_path else None
        self.tiers = tiers if tiers is not None else build_default_tiers()
        self._caller = caller
        self._session: Optional[aiohttp.ClientSession] = None
        self._sleep = sleep
        self.max_429 = max_429_retries
        self.max_5xx = max_5xx_retries
        self._sem = asyncio.Semaphore(concurrency)
        self.stats: dict[str, int] = {}

    async def __aenter__(self):
        if self._caller is None:
            self._session = aiohttp.ClientSession()
            self._caller = lambda t, s, u: call_tier(self._session, t, s, u)
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    def _bump(self, key: str):
        self.stats[key] = self.stats.get(key, 0) + 1

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    async def extract(self, *, text: str, instruction: str, schema_hint: str,
                      grounded_fields: list[str], required: Optional[list[str]] = None) -> ExtractionResult:
        ck = ResultCache.key(instruction, schema_hint, grounded_fields, text) if self.cache else None
        if ck:
            hit = self.cache.get(ck)
            if hit:
                self._bump("cache_hit")
                return ExtractionResult(hit["data"], "cache", hit["truncated"], hit["ungrounded"])
        if not self.tiers:
            raise ExtractionFailed("no LLM tiers configured (no API keys)")
        errors: list[str] = []
        async with self._sem:
            for tier in self.tiers:
                if tier.disabled:
                    continue
                if tier.cooldown_until > self._now():
                    errors.append(f"{tier.name}: cooling down")
                    continue
                try:
                    res = await self._run_tier(tier, text, instruction, schema_hint)
                except LLMError as e:
                    errors.append(f"{tier.name}: {type(e).__name__} {e}")
                    self._bump(f"fail:{tier.name}")
                    continue
                data, truncated = res
                try:
                    clean, bad = ground_fields(data, text, grounded_fields, required)
                except ValueError as e:      # required field not supported by source -> try next tier once
                    errors.append(f"{tier.name}: ungrounded {e}")
                    self._bump(f"ungrounded:{tier.name}")
                    continue
                self._bump(f"ok:{tier.name}")
                if bad:
                    log.warning("nulled ungrounded fields %s from %s", bad, tier.name)
                if ck:
                    self.cache.put(ck, {"data": clean, "truncated": truncated, "ungrounded": bad})
                return ExtractionResult(clean, tier.name, truncated, bad)
        raise ExtractionFailed("; ".join(errors))

    async def _throttle(self, tier: Tier) -> None:
        if tier.rpm <= 0:
            return
        now = self._now()
        slot = max(now, tier.next_slot)
        tier.next_slot = slot + 60.0 / tier.rpm
        if slot > now:
            await self._sleep(slot - now)

    async def _run_tier(self, tier: Tier, text: str, instruction: str, schema_hint: str):
        budget = tier.max_input_chars
        rate_tries = err_tries = bad_tries = shrinks = 0
        while True:
            payload = smart_truncate(text, budget)
            user = build_user_prompt(instruction, schema_hint, payload)
            try:
                await self._throttle(tier)
                raw = await self._caller(tier, SYSTEM_PROMPT, user)
                return parse_json_loose(raw), len(payload) < len(text)
            except PayloadTooLarge:                          # 413 -> shrink and retry same tier
                shrinks += 1
                self._bump("413")
                if shrinks > 3 or budget < 1500:
                    raise
                budget //= 2
                log.warning("413 on %s: shrinking budget to %d chars", tier.name, budget)
            except RateLimited as e:                         # 429 -> backoff + jitter
                self._bump("429")
                rate_tries += 1
                delay = min(e.retry_after, config.BACKOFF_CAP_S) if e.retry_after is not None \
                    else backoff_delay(rate_tries)
                if rate_tries > self.max_429:
                    tier.cooldown_until = self._now() + max(delay, 30.0)
                    raise
                log.warning("429 on %s: retry %d in %.1fs", tier.name, rate_tries, delay)
                await self._sleep(delay)
            except ProviderError:
                err_tries += 1
                if err_tries > self.max_5xx:
                    raise
                await self._sleep(backoff_delay(err_tries))
            except BadOutput:
                bad_tries += 1
                if bad_tries > 1:
                    raise
            except TierDisabled:
                tier.disabled = True
                log.error("tier %s disabled for this run (auth/model error)", tier.name)
                raise
