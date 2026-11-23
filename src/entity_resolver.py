"""Deterministic entity resolution (Phase IV).

Pipeline for each raw name (first hit wins, every decision is logged with its method + score):
  1. exact      : normalized raw == normalized canonical/alias
  2. squashed   : same after removing ALL spaces/punctuation  ("Open AI" == "OpenAI")
  3. fuzzy      : RapidFuzz token ratio >= FUZZY_MIN, only if the runner-up is clearly worse (no ambiguity)
  4. unresolved : keep the cleaned raw name; flagged as NEW entity (never forced onto a wrong canonical)
Fully deterministic: no LLM involved, same input -> same output.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path

from rapidfuzz import fuzz, process

LEGAL_SUFFIX = re.compile(
    r"\b(incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|b\.?v|s\.?a\.?s|pbc|lp|plc|pte|pvt|private)\b\.?",
    re.I)
FUZZY_MIN = 92
AMBIGUITY_GAP = 4


def clean_display(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw).strip()
    s = LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"[,;|]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .-")


def norm_key(raw: str) -> str:
    s = clean_display(raw).casefold().replace("&", " and ")
    return re.sub(r"[^\w]+", " ", s).strip()


def squash(raw: str) -> str:
    s = norm_key(raw).replace(" ", "")
    s = re.sub(r"(ai|io|com)$", "", s) if len(s) > 5 and s.endswith(("ai", "io", "com")) and s not in {"openai"} else s
    return s


@dataclass
class Resolution:
    raw: str
    canonical: str
    method: str          # exact | squashed | fuzzy | unresolved
    score: float
    is_new_entity: bool

    def as_row(self) -> dict:
        return asdict(self)


class EntityResolver:
    def __init__(self, seed_path: str = "data/seed_startups.json"):
        seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        self.exact: dict[str, str] = {}
        self.squashed: dict[str, str] = {}
        for e in seed:
            for name in [e["canonical"], *e["aliases"]]:
                self.exact.setdefault(norm_key(name), e["canonical"])
                self.squashed.setdefault(squash(name), e["canonical"])
        self.fuzzy_keys = list(self.exact.keys())
        self.log: list[Resolution] = []

    def register(self, canonical: str, aliases: list[str] | None = None) -> None:
        """Add a canonical entity (+aliases) at runtime. Seed entries always win on key collisions."""
        for name in [canonical, *(aliases or [])]:
            k = norm_key(name)
            if not k:
                continue
            if k not in self.exact:
                self.exact[k] = canonical
                self.fuzzy_keys.append(k)
            sq = squash(name)
            if sq:
                self.squashed.setdefault(sq, canonical)

    def resolve(self, raw: str) -> Resolution:
        k = norm_key(raw)
        if k in self.exact:
            r = Resolution(raw, self.exact[k], "exact", 100.0, False)
        elif squash(raw) and squash(raw) in self.squashed:
            r = Resolution(raw, self.squashed[squash(raw)], "squashed", 98.0, False)
        else:
            r = self._fuzzy(raw, k)
        self.log.append(r)
        return r

    def _fuzzy(self, raw: str, k: str) -> Resolution:
        if len(k) >= 4:
            hits = process.extract(k, self.fuzzy_keys, scorer=fuzz.ratio, limit=5)
            if hits and hits[0][1] >= FUZZY_MIN:
                best = self.exact[hits[0][0]]
                rival = next((s for key, s, _ in hits[1:] if self.exact[key] != best), 0)
                if hits[0][1] - rival >= AMBIGUITY_GAP:
                    return Resolution(raw, best, "fuzzy", float(hits[0][1]), False)
        return Resolution(raw, clean_display(raw) or raw.strip(), "unresolved", 0.0, True)

    def mapping_rows(self) -> list[dict]:
        """Rows for the 'Entity Mapping Log' sheet (deduplicated on raw+canonical)."""
        seen, rows = set(), []
        for r in self.log:
            key = (r.raw, r.canonical)
            if key not in seen:
                seen.add(key); rows.append(r.as_row())
        return rows


def write_mapping_log(resolver: "EntityResolver", path: str = "output/entity_mapping.jsonl") -> int:
    """Merge this run's decisions into the mapping log (dedup on raw+canonical)."""
    import json
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    have = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line); have.add((r["raw"], r["canonical"]))
    n = 0
    with open(p, "a", encoding="utf-8") as f:
        for row in resolver.mapping_rows():
            if (row["raw"], row["canonical"]) not in have:
                f.write(json.dumps(row, ensure_ascii=False) + "\n"); n += 1
    return n
