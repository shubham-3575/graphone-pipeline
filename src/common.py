"""Shared helpers for pipelines."""
from __future__ import annotations

import re
from pathlib import Path

from src.entity_resolver import EntityResolver
from src.io_utils import read_jsonl


def load_resolver() -> EntityResolver:
    """Seed list + every startup we already collected (so jobs/products resolve to the same canonical names)."""
    R = EntityResolver()
    if Path("output/startups.jsonl").exists():
        for r in read_jsonl("output/startups.jsonl"):
            R.register(r["content"]["entityName"])
    return R


_FAMILIES = [
    ("Data & ML", r"\b(data scien\w+|machine learning|ml\b|ai\b|a\.i\.|llm|nlp|computer vision|research (scientist|engineer)|"
                  r"data (engineer|analyst)|analytics|deep learning|mlops|prompt engineer)"),
    ("Engineering", r"\b(engineer\w*|developer|devops|sre|software|backend|back-end|frontend|front-end|full.?stack|"
                    r"architect|programmer|qa\b|security)"),
    ("Design", r"\b(design\w*|ux|ui\b|creative)"),
    ("Product", r"\b(product (manager|owner|lead)|program manager|project manager|pm\b)"),
    ("Marketing", r"\b(marketing|growth|seo|content|brand|community|social media)"),
    ("Sales", r"\b(sales|account executive|business development|bdr|sdr|partnerships?)"),
    ("Customer Support", r"\b(support|customer (success|service)|helpdesk)"),
    ("People & Recruiting", r"\b(recruit\w*|talent|hr\b|people (ops|partner))"),
    ("Finance & Legal", r"\b(financ\w+|accountant|accounting|legal|counsel|controller)"),
    ("Operations", r"\b(operations?|ops\b|administrat\w+|assistant|coordinator|logistics)"),
]
AI_JOB_RE = re.compile(r"\b(ai|a\.i\.|ml|machine learning|llm|llms|nlp|deep learning|data scien\w+|genai|generative|"
                       r"computer vision|mlops|prompt|agentic)\b", re.I)


def role_family(title: str) -> str:
    t = (title or "").lower()
    for fam, pat in _FAMILIES:
        if re.search(pat, t):
            return fam
    return "Other"
