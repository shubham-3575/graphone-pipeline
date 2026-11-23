"""Phase II - AI news sources. 5 sources, only articles published in the last 24h, full text extracted.

Date logic (in priority order): feed timestamp -> date found inside the article page (JSON-LD/meta/<time>) ->
first-seen heuristic (item is only accepted once, marked date_confidence='heuristic').
Full text: fetch the article and extract the main content (trafilatura). If the site blocks us (403/anti-bot) we fall
back to the text the feed itself provides - never to anything generated.

Run:  python -m src.pipelines.news --reset
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import html as htmllib
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import feedparser
import trafilatura

from src.dates import extract_published_date, is_fresh
from src.freshness import SeenStore
from src.http_client import AsyncFetcher
from src.io_utils import append_jsonl
from src.logging_setup import setup_logging
from src.schemas import NewsContent, NewsRecord, Source

log = logging.getLogger("news")

# Final five sources, chosen from measured 24h volume (see README, "source selection"). Evaluated and dropped:
# MIT Technology Review / Ars Technica / Google AI Blog (no fresh items in window), Hugging Face Blog (research posts,
# none fresh), WIRED (1 item), VentureBeat (HTTP 429), AI News (HTTP 403).
FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("The Guardian", "https://www.theguardian.com/technology/artificialintelligenceai/rss"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/"),
]
SOCIAL = ("twitter.com", "x.com", "youtube.com", "youtu.be", "github.com", "reddit.com", "linkedin.com")
HN_URL = ("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=100"
          "&query=AI&numericFilters=points%3E%3D5")


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_feed(source: str, xml: str) -> list[dict]:
    out = []
    for e in feedparser.parse(xml).entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        date = datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None
        body = ""
        if e.get("content"):
            body = _strip_html(e["content"][0].get("value", ""))
        body = body or _strip_html(e.get("summary", ""))
        if e.get("link") and e.get("title"):
            out.append({"source": source, "title": _strip_html(e["title"]), "url": e["link"],
                        "date": date, "feed_text": body, "page_url": e["link"]})
    return out


def parse_hn(data: dict) -> list[dict]:
    out = []
    for h in (data or {}).get("hits", []):
        if not (h.get("title") and h.get("url") and h.get("created_at_i")):
            continue          # Ask HN / text posts have no external article
        host = urlparse(h["url"]).netloc.lower().split(":")[0].removeprefix("www.")
        if any(host == d or host.endswith("." + d) for d in SOCIAL):
            continue          # social posts / repos are not news articles
        out.append({"source": "Hacker News", "title": h["title"], "url": h["url"],
                    "date": datetime.fromtimestamp(h["created_at_i"], timezone.utc), "feed_text": "",
                    "page_url": h["url"], "discussion": f"https://news.ycombinator.com/item?id={h.get('objectID')}"})
    return out


MIN_TEXT = 200


def has_full_text(text: str) -> bool:
    """Task requires full-text content: never save an article we could not read."""
    return len((text or "").strip()) >= MIN_TEXT


def extract_text(html: str) -> str:
    return trafilatura.extract(html, include_comments=False, include_tables=False) or ""


async def enrich(f: AsyncFetcher, item: dict, now: datetime):
    """Fetch the article; return (full_text, page_date, how)."""
    try:
        page = await f.get_text(item["page_url"], max_retries=2)
        text = await asyncio.to_thread(extract_text, page)
        pdate, _, _ = extract_published_date(page, now)
        if len(text) >= 200:
            return text, pdate, "page"
    except Exception as e:
        log.info("article fetch blocked/failed (%s): %s", item["page_url"][:80], e)
    return item["feed_text"], None, "feed"


async def run(out_path="output/news.jsonl", reset=False, with_hn=False) -> int:
    if reset:
        for p in (out_path, "data/seen_news.sqlite"):
            if os.path.exists(p):
                os.remove(p)
    store, now = SeenStore("data/seen_news.sqlite"), datetime.now(timezone.utc)
    items: list[dict] = []
    async with AsyncFetcher() as f:
        async def feed(name, url):
            try:
                return parse_feed(name, await f.get_text(url))
            except Exception as e:
                log.error("feed %s failed: %s", name, e); return []

        async def hn():
            try:
                return parse_hn(await f.get_json(HN_URL, timeout=aiohttp.ClientTimeout(total=90)))
            except Exception as e:
                log.error("HN failed: %s", e); return []

        for lst in await asyncio.gather(*(feed(n, u) for n, u in FEEDS), *([hn()] if with_hn else [])):
            items.extend(lst)
        log.info("listed %d items across sources", len(items))
        fresh, undated = [], []
        per = {}
        for it in items:
            if it["date"] is None:
                undated.append(it)
            elif is_fresh(it["date"], now):
                fresh.append(it)
        for it in items:
            c = per.setdefault(it["source"], [0, 0])
            c[0] += 1
            c[1] += 1 if (it["date"] and is_fresh(it["date"], now)) else 0
        log.info("per-source (listed, fresh<24h): %s", {k: tuple(v) for k, v in sorted(per.items())})
        enriched = await asyncio.gather(*(enrich(f, it, now) for it in fresh + undated))
    saved, stats = 0, {"stale": len(items) - len(fresh) - len(undated)}
    for it, (text, pdate, how) in zip(fresh + undated, enriched):
        date, conf = it["date"], "explicit"
        if date is None:
            if pdate and is_fresh(pdate, now):
                date, conf = pdate, "explicit"
            elif pdate:
                stats["stale"] += 1; continue
            else:
                date, conf = now, "heuristic"           # no date anywhere: accept once, flagged
        if not has_full_text(text):
            stats["no-text"] = stats.get("no-text", 0) + 1; continue     # blocked/paywalled: not claimed, can retry
        if not store.claim(it["url"], it["title"]):
            stats["duplicate"] = stats.get("duplicate", 0) + 1; continue
        append_jsonl(out_path, NewsRecord(
            source=Source(name=it["source"], url=it["url"]),
            content=NewsContent(title=it["title"], published_date=date, date_confidence=conf,
                                full_text=text, article_url=it["url"])))
        saved += 1; stats[how] = stats.get(how, 0) + 1
        stats["src:" + it["source"]] = stats.get("src:" + it["source"], 0) + 1
    log.info("news saved=%d breakdown=%s", saved, stats)
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--with-hn", action="store_true", help="also include Hacker News AI stories (aggregator)")
    a = ap.parse_args()
    setup_logging()
    print(f"fresh news saved: {asyncio.run(run(reset=a.reset, with_hn=a.with_hn))}")
