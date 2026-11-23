"""Phase I - Products vertical.

Discovery : Hacker News "Show HN" launches (Algolia API), weekly windows going back in time (each window < 1000 hits).
Enrichment: fetch the product's OWN site (+ its pricing page if linked) and decide the pricing model with strict
            keyword rules; the quote that triggered the rule is stored as evidence. Unclear cases go to the LLM
            orchestrator, whose answer is accepted only if its quoted evidence literally appears on the page.
Startup   : YC domain match -> known-name match -> the product's own brand (flagged in `startupMatch`).
Nothing is guessed: pricingModel stays empty when the page doesn't support a decision.

Run:  python -m src.pipelines.products --target 1100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from src.common import load_resolver
from src.entity_resolver import write_mapping_log
from src.http_client import AsyncFetcher
from src.io_utils import append_jsonl, read_jsonl
from src.logging_setup import setup_logging
from src.schemas import PricingModel, ProductContent, ProductRecord, Source

log = logging.getLogger("products")

HN_API = "https://hn.algolia.com/api/v1/search_by_date"
YC_URL = "https://yc-oss.github.io/api/companies/all.json"
SKIP_HOSTS = {"github.com", "gitlab.com", "medium.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
              "substack.com", "reddit.com", "arxiv.org", "huggingface.co", "docs.google.com", "notion.site",
              "linkedin.com", "facebook.com", "wikipedia.org", "news.ycombinator.com", "apps.apple.com",
              "play.google.com", "chromewebstore.google.com", "pypi.org", "npmjs.com", "crates.io",
              "producthunt.com", "vimeo.com", "t.me", "bsky.app", "dev.to", "hashnode.dev", "github.io"}
TITLE_RE = re.compile(r"^\s*show hn:\s*(?P<rest>.+)$", re.I)
SEP_RE = re.compile(r"\s[–—-]\s|:\s|\s\|\s")
MULTI_TLD = {"co.uk", "com.au", "co.in", "co.jp", "com.br", "co.nz"}


def registered_domain(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().split(":")[0].removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def parse_show_hn(hit: dict) -> dict | None:
    m = TITLE_RE.match(hit.get("title") or "")
    url = hit.get("url") or ""
    if not m or not url.startswith("http"):
        return None
    dom = registered_domain(url)
    if dom in SKIP_HOSTS or any(dom.endswith("." + h) or dom == h for h in SKIP_HOSTS):
        return None
    sep = SEP_RE.search(m.group("rest"))
    if not sep:
        return None
    name = m.group("rest")[:sep.start()].strip(" \"'")
    if not (2 <= len(name) <= 50) or len(name.split()) > 5:
        return None
    return {"name": name, "url": url, "domain": dom, "hn_id": hit.get("objectID"),
            "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "points": hit.get("points") or 0}


# ---- pricing rules ----------------------------------------------------------
_R = lambda p: re.compile(p, re.I)
STRONG_FREE = _R(r"\b(free (plan|tier|forever|version|for individuals|for personal use)|freemium|"
                 r"always free|forever free|free forever)\b")
WEAK_FREE = _R(r"\b(free account|(start|get started|sign up|try it|try) (for )?free)\b")   # could be a trial: not enough alone
PRICE = _R(r"[$€£₹]\s?\d{1,4}(?:[.,]\d{1,2})?\s?(?:/|per\s)\s?(?:mo|month|user|seat|year|yr)\b|"
           r"\b(?:per|/)\s?(?:month|user|seat)\b|billed (?:monthly|annually|yearly)|\b\d{1,4}\s?(?:usd|eur)\s?/\s?mo")
ENTERPRISE = _R(r"\b(contact (our )?sales|talk to sales|book a demo|request a demo|schedule a demo|"
                r"enterprise (plan|pricing|plans|edition|tier)|custom pricing|get a quote)\b")
FULLY_FREE = _R(r"\b(100% free|completely free|totally free|free and open[- ]source|free to use|"
                r"free (?:&|and) open|no (?:payment|credit card) (?:needed|required) ever)\b")


ANY_AMOUNT = _R(r"[$€£₹]\s?\d")


def _quote(text: str, m: re.Match, w: int = 70) -> str:
    """Verbatim snippet around the match, trimmed to whole words."""
    a, b = max(0, m.start() - w), min(len(text), m.end() + w)
    snippet = text[a:b]
    if a > 0:
        snippet = snippet.split(" ", 1)[-1]
    if b < len(text):
        snippet = snippet.rsplit(" ", 1)[0]
    return re.sub(r"\s+", " ", snippet).strip()


def classify_pricing(text: str) -> tuple[str | None, str | None]:
    """Deterministic pricing model + verbatim evidence snippet, or (None, None) if inconclusive."""
    strong, weak, price, ent, fully = (r.search(text) for r in (STRONG_FREE, WEAK_FREE, PRICE, ENTERPRISE, FULLY_FREE))
    free = strong or weak
    if price:                                   # a published price beats a "contact sales" tier
        return ("FREEMIUM", _quote(text, free)) if free else ("PAID", _quote(text, price))
    if ent:                                     # ENTERPRISE only when no dollar amount is shown anywhere
        amt = ANY_AMOUNT.search(text)
        return ("PAID", _quote(text, amt)) if amt else ("ENTERPRISE", _quote(text, ent))
    if fully:
        return "FREE", _quote(text, fully)
    if strong:                                  # "start for free" alone is a guess (trial?), so it stays undecided
        return "FREEMIUM", _quote(text, strong)
    return None, None


def page_text(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    links = [a.get("href") for a in soup.find_all("a", href=True)
             if "pricing" in (a.get("href") or "").lower() or (a.get_text() or "").strip().lower() in ("pricing", "plans")]
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()[:80000], links


LLM_INSTRUCTION = ("Decide which pricing model this product offers, using ONLY the text. Allowed values: "
                   "FREE, FREEMIUM, PAID, ENTERPRISE. If the text does not say, use null. Also copy ONE "
                   "short sentence from the text, verbatim, that supports your answer.")
LLM_SCHEMA = '{"pricing_model": "FREE|FREEMIUM|PAID|ENTERPRISE|null", "evidence": "verbatim sentence"}'


async def fetch_page_text(f: AsyncFetcher, url: str, domain: str) -> str:
    """Homepage text (+ the pricing page if the homepage links to one on the same domain). '' if unreachable."""
    try:
        html = await f.get_text(url, max_retries=1)
    except Exception:
        return ""
    text, links = await asyncio.to_thread(page_text, html)
    for href in links[:1]:
        u = urljoin(url, href)
        if registered_domain(u) == domain:
            try:
                ph = await f.get_text(u, max_retries=1)
                t2, _ = await asyncio.to_thread(page_text, ph)
                text += " " + t2
            except Exception:
                pass
    return text


def apply_yc_match(content: dict, yc_by_domain: dict) -> bool:
    """Set startupName/startupMatch from a YC domain match. True if the record changed."""
    dom = registered_domain(content.get("productUrl") or "")
    if dom in yc_by_domain and content.get("startupMatch") != "yc-domain":
        content["startupName"], content["startupMatch"] = yc_by_domain[dom], "yc-domain"
        return True
    return False


async def load_yc_domains(f: AsyncFetcher) -> dict:
    data = await f.get_json(YC_URL, timeout=aiohttp.ClientTimeout(total=180))
    return {registered_domain(c["website"]): c["name"] for c in data
            if isinstance(c, dict) and c.get("website") and c.get("name")}


async def enrich(f: AsyncFetcher, cand: dict, llm=None) -> dict:
    out = {**cand, "pricing": None, "evidence": None, "how": "none"}
    text = await fetch_page_text(f, cand["url"], cand["domain"])
    if not text:
        return out
    model, ev = classify_pricing(text)
    if model:
        out.update(pricing=model, evidence=ev, how="rules")
    elif llm is not None and len(text) > 300:
        try:
            r = await llm.extract(text=text, grounded_fields=["evidence"], instruction=LLM_INSTRUCTION,
                                  schema_hint=LLM_SCHEMA)
            pm = (r.data.get("pricing_model") or "").upper()
            if pm in PricingModel.__members__ and r.data.get("evidence"):   # evidence nulled if not in source
                out.update(pricing=pm, evidence=r.data["evidence"], how=f"llm:{r.tier}")
        except Exception as e:
            log.info("llm skipped for %s: %s", cand["url"][:60], str(e)[:80])
    return out


def build_record(c: dict, yc_by_domain: dict, resolver) -> ProductRecord:
    if c["domain"] in yc_by_domain:
        startup, match = yc_by_domain[c["domain"]], "yc-domain"
    else:
        r = resolver.resolve(c["name"])
        startup, match = (r.canonical, "name-match") if r.method != "unresolved" else (c["name"], "maker-brand")
    return ProductRecord(
        source=Source(name="Hacker News (Show HN)", url=c["hn_url"]),
        content=ProductContent(productName=c["name"], startupName=startup, pricingModel=c["pricing"],
                               productUrl=c["url"], pricingEvidence=c["evidence"], startupMatch=match))


async def discover(f: AsyncFetcher, weeks: int, want: int) -> list[dict]:
    now, cands, seen = int(time.time()), [], set()
    for w in range(weeks):
        hi, lo = now - w * 7 * 86400, now - (w + 1) * 7 * 86400
        q = urlencode({"tags": "show_hn", "hitsPerPage": 1000,
                       "numericFilters": f"created_at_i>{lo},created_at_i<={hi},points>=3"})
        try:
            data = await f.get_json(f"{HN_API}?{q}")
        except Exception as e:
            log.error("HN window %d failed: %s", w, e); continue
        for h in data.get("hits", []):
            c = parse_show_hn(h)
            if c and c["domain"] not in seen:
                seen.add(c["domain"]); cands.append(c)
        log.info("week -%d: %d candidates so far", w, len(cands))
        if len(cands) >= want:
            break
    return cands


CACHE_PATH = "data/products_cache_v2.jsonl"      # v2: stricter pricing rules (old cache must not be reused)


async def run(target: int, out_path: str, weeks: int = 60, llm_budget: int = 100) -> int:
    resolver = load_resolver()
    llm = None
    cache = {r["url"]: r for r in read_jsonl(CACHE_PATH)}      # pages already processed in earlier runs
    log.info("resuming with %d cached product pages", len(cache))
    async with AsyncFetcher() as f:
        try:
            yc_by_domain = await load_yc_domains(f)
        except Exception as e:                                  # never fail silently: matching quality depends on it
            log.error("YC dataset fetch FAILED (%s): startupName falls back to the product's own brand", e)
            yc_by_domain = {}
        cands = await discover(f, weeks, int(target * 4))   # rules decide ~1/3 of sites, so over-collect
        log.info("%d product candidates; %d YC domains known", len(cands), len(yc_by_domain))
        done: list[dict] = []
        if llm_budget > 0:
            from src.llm_orchestrator import LLMOrchestrator
            llm = LLMOrchestrator(cache_path="data/llm_cache.sqlite")
            await llm.__aenter__()
        try:
            for i in range(0, len(cands), 150):
                chunk = cands[i:i + 150]
                todo = [c for c in chunk if c["url"] not in cache]
                fresh = await asyncio.gather(*(enrich(f, c, llm if llm_budget > 0 else None) for c in todo))
                llm_budget -= sum(1 for r in fresh if r["how"].startswith("llm"))
                for r in fresh:
                    if r["pricing"]:                                  # only cache decided pages (negatives are cheap to retry)
                        cache[r["url"]] = {"url": r["url"], "pricing": r["pricing"], "evidence": r["evidence"],
                                           "how": r["how"]}
                        with open(CACHE_PATH, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(cache[r["url"]], ensure_ascii=False) + "\n")
                by_url = {r["url"]: r for r in fresh}
                res = [by_url[c["url"]] if c["url"] in by_url else
                       {**c, "pricing": cache[c["url"]]["pricing"], "evidence": cache[c["url"]]["evidence"],
                        "how": "cache"} for c in chunk]
                done.extend(res)
                log.info("enriched %d/%d  (pricing found: %d)", len(done), len(cands),
                         sum(1 for d in done if d["pricing"]))
                if sum(1 for d in done if d["pricing"]) >= target + 50:
                    break
        finally:
            if llm:
                await llm.__aexit__(None, None, None)
    done.sort(key=lambda d: (d["pricing"] is None, -d["points"]))     # determined first, then by HN points
    open(out_path, "w").close()
    n = 0
    for c in done[:target]:
        try:
            append_jsonl(out_path, build_record(c, yc_by_domain, resolver)); n += 1
        except Exception as e:
            log.warning("rejected %s: %s", c["url"], e)
    write_mapping_log(resolver)
    with_price = sum(1 for d in done[:target] if d["pricing"])
    log.info("products saved=%d (pricing determined for %d)", n, with_price)
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1100)
    ap.add_argument("--out", default="output/products.jsonl")
    ap.add_argument("--llm-budget", type=int, default=100, help="max LLM calls for unclear pricing (0 = rules only)")
    a = ap.parse_args()
    setup_logging()
    print(f"products saved: {asyncio.run(run(a.target, a.out, llm_budget=a.llm_budget))} -> {a.out}")
