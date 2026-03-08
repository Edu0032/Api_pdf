from __future__ import annotations

import re
from typing import Iterable, List, Optional


def _normalize_space(s: str) -> str:
    return re.sub(r"\s{2,}", " ", (s or "").replace("\u00a0", " ").strip())


def _merge_markers(static: List[str], dynamic: Optional[List[str]]) -> List[str]:
    dyn = [m for m in (dynamic or []) if m and m.strip()]
    merged = list(dict.fromkeys([m for m in (static or []) if m and m.strip()] + dyn))
    merged.sort(key=len, reverse=True)
    return merged


def break_glued_markers(text: str, break_before: List[str], dynamic_markers: Optional[List[str]] = None) -> str:
    if not text:
        return text or ""
    markers = _merge_markers(break_before, dynamic_markers)
    if not markers:
        return text
    pattern = r"(?=(" + "|".join(re.escape(m) for m in markers) + r"))"
    rx = re.compile(pattern)
    return rx.sub("\n", text)


def clean_inline(text: str, strip_inline_from: List[str], dynamic_markers: Optional[List[str]] = None) -> str:
    s = _normalize_space(text)
    markers = _merge_markers(strip_inline_from, dynamic_markers)
    if not s or not markers:
        return s
    cut_idx = None
    for m in markers:
        idx = s.find(m)
        if idx != -1:
            cut_idx = idx if cut_idx is None else min(cut_idx, idx)
    if cut_idx is not None:
        s = s[:cut_idx].rstrip()
    return _normalize_space(s)


def sanitize_lines(
    lines: Iterable[str],
    drop_lines_if_contains: List[str],
    strip_inline_from: List[str],
    dynamic_markers: Optional[List[str]] = None,
) -> List[str]:
    out: List[str] = []
    for ln in lines:
        s = _normalize_space(ln)
        if not s:
            continue
        s = clean_inline(s, strip_inline_from=strip_inline_from, dynamic_markers=dynamic_markers)
        if not s:
            continue
        if drop_lines_if_contains and any(m in s for m in drop_lines_if_contains if m):
            continue
        out.append(s)
    return out


def contains_any(text: str, markers: List[str], dynamic_markers: Optional[List[str]] = None) -> bool:
    s = text or ""
    merged = _merge_markers(markers, dynamic_markers)
    return any(m in s for m in merged)


def is_safe_continuation(prev_text: str, next_line: str, toxic_for_continuation: List[str], dynamic_markers: Optional[List[str]] = None) -> bool:
    s = _normalize_space(next_line)
    if not s:
        return False
    if re.match(r"^\d+(?:\.\d+)*\s+\S+", s):
        return False
    if contains_any(s, toxic_for_continuation, dynamic_markers=dynamic_markers):
        return False
    return True