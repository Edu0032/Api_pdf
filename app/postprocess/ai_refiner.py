from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

from app.core.schemas import Composicoes, BlocoComposicao


def _norm(s: str) -> str:
    return "".join((s or "").upper().split())


def _split_ref_id(ref_id: str) -> Tuple[str, str]:
    if "|" not in ref_id:
        return ref_id.strip(), ""
    a, b = ref_id.split("|", 1)
    return a.strip(), b.strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _choose_best_expected(code: str, bank: str, expected_by_bank: Dict[str, List[str]]) -> Tuple[str, float]:
    candidates = expected_by_bank.get(_norm(bank), [])
    if not candidates:
        return "", 0.0

    scored = [(cand, _similarity(code, cand)) for cand in candidates]
    scored.sort(key=lambda t: t[1], reverse=True)

    best_code, best_score = scored[0]
    if len(scored) > 1 and abs(scored[0][1] - scored[1][1]) < 0.03:
        return "", best_score
    return best_code, best_score


def _score_bloco(bloco: BlocoComposicao, has_expected_bank: bool, best_score: float) -> float:
    score = 1.0

    if not bloco.principal.codigo:
        score -= 0.45
    if not bloco.principal.banco:
        score -= 0.30
    if not bloco.principal.descricao:
        score -= 0.15

    if not has_expected_bank:
        score -= 0.20
    elif best_score < 0.65:
        score -= 0.30
    elif best_score < 0.80:
        score -= 0.12

    if len(bloco.insumos) == 0:
        score -= 0.08

    return max(0.0, min(1.0, score))


def refine_composicoes_with_ai(
    comp: Composicoes,
    item_refs: List[Dict[str, Any]] | None,
    *,
    min_confidence: float = 0.68,
    min_correction_similarity: float = 0.84,
) -> tuple[Composicoes, List[str]]:
    """
    Refino heurístico inspirado em IA para blocos com baixa confiança.
    - Calcula confidence score por bloco.
    - Quando confiança baixa, tenta corrigir código principal por similaridade com referências do orçamento.
    """
    if not item_refs:
        return comp, []

    expected_by_bank: Dict[str, List[str]] = {}
    for r in item_refs:
        code, bank = _split_ref_id(str(r.get("ref_id") or ""))
        if code and bank:
            expected_by_bank.setdefault(_norm(bank), []).append(code)

    avisos: List[str] = []
    novos_principais: Dict[str, BlocoComposicao] = {}

    for rid, bloco in comp.principais.items():
        code = bloco.principal.codigo
        bank = bloco.principal.banco

        best_code, best_score = _choose_best_expected(code, bank, expected_by_bank)
        has_expected_bank = _norm(bank) in expected_by_bank
        score = _score_bloco(bloco, has_expected_bank, best_score)

        final_code = code
        if score < min_confidence and best_code and best_score >= min_correction_similarity and _norm(best_code) != _norm(code):
            final_code = best_code
            bloco.principal.codigo = best_code
            avisos.append(
                f"[ai_refiner] Código principal ajustado por baixa confiança: '{code}' -> '{best_code}' "
                f"(banco={bank}, score={score:.2f}, sim={best_score:.2f})"
            )

        avisos.append(
            f"[ai_refiner] Bloco {final_code}|{bank} confidence={score:.2f} "
            f"(insumos={len(bloco.insumos)}, auxiliares={len(bloco.composicoes_auxiliares)})"
        )
        novos_principais[f"{final_code}|{bank}"] = bloco

    comp.principais = novos_principais
    return comp, avisos
