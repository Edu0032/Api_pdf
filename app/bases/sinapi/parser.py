from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Tuple, Optional

from app.core.pdf_text import extract_pages_text, normalize_lines
from app.core.money import parse_ptbr_number
from app.core.sanitizer import (
    break_glued_markers,
    sanitize_lines,
    is_safe_continuation,
    clean_inline,
    contains_any,
)
from app.core.schemas import ParseResponse, OrcamentoSintetico, Composicoes, Validacao
from app.bases.sinapi.composicoes_parser import parse_composicoes_sinapi


_NUM = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?"


# =====================
# ORÇAMENTO (SINTÉTICO)
# =====================
_RE_ITEM_START = re.compile(
    rf"^(?P<item>\d+(?:\.\d+)*)\s+(?P<codigo>[0-9A-Z_]+)\s+(?P<fonte>SINAPI|Próprio)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_RE_ITEM_COMPOSICAO_START = re.compile(
    rf"^(?P<item>\d+(?:\.\d+)*)\s+COMPOSI(?:ÇÃO|CAO)\s+(?P<fonte>SINAPI|Próprio)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)

_RE_ITEM_TAIL = re.compile(
    rf"\s(?P<und>[A-Za-z0-9/%²³]+)\s+(?P<quant>{_NUM})\s+(?P<s_bdi>{_NUM})\s+(?P<c_bdi>{_NUM})\s+(?P<parcial>{_NUM})\s*$"
)

_RE_GROUP_WITH_TOTAL = re.compile(rf"^(?P<item>\d+(?:\.\d+)*)\s+(?P<desc>.+?)\s+(?P<total>{_NUM})\s*$")
_RE_GROUP_NO_TOTAL = re.compile(r"^(?P<item>\d+(?:\.\d+)*)\s+(?P<desc>.+?)\s*$")
_RE_ONLY_NUMBER = re.compile(rf"^(?P<num>{_NUM})$")

_GROUP_BLACKLIST = ("SINAPI", "PRÓPRIO", "COMPOSIÇÃO", "UND", "QUANT", "CUSTO", "BDI", "%")


def _is_probably_insumo_codigo(codigo: str) -> bool:
    """
    Heurística prática SINAPI:
    - Insumos costumam ser numéricos com zeros à esquerda (ex.: 00000370, 00005069)
    - Composições "principais" normalmente não começam com 0000
    """
    s = (codigo or "").strip()
    return s.isdigit() and len(s) >= 6 and s.startswith("0000")


def parse_sinapi(
    pdf_bytes: bytes,
    ranges: Dict[str, Tuple[int, int]],
    config: dict,
    context: dict | None = None,
) -> Dict[str, Any]:
    """
    Parser SINAPI (Orçamento Sintético + Composições Analíticas).

    - ranges["orcamento"] = (ini, fim) 1-based
    - ranges["composicoes"] = (ini, fim) 1-based (ou (0,0) para pular)
    """
    context = context or {}

    avisos: List[str] = []
    erros: List[str] = []
    divergencias: List[dict] = []

    # ===== ORÇAMENTO =====
    o_ini, o_fim = ranges.get("orcamento", (0, 0))
    if o_ini and o_fim and o_ini >= 1 and o_fim >= o_ini:
        pages_text = extract_pages_text(pdf_bytes, o_ini, o_fim)
        orc, a, e, d = _parse_orcamento_sintetico(pages_text, config=config, context=context)
        avisos.extend(a)
        erros.extend(e)
        divergencias.extend(d)
    else:
        orc = OrcamentoSintetico(itens_raiz=[], itens_plano=[])
        avisos.append("Orçamento: intervalo de páginas inválido -> orçamento não processado.")

    # refs para validar/associar composições (inclui números úteis)
    item_refs_list = _collect_item_refs(orc.itens_raiz)

    # Avisar e remover "insumo citado no orçamento"
    # detectar insumos citados como item de orçamento (ex.: 00000370)
    insumos_no_orc = []

    def _walk_nodes(nodes):
        for n in nodes or []:
            yield n
            yield from _walk_nodes(getattr(n, "filhos", None) or (n.get("filhos") if isinstance(n, dict) else []) or [])

    for n in _walk_nodes(orc.itens_raiz):
        tipo = (getattr(n, "tipo", None) if not isinstance(n, dict) else n.get("tipo")) or ""
        if str(tipo).lower() != "item":
            continue
        codigo = (getattr(n, "codigo", None) if not isinstance(n, dict) else n.get("codigo")) or ""
        fonte = (getattr(n, "fonte", None) if not isinstance(n, dict) else n.get("fonte")) or ""
        item = (getattr(n, "item", None) if not isinstance(n, dict) else n.get("item")) or ""
        if _is_probably_insumo_codigo(str(codigo), str(fonte)):
            insumos_no_orc.append(f"{codigo}|{fonte} (item {item})")

    for rid in sorted(set(insumos_no_orc)):
        avisos.append(f"Insumo citado no orçamento como item de composição: {rid}. Revisar planilha/PDF.")

    # Também remover placeholders do tipo COMPOSICAO
    placeholders = [r for r in item_refs_list if str(r.get("codigo", "")).strip().upper() == "COMPOSICAO"]
    if placeholders:
        exemplos = ", ".join([f"item {r.get('item')}" for r in placeholders[:10]])
        avisos.append(
            f"[orcamento] {len(placeholders)} item(ns) com código ausente/quebrado (COMPOSICAO placeholder). Exemplos: {exemplos}"
        )
        item_refs_list = [r for r in item_refs_list if r not in placeholders]

    # ===== COMPOSIÇÕES =====
    comp = Composicoes(principais={}, auxiliares_globais={}, aliases_auxiliares={})
    itens_faltando: List[str] = []
    itens_extras: List[str] = []

    c_ini, c_fim = ranges.get("composicoes", (0, 0))

    if not (c_ini and c_fim and c_ini >= 1 and c_fim >= c_ini):
        avisos.append(
            "Composições: não processadas (composicoes_inicio/fim inválidos ou 0). "
            "Dica: se você quer composições no JSON, envie um intervalo 1-based válido."
        )
    else:
        comp, comp_avisos, comp_erros, itens_faltando, itens_extras = parse_composicoes_sinapi(
            pdf_bytes=pdf_bytes,
            start_1based=c_ini,
            end_1based=c_fim,
            config=config,
            item_refs=item_refs_list,
            context=context,
        )
        avisos.extend(comp_avisos)
        erros.extend(comp_erros)

        avisos.append(
            f"Composições: processadas páginas {c_ini}-{c_fim}; "
            f"principais={len(comp.principais)}; auxiliares_globais={len(comp.auxiliares_globais)}; aliases={len(comp.aliases_auxiliares)}."
        )

    resp = ParseResponse(
        base_id="sinapi",
        orcamento_sintetico=orc,
        composicoes=comp,
        validacao=Validacao(
            itens_faltando=itens_faltando,
            itens_extras=itens_extras,
            avisos=avisos,
            erros=erros,
            divergencias=divergencias,
        ),
    )
    return resp.model_dump()


# --------------------
# ORÇAMENTO: helpers
# --------------------
def _build_dynamic_markers(context: dict) -> List[str]:
    def deaccent(s: str) -> str:
        s = unicodedata.normalize("NFD", s)
        return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

    out: List[str] = []
    for k in ("obra_nome", "obra_localizacao"):
        v = context.get(k)
        if not v:
            continue
        s = str(v).strip()
        if not s:
            continue
        out.append(s)
        out.append(s.replace(" ", ""))
        out.append(deaccent(s))
        out.append(deaccent(s).replace(" ", ""))
    # unique
    seen = set()
    uniq = []
    for m in out:
        if m not in seen:
            uniq.append(m)
            seen.add(m)
    return uniq


def _parse_orcamento_sintetico(
    pages_text: List[str],
    config: dict,
    context: dict,
) -> tuple[OrcamentoSintetico, List[str], List[str], List[dict]]:
    syn = config.get("synthetic", {})
    san = config.get("sanitizer", {})
    val = config.get("validation", {})

    ignore_markers = set(syn.get("ignore_markers", []))
    header_markers = set(syn.get("header_markers", []))

    break_before = san.get("break_before", [])
    strip_inline_from = san.get("strip_inline_from", [])
    drop_lines_if_contains = san.get("drop_lines_if_contains", [])
    toxic_for_continuation = san.get("toxic_for_continuation", [])

    missing_total_value = val.get("missing_group_total_value", "")
    allow_missing_group_total = bool(val.get("allow_missing_group_total", True))
    fail_if_contaminated_text = bool(val.get("fail_if_contaminated_text", True))

    # NOVO: controlar se você quer registrar divergências mesmo quando bate
    report_all_group_checks = bool(val.get("report_all_group_checks", False))

    tol_item_abs = float(val.get("tolerances", {}).get("item_abs", 0.02))
    tol_item_rel = float(val.get("tolerances", {}).get("item_rel", 0.0002))
    tol_group_abs = float(val.get("tolerances", {}).get("group_abs", 0.05))
    tol_group_rel = float(val.get("tolerances", {}).get("group_rel", 0.0001))

    dynamic_markers = _build_dynamic_markers(context)

    raw_lines: List[str] = []
    for page_text in pages_text:
        fixed = break_glued_markers(page_text, break_before=break_before, dynamic_markers=dynamic_markers)
        for ln in normalize_lines(fixed):
            if any(m in ln for m in ignore_markers):
                continue
            if ln in header_markers:
                continue
            if ln.startswith("CUSTO UNITÁRIO") or ln.startswith("ITEM CÓDIGO") or ln.startswith("S/"):
                continue
            raw_lines.append(ln)

    raw_lines = sanitize_lines(
        raw_lines,
        drop_lines_if_contains=drop_lines_if_contains,
        strip_inline_from=strip_inline_from,
        dynamic_markers=dynamic_markers,
    )

    started = False
    lines: List[str] = []
    for ln in raw_lines:
        if not started:
            mg = _RE_GROUP_WITH_TOTAL.match(ln)
            if mg and _is_probable_group_heading(mg.group("desc")):
                started = True
                lines.append(ln)
        else:
            if "TOTAL SEM BDI" in ln or "TOTAL COM BDI" in ln:
                break
            lines.append(ln)

    avisos: List[str] = []
    erros: List[str] = []
    divergencias: List[dict] = []

    if not lines:
        erros.append("Nenhuma linha do orçamento sintético foi detectada no intervalo informado.")
        return OrcamentoSintetico(itens_raiz=[], itens_plano=[]), avisos, erros, divergencias

    root = {"tipo": "raiz", "filhos": []}
    stack: List[tuple[int, dict]] = [(0, root)]
    itens_plano: List[str] = []
    last_item_node: Optional[dict] = None
    buf_item: List[str] = []

    def normalize_group_total(v: str) -> str:
        return (v or "").strip()

    def push_node(node: dict):
        nonlocal last_item_node
        level = node["item"].count(".") + 1
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1]
        parent.setdefault("filhos", []).append(node)
        stack.append((level, node))
        if node.get("tipo") == "item":
            last_item_node = node

    def _try_parse_composicao_item_row(line: str) -> Optional[dict]:
        m = _RE_ITEM_COMPOSICAO_START.match(line)
        if not m:
            return None

        item = m.group("item").strip()
        fonte = m.group("fonte").strip()
        rest = m.group("rest").strip()

        mt = _RE_ITEM_TAIL.search(line)
        if not mt:
            return None

        und = mt.group("und").strip()
        quant = mt.group("quant").strip()
        s_bdi = mt.group("s_bdi").strip()
        c_bdi = mt.group("c_bdi").strip()
        parcial = mt.group("parcial").strip()

        especificacao = _RE_ITEM_TAIL.sub("", rest).strip()
        especificacao = clean_inline(especificacao, strip_inline_from, dynamic_markers=dynamic_markers)

        return {
            "tipo": "item",
            "item": item,
            "codigo": "COMPOSICAO",
            "fonte": fonte,
            "especificacao": especificacao,
            "und": und,
            "quant": quant,
            "custo_unitario_sem_bdi": s_bdi,
            "custo_unitario_com_bdi": c_bdi,
            "custo_parcial": parcial,
        }

    def try_finalize_item(buffer_lines: List[str], lookahead_lines: List[str]) -> tuple[Optional[dict], int]:
        max_extra = min(2, len(lookahead_lines))
        cur = list(buffer_lines)

        for extra_used in range(0, max_extra + 1):
            text = " ".join(cur)

            parsed = _try_parse_item_row(text, strip_inline_from=strip_inline_from, dynamic_markers=dynamic_markers)
            if not parsed:
                parsed = _try_parse_composicao_item_row(buffer_lines[0])
                if parsed:
                    avisos.append(
                        f"Item {parsed.get('item')} com irregularidade: linha indica COMPOSIÇÃO com código quebrado/ausente. "
                        "Mantido como codigo='COMPOSICAO'."
                    )

            if parsed:
                ok, reason = _validate_item_math(
                    parsed,
                    tol_abs=tol_item_abs,
                    tol_rel=tol_item_rel,
                    fail_if_contaminated_text=fail_if_contaminated_text,
                    toxic_markers=strip_inline_from,
                    dynamic_markers=dynamic_markers,
                )
                if ok:
                    return parsed, extra_used

                divergencias.append({
                    "tipo": "item",
                    "item": parsed.get("item"),
                    "quant": parsed.get("quant"),
                    "custo_unitario_com_bdi": parsed.get("custo_unitario_com_bdi"),
                    "custo_parcial": parsed.get("custo_parcial"),
                    "motivo": reason,
                })

                if extra_used < max_extra and not _looks_like_new_row(lookahead_lines[extra_used]):
                    cur.append(lookahead_lines[extra_used])
                    continue

                erros.append(f"Item {parsed.get('item')} falhou validação: {reason}")
                return parsed, extra_used

            if extra_used < max_extra and not _looks_like_new_row(lookahead_lines[extra_used]):
                cur.append(lookahead_lines[extra_used])
                continue

            return None, extra_used

        return None, 0

    i = 0
    while i < len(lines):
        ln = lines[i]

        if buf_item:
            if _looks_like_new_row(ln):
                parsed, used = try_finalize_item(buf_item, lines[i:i+3])
                buf_item = []
                if parsed:
                    push_node(parsed)
                    itens_plano.append(parsed["item"])
                    i += used
                continue

            buf_item.append(ln)
            if _RE_ITEM_TAIL.search(" ".join(buf_item)):
                parsed, used = try_finalize_item(buf_item, lines[i+1:i+3])
                buf_item = []
                if parsed:
                    push_node(parsed)
                    itens_plano.append(parsed["item"])
                    i += used
            i += 1
            continue

        if _RE_ITEM_START.match(ln) or _RE_ITEM_COMPOSICAO_START.match(ln):
            buf_item = [ln]
            if _RE_ITEM_TAIL.search(ln):
                parsed, used = try_finalize_item(buf_item, lines[i + 1:i + 3])
                buf_item = []
                if parsed:
                    push_node(parsed)
                    itens_plano.append(parsed["item"])
                    i += used
            i += 1
            continue

        mg = _RE_GROUP_WITH_TOTAL.match(ln)
        if mg:
            item = mg.group("item").strip()
            desc = mg.group("desc").strip()
            total = mg.group("total").strip()

            if not _is_probable_group_heading(desc):
                if last_item_node and is_safe_continuation(
                    last_item_node.get("especificacao", ""),
                    ln,
                    toxic_for_continuation,
                    dynamic_markers=dynamic_markers,
                ):
                    last_item_node["especificacao"] = (
                        last_item_node.get("especificacao", "")
                        + " "
                        + clean_inline(ln, strip_inline_from, dynamic_markers=dynamic_markers)
                    ).strip()
                else:
                    avisos.append(f"Grupo suspeito incluído (revisar): {ln[:180]}")
                    tipo = "meta" if "." not in item else "submeta"
                    push_node({"tipo": tipo, "item": item, "descricao": desc, "custo_total": normalize_group_total(total), "filhos": []})
                i += 1
                continue

            tipo = "meta" if "." not in item else "submeta"
            push_node({"tipo": tipo, "item": item, "descricao": desc, "custo_total": normalize_group_total(total), "filhos": []})
            i += 1
            continue

        mg2 = _RE_GROUP_NO_TOTAL.match(ln)
        if mg2 and _is_probable_group_heading(mg2.group("desc")):
            item = mg2.group("item").strip()
            desc = mg2.group("desc").strip()

            total = ""
            if i + 1 < len(lines):
                mn = _RE_ONLY_NUMBER.match(lines[i + 1])
                if mn:
                    total = mn.group("num").strip()
                    i += 1

            if not total:
                if allow_missing_group_total:
                    total = missing_total_value
                    avisos.append(f"Grupo {item} sem CUSTO TOTAL no documento -> vazio.")
                else:
                    erros.append(f"Grupo {item} sem CUSTO TOTAL e allow_missing_group_total=false.")
                    total = missing_total_value

            tipo = "meta" if "." not in item else "submeta"
            push_node({"tipo": tipo, "item": item, "descricao": desc, "custo_total": normalize_group_total(total), "filhos": []})
            i += 1
            continue

        if last_item_node and is_safe_continuation(
            last_item_node.get("especificacao", ""),
            ln,
            toxic_for_continuation,
            dynamic_markers=dynamic_markers,
        ):
            last_item_node["especificacao"] = (
                last_item_node.get("especificacao", "")
                + " "
                + clean_inline(ln, strip_inline_from, dynamic_markers=dynamic_markers)
            ).strip()
            i += 1
            continue

        avisos.append(f"Linha ignorada (não casou com item/grupo): {ln[:180]}")
        i += 1

    if buf_item:
        parsed, _used = try_finalize_item(buf_item, [])
        if parsed:
            push_node(parsed)
            itens_plano.append(parsed["item"])
        buf_item = []

    _validate_tree_math(
        root.get("filhos", []),
        avisos=avisos,
        erros=erros,
        divergencias=divergencias,
        tol_abs=tol_group_abs,
        tol_rel=tol_group_rel,
        missing_total_value=missing_total_value,
        report_all=report_all_group_checks,
    )

    return OrcamentoSintetico(itens_raiz=root["filhos"], itens_plano=itens_plano), avisos, erros, divergencias


def _try_parse_item_row(text: str, strip_inline_from: List[str], dynamic_markers: List[str]) -> Optional[dict]:
    m = _RE_ITEM_START.match(text)
    if not m:
        return None

    item = m.group("item").strip()
    codigo = m.group("codigo").strip()
    fonte = m.group("fonte").strip()
    rest = m.group("rest").strip()

    mt = _RE_ITEM_TAIL.search(text)
    if not mt:
        return None

    und = mt.group("und").strip()
    quant = mt.group("quant").strip()
    s_bdi = mt.group("s_bdi").strip()
    c_bdi = mt.group("c_bdi").strip()
    parcial = mt.group("parcial").strip()

    especificacao = _RE_ITEM_TAIL.sub("", rest).strip()
    especificacao = clean_inline(especificacao, strip_inline_from, dynamic_markers=dynamic_markers)

    return {
        "tipo": "item",
        "item": item,
        "codigo": codigo,
        "fonte": fonte,
        "especificacao": especificacao,
        "und": und,
        "quant": quant,
        "custo_unitario_sem_bdi": s_bdi,
        "custo_unitario_com_bdi": c_bdi,
        "custo_parcial": parcial,
    }


def _validate_item_math(
    item_node: dict,
    tol_abs: float,
    tol_rel: float,
    fail_if_contaminated_text: bool,
    toxic_markers: List[str],
    dynamic_markers: List[str],
) -> tuple[bool, str]:
    espec = item_node.get("especificacao") or ""
    if fail_if_contaminated_text and toxic_markers and contains_any(espec, toxic_markers, dynamic_markers=dynamic_markers):
        return False, "especificacao contaminada (marcadores detectados)"

    q = parse_ptbr_number(item_node.get("quant", ""))
    u = parse_ptbr_number(item_node.get("custo_unitario_com_bdi", ""))
    p = parse_ptbr_number(item_node.get("custo_parcial", ""))

    if q is None or u is None or p is None:
        return True, "campos numéricos não parseáveis (ok)"

    expected = q * u
    tol = max(tol_abs, abs(expected) * tol_rel)

    if abs(expected - p) <= tol:
        return True, "ok"

    return False, f"parcial {p:.2f} != quant*unit {expected:.2f} (tol {tol:.2f})"


def _validate_tree_math(
    nodes: List[dict],
    avisos: List[str],
    erros: List[str],
    divergencias: List[dict],
    tol_abs: float,
    tol_rel: float,
    missing_total_value: str,
    report_all: bool,
) -> float:
    total_sum = 0.0
    for node in nodes:
        if node.get("tipo") == "item":
            v = parse_ptbr_number(node.get("custo_parcial", "")) or 0.0
            total_sum += v
        else:
            child_sum = _validate_tree_math(
                node.get("filhos", []),
                avisos,
                erros,
                divergencias,
                tol_abs,
                tol_rel,
                missing_total_value,
                report_all,
            )
            total_sum += child_sum

            ct_raw = (node.get("custo_total") or "").strip()
            if not ct_raw or ct_raw == missing_total_value:
                continue

            ct = parse_ptbr_number(ct_raw)
            if ct is None:
                avisos.append(f"custo_total não numérico em {node.get('item')}: '{ct_raw}'")
                continue

            tol = max(tol_abs, abs(ct) * tol_rel)
            diff = child_sum - ct

            if report_all or abs(diff) > tol:
                divergencias.append({
                    "tipo": "grupo",
                    "item": node.get("item"),
                    "custo_total": ct_raw,
                    "soma_filhos": f"{child_sum:.2f}",
                    "diferenca": f"{diff:.2f}",
                    "tolerancia": f"{tol:.2f}",
                })

            if abs(diff) > tol:
                erros.append(
                    f"Divergência matemática no grupo {node.get('item')} — soma_filhos={child_sum:.2f} vs custo_total={ct:.2f} (tol={tol:.2f})"
                )

    return total_sum


def _looks_like_new_row(ln: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)*\s+", ln))


def _is_probable_group_heading(desc: str) -> bool:
    up = desc.upper().strip()
    if len(up) < 3:
        return False
    for w in _GROUP_BLACKLIST:
        if w in up:
            return False
    nums = re.findall(rf"{_NUM}", desc)
    if len(nums) >= 2:
        return False
    return True


from typing import Any, Dict, List

def _node_get(n: Any, key: str, default: Any = "") -> Any:
    if isinstance(n, dict):
        return n.get(key, default)
    return getattr(n, key, default)

def _node_children(n: Any) -> List[Any]:
    ch = _node_get(n, "filhos", []) or []
    return list(ch)

def _is_probably_insumo_codigo(codigo: str, banco: str) -> bool:
    c = (codigo or "").strip()
    b = (banco or "").strip().upper()
    return b == "SINAPI" and c.isdigit() and c.startswith("0000")

def _collect_item_refs(itens_raiz) -> List[Dict[str, str]]:
    """
    Retorna lista de refs:
      [{"item":"9.4", "ref_id":"CODIGO|BANCO"}, ...]
    Ignora:
      - placeholder "COMPOSICAO"
      - códigos que parecem INSUMO (ex.: 0000xxxx) — esses devem virar AVISO, não referência de composição
    """
    refs: List[Dict[str, str]] = []

    def walk(nodes):
        for n in nodes or []:
            tipo = str(_node_get(n, "tipo", "") or "").strip().lower()

            if tipo == "item":
                item = str(_node_get(n, "item", "") or "").strip()
                codigo = str(_node_get(n, "codigo", "") or "").strip()
                banco = str(_node_get(n, "fonte", "") or "").strip()  # orçamento usa "fonte"

                if item and codigo and banco:
                    if codigo.strip().upper() != "COMPOSICAO" and not _is_probably_insumo_codigo(codigo, banco):
                        refs.append({"item": item, "ref_id": f"{codigo}|{banco}"})

            walk(_node_children(n))

    walk(itens_raiz)
    return refs