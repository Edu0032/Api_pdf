from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9/%\.\s]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _best_match(header_cell: str, aliases: Dict[str, List[str]], min_similarity: float) -> Optional[str]:
    h = _norm(header_cell)
    if not h:
        return None

    # exact / substring
    for canonical, opts in aliases.items():
        for opt in opts:
            o = _norm(opt)
            if not o:
                continue
            if h == o or o in h or h in o:
                return canonical

    # fuzzy
    best_key = None
    best_score = 0.0
    for canonical, opts in aliases.items():
        for opt in opts:
            o = _norm(opt)
            if not o:
                continue
            score = SequenceMatcher(None, h, o).ratio()
            if score > best_score:
                best_key, best_score = canonical, score

    if best_score >= float(min_similarity):
        return best_key
    return None


def resolve_header_map(
    header_cells: List[str],
    aliases: Dict[str, List[str]],
    required: List[str],
    min_similarity: float = 0.88,
) -> Tuple[Dict[str, int], List[str]]:
    mapping: Dict[str, int] = {}
    for idx, cell in enumerate(header_cells):
        key = _best_match(cell, aliases=aliases, min_similarity=min_similarity)
        if key and key not in mapping:
            mapping[key] = idx
    missing = [k for k in required if k not in mapping]
    return mapping, missing
