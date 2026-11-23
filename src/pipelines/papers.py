"""Phase I - Research papers vertical.

arXiv API (listing: title, authors, published date, abstract, comment)
  -> GitHub repo URL extracted from the paper's OWN abstract / comment / links (verifiable, never guessed)
  -> GitHub REST API for the CURRENT star count (+ timestamp of when we read it)

Resumable & idempotent: re-running skips arXiv IDs already in the output file.
Scale note: pages are independent (start offset), so this shards across workers by offset ranges.

Run:  python -m src.pipelines.papers --target 1200
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import aiohttp
import feedparser

from src import config
from src.dates import parse_date_string
from src.http_client import AsyncFetcher, FetchError
from src.io_utils import append_jsonl, read_jsonl
from src.logging_setup import setup_logging
from src.schemas import PaperContent, PaperRecord, Source

log = logging.getLogger("papers")

ARXIV_API = "https://export.arxiv.org/api/query"
CATS = "(cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:cs.AI OR cat:stat.ML)"
QUERIES = {"github": CATS + " AND all:github",   # precise but heavy: arXiv may be slow / time out
           "plain": CATS}                         # light: we filter for GitHub links ourselves
PAGE_SIZE = {"github": 50, "plain": 100}
ARXIV_TIMEOUT_S = 90
GH_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)", re.I)
NON_REPO_OWNERS = {"features", "orgs", "sponsors", "topics", "about", "pricing", "marketplace", "settings",
                   "login", "join", "collections", "enterprise", "apps", "site", "readme", "explore"}
ARXIV_DELAY_S = 3.0      # arXiv API terms: max 1 request / 3 seconds


def arxiv_url(start: int, n: int, mode: str = "github") -> str:
    return ARXIV_API + "?" + urlencode({"search_query": QUERIES[mode], "start": start, "max_results": n,
                                        "sortBy": "submittedDate", "sortOrder": "descending"})


def extract_github_repo(*texts: str) -> tuple[str, str] | None:
    for t in texts:
        for m in GH_RE.finditer(t or ""):
            owner, repo = m.group(1), re.sub(r"(\.git|[.,;:)\]}>\"']+)$", "", m.group(2))
            if owner.lower() in NON_REPO_OWNERS or not repo:
                continue
            return owner, repo
    return None


def parse_arxiv_feed(xml: str) -> list[dict]:
    feed = feedparser.parse(xml)
    out = []
    for e in feed.entries:
        abs_url = re.sub(r"v\d+$", "", e.get("id", "").replace("http://", "https://"))
        if "arxiv.org/abs/" not in abs_url:
            continue
        published, _ = parse_date_string(e.get("published", ""))
        if not published:
            continue
        link_texts = " ".join(l.get("href", "") for l in e.get("links", []))
        repo = extract_github_repo(e.get("arxiv_comment", ""), e.get("summary", ""), link_texts)
        out.append({
            "arxiv_id": abs_url.rsplit("/abs/", 1)[1],
            "title": re.sub(r"\s+", " ", e.get("title", "")).strip(),
            "authors": [a.get("name", "").strip() for a in e.get("authors", []) if a.get("name")],
            "paper_url": abs_url, "published": published, "repo": repo,
        })
    return out


_star_cache: dict[tuple[str, str], tuple[int, datetime] | None] = {}


async def fetch_stars(fetcher: AsyncFetcher, owner: str, repo: str):
    """Current star count straight from GitHub (authoritative). None if repo missing/unreachable."""
    key = (owner.lower(), repo.lower())
    if key in _star_cache:
        return _star_cache[key]
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    res = None
    try:
        # 403 is how GitHub signals primary/secondary rate limiting -> treat as retryable w/ backoff
        j = await fetcher.get_json(f"https://api.github.com/repos/{owner}/{repo}", headers=headers,
                                   extra_retry=frozenset({403}), max_retries=4)
        if isinstance(j.get("stargazers_count"), int):
            res = (j["stargazers_count"], datetime.now(timezone.utc))
    except FetchError as e:
        log.info("no stars for %s/%s (%s)", owner, repo, e.status)
    _star_cache[key] = res
    return res


async def run(target: int, out_path: str, mode: str = "github", max_requests: int = 400) -> int:
    done = {r["content"]["paper_url"] for r in read_jsonl(out_path)}
    count = len(done)
    log.info("resuming with %d existing records, target %d, mode=%s", count, target, mode)
    start, requests = 0, 0
    async with AsyncFetcher() as f:
        while count < target and requests < max_requests:
            requests += 1
            n = PAGE_SIZE[mode]
            try:
                xml = await f.get_text(arxiv_url(start, n, mode), max_retries=2,
                                       timeout=aiohttp.ClientTimeout(total=ARXIV_TIMEOUT_S))
            except FetchError as e:
                if mode == "github":
                    log.warning("arXiv 'github' query failed (%s) -> falling back to light 'plain' mode", e)
                    mode, start = "plain", 0
                    continue
                log.error("arXiv request at offset %d failed: %s", start, e)
                break
            items = parse_arxiv_feed(xml)
            if not items:
                log.info("arXiv returned no entries at offset %d; stopping", start)
                break
            todo = [i for i in items if i["repo"] and i["paper_url"] not in done]
            stars = await asyncio.gather(*(fetch_stars(f, *i["repo"]) for i in todo))
            for i, st in zip(todo, stars):
                if st is None:
                    continue
                owner, repo = i["repo"]
                try:
                    rec = PaperRecord(
                        source=Source(name="arXiv", url=i["paper_url"]),
                        content=PaperContent(title=i["title"], authors=i["authors"], paper_url=i["paper_url"],
                                             github_url=f"https://github.com/{owner}/{repo}",
                                             github_stars=st[0], published_date=i["published"],
                                             github_stars_fetched_at=st[1]))
                except Exception as e:                       # schema rejection -> log, never write bad data
                    log.warning("rejected %s: %s", i["paper_url"], e)
                    continue
                append_jsonl(out_path, rec)
                done.add(i["paper_url"]); count += 1
            log.info("offset %d (%s): %d entries, %d with repo, total saved %d", start, mode, len(items), len(todo), count)
            start += n
            await asyncio.sleep(ARXIV_DELAY_S)
    return count


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1200)
    ap.add_argument("--out", default="output/papers.jsonl")
    ap.add_argument("--mode", choices=["github", "plain"], default="github")
    a = ap.parse_args()
    setup_logging()
    n = asyncio.run(run(a.target, a.out, a.mode))
    print(f"papers saved: {n} -> {a.out}")
