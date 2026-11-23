"""Publication-date extraction + normalization (Phase II).

Priority order for a page:
  1. JSON-LD  datePublished / dateCreated
  2. <meta> article:published_time / og:published_time / pubdate / date / DC.date
  3. <time datetime="..."> and itemprop=datePublished
  4. Visible relative text ("2 hours ago", "yesterday", "Posted 3d ago")
  5. None  -> caller applies the first-seen heuristic (see freshness.py)
Everything is returned as timezone-aware UTC datetimes with a confidence label.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import dateparser
from bs4 import BeautifulSoup

UTC = timezone.utc
_REL = re.compile(r"(\d+|an?|one)\s*(second|sec|minute|min|hour|hr|h|day|d|week|wk|w|month|mo)s?\s*(?:ago)?", re.I)
_UNIT = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "h": 3600,
         "day": 86400, "d": 86400, "week": 604800, "wk": 604800, "w": 604800, "month": 2592000, "mo": 2592000}


def _aware(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_relative(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """'2 hours ago', 'an hour ago', '3d ago', 'just now', 'yesterday', 'today'."""
    now = _aware(now or datetime.now(UTC))
    t = text.strip().lower()
    if re.search(r"\b(just now|moments? ago|a moment ago)\b", t):
        return now
    if re.search(r"\byesterday\b", t):
        return now - timedelta(days=1)
    if re.search(r"\btoday\b", t) and not re.search(r"\d", t):
        return now
    m = _REL.search(t)
    if m and ("ago" in t or re.fullmatch(r"\s*(posted\s*)?\d+\s*[a-z]+\s*", t)):
        n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
        return now - timedelta(seconds=n * _UNIT[m.group(2).lower()])
    return None


def parse_date_string(text: str, now: Optional[datetime] = None) -> tuple[Optional[datetime], str]:
    """Returns (utc datetime | None, confidence: 'explicit' | 'relative')."""
    if not text or not text.strip():
        return None, "explicit"
    s = text.strip()
    if re.fullmatch(r"\d{10}(\.\d+)?", s):              # unix seconds
        return datetime.fromtimestamp(float(s), UTC), "explicit"
    if re.fullmatch(r"\d{13}", s):                      # unix millis
        return datetime.fromtimestamp(int(s) / 1000, UTC), "explicit"
    rel = parse_relative(s, now)
    if rel:
        return rel, "relative"
    d = dateparser.parse(s, languages=["en"], settings={
        "TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "past",
        "RELATIVE_BASE": (now or datetime.now(UTC)).replace(tzinfo=None)})
    return (_aware(d), "explicit") if d else (None, "explicit")


_META_KEYS = ["article:published_time", "og:published_time", "og:article:published_time", "publishdate",
              "pubdate", "date", "dc.date", "dc.date.issued", "parsely-pub-date", "sailthru.date",
              "article:modified_time_never_used"]


def _jsonld_dates(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                for k in ("datePublished", "dateCreated", "uploadDate"):
                    if isinstance(o.get(k), str):
                        yield o[k]
                stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
            elif isinstance(o, list):
                stack.extend(o)


def extract_published_date(html: str, now: Optional[datetime] = None,
                           ) -> tuple[Optional[datetime], str, str]:
    """Returns (utc datetime | None, confidence, method)."""
    soup = BeautifulSoup(html, "html.parser")
    for raw in _jsonld_dates(soup):
        d, c = parse_date_string(raw, now)
        if d:
            return d, c, "json-ld"
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        if key in _META_KEYS and m.get("content"):
            d, c = parse_date_string(m["content"], now)
            if d:
                return d, c, f"meta:{key}"
    for t in soup.find_all("time"):
        raw = t.get("datetime") or t.get_text(" ", strip=True)
        d, c = parse_date_string(raw, now)
        if d:
            return d, c, "time-tag"
    for el in soup.find_all(attrs={"itemprop": "datePublished"}):
        d, c = parse_date_string(el.get("content") or el.get_text(" ", strip=True), now)
        if d:
            return d, c, "itemprop"
    text = soup.get_text(" ", strip=True)[:4000]
    m = re.search(r"(?:posted|published|updated)?\s*(\d+|an?)\s*(?:seconds?|minutes?|mins?|hours?|hrs?|days?|d|h)\s+ago", text, re.I)
    if m:
        d = parse_relative(m.group(0), now)
        if d:
            return d, "relative", "visible-relative"
    return None, "heuristic", "none"


def is_fresh(published: datetime, now: Optional[datetime] = None, hours: int = 24,
             future_tolerance_min: int = 10) -> bool:
    now = _aware(now or datetime.now(UTC))
    p = _aware(published)
    return (now - timedelta(hours=hours)) <= p <= (now + timedelta(minutes=future_tolerance_min))
