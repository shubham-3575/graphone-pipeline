"""Final QA over everything in output/*.jsonl. Run before exporting:  python scripts/validate_outputs.py
Exit code 1 on any HARD failure (bad record, stale job/news, missing source URL, duplicates)."""
import argparse, json, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HARD, SOFT = [], []


def load(p):
    f = Path(p)
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []


def dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def check_common(name, rows):
    for i, r in enumerate(rows):
        if not str(r.get("source", {}).get("url", "")).startswith("http"):
            HARD.append(f"{name}[{i}]: missing/invalid source.url")
        if not r.get("schemaVersion") or not r.get("collectedAt"):
            HARD.append(f"{name}[{i}]: missing schemaVersion/collectedAt")


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "n/a"


def main(hours):
    now = datetime.now(timezone.utc)
    cut = now - timedelta(hours=hours)
    out = []

    papers = load("output/papers.jsonl"); check_common("papers", papers)
    c = [r["content"] for r in papers]
    dup = len(c) - len({x["paper_url"] for x in c})
    if dup: HARD.append(f"papers: {dup} duplicate paper_url")
    if len(c) < 1000: HARD.append(f"papers: only {len(c)} rows (need >= 1000)")
    nostar = sum(1 for x in c if x.get("github_stars") is None)
    if nostar: HARD.append(f"papers: {nostar} rows without github_stars")
    out.append(f"Research Papers  {len(c):5d} rows | with github url+stars {pct(len(c)-nostar, len(c))} | "
               f"stars median {sorted(x['github_stars'] for x in c if x.get('github_stars') is not None)[len(c)//2] if c else '-'}")

    st = load("output/startups.jsonl"); check_common("startups", st)
    names = [r["content"]["entityName"] for r in st]
    if len(names) != len(set(n.casefold() for n in names)): HARD.append("startups: duplicate entityName")
    if len(st) < 1000: HARD.append(f"startups: only {len(st)} rows (need >= 1000)")
    ec = sum(1 for r in st if (r["content"].get("data") or {}).get("employeeCount"))
    out.append(f"Startups         {len(st):5d} rows | employeeCount present {pct(ec, len(st))}")

    pr = load("output/products.jsonl"); check_common("products", pr)
    urls = [r["content"].get("productUrl") for r in pr]
    if len(urls) != len(set(urls)): HARD.append("products: duplicate productUrl")
    if len(pr) < 1000: HARD.append(f"products: only {len(pr)} rows (need >= 1000)")
    pm = Counter(r["content"].get("pricingModel") for r in pr)
    for i, r in enumerate(pr):
        if r["content"].get("pricingModel") and not r["content"].get("pricingEvidence"):
            HARD.append(f"products[{i}]: pricingModel without evidence")
    out.append(f"Products         {len(pr):5d} rows | pricing {dict(pm)} | match {dict(Counter(r['content'].get('startupMatch') for r in pr))}")

    jobs = load("output/jobs.jsonl"); check_common("jobs", jobs)
    old = [r for r in jobs if not (cut <= dt(r["content"]["date"]) <= now + timedelta(minutes=10))]
    if old: HARD.append(f"jobs: {len(old)} outside last {hours}h (rerun jobs --reset right before export)")
    ai_jobs = sum(1 for r in jobs if r["content"].get("ai_related"))
    out.append(f"Jobs             {len(jobs):5d} rows | AI-related {ai_jobs} | by source {dict(Counter(r['source']['name'] for r in jobs))}")

    news = load("output/news.jsonl"); check_common("news", news)
    old = [r for r in news if not (cut <= dt(r["content"]["published_date"]) <= now + timedelta(minutes=10))]
    if old: HARD.append(f"news: {len(old)} outside last {hours}h (rerun news --reset right before export)")
    short = [r for r in news if len(r["content"].get("full_text", "")) < 200]
    if short: HARD.append(f"news: {len(short)} articles without full text")
    heur = sum(1 for r in news if r["content"].get("date_confidence") == "heuristic")
    out.append(f"News             {len(news):5d} rows | by source {dict(Counter(r['source']['name'] for r in news))} | heuristic-dated {heur}")
    if len({r['source']['name'] for r in news}) < 5: SOFT.append("news: fewer than 5 sources contributed in this window")
    if len({r['source']['name'] for r in jobs}) < 5: SOFT.append("jobs: fewer than 5 boards contributed in this window")

    mp = load("output/entity_mapping.jsonl")
    out.append(f"Entity mapping   {len(mp):5d} rows | methods {dict(Counter(r['method'] for r in mp))}")

    print("\n".join(out))
    for s in SOFT: print("WARN :", s)
    for h in HARD[:40]: print("FAIL :", h)
    print(f"\n{'FAILED' if HARD else 'ALL HARD CHECKS PASSED'} ({len(HARD)} hard, {len(SOFT)} soft)")
    return 1 if HARD else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--hours", type=int, default=24)
    sys.exit(main(ap.parse_args().hours))
