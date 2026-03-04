from __future__ import annotations

import io
import re
import unicodedata
from typing import Dict, Any, Optional, Tuple, List

import pdfplumber

from app.core.schemas import Composicoes, BlocoComposicao, LinhaComposicao, LinhaInsumo

_RE_SPACES = re.compile(r"\s+")
_NUM = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?"
_RE_NUM = re.compile(rf"^{_NUM}$")

# Heurística de cabeçalho de composição (SICRO costuma ter "CODIGO - DESCRIÇÃO")
_RE_COMP_HEADER = re.compile(r"^(?P<codigo>[0-9A-Z_\-\/\.]{4,})\s*-\s*(?P<desc>.+)$", re.IGNORECASE)
_RE_ITEM_PREFIX = re.compile(r"^(?P<item>\d+(?:\.\d+)*)\s+(?P<rest>.+)$")


def colapsar(s: str) -> str:
    return _RE_SPACES.sub(" ", (s or "").replace("\n", " ")).strip()


def _strip_accents_upper(s: str) -> str:
    s = colapsar(s)
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).upper()


def _norm_code(s: str) -> str:
    return _strip_accents_upper(s).replace(" ", "")


def _norm_bank(s: str) -> str:
    return _strip_accents_upper(s).replace(" ", "")


def parse_pt_number(s: str) -> Optional[float]:
    s = colapsar(s)
    if not s:
        return None
    s2 = s.replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except ValueError:
        return None


def to_pdf_index(page_num: int, page_indexing: str) -> int:
    return page_num - 1 if str(page_indexing).lower().startswith("1") else page_num


def _split_id(raw_id: str) -> Tuple[str, str]:
    raw_id = colapsar(raw_id)
    if "|" not in raw_id:
        return raw_id, ""
    c, b = raw_id.split("|", 1)
    return c.strip(), b.strip()


def _make_id(codigo: str, banco: str) -> str:
    return f"{colapsar(codigo)}|{colapsar(banco)}"


def _make_id_norm(codigo: str, banco: str) -> str:
    return f"{_norm_code(codigo)}|{_norm_bank(banco)}"


def _prefix_match(a: str, b: str, *, max_missing: int = 4, min_len: int = 5) -> bool:
    a = _norm_code(a)
    b = _norm_code(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) < min_len:
        return False
    if abs(len(a) - len(b)) > max_missing:
        return False
    return a.startswith(b) or b.startswith(a)


def _choose_best_prefix_candidate(expected_code: str, candidates: List[str]) -> Optional[str]:
    if not candidates:
        return None
    expn = _norm_code(expected_code)
    scored = []
    for cand in candidates:
        cn = _norm_code(cand)
        diff = abs(len(expn) - len(cn))
        scored.append((diff, -max(len(expn), len(cn)), cand))
    scored.sort()
    best = scored[0]
    if len(scored) > 1 and scored[1][:2] == best[:2]:
        return None
    return best[2]


def _is_section(line_up: str, sections_up: set[str]) -> Optional[str]:
    l = line_up.strip()
    if l in sections_up:
        return l
    # tolera "MÃO DE OBRA:" etc
    l2 = l.rstrip(":").strip()
    if l2 in sections_up:
        return l2
    return None


def _parse_tabular_like_line(line: str) -> Optional[dict]:
    """
    Pega linha parecida com tabela:
      COD  DESCRICAO ... UND  CONSUMO  VU  CU
    A gente extrai números do final (2 ou 3), e tenta detectar UND antes deles.
    """
    txt = colapsar(line)
    if len(txt) < 8:
        return None

    tokens = txt.split()
    if len(tokens) < 4:
        return None

    nums: List[str] = []
    while tokens and _RE_NUM.match(tokens[-1]):
        nums.append(tokens.pop())
        if len(nums) >= 3:
            break
    nums = list(reversed(nums))

    if len(nums) < 2:
        return None

    total = parse_pt_number(nums[-1])
    valor_unit = parse_pt_number(nums[-2])
    quant = parse_pt_number(nums[-3]) if len(nums) >= 3 else None

    und = ""
    if tokens and re.match(r"^[A-Za-z0-9/%²³]{1,8}$", tokens[-1]):
        und = tokens.pop()

    if not tokens:
        return None

    codigo = tokens.pop(0)
    descricao = " ".join(tokens).strip()

    if not descricao:
        return None

    return {
        "codigo": codigo,
        "descricao": descricao,
        "und": und,
        "quant": quant,
        "valor_unit": valor_unit,
        "total": total,
    }


def parse_composicoes_sicro(
    pdf_bytes: bytes,
    start_1based: int,
    end_1based: int,
    config: Dict[str, Any],
    item_refs: Optional[List[Dict[str, Any]]] = None,
    context: Any = None,
) -> Tuple[Composicoes, List[str], List[str], List[str], List[str]]:
    avisos: List[str] = []
    erros: List[str] = []

    page_indexing = config.get("page_indexing", "1-based")
    base_bank = "SICRO"

    sections_cfg = config.get("compositions", {}).get("sections") or []
    sections_up = {_strip_accents_upper(s) for s in sections_cfg} if sections_cfg else {
        "MATERIAIS", "SERVICOS", "SERVIÇOS", "MAO DE OBRA", "MÃO DE OBRA", "EQUIPAMENTOS", "TRANSPORTE", "OUTROS"
    }
    # normaliza o set (sem acento)
    sections_up = {_strip_accents_upper(s) for s in sections_up}

    # index orçamento
    expected_by_bank: Dict[str, List[str]] = {}
    id_to_item: Dict[str, str] = {}
    if item_refs:
        for r in item_refs:
            rid = str(r.get("ref_id") or "").strip()
            it = str(r.get("item") or "").strip()
            if not rid or "|" not in rid:
                continue
            c, b = _split_id(rid)
            bn = _norm_bank(b)
            expected_by_bank.setdefault(bn, []).append(c)
            id_to_item[_make_id_norm(c, b)] = it

    def recover_full_code(code_raw: str, bank_raw: str) -> Optional[str]:
        bn = _norm_bank(bank_raw)
        exp_codes = expected_by_bank.get(bn) or []
        if not exp_codes:
            return None
        candidates = [c for c in exp_codes if _prefix_match(c, code_raw, max_missing=4, min_len=5)]
        return _choose_best_prefix_candidate(code_raw, candidates)

    principais: Dict[str, BlocoComposicao] = {}
    auxiliares_globais: Dict[str, LinhaComposicao] = {}

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        erros.append(f"Falha ao abrir PDF (pdfplumber): {e}")
        return Composicoes(principais={}, auxiliares_globais={}, aliases_auxiliares={}), avisos, erros, [], []

    with pdf:
        n_pages = len(pdf.pages)
        s = to_pdf_index(start_1based, page_indexing)
        e = to_pdf_index(end_1based, page_indexing)
        s = max(0, min(s, n_pages - 1))
        e = max(0, min(e, n_pages - 1))
        if e < s:
            s, e = e, s

        current_item = ""
        current_section = ""
        current_bloco: Optional[BlocoComposicao] = None

        recovered_warned: set[str] = set()

        for pi in range(s, e + 1):
            text = pdf.pages[pi].extract_text() or ""
            lines = [colapsar(x) for x in (text.splitlines() if text else [])]
            lines = [ln for ln in lines if ln]

            for ln in lines:
                up = _strip_accents_upper(ln)

                # 1) detecta seção
                sec = _is_section(up, sections_up)
                if sec:
                    current_section = sec
                    continue

                # 2) detecta cabeçalho com ITEM + CODIGO - DESC
                item_m = _RE_ITEM_PREFIX.match(ln)
                if item_m:
                    maybe_item = item_m.group("item").strip()
                    rest = item_m.group("rest").strip()
                    m2 = _RE_COMP_HEADER.match(rest)
                    if m2:
                        # fecha bloco anterior
                        if current_bloco is not None:
                            pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
                            principais[pid] = current_bloco

                        current_item = maybe_item
                        codigo = colapsar(m2.group("codigo"))
                        desc = colapsar(m2.group("desc"))

                        # tenta recuperar truncamento pelo orçamento
                        rec = recover_full_code(codigo, base_bank)
                        if rec and _norm_code(rec) != _norm_code(codigo):
                            k = f"{codigo}|{base_bank}"
                            if k not in recovered_warned:
                                recovered_warned.add(k)
                                avisos.append(f"[sicro] código truncado recuperado: '{codigo}' -> '{rec}'")
                            codigo = rec

                        pid_norm = _make_id_norm(codigo, base_bank)
                        item_final = current_item or id_to_item.get(pid_norm, "")

                        principal = LinhaComposicao(
                            codigo=codigo,
                            banco=base_bank,
                            descricao=desc,
                            tipo="COMPOSICAO",
                            und="",
                            quant=None,
                            valor_unit=None,
                            total=None,
                            banco_coluna="",
                        )
                        current_bloco = BlocoComposicao(
                            item=item_final,
                            principal=principal,
                            composicoes_auxiliares=[],
                            insumos=[],
                        )
                        current_section = ""
                        continue

                # 3) detecta cabeçalho sem ITEM: CODIGO - DESC
                m = _RE_COMP_HEADER.match(ln)
                if m:
                    # fecha bloco anterior
                    if current_bloco is not None:
                        pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
                        principais[pid] = current_bloco

                    codigo = colapsar(m.group("codigo"))
                    desc = colapsar(m.group("desc"))

                    rec = recover_full_code(codigo, base_bank)
                    if rec and _norm_code(rec) != _norm_code(codigo):
                        k = f"{codigo}|{base_bank}"
                        if k not in recovered_warned:
                            recovered_warned.add(k)
                            avisos.append(f"[sicro] código truncado recuperado: '{codigo}' -> '{rec}'")
                        codigo = rec

                    pid_norm = _make_id_norm(codigo, base_bank)
                    item_final = id_to_item.get(pid_norm, "")

                    principal = LinhaComposicao(
                        codigo=codigo,
                        banco=base_bank,
                        descricao=desc,
                        tipo="COMPOSICAO",
                        und="",
                        quant=None,
                        valor_unit=None,
                        total=None,
                        banco_coluna="",
                    )
                    current_bloco = BlocoComposicao(
                        item=item_final,
                        principal=principal,
                        composicoes_auxiliares=[],
                        insumos=[],
                    )
                    current_item = item_final
                    current_section = ""
                    continue

                # 4) linhas tabulares (insumos/serviços etc)
                if current_bloco is None:
                    continue

                row = _parse_tabular_like_line(ln)
                if not row:
                    continue

                tipo = current_section or "ITEM"
                ins = LinhaInsumo(
                    codigo=row["codigo"],
                    banco=base_bank,
                    descricao=row["descricao"],
                    tipo=tipo,
                    und=row["und"],
                    quant=row["quant"],
                    valor_unit=row["valor_unit"],
                    total=row["total"],
                    banco_coluna="",
                )
                current_bloco.insumos.append(ins)

        # fecha último
        if current_bloco is not None:
            pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
            principais[pid] = current_bloco

        comp = Composicoes(principais=principais, auxiliares_globais=auxiliares_globais, aliases_auxiliares={})
        avisos.append(f"[sicro] processadas páginas {s+1}-{e+1}; principais={len(comp.principais)}")

        # ===== validação: faltando/extras com tolerância a truncamento =====
        itens_faltando: List[str] = []
        itens_extras: List[str] = []

        if item_refs:
            expected_norm: Dict[str, str] = {}
            expected_raw: List[str] = []

            for r in item_refs:
                rid = str(r.get("ref_id") or "").strip()
                if not rid or "|" not in rid:
                    continue
                c, b = _split_id(rid)
                expected_raw.append(rid)
                expected_norm[_make_id_norm(c, b)] = rid

            detected_raw = list(comp.principais.keys())
            detected_pairs = []
            detected_norm_set = set()
            for rid in detected_raw:
                c, b = _split_id(rid)
                n = _make_id_norm(c, b)
                detected_pairs.append((c, b, rid, n))
                detected_norm_set.add(n)

            matched_expected_norm = set()

            detected_by_bank: Dict[str, List[Tuple[str, str]]] = {}
            for c, b, rid, _n in detected_pairs:
                detected_by_bank.setdefault(_norm_bank(b), []).append((c, rid))

            for exp_norm, exp_rid in expected_norm.items():
                if exp_norm in detected_norm_set:
                    matched_expected_norm.add(exp_norm)
                    continue

                exp_code, exp_bank = _split_id(exp_rid)
                bn = _norm_bank(exp_bank)
                det_list = detected_by_bank.get(bn) or []
                candidates = [det_code for (det_code, _det_rid) in det_list if _prefix_match(exp_code, det_code)]
                best = _choose_best_prefix_candidate(exp_code, candidates)
                if best:
                    matched_expected_norm.add(exp_norm)
                    avisos.append(f"[sicro][validacao] match por truncamento: '{exp_rid}' ~ '{best}|{exp_bank}'")

            for exp_norm, exp_rid in expected_norm.items():
                if exp_norm not in matched_expected_norm:
                    itens_faltando.append(exp_rid)

            expected_by_bank_norm: Dict[str, List[str]] = {}
            for rid in expected_raw:
                c, b = _split_id(rid)
                expected_by_bank_norm.setdefault(_norm_bank(b), []).append(c)

            for det_code, det_bank, det_rid, det_norm in detected_pairs:
                if det_norm in expected_norm:
                    continue
                bn = _norm_bank(det_bank)
                exp_codes = expected_by_bank_norm.get(bn) or []
                if any(_prefix_match(det_code, exp_c) for exp_c in exp_codes):
                    continue
                itens_extras.append(det_rid)

        return comp, avisos, erros, itens_faltando, itens_extras