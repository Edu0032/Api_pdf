from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pdfplumber

from app.core.money import parse_ptbr_number
from app.core.schemas import BlocoComposicao, Composicoes, LinhaComposicao, LinhaInsumo

TABLE_SETTINGS_LINES = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 5,
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 10,
    "text_tolerance": 2,
}
TABLE_SETTINGS_TEXT = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "intersection_tolerance": 5,
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 10,
    "text_tolerance": 2,
}

BANCOS_CANON = {"SINAPI", "PRÓPRIO", "PROPRIO", "ORSE", "SICRO", "SEINFRA", "DNIT"}
RE_NUM = re.compile(r"^\d[\d\.,]*$")
RE_ID_TABELA = re.compile(r"^\d+(?:\.\d+)*$")
RE_CODE_CAND = re.compile(r"^(?=.*\d)[0-9A-Z_]{3,}$")


@dataclass
class RawBlock:
    item: str = ""
    key: str = ""
    principal: Optional[LinhaComposicao] = None
    auxiliares: List[LinhaComposicao] = field(default_factory=list)
    insumos: List[LinhaInsumo] = field(default_factory=list)
    page: int = 0


# -------------------------------
# limpeza / classificação
# -------------------------------
def _clean(txt: Any) -> str:
    if txt is None:
        return ""
    return str(txt).replace("\n", " ").replace("\xa0", " ").strip()


def _norm(txt: Any) -> str:
    return re.sub(r"\s+", " ", _clean(txt)).upper()


def _is_item_id(value: str) -> bool:
    return bool(RE_ID_TABELA.fullmatch(_clean(value)))


def _extract_tables(page) -> List[List[List[str]]]:
    t1 = page.extract_tables(table_settings=TABLE_SETTINGS_LINES) or []
    t2 = page.extract_tables(table_settings=TABLE_SETTINGS_TEXT) or []
    return t1 + t2


def _looks_like_header(row: List[str]) -> bool:
    up = _norm(" ".join(_clean(c) for c in row if c))
    signals = ["CODIGO", "CÓDIGO", "BANCO", "DESCRICAO", "DESCRIÇÃO", "UND", "QUANT", "VALOR", "TOTAL"]
    return sum(1 for s in signals if s in up) >= 5


def _looks_like_comp_table(table: List[List[str]]) -> bool:
    if not table:
        return False
    for r in table[:25]:
        if r and _looks_like_header(r):
            return True
    for r in table[:80]:
        if not r:
            continue
        full = _norm(" ".join(_clean(c) for c in r if c))
        if "COMPOS" in full or "INSUMO" in full:
            return True
    return False


def _row_kind(cells: List[str]) -> str:
    full = _norm(" ".join(cells))
    if "INSUMO" in full:
        return "INSUMO"
    if "AUXILIAR" in full and "COMPOS" in full:
        return "AUXILIAR"
    if "COMPOS" in full:
        return "COMPOSICAO"
    return ""


def _join_bank_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "SINAP" and i + 1 < len(tokens) and tokens[i + 1] == "I":
            out.append("SINAPI")
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _extract_code_bank(cells: List[str]) -> Tuple[str, str]:
    tokens: List[str] = []
    for c in cells:
        t = _norm(c)
        if t:
            tokens.extend(t.split())
    tokens = _join_bank_tokens(tokens)

    bank = ""
    for t in tokens:
        if t in BANCOS_CANON:
            bank = "PRÓPRIO" if t in ("PROPRIO", "PRÓPRIO") else t
            break

    blacklist = {
        "COMPOSIÇÃO",
        "COMPOSICAO",
        "COMPOSIÇÃ",
        "COMPOSIÇ",
        "COMPOSIC",
        "AUXILIAR",
        "INSUMO",
        "CODIGO",
        "CÓDIGO",
        "BANCO",
    }
    code = ""
    for t in tokens:
        if t in blacklist or t == bank:
            continue
        cand = re.sub(r"[^0-9A-Z_]", "", t)
        if RE_CODE_CAND.fullmatch(cand):
            code = cand
            break

    return code, bank


def _extract_tail_values(cells: List[str]) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    tokens: List[str] = []
    for c in cells:
        c = _clean(c)
        if c:
            tokens.extend(c.split())
    tokens = _join_bank_tokens([t.upper() for t in tokens])

    nums: List[str] = []
    for t in reversed(tokens):
        if RE_NUM.fullmatch(t):
            nums.append(t)
            if len(nums) >= 3:
                break

    total = parse_ptbr_number(nums[0]) if len(nums) > 0 else None
    valor_unit = parse_ptbr_number(nums[1]) if len(nums) > 1 else None
    quant = parse_ptbr_number(nums[2]) if len(nums) > 2 else None

    und = ""
    if len(nums) > 2:
        try:
            idx_q = tokens.index(nums[2])
            if idx_q - 1 >= 0:
                cand = tokens[idx_q - 1]
                if re.fullmatch(r"[A-Z/%²³]{1,8}", cand):
                    und = cand.replace("M2", "M²")
        except ValueError:
            pass

    return und, quant, valor_unit, total


def _extract_description(cells: List[str], code: str, bank: str) -> str:
    full = " ".join(cells)
    full = re.sub(r"(?i)\b(composi[cç][aã]o|auxiliar|insumo)\b", " ", full)
    if code:
        full = full.replace(code, " ")
    if bank:
        full = full.replace(bank, " ")
    full = re.sub(r"\s+", " ", full).strip()
    return full


def _find_table_item_id(table: List[List[str]]) -> str:
    for r in table[:40]:
        if not r:
            continue
        c0 = _clean(r[0]) if len(r) > 0 else ""
        if _is_item_id(c0):
            return c0
    return ""


def _find_start_index(table: List[List[str]]) -> int:
    for i, r in enumerate(table[:40]):
        if r and _looks_like_header(r):
            return i + 1
    return 0


def _make_line(cells: List[str]) -> Tuple[LinhaComposicao, str]:
    code, bank = _extract_code_bank(cells)
    und, quant, valor_unit, total = _extract_tail_values(cells)
    desc = _extract_description(cells, code, bank)
    line = LinhaComposicao(
        codigo=code,
        banco=bank,
        descricao=desc,
        tipo="",
        und=und,
        quant=quant,
        valor_unit=valor_unit,
        total=total,
        banco_coluna=bank,
    )
    return line, f"{code}|{bank}" if code and bank else ""


def _make_insumo(cells: List[str]) -> LinhaInsumo:
    line, _ = _make_line(cells)
    return LinhaInsumo(**line.model_dump())


def _merge_line(base: LinhaComposicao, new: LinhaComposicao) -> LinhaComposicao:
    if not base.descricao and new.descricao:
        base.descricao = new.descricao
    if not base.tipo and new.tipo:
        base.tipo = new.tipo
    if not base.und and new.und:
        base.und = new.und
    if base.quant is None and new.quant is not None:
        base.quant = new.quant
    if base.valor_unit is None and new.valor_unit is not None:
        base.valor_unit = new.valor_unit
    if base.total is None and new.total is not None:
        base.total = new.total
    return base


def _dedup_lines(lines: Iterable[LinhaComposicao]) -> List[LinhaComposicao]:
    seen = set()
    out: List[LinhaComposicao] = []
    for line in lines:
        key = (line.codigo, line.banco, line.quant, line.valor_unit, line.total)
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


# -------------------------------
# parser principal das composições
# -------------------------------
def parse_composicoes_sinapi(
    pdf_bytes: bytes,
    start_1based: int,
    end_1based: int,
    config: dict,
    item_refs: List[dict],
    context: dict | None = None,
):
    avisos: List[str] = []
    erros: List[str] = []

    orc_by_codebank: Dict[str, dict] = {}
    orc_by_item: Dict[str, dict] = {}
    for ref in item_refs or []:
        codigo = str(ref.get("codigo", "") or "").strip().upper()
        fonte = str(ref.get("fonte", "") or "").strip()
        item = str(ref.get("item", "") or "").strip()
        if codigo and fonte:
            orc_by_codebank[f"{codigo}|{fonte}"] = ref
        if item:
            orc_by_item[item] = ref

    raw_blocks: Dict[str, RawBlock] = {}
    orphan_aux_globals: Dict[str, LinhaComposicao] = {}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_no in range(max(1, start_1based), min(end_1based, len(pdf.pages)) + 1):
            page = pdf.pages[page_no - 1]
            for table in _extract_tables(page):
                if not _looks_like_comp_table(table):
                    continue

                item_id = _find_table_item_id(table)
                start_idx = _find_start_index(table)
                current: Optional[RawBlock] = None

                for raw in table[start_idx:]:
                    if not raw:
                        continue
                    cells = [_clean(c) for c in raw if c is not None]
                    if not any(cells):
                        continue

                    kind = _row_kind(cells)
                    if not kind:
                        continue

                    line, key = _make_line(cells)
                    if not key:
                        continue

                    if kind == "COMPOSICAO":
                        # cria/mescla bloco bruto
                        block = raw_blocks.get(key)
                        if block is None:
                            block = RawBlock(item=item_id, key=key, principal=line, page=page_no)
                            raw_blocks[key] = block
                        else:
                            block.principal = _merge_line(block.principal or line, line)
                            if not block.item and item_id:
                                block.item = item_id
                        current = block
                    elif kind == "AUXILIAR":
                        if current is None:
                            if key not in orphan_aux_globals:
                                orphan_aux_globals[key] = line
                            else:
                                orphan_aux_globals[key] = _merge_line(orphan_aux_globals[key], line)
                            continue
                        current.auxiliares.append(line)
                    elif kind == "INSUMO":
                        if current is None:
                            continue
                        current.insumos.append(_make_insumo(cells))

    # dedup listas internas
    for block in raw_blocks.values():
        block.auxiliares = _dedup_lines(block.auxiliares)
        block.insumos = [LinhaInsumo(**x.model_dump()) for x in _dedup_lines(block.insumos)]

    # catálogo completo por codigo|banco
    catalog_principals: Dict[str, RawBlock] = raw_blocks
    referenced_aux_keys = {f"{a.codigo}|{a.banco}" for b in raw_blocks.values() for a in b.auxiliares if a.codigo and a.banco}

    principais: Dict[str, BlocoComposicao] = {}
    auxiliares_globais: Dict[str, LinhaComposicao] = {}

    # 1) blocos principais que pertencem ao orçamento
    for key, block in catalog_principals.items():
        ref = orc_by_item.get(block.item) if block.item else None
        if ref is None:
            ref = orc_by_codebank.get(key)
            if ref and not block.item:
                block.item = str(ref.get("item", "") or "")

        if ref is not None or block.item:
            principais[key] = BlocoComposicao(
                item=block.item or str((ref or {}).get("item", "") or ""),
                principal=block.principal,
                composicoes_auxiliares=block.auxiliares,
                insumos=block.insumos,
            )

    # 2) blocos detalhados que não pertencem ao orçamento, mas são referenciados como auxiliares
    for key, block in catalog_principals.items():
        if key in principais:
            continue
        if key in referenced_aux_keys:
            auxiliares_globais[key] = block.principal

    # 3) auxiliares órfãs capturadas fora de bloco
    for key, line in orphan_aux_globals.items():
        auxiliares_globais.setdefault(key, line)

    # 4) aliases genéricos por evidência (código com dígito extra)
    aliases_aux: Dict[str, str] = {}
    all_known_keys = set(auxiliares_globais) | set(principais)
    for ref_key in sorted(referenced_aux_keys):
        if ref_key in all_known_keys:
            continue
        codigo, banco = ref_key.split("|", 1)
        digits = re.sub(r"[^0-9]", "", codigo)
        if len(digits) <= 5:
            continue
        for n in range(len(digits) - 1, 4, -1):
            cand = digits[:n]
            cand_key = f"{cand}|{banco}"
            if cand_key in all_known_keys:
                aliases_aux[ref_key] = cand_key
                break

    # 5) itens faltando = orçamento esperado e não encontrado como principal
    found_principal_keys = set(principais)
    itens_faltando = sorted(k for k in orc_by_codebank if k not in found_principal_keys)

    # 6) itens extras = somente blocos que NÃO estão no orçamento e NÃO são auxiliares detalhadas/referenciadas
    itens_extras = sorted(
        key
        for key in catalog_principals
        if key not in orc_by_codebank and key not in referenced_aux_keys
    )

    comp = Composicoes(
        principais=principais,
        auxiliares_globais=auxiliares_globais,
        aliases_auxiliares=aliases_aux,
    )

    avisos.append(
        f"[composicoes] páginas {start_1based}-{end_1based}; blocos_brutos={len(catalog_principals)}; "
        f"principais={len(principais)}; auxiliares_globais={len(auxiliares_globais)}; aliases={len(aliases_aux)}"
    )
    if itens_extras:
        exemplos = ", ".join(itens_extras[:10])
        avisos.append(f"[composicoes] {len(itens_extras)} item(ns) extra(s) restante(s). Exemplos: {exemplos}")

    return comp, avisos, erros, itens_faltando, itens_extras
