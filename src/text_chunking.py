"""Context-window protection: shrink text to a character budget while keeping the most
information-dense parts (lead paragraphs + paragraphs rich in numbers, names and signal words),
and restoring original order so the LLM still sees a coherent document."""
from __future__ import annotations

import re

SIGNAL = re.compile(
    r"\b(founded|raised|funding|series|employees|team|pricing|price|free|plan|enterprise|"
    r"per month|/mo|launch|announce|hiring|remote|salary|stars|github|model|paper|released)\b", re.I)


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _score(p: str) -> float:
    digits = sum(c.isdigit() for c in p)
    caps = len(re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", p))
    sig = len(SIGNAL.findall(p))
    return (digits * 1.0 + caps * 0.5 + sig * 3.0) / max(len(p), 40) * 100


def _hard_cut(p: str, limit: int) -> str:
    if len(p) <= limit:
        return p
    cut = p[:limit]
    m = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(", "))
    return cut[: m + 1] if m > limit * 0.5 else cut


def smart_truncate(text: str, max_chars: int, head_ratio: float = 0.3) -> str:
    """Guarantee len(result) <= max_chars."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    paras = _paragraphs(text)
    head_budget = int(max_chars * head_ratio)
    chosen: dict[int, str] = {}
    used = 0
    # 1) lead paragraphs (title / summary usually live here)
    for i, p in enumerate(paras):
        if used >= head_budget:
            break
        room = head_budget - used
        piece = _hard_cut(p, room)
        chosen[i] = piece
        used += len(piece) + 1
    # 2) densest remaining paragraphs
    order = sorted((i for i in range(len(paras)) if i not in chosen), key=lambda i: -_score(paras[i]))
    for i in order:
        room = max_chars - used
        if room < 40:
            break
        piece = _hard_cut(paras[i], room - 1)
        chosen[i] = piece
        used += len(piece) + 1
    out = "\n".join(chosen[i] for i in sorted(chosen))
    return out[:max_chars]
