"""Export pipeline output (JSONL) to the 6 required Google-Sheet tabs, plus CSV backups.

Setup (one time):
  1. Google Cloud console -> create project -> enable "Google Sheets API" (and Drive API).
  2. IAM & Admin -> Service Accounts -> create -> Keys -> Add key (JSON) -> save as service_account.json (project root).
  3. Create an empty Google Sheet yourself, click Share, add the service account's e-mail as EDITOR.
  4. Also share the sheet as "Anyone with the link: Viewer" (the assignment needs a public link).
Run:  python -m src.export_sheets --sheet-url "https://docs.google.com/spreadsheets/d/XXXX/edit"
      python -m src.export_sheets --csv-only          (no Google needed; writes output/csv/*.csv)
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from src.io_utils import read_jsonl
from src.logging_setup import setup_logging

log = logging.getLogger("export")
CELL_LIMIT = 45000   # Google Sheets cell max is 50,000 chars


def _iso(v):
    """One date format everywhere: ISO-8601 UTC, seconds precision (2026-11-23T08:30:11Z)."""
    if v is None or v == "":
        return v
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        d = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return str(v)


def _common(r):
    return [r.get("source", {}).get("name"), r.get("source", {}).get("url"), _iso(r.get("collectedAt")),
            r.get("schemaVersion")]


COMMON_H = ["source_name", "source_url", "collectedAt", "schemaVersion"]

TABS = {
    "Startups": ("output/startups.jsonl",
                 ["entityName", "employeeCount", "recordType"] + COMMON_H,
                 lambda r: [r["content"]["entityName"], (r["content"].get("data") or {}).get("employeeCount"),
                            r["recordType"]] + _common(r)),
    "Products": ("output/products.jsonl",
                 ["productName", "startupName", "pricingModel", "pricingEvidence", "productUrl", "startupMatch",
                  "recordType"] + COMMON_H,
                 lambda r: [r["content"].get("productName"), r["content"]["startupName"],
                            r["content"].get("pricingModel"), r["content"].get("pricingEvidence"),
                            r["content"].get("productUrl"), r["content"].get("startupMatch"),
                            r["recordType"]] + _common(r)),
    "Research Papers": ("output/papers.jsonl",
                        ["title", "authors", "paper_url", "github_url", "github_stars", "github_stars_fetched_at",
                         "published_date", "recordType"] + COMMON_H,
                        lambda r: [r["content"]["title"], "; ".join(r["content"].get("authors", [])),
                                   r["content"]["paper_url"], r["content"].get("github_url"),
                                   r["content"].get("github_stars"),
                                   _iso(r["content"].get("github_stars_fetched_at")),
                                   _iso(r["content"]["published_date"]), r["recordType"]] + _common(r)),
    "Jobs": ("output/jobs.jsonl",
             ["title", "company", "date", "is_remote", "role_family", "ai_related", "job_url", "recordType"] + COMMON_H,
             lambda r: [r["content"].get("title"), r["content"]["company"], _iso(r["content"]["date"]),
                        r["content"].get("is_remote"), r["content"].get("role_family"),
                        r["content"].get("ai_related"), r["content"].get("job_url"), r["recordType"]] + _common(r)),
    "News": ("output/news.jsonl",
             ["title", "published_date", "date_confidence", "article_url", "full_text", "recordType"] + COMMON_H,
             lambda r: [r["content"]["title"], _iso(r["content"]["published_date"]),
                        r["content"].get("date_confidence"), r["content"]["article_url"],
                        re.sub(r"\s+", " ", r["content"].get("full_text") or "").strip()[:CELL_LIMIT], r["recordType"]] + _common(r)),
    "Entity Mapping Log": ("output/entity_mapping.jsonl",
                           ["raw", "canonical", "method", "score", "is_new_entity", "origin", "expected", "correct"],
                           lambda r: [r["raw"], r["canonical"], r["method"], r["score"], r["is_new_entity"],
                                      r.get("origin", "pipeline"), r.get("expected"), r.get("correct")]),
}


def mapping_records() -> list[dict]:
    """Pipeline decisions + labeled evaluation cases. Drops the noise of unresolved names that map to themselves,
    and lists real resolutions first."""
    recs = [r for r in read_jsonl("output/entity_mapping.jsonl")
            if not (r.get("method") == "unresolved" and r.get("raw", "").strip() == r.get("canonical", ""))]
    recs += read_jsonl("output/entity_eval.jsonl")
    return sorted(recs, key=lambda r: (r.get("method") == "unresolved", r.get("origin", "pipeline"),
                                       r.get("canonical", "").casefold(), r.get("raw", "").casefold()))


def build_tables() -> dict[str, list[list]]:
    tables = {}
    for tab, (path, header, flat) in TABS.items():
        rows = []
        for rec in (mapping_records() if tab == "Entity Mapping Log" else read_jsonl(path)):
            try:
                rows.append(flat(rec))
            except KeyError as e:
                log.warning("skipping malformed record in %s (missing %s)", path, e)
        tables[tab] = [header] + rows
        log.info("%-20s %6d rows  (%s)", tab, len(rows), path)
    return tables


def write_csv(tables: dict[str, list[list]], out_dir: str = "output/csv") -> None:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for tab, rows in tables.items():
        with open(Path(out_dir) / f"{tab.replace(' ', '_')}.csv", "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(rows)
    log.info("CSV backups written to %s", out_dir)


def push_to_sheets(tables: dict[str, list[list]], sheet_url: str, creds: str = "service_account.json") -> None:
    import gspread
    gc = gspread.service_account(filename=creds)
    sh = gc.open_by_url(sheet_url)
    existing = {w.title: w for w in sh.worksheets()}
    for tab, rows in tables.items():
        ws = existing.get(tab) or sh.add_worksheet(tab, rows=max(len(rows) + 10, 100), cols=max(len(rows[0]), 5))
        ws.clear()
        if ws.row_count < len(rows) + 5:
            ws.resize(rows=len(rows) + 5, cols=max(len(rows[0]), ws.col_count))
        for i in range(0, len(rows), 2000):                     # chunked writes stay under API payload limits
            ws.update(range_name=f"A{i + 1}", values=rows[i:i + 2000], value_input_option="RAW")
        ws.freeze(rows=1)
        log.info("uploaded tab %s (%d rows)", tab, len(rows) - 1)
    if "Sheet1" in existing and len(sh.worksheets()) > 1:
        try:
            sh.del_worksheet(existing["Sheet1"])
        except Exception:
            pass
    log.info("done: %s", sheet_url)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet-url")
    ap.add_argument("--creds", default="service_account.json")
    ap.add_argument("--csv-only", action="store_true")
    a = ap.parse_args()
    setup_logging()
    t = build_tables()
    write_csv(t)
    if not a.csv_only:
        if not a.sheet_url:
            raise SystemExit("--sheet-url required (or use --csv-only)")
        push_to_sheets(t, a.sheet_url, a.creds)
