"""Never process the same article/job twice, even across crawler nodes.

SeenStore.claim(url, content_hash) is ATOMIC: exactly one caller ever gets True for a given key.
  * Local/trial: SQLite  (INSERT OR IGNORE on a PRIMARY KEY is atomic).
  * Distributed:  same interface backed by Redis `SET key 1 NX EX ttl` or Postgres `INSERT ... ON CONFLICT DO NOTHING`.
Two keys: canonical URL, and a content fingerprint (catches the same story syndicated under another URL).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_src|source$|cmpid)", re.I)


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    q = sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k))
    path = re.sub(r"/+$", "", p.path) or "/"
    host = p.netloc.lower().removeprefix("www.")
    return urlunsplit((p.scheme.lower() or "https", host, path, urlencode(q), ""))


def content_fingerprint(title: str, text: str = "") -> str:
    norm = re.sub(r"\W+", " ", (title + " " + text[:500]).casefold()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


class SeenStore:
    def __init__(self, path: str = "data/seen.sqlite"):
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; each statement atomic
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")

    def claim(self, url: str, title: str = "", text: str = "") -> bool:
        """True if this item is NEW (and is now claimed). False if URL or fingerprint was seen before."""
        keys = [f"u:{canonical_url(url)}", f"f:{content_fingerprint(title, text)}"]
        now = datetime.now(timezone.utc).isoformat()
        new = True
        for k in keys:
            cur = self.db.execute("INSERT OR IGNORE INTO seen(key, first_seen) VALUES (?, ?)", (k, now))
            if cur.rowcount == 0:
                new = False
        return new

    def first_seen(self, url: str) -> datetime | None:
        row = self.db.execute("SELECT first_seen FROM seen WHERE key=?", (f"u:{canonical_url(url)}",)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def close(self):
        self.db.close()
