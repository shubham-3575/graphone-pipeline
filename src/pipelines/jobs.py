"""Phase II - AI job boards. 5 boards, only jobs published in the last 24h.

Adapters turn each board's payload into one normalized dict; everything after that (date check, dedup,
company canonicalization, role family) is shared. No LLM: all values come straight from the board's fields,
so every record traces to its job URL.

Assumptions (documented for the README): boards whose timestamps carry no timezone are treated as UTC;
RemoteOK / Remotive / WeWorkRemotely / Jobicy are remote-only boards, so is_remote=True for them.

Run:  python -m src.pipelines.jobs --reset          (add --ai-only to keep only AI/ML/data roles)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

import feedparser

from src.common import AI_JOB_RE, load_resolver, role_family
from src.dates import is_fresh, parse_date_string
from src.entity_resolver import write_mapping_log
from src.freshness import SeenStore
from src.http_client import AsyncFetcher, FetchError
from src.io_utils import append_jsonl
from src.logging_setup import setup_logging
from src.schemas import JobContent, JobRecord, Source

log = logging.getLogger("jobs")


def _job(source, title, company, date_raw, url, remote, ai_board=False):
    return {"source": source, "title": (title or "").strip(), "company": (company or "").strip(),
            "date_raw": date_raw, "url": (url or "").strip(), "remote": remote, "ai_board": ai_board}


def parse_remoteok(data) -> list[dict]:
    return [_job("RemoteOK", j.get("position"), j.get("company"), str(j.get("epoch") or j.get("date") or ""),
                 j.get("url"), True) for j in data if isinstance(j, dict) and j.get("position")]


def parse_remotive(data) -> list[dict]:
    return [_job("Remotive", j.get("title"), j.get("company_name"), j.get("publication_date"), j.get("url"), True)
            for j in (data or {}).get("jobs", [])]


def parse_arbeitnow(data) -> list[dict]:
    return [_job("Arbeitnow", j.get("title"), j.get("company_name"), str(j.get("created_at") or ""), j.get("url"),
                 j.get("remote") if isinstance(j.get("remote"), bool) else None)
            for j in (data or {}).get("data", [])]


def parse_jobicy(data) -> list[dict]:
    return [_job("Jobicy", j.get("jobTitle"), j.get("companyName"), j.get("pubDate"), j.get("url"), True)
            for j in (data or {}).get("jobs", [])]


def parse_wwr(xml: str) -> list[dict]:
    out = []
    for e in feedparser.parse(xml).entries:
        title = e.get("title", "")
        company, _, role = title.partition(": ")
        if not role:
            company, role = "", title
        out.append(_job("We Work Remotely", role, company, e.get("published", ""), e.get("link"), True))
    return out


def parse_himalayas(data) -> list[dict]:
    return [_job("Himalayas", j.get("title"), j.get("companyName"), str(j.get("pubDate") or ""),
                 j.get("applicationLink") or j.get("guid") or j.get("url"), True)
            for j in (data or {}).get("jobs", []) if isinstance(j, dict)]


# ---- AI-company career pages on Ashby's public job-board API. Checked live on 2026-11-23: 18 of these 20 slugs
# answered (2 did not exist and are skipped), publishedAt is a real publish date. (Lever was tried and dropped:
# 0 of 7 company boards reachable.) ----
ASHBY = {"openai": "OpenAI", "cohere": "Cohere", "perplexity": "Perplexity AI", "elevenlabs": "ElevenLabs",
         "runway": "Runway", "together-ai": "Together AI", "replit": "Replit", "cursor": "Cursor", "sierra": "Sierra",
         "harvey": "Harvey", "modal": "Modal", "pinecone": "Pinecone", "langchain": "LangChain",
         "anyscale": "Anyscale", "lambda": "Lambda", "synthesia": "Synthesia", "character": "Character.AI",
         "deepgram": "Deepgram", "perplexityai": "Perplexity AI", "cognition": "Cognition"}
ASHBY_NAME = "Ashby (AI company career pages)"


def parse_ashby(data, company: str) -> list[dict]:
    out = []
    for j in (data or {}).get("jobs", []) if isinstance(data, dict) else []:
        out.append(_job(ASHBY_NAME, j.get("title"), company, j.get("publishedAt") or j.get("publishedDate"),
                        j.get("jobUrl") or j.get("applyUrl"),
                        j.get("isRemote") if isinstance(j.get("isRemote"), bool) else None, ai_board=True))
    return out


async def fetch_ashby(f: AsyncFetcher) -> list[dict]:
    async def one(slug, company):
        try:
            return parse_ashby(await f.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", max_retries=1),
                               company), True
        except Exception:
            return [], False

    res = await asyncio.gather(*(one(s, c) for s, c in ASHBY.items()))
    jobs = [j for lst, _ in res for j in lst]
    log.info("%-32s %d/%d company boards reachable, %d jobs listed", ASHBY_NAME,
             sum(1 for _, ok in res if ok), len(ASHBY), len(jobs))
    return jobs


def board_stats(jobs: list[dict], now: datetime) -> dict:
    """Why a board contributes nothing: how many dates parse, how many are fresh, how old is the newest one."""
    dates = [parse_date_string(j["date_raw"], now)[0] for j in jobs]
    ok = [d for d in dates if d]
    return {"listed": len(jobs), "dated": len(ok), "fresh": sum(1 for d in ok if is_fresh(d, now)),
            "newest_age_h": round((now - max(ok)).total_seconds() / 3600, 1) if ok else None}


BOARDS = [  # (name, url, kind, parser)
    ("RemoteOK", "https://remoteok.com/api", "json", parse_remoteok),
    ("Remotive", "https://remotive.com/api/remote-jobs", "json", parse_remotive),
    ("We Work Remotely", "https://weworkremotely.com/categories/remote-programming-jobs.rss", "text", parse_wwr),
    ("We Work Remotely", "https://weworkremotely.com/remote-jobs.rss", "text", parse_wwr),   # all-categories feed
    ("Arbeitnow", "https://www.arbeitnow.com/api/job-board-api", "json", parse_arbeitnow),
    ("Jobicy", "https://jobicy.com/api/v2/remote-jobs?count=100", "json", parse_jobicy),
    ("Himalayas", "https://himalayas.app/jobs/api?limit=100", "json", parse_himalayas),
]


async def fetch_board(f: AsyncFetcher, name, url, kind, parser) -> list[dict]:
    try:
        payload = await (f.get_json(url) if kind == "json" else f.get_text(url))
        jobs = parser(payload)
        log.info("%-18s %4d jobs listed", name, len(jobs))
        return jobs
    except (FetchError, Exception) as e:       # one broken board must never kill the run
        log.error("board %s failed: %s", name, e)
        return []


def to_record(j: dict, now: datetime, resolver, store: SeenStore | None, ai_only: bool = False):
    """Returns (JobRecord | None, reason). Pure apart from the dedup claim."""
    if not (j["title"] and j["company"] and j["url"].startswith("http")):
        return None, "missing-field"
    date, _ = parse_date_string(j["date_raw"], now)
    if date is None:
        return None, "no-date"
    if not is_fresh(date, now):
        return None, "stale"
    ai = bool(j.get("ai_board")) or bool(AI_JOB_RE.search(j["title"]))
    if ai_only and not ai:
        return None, "not-ai"
    if store is not None and not store.claim(j["url"], j["title"] + " " + j["company"]):
        return None, "duplicate"
    r = resolver.resolve(j["company"])
    rec = JobRecord(source=Source(name=j["source"], url=j["url"]),
                    content=JobContent(title=j["title"], company=r.canonical, date=date, is_remote=j["remote"],
                                       role_family=role_family(j["title"]), job_url=j["url"], ai_related=ai))
    return rec, "ok"


async def run(out_path="output/jobs.jsonl", reset=False, ai_only=False) -> int:
    if reset:
        for p in (out_path, "data/seen_jobs.sqlite"):
            if os.path.exists(p):
                os.remove(p)
    store, resolver, now = SeenStore("data/seen_jobs.sqlite"), load_resolver(), datetime.now(timezone.utc)
    async with AsyncFetcher() as f:
        lists = await asyncio.gather(*(fetch_board(f, *b) for b in BOARDS), fetch_ashby(f))
    names = [b[0] for b in BOARDS] + [ASHBY_NAME]
    for name, jobs in zip(names, lists):
        log.info("board %-32s %s", name, board_stats(jobs, now))
    stats: dict[str, int] = {}
    saved = 0
    for jobs in lists:
        for j in jobs:
            rec, why = to_record(j, now, resolver, store, ai_only)
            stats[why] = stats.get(why, 0) + 1
            if rec:
                append_jsonl(out_path, rec); saved += 1
    write_mapping_log(resolver)
    log.info("jobs saved=%d  breakdown=%s", saved, stats)
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="start clean (do this for the final run)")
    ap.add_argument("--ai-only", action="store_true", help="keep only AI-related jobs (default: keep all, flagged in ai_related)")
    a = ap.parse_args()
    setup_logging()
    print(f"fresh jobs saved: {asyncio.run(run(reset=a.reset, ai_only=a.ai_only))}")
