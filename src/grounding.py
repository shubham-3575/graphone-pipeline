"""Anti-hallucination guard. Any LLM-extracted value must be literally supported by the fetched
source text, otherwise it is nulled (optional field) or the record is rejected (required field)."""
from __future__ import annotations

import re
from typing import Any

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", str(s).casefold())).strip()


def is_grounded(value: Any, source_text: str, _norm_source: str | None = None) -> bool:
    src = _norm_source if _norm_source is not None else norm(source_text)
    if value is None:
        return True
    if isinstance(value, bool):
        return True  # booleans are judged by the extractor + evidence, not by string match
    if isinstance(value, (int, float)):
        raw = str(int(value)) if float(value).is_integer() else str(value)
        digits_src = re.sub(r"(?<=\d)[,_](?=\d)", "", source_text)
        return re.search(rf"(?<!\d){re.escape(raw)}(?!\d)", digits_src) is not None or \
            _k_form(value, source_text)
    if isinstance(value, (list, tuple)):
        return all(is_grounded(v, source_text, src) for v in value)
    v = norm(value)
    return bool(v) and v in src


def _k_form(value: float, text: str) -> bool:
    """Accept '1.2k' / '12k' style abbreviations for stars/employee counts."""
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*[kK]\b", text):
        if abs(float(m.group(1)) * 1000 - float(value)) < 1:
            return True
    return False


def ground_fields(data: dict, source_text: str, fields: list[str], required: list[str] | None = None):
    """Return (clean_data, ungrounded_field_names). Raises ValueError if a required field is ungrounded."""
    required = required or []
    src = norm(source_text)
    clean, bad = dict(data), []
    for f in fields:
        if f in clean and clean[f] is not None and not is_grounded(clean[f], source_text, src):
            bad.append(f)
            clean[f] = None
    missing = [f for f in required if clean.get(f) in (None, "", [])]
    if missing:
        raise ValueError(f"required fields missing/ungrounded: {missing}")
    return clean, bad
