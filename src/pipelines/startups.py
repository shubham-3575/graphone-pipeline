"""Phase I - Startups vertical.

Source: open Y Combinator company dataset (yc-oss, JSON, thousands of companies). One bulk fetch, then
deterministic field mapping - NO LLM involved, so nothing can be hallucinated: every value comes from the dataset
and every record links to the company's own YC page.

Why this scales: the directory is paged/sharded by batch; replace the single fetch with one task per batch
(https://yc-oss.github.io/api/batches/<batch>.json) and the rest is unchanged.

Run:  python -m src.pipelines.startups --target 1100
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re

import aiohttp

from src.entity_resolver import EntityResolver, write_mapping_log
from src.http_client import AsyncFetcher
from src.io_utils import append_jsonl
from src.logging_setup import setup_logging
from src.schemas import Source, StartupContent, StartupData, StartupRecord

log = logging.getLogger("startups")
URL = "https://yc-oss.github.io/api/companies/all.json"
AI_RE = re.compile(r"\b(AI|A\.I\.|artificial intelligence|machine learning|ML|LLMs?|generative|GPT|"
                   r"agents?|agentic|deep learning|computer vision|NLP|copilot)\b", re.I)


def _text_blob(c: dict) -> str:
    parts = [c.get("one_liner"), c.get("industry"), c.get("subindustry")]
    for k in ("industries", "tags"):
        v = c.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
    return " ".join(str(p) for p in parts if p)


def is_ai_company(c: dict) -> bool:
    return bool(AI_RE.search(_text_blob(c)))


def company_page(c: dict) -> str | None:
    u = c.get("url")
    if isinstance(u, str) and u.startswith("http") and "ycombinator.com/companies/" in u:
        return u
    slug = c.get("slug")
    return f"https://www.ycombinator.com/companies/{slug}" if slug else None


def team_size(c: dict) -> int | None:
    v = c.get("team_size")
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def to_record(c: dict) -> StartupRecord | None:
    name = (c.get("name") or "").strip()
    page = company_page(c)
    if not name or not page:
        return None
    return StartupRecord(source=Source(name="Y Combinator", url=page),
                         content=StartupContent(entityName=name, data=StartupData(employeeCount=team_size(c))))


def select(companies: list[dict], target: int, ai_only: bool = True) -> list[dict]:
    seen, out = set(), []
    pool = [c for c in companies if isinstance(c, dict) and (not ai_only or is_ai_company(c))]
    pool.sort(key=lambda c: c.get("launched_at") or 0, reverse=True)     # newest first
    for c in pool:
        key = (c.get("slug") or c.get("name") or "").casefold()
        if not (c.get("name") or "").strip() or not key or key in seen:
            continue
        if str(c.get("status", "Active")).lower() == "inactive":
            continue
        seen.add(key); out.append(c)
        if len(out) >= target:
            break
    return out


def unique_records(chosen: list[dict], resolver, target: int) -> list[StartupRecord]:
    """Records with unique (case-insensitive) canonical names. Two distinct YC companies can share a name, and two
    different names can resolve to one canonical entity: keep the first (newest) and skip the rest."""
    out, seen = [], set()
    for c in chosen:
        rec = to_record(c)
        if not rec:
            continue
        r = resolver.resolve(rec.content.entityName)
        if r.method != "unresolved":
            rec.content.entityName = r.canonical
        key = rec.content.entityName.casefold()
        if key in seen:
            continue
        seen.add(key); out.append(rec)
        if len(out) >= target:
            break
    return out


async def run(target: int, out_path: str, ai_only: bool = True) -> int:
    async with AsyncFetcher() as f:
        data = await f.get_json(URL, timeout=aiohttp.ClientTimeout(total=180))
    log.info("fetched %d companies from YC dataset", len(data))
    chosen = select(data, int(target * 1.4), ai_only)           # buffer so dropped duplicates are replaced
    resolver = EntityResolver()
    recs = unique_records(chosen, resolver, target)
    open(out_path, "w").close()                                  # idempotent: regenerate file
    for rec in recs:
        append_jsonl(out_path, rec)
    n = len(recs)
    new = write_mapping_log(resolver)
    log.info("saved %d startups (ai_only=%s); %d mapping-log rows added", n, ai_only, new)
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1100)
    ap.add_argument("--out", default="output/startups.jsonl")
    ap.add_argument("--all", action="store_true", help="do not filter to AI companies")
    a = ap.parse_args()
    setup_logging()
    print(f"startups saved: {asyncio.run(run(a.target, a.out, not a.all))} -> {a.out}")
