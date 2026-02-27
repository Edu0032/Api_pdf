# app/bases/sinapi/composicoes_parser.py
import io
import re
import unicodedata
import difflib
from typing import Dict, Any, Optional, Tuple, List

import pdfplumber

from app.core.schemas import Composicoes, BlocoComposicao, LinhaComposicao, LinhaInsumo

_RE_SPACES = re.compile(r"\s+")
_RE_ITEM = re.compile(r"^\d+(?:\.\d+)*$")  # 1.2.3 etc


# -----------------------------
# Helpers básicos
# -----------------------------
def limpar(x: Any) -> str:
    if x is None:
        return ""
    return str(x).replace("\r", "\n").strip()


def colapsar_espacos(s: str) -> str:
    return _RE_SPACES.sub(" ", s.replace("\n", " ")).strip()


def _strip_accents_upper(s: str) -> str:
    s = colapsar_espacos(s)
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).upper()


def _norm_code(s: str) -> str:
    return _strip_accents_upper(s).replace(" ", "")


def _norm_bank(s: str) -> str:
    return _strip_accents_upper(s).replace(" ", "")


def _prefix_match(a: str, b: str, *, max_missing: int = 4, min_len: int = 5) -> bool:
    """
    True se um é prefixo do outro e diferença de tamanho <= max_missing.
    """
    a = _norm_code(a)
    b = _norm_code(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) < min_len:
        return False
    if abs(len(a) - len(b)) > max_missing:
        return False
    return a.startswith(b) or b.startswith(a)


def parse_pt_number(s: str) -> Optional[float]:
    s = colapsar_espacos(s)
    if not s:
        return None
    s2 = s.replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except ValueError:
        return None


def split_codigo_banco_embutido(codigo_raw: str) -> Tuple[str, str]:
    """
    "00000367/ SINAPI" -> ("00000367", "SINAPI")
    """
    codigo_raw = colapsar_espacos(codigo_raw)
    if "/" not in codigo_raw:
        return codigo_raw, ""
    a, b = codigo_raw.split("/", 1)
    return a.strip(), colapsar_espacos(b)


def to_pdf_index(page_num: int, page_indexing: str) -> int:
    if page_indexing.lower().startswith("1"):
        return page_num - 1
    return page_num


def row_get(row: List[Any], idx: int) -> str:
    if idx >= len(row):
        return ""
    return limpar(row[idx])


def normalize_row(row: List[Any]) -> List[Any]:
    """
    Alguns PDFs quebram "Composição" em duas colunas:
      ["Composiçã", "o", ...]
    """
    if not row or len(row) < 2:
        return row
    c0 = colapsar_espacos(limpar(row[0])).lower()
    c1 = colapsar_espacos(limpar(row[1])).lower()

    if c0.startswith("compos") and (c1 == "o" or c1.startswith("o aux")):
        row = list(row)
        row[0] = f"{limpar(row[0])} {limpar(row[1])}"
        del row[1]
        return row

    return row


def is_item_header_row(row: List[Any]) -> bool:
    """
    Header típico do bloco:
      ["1.1.1", "Código", "Banco", ...]
    """
    if not row or len(row) < 2:
        return False
    c0 = colapsar_espacos(limpar(row[0]))
    c1 = colapsar_espacos(limpar(row[1])).lower()
    return bool(_RE_ITEM.match(c0)) and (c1.startswith("cód") or c1.startswith("cod"))


def is_column_header_only(row: List[Any]) -> bool:
    """
    Continuação de página:
      ["", "Código", "Banco", ...]
    """
    if not row or len(row) < 2:
        return False
    c0 = colapsar_espacos(limpar(row[0]))
    c1 = colapsar_espacos(limpar(row[1])).lower()
    return (c0 == "") and (c1.startswith("cód") or c1.startswith("cod"))


# -----------------------------
# Detectores
# -----------------------------
def detect_row_type(c0_raw: str) -> Optional[str]:
    c0 = colapsar_espacos(c0_raw).lower()
    if not c0:
        return None

    # remove "o" colado no começo
    c0 = re.sub(r"^o(?=compos|insumo)", "", c0).strip()

    letters = re.sub(r"[^a-zà-úç]", "", c0)
    letters = _strip_accents_upper(letters).lower()

    if "insumo" in letters[:12] or letters.startswith("insu"):
        return "insumo"

    if "compos" in letters[:14] or letters.startswith("comp"):
        if "auxiliar" in c0 or "auxiliar" in letters:
            return "composicao_auxiliar"
        return "composicao"

    head = letters[:10]
    if difflib.SequenceMatcher(None, head[:6], "insumo").ratio() >= 0.60:
        return "insumo"
    if difflib.SequenceMatcher(None, head[:9], "composicao").ratio() >= 0.60:
        if "auxiliar" in c0 or "auxiliar" in letters:
            return "composicao_auxiliar"
        return "composicao"

    return None


def build_entry_from_row(row: List[Any], row_type: str) -> Dict[str, Any]:
    codigo_raw = row_get(row, 1)
    banco_col = colapsar_espacos(row_get(row, 2))

    codigo = colapsar_espacos(codigo_raw)
    banco_embutido = ""

    if row_type == "insumo":
        codigo_split, banco_split = split_codigo_banco_embutido(codigo_raw)
        if banco_split:
            codigo = codigo_split
            banco_embutido = banco_split

    banco_final = banco_embutido or banco_col

    return {
        "codigo": codigo,
        "banco": banco_final,
        "banco_coluna": banco_col,  # debug
        "descricao": colapsar_espacos(row_get(row, 3)),
        "tipo": colapsar_espacos(row_get(row, 4)),
        "und": colapsar_espacos(row_get(row, 5)),
        "quant": parse_pt_number(row_get(row, 6)),
        "valor_unit": parse_pt_number(row_get(row, 7)),
        "total": parse_pt_number(row_get(row, 8)),
    }


def _make_id(codigo: str, banco: str) -> str:
    return f"{colapsar_espacos(codigo)}|{colapsar_espacos(banco)}"


def _make_id_norm(codigo: str, banco: str) -> str:
    return f"{_norm_code(codigo)}|{_norm_bank(banco)}"


def _split_id(raw_id: str) -> Tuple[str, str]:
    raw_id = colapsar_espacos(raw_id)
    if "|" not in raw_id:
        return raw_id, ""
    c, b = raw_id.split("|", 1)
    return c.strip(), b.strip()


def _choose_best_prefix_candidate(expected_code: str, candidates: List[str]) -> Optional[str]:
    """
    Melhor candidato:
      - menor diff de tamanho
      - depois o mais completo (maior len)
    Empate real => None (evita falso positivo).
    """
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


def _close(a: Optional[float], b: Optional[float], *, abs_tol: float = 0.02, rel_tol: float = 0.002) -> bool:
    if a is None or b is None:
        return True  # sem dado, não bloqueia
    tol = max(abs_tol, abs(a) * rel_tol)
    return abs(a - b) <= tol


# -----------------------------
# Parser principal
# -----------------------------
def parse_composicoes_sinapi(
    pdf_bytes: bytes,
    start_1based: int,
    end_1based: int,
    config: Dict[str, Any],
    itens_plano: Optional[List[Dict[str, Any]]] = None,
    item_refs: Optional[List[Dict[str, Any]]] = None,
    context: Any = None,
) -> Tuple[Composicoes, List[str], List[str], List[str], List[str]]:
    avisos: List[str] = []
    erros: List[str] = []

    page_indexing = config.get("page_indexing", "1-based")

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        erros.append(f"Falha ao abrir PDF (pdfplumber): {e}")
        return Composicoes(principais={}, auxiliares_globais={}, aliases_auxiliares={}), avisos, erros, [], []

    with pdf:
        n_pages = len(pdf.pages)

        if page_indexing.lower().startswith("1") and (start_1based <= 0 or end_1based <= 0):
            avisos.append(
                f"[composicoes] Range suspeito (start/end <= 0) com page_indexing=1-based: {start_1based}-{end_1based}. "
                "Vou tratar como 0-based para evitar offset duplo."
            )
            start_idx = start_1based
            end_idx = end_1based
        else:
            start_idx = to_pdf_index(start_1based, page_indexing)
            end_idx = to_pdf_index(end_1based, page_indexing)

        start_idx = max(0, min(start_idx, n_pages - 1))
        end_idx = max(0, min(end_idx, n_pages - 1))
        if end_idx < start_idx:
            start_idx, end_idx = end_idx, start_idx

        # -----------------------------
        # Índice do orçamento (verdade)
        # -----------------------------
        expected_by_bank: Dict[str, List[Dict[str, Any]]] = {}
        id_to_item: Dict[str, str] = {}

        if item_refs:
            for r in item_refs:
                rid = str(r.get("ref_id") or "").strip()
                it = str(r.get("item") or "").strip()
                if not rid or "|" not in rid:
                    continue
                c, b = _split_id(rid)
                bn = _norm_bank(b)
                expected_by_bank.setdefault(bn, []).append({
                    "code": c,
                    "rid": rid,
                    "item": it,
                    "sem_bdi": r.get("sem_bdi"),
                    "com_bdi": r.get("com_bdi"),
                })
                if it:
                    id_to_item[_make_id_norm(c, b)] = it

        recovered_codes: Dict[str, str] = {}

        def recover_full_code(code_raw: str, bank_raw: str) -> Optional[str]:
            bn = _norm_bank(bank_raw)
            exp = expected_by_bank.get(bn) or []
            if not exp:
                return None
            candidates = [e["code"] for e in exp if _prefix_match(e["code"], code_raw, max_missing=4, min_len=5)]
            return _choose_best_prefix_candidate(code_raw, candidates)

        principais: Dict[str, BlocoComposicao] = {}
        auxiliares_globais: Dict[str, LinhaComposicao] = {}

        current_item = ""
        current_bloco: Optional[BlocoComposicao] = None

        table_settings_lines = {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 3,
        }
        table_settings_text = {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
        }

        for pi in range(start_idx, end_idx + 1):
            page = pdf.pages[pi]

            tables = page.extract_tables(table_settings=table_settings_lines) or []
            if not tables:
                tables = page.extract_tables(table_settings=table_settings_text) or []

            for tb in tables:
                for raw_row in tb:
                    if not raw_row:
                        continue

                    if all(colapsar_espacos(limpar(c)) == "" for c in raw_row if c is not None):
                        continue

                    row = normalize_row(list(raw_row))

                    if is_item_header_row(row):
                        if current_bloco is not None:
                            pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
                            principais[pid] = current_bloco
                            current_bloco = None
                        current_item = colapsar_espacos(row_get(row, 0))
                        continue

                    if is_column_header_only(row):
                        continue

                    c0 = row_get(row, 0)
                    c0_flat = colapsar_espacos(c0)
                    if "ANEXO 3" in c0_flat.upper():
                        continue

                    rtype = detect_row_type(c0)
                    if rtype is None:
                        continue

                    entry = build_entry_from_row(row, rtype)

                    if rtype == "composicao":
                        if current_bloco is not None:
                            pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
                            principais[pid] = current_bloco

                        # RECUPERAÇÃO DE CÓDIGO TRUNCADO
                        recovered_full = recover_full_code(entry["codigo"], entry["banco"])
                        if recovered_full and _norm_code(recovered_full) != _norm_code(entry["codigo"]):
                            key = _make_id(entry["codigo"], entry["banco"])
                            if key not in recovered_codes:
                                recovered_codes[key] = recovered_full
                                avisos.append(
                                    f"[composicoes] código truncado recuperado: '{entry['codigo']}' -> '{recovered_full}' (banco={entry['banco']})"
                                )
                            entry["codigo"] = recovered_full

                        pid_norm = _make_id_norm(entry["codigo"], entry["banco"])
                        item_final = current_item or id_to_item.get(pid_norm, "")

                        principal = LinhaComposicao(
                            codigo=entry["codigo"],
                            banco=entry["banco"],
                            descricao=entry["descricao"],
                            tipo=entry["tipo"],
                            und=entry["und"],
                            quant=entry["quant"],
                            valor_unit=entry["valor_unit"],
                            total=entry["total"],
                            banco_coluna=entry.get("banco_coluna", ""),
                        )

                        current_bloco = BlocoComposicao(
                            item=item_final,
                            principal=principal,
                            composicoes_auxiliares=[],
                            insumos=[],
                        )

                    else:
                        if current_bloco is None:
                            continue

                        if rtype == "composicao_auxiliar":
                            aux = LinhaComposicao(
                                codigo=entry["codigo"],
                                banco=entry["banco"],
                                descricao=entry["descricao"],
                                tipo=entry["tipo"],
                                und=entry["und"],
                                quant=entry["quant"],
                                valor_unit=entry["valor_unit"],
                                total=entry["total"],
                                banco_coluna=entry.get("banco_coluna", ""),
                            )
                            current_bloco.composicoes_auxiliares.append(aux)
                            auxiliares_globais[_make_id(aux.codigo, aux.banco)] = aux

                        elif rtype == "insumo":
                            ins = LinhaInsumo(
                                codigo=entry["codigo"],
                                banco=entry["banco"],
                                descricao=entry["descricao"],
                                tipo=entry["tipo"],
                                und=entry["und"],
                                quant=entry["quant"],
                                valor_unit=entry["valor_unit"],
                                total=entry["total"],
                                banco_coluna=entry.get("banco_coluna", ""),
                            )
                            current_bloco.insumos.append(ins)

        if current_bloco is not None:
            pid = _make_id(current_bloco.principal.codigo, current_bloco.principal.banco)
            principais[pid] = current_bloco

        # -----------------------------
        # Aliases para auxiliares (ex: 883164|SINAPI -> 88316|SINAPI)
        # -----------------------------
        aliases_aux: Dict[str, str] = {}
        principais_by_bank: Dict[str, List[str]] = {}
        for rid in principais.keys():
            c, b = _split_id(rid)
            principais_by_bank.setdefault(_norm_bank(b), []).append(c)

        for aux_id, aux in auxiliares_globais.items():
            if aux_id in principais:
                continue
            bn = _norm_bank(aux.banco)
            cands = [pc for pc in (principais_by_bank.get(bn) or []) if _prefix_match(pc, aux.codigo, max_missing=1, min_len=5)]
            best = _choose_best_prefix_candidate(aux.codigo, cands)
            if best:
                alias_to = f"{best}|{aux.banco}"
                aliases_aux[aux_id] = alias_to

        comp = Composicoes(principais=principais, auxiliares_globais=auxiliares_globais, aliases_auxiliares=aliases_aux)

        avisos.append(
            f"[composicoes] processadas páginas {start_idx+1}-{end_idx+1}; "
            f"principais={len(comp.principais)}; auxiliares_globais={len(comp.auxiliares_globais)}; aliases={len(comp.aliases_auxiliares)}"
        )

        # -----------------------------
        # Validação faltando/extras com tolerância
        # -----------------------------
        itens_faltando: List[str] = []
        itens_extras: List[str] = []

        if item_refs:
            expected_norm: Dict[str, Dict[str, Any]] = {}
            expected_by_bank_codes: Dict[str, List[Dict[str, Any]]] = {}

            for r in item_refs:
                rid = str(r.get("ref_id") or "").strip()
                if not rid or "|" not in rid:
                    continue
                c, b = _split_id(rid)

                # ignore placeholders
                if _norm_code(c) == "COMPOSICAO":
                    continue
                # ignore insumo disfarçado (mantém coerente com parser.py)
                if b.upper().strip() == "SINAPI" and c.isdigit() and c.startswith("0000"):
                    continue

                k = _make_id_norm(c, b)
                expected_norm[k] = {
                    "rid": rid,
                    "code": c,
                    "bank": b,
                    "sem_bdi": r.get("sem_bdi"),
                    "com_bdi": r.get("com_bdi"),
                }
                expected_by_bank_codes.setdefault(_norm_bank(b), []).append(expected_norm[k])

            detected_norm_set = set()
            detected_pairs: List[Tuple[str, str, str, str]] = []
            for rid in comp.principais.keys():
                c, b = _split_id(rid)
                n = _make_id_norm(c, b)
                detected_pairs.append((c, b, rid, n))
                detected_norm_set.add(n)

            matched_expected = set()

            # mapa detectado por banco para prefix-match
            detected_by_bank: Dict[str, List[Tuple[str, BlocoComposicao]]] = {}
            for c, b, rid, _n in detected_pairs:
                detected_by_bank.setdefault(_norm_bank(b), []).append((c, comp.principais[rid]))

            # tenta casar expected
            for expk, exp in expected_norm.items():
                if expk in detected_norm_set:
                    matched_expected.add(expk)
                    continue

                bn = _norm_bank(exp["bank"])
                det_list = detected_by_bank.get(bn) or []
                candidates = [(det_code, bloco) for (det_code, bloco) in det_list if _prefix_match(exp["code"], det_code, max_missing=4, min_len=5)]

                # Se tiver mais de um, usa número pra desempatar (valor_unit ~ sem_bdi)
                best_code = None
                if candidates:
                    if len(candidates) == 1:
                        best_code = candidates[0][0]
                    else:
                        filtered = []
                        for det_code, bloco in candidates:
                            vu = bloco.principal.valor_unit
                            if _close(exp.get("sem_bdi"), vu):
                                filtered.append(det_code)
                        best_code = _choose_best_prefix_candidate(exp["code"], filtered or [c[0] for c in candidates])

                if best_code:
                    matched_expected.add(expk)
                    avisos.append(f"[validacao] match por truncamento: esperado '{exp['rid']}' ~ detectado '{best_code}|{exp['bank']}'")

            for expk, exp in expected_norm.items():
                if expk not in matched_expected:
                    itens_faltando.append(exp["rid"])

            # extras: detectado que não casa com nenhum expected (exato ou prefixo)
            expected_by_bank_simple: Dict[str, List[str]] = {}
            for exp in expected_norm.values():
                expected_by_bank_simple.setdefault(_norm_bank(exp["bank"]), []).append(exp["code"])

            for det_code, det_bank, det_rid, det_norm in detected_pairs:
                if det_norm in expected_norm:
                    continue
                bn = _norm_bank(det_bank)
                exp_codes = expected_by_bank_simple.get(bn) or []
                if any(_prefix_match(det_code, exp_c, max_missing=4, min_len=5) for exp_c in exp_codes):
                    continue
                itens_extras.append(det_rid)

        return comp, avisos, erros, itens_faltando, itens_extras