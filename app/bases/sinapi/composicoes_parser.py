from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pdfplumber
from pypdf import PdfReader

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
RE_ITEM_HEADER_TEXT = re.compile(r"^(?P<item>\d+(?:\.\d+)*)\s+C[ÓO]DIGO(?:\s*BANCO|BANCO)", re.IGNORECASE)
RE_ROW_START_TEXT = re.compile(r"^(COMPOSI[ÇC][AÃ]O?|INSUMO)\b", re.IGNORECASE)
RE_SPLIT_ROW_START_TEXT = re.compile(r"(?=(?:Composi[çc][aã]o?|Insumo))", re.IGNORECASE)
RE_TAIL_VALUES_TEXT = re.compile(
    r"(?P<und>[A-Za-z0-9/%²³]+)\s+(?P<quant>\d[\d\.,]*)\s+(?P<valor>\d[\d\.,]*)\s+(?P<total>\d[\d\.,]*)\s*$"
)


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


def _canon_bank(bank: str) -> str:
    up = _norm(bank)
    return "PRÓPRIO" if up in ("PROPRIO", "PRÓPRIO") else up


def _is_strong_code_candidate(raw: str) -> bool:
    raw = _clean(raw)
    if not raw:
        return False

    # quantidades quebradas como 0,0003025 não são códigos
    if ("," in raw or "." in raw) and parse_ptbr_number(raw) is not None:
        return False

    cand = re.sub(r"[^0-9A-Z_]", "", raw.upper())
    if not RE_CODE_CAND.fullmatch(cand):
        return False
    if cand in {"COMPOSICAO", "COMPOSICAOAUXILIAR", "INSUMO"}:
        return False
    return True


def _extract_code_bank(cells: List[str]) -> Tuple[str, str]:
    # caminho preferencial: colunas explícitas Código/Banco
    if len(cells) >= 3:
        raw_code = _clean(cells[1])
        raw_bank = _clean(cells[2])
        bank_norm = _canon_bank(raw_bank)
        if bank_norm in BANCOS_CANON and _is_strong_code_candidate(raw_code):
            code = re.sub(r"[^0-9A-Z_]", "", raw_code.upper())
            return code, bank_norm

    tokens: List[str] = []
    early_cells = cells[:4] if cells else []
    for c in early_cells:
        t = _norm(c)
        if t:
            tokens.extend(t.split())
    tokens = _join_bank_tokens(tokens)

    bank = ""
    for t in tokens:
        canon = _canon_bank(t)
        if canon in BANCOS_CANON:
            bank = canon
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
    for cell in early_cells:
        raw = _clean(cell)
        if not raw:
            continue
        for part in raw.split():
            part_up = part.upper()
            if part_up in blacklist or _canon_bank(part_up) == bank:
                continue
            if _is_strong_code_candidate(part):
                code = re.sub(r"[^0-9A-Z_]", "", part_up)
                break
        if code:
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


def _looks_like_noise_text_line(line: str) -> bool:
    up = _norm(line)
    return up.startswith(
        (
            "ANEXO ",
            "ESTADO ",
            "SECRETARIA ",
            "REVITALIZA",
            "OBJETO:",
            "MUNICÍPIO:",
            "ENDEREÇO:",
            "DATA:",
            "ENC. SOCIAIS",
        )
    )


def _split_text_segments(line: str) -> List[str]:
    parts = re.split(RE_SPLIT_ROW_START_TEXT, _clean(line))
    return [p.strip() for p in parts if p and p.strip()]


def _parse_text_row(start_text: str, continuations: List[str]) -> Optional[Tuple[str, LinhaComposicao]]:
    start_text = _clean(start_text)
    continuations = [_clean(c) for c in continuations if _clean(c)]
    if not start_text:
        return None

    assembled = start_text
    consumed = 0
    while consumed < len(continuations):
        has_tail = RE_TAIL_VALUES_TEXT.search(assembled) is not None
        has_bank = any(_canon_bank(tok) in BANCOS_CANON for tok in _join_bank_tokens(_norm(assembled).split()))
        if has_tail and has_bank:
            break
        assembled = _clean(f"{assembled} {continuations[consumed]}")
        consumed += 1
    remaining_conts = continuations[consumed:]

    kind = "INSUMO" if assembled.upper().startswith("INSUMO") else "COMPOSICAO"
    if any("AUXILIAR" in _norm(c) for c in continuations) or "AUXILIAR" in _norm(assembled):
        kind = "AUXILIAR"

    m = re.match(r"^(Insumo|Composi[çc][aã]o?)\s*(.*)$", assembled, re.IGNORECASE)
    rest = _clean(m.group(2) if m else assembled)
    tokens = _join_bank_tokens(rest.upper().split())

    bank = ""
    bank_token = ""
    bank_idx = -1
    for i, token in enumerate(tokens):
        canon = _canon_bank(token)
        if canon in BANCOS_CANON:
            bank = canon
            bank_token = token
            bank_idx = i
            break

    code = ""
    if bank_idx > 0:
        candidate = tokens[bank_idx - 1]
        if _is_strong_code_candidate(candidate):
            code = re.sub(r"[^0-9A-Z_]", "", candidate.upper())
    elif bank_idx == 0:
        for cont in remaining_conts:
            m_code = re.match(r"^o\s+([0-9A-Z_]+)\b(.*)$", cont, re.IGNORECASE)
            if not m_code:
                continue
            candidate = m_code.group(1)
            if candidate.upper() == "AUXILIAR":
                continue
            if _is_strong_code_candidate(candidate):
                code = re.sub(r"[^0-9A-Z_]", "", candidate.upper())
                break

    und = ""
    quant = valor_unit = total = None
    tail_match = RE_TAIL_VALUES_TEXT.search(assembled)
    prefix = assembled
    if tail_match:
        und = tail_match.group("und")
        quant = parse_ptbr_number(tail_match.group("quant"))
        valor_unit = parse_ptbr_number(tail_match.group("valor"))
        total = parse_ptbr_number(tail_match.group("total"))
        prefix = assembled[: tail_match.start()].strip()

    prefix = re.sub(r"^(Insumo|Composi[çc][aã]o?)\s*", "", prefix, flags=re.IGNORECASE).strip()
    if bank_token:
        prefix = re.sub(rf"^(?:{re.escape(code)}\s+)?{re.escape(bank_token)}\s*", "", prefix, count=1, flags=re.IGNORECASE).strip()
    elif code:
        prefix = re.sub(rf"^{re.escape(code)}\s*", "", prefix, count=1, flags=re.IGNORECASE).strip()

    extras: List[str] = []
    for cont in remaining_conts:
        part = cont
        part = re.sub(r"^o\s+Auxiliar\b", "", part, flags=re.IGNORECASE).strip()
        part = re.sub(r"^Auxiliar\b", "", part, flags=re.IGNORECASE).strip()
        if code:
            part = re.sub(rf"^o\s+{re.escape(code)}\b", "", part, count=1, flags=re.IGNORECASE).strip()
        else:
            part = re.sub(r"^o\b", "", part, count=1, flags=re.IGNORECASE).strip()
        if not part:
            continue
        up = _norm(part)
        if up.startswith(("MO SEM", "LS =>", "VALOR DO", "BDI =>")):
            continue
        extras.append(part)

    descricao = re.sub(r"\s+", " ", " ".join([prefix] + extras)).strip()
    line = LinhaComposicao(
        codigo=code,
        banco=bank,
        descricao=descricao,
        tipo="",
        und=und,
        quant=quant,
        valor_unit=valor_unit,
        total=total,
        banco_coluna=bank,
    )
    return kind, line


def _extract_blocks_from_text(pdf_bytes: bytes, start_1based: int, end_1based: int) -> Dict[str, RawBlock]:
    blocks: Dict[str, RawBlock] = {}
    current_item = ""
    current_block: Optional[RawBlock] = None
    current_row_start: str = ""
    current_conts: List[str] = []
    current_page = start_1based

    def flush_row() -> None:
        nonlocal current_row_start, current_conts, current_block
        if not current_row_start:
            return
        parsed = _parse_text_row(current_row_start, current_conts)
        current_row_start = ""
        current_conts = []
        if not parsed:
            return
        kind, line = parsed
        if current_block is None:
            current_block = RawBlock(item=current_item, page=current_page)
        if kind == "INSUMO":
            if line.codigo and line.banco:
                current_block.insumos.append(LinhaInsumo(**line.model_dump()))
            return
        if kind == "AUXILIAR":
            if line.codigo and line.banco:
                current_block.auxiliares.append(line)
            return
        if line.codigo and line.banco:
            if current_block.principal is None:
                current_block.principal = line
                current_block.key = f"{line.codigo}|{line.banco}"
            else:
                current_block.auxiliares.append(line)

    def flush_block() -> None:
        nonlocal current_block
        flush_row()
        if current_block and current_block.principal and current_block.key:
            current_block.auxiliares = _dedup_lines(current_block.auxiliares)
            current_block.insumos = [LinhaInsumo(**x.model_dump()) for x in _dedup_lines(current_block.insumos)]
            blocks[current_block.key] = current_block
        current_block = None

    reader = PdfReader(io.BytesIO(pdf_bytes))
    max_page = min(end_1based, len(reader.pages))
    for page_no in range(max(1, start_1based), max_page + 1):
        current_page = page_no
        page_text = reader.pages[page_no - 1].extract_text() or ""
        for raw_line in page_text.splitlines():
                line = _clean(raw_line)
                if not line:
                    continue

                m_item = RE_ITEM_HEADER_TEXT.match(_norm(line))
                if m_item:
                    flush_block()
                    current_item = m_item.group("item")
                    current_block = RawBlock(item=current_item, page=page_no)
                    continue

                if _looks_like_noise_text_line(line):
                    continue

                if _norm(line).startswith(("MO SEM", "LS =>", "VALOR DO", "BDI =>")):
                    continue

                segments = _split_text_segments(line)
                if not segments:
                    continue

                for seg in segments:
                    if RE_ROW_START_TEXT.match(seg):
                        flush_row()
                        current_row_start = seg
                        current_conts = []
                    elif current_row_start:
                        current_conts.append(seg)

    flush_block()
    return blocks


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
        fonte = _canon_bank(str(ref.get("fonte", "") or "").strip())
        item = str(ref.get("item", "") or "").strip()
        ref_id = str(ref.get("ref_id", "") or "").strip()

        if (not codigo or not fonte) and ref_id and "|" in ref_id:
            codigo_ref, fonte_ref = ref_id.split("|", 1)
            codigo = codigo or codigo_ref.strip().upper()
            fonte = fonte or _canon_bank(fonte_ref.strip())

        if codigo and fonte:
            normalized_ref = dict(ref)
            normalized_ref.setdefault("codigo", codigo)
            normalized_ref.setdefault("fonte", fonte)
            normalized_ref.setdefault("ref_id", f"{codigo}|{fonte}")
            orc_by_codebank[f"{codigo}|{fonte}"] = normalized_ref
            if item:
                orc_by_item[item] = normalized_ref
        elif item:
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

    # complemento por texto corrido: recupera blocos quando a tabela cortou código ou confundiu quantidade
    text_blocks = _extract_blocks_from_text(pdf_bytes=pdf_bytes, start_1based=start_1based, end_1based=end_1based)
    for key, text_block in text_blocks.items():
        if key not in raw_blocks:
            raw_blocks[key] = text_block
            continue
        base = raw_blocks[key]
        if not base.item and text_block.item:
            base.item = text_block.item
        if base.principal is None and text_block.principal is not None:
            base.principal = text_block.principal
        elif base.principal is not None and text_block.principal is not None:
            base.principal = _merge_line(base.principal, text_block.principal)
        base.auxiliares = _dedup_lines(list(base.auxiliares) + list(text_block.auxiliares))
        base.insumos = [LinhaInsumo(**x.model_dump()) for x in _dedup_lines(list(base.insumos) + list(text_block.insumos))]

    # catálogo completo por codigo|banco
    catalog_principals: Dict[str, RawBlock] = raw_blocks
    referenced_aux_keys = {f"{a.codigo}|{a.banco}" for b in raw_blocks.values() for a in b.auxiliares if a.codigo and a.banco}

    principais: Dict[str, BlocoComposicao] = {}
    auxiliares_globais: Dict[str, LinhaComposicao] = {}

    # 1) blocos principais que pertencem ao orçamento
    for key, block in catalog_principals.items():
        ref = orc_by_codebank.get(key)
        if ref is None and block.item:
            item_ref = orc_by_item.get(block.item)
            if item_ref is not None and str(item_ref.get("ref_id", "") or "") == key:
                ref = item_ref

        if ref is not None:
            block.item = str(ref.get("item", "") or block.item or "")
            principais[key] = BlocoComposicao(
                item=block.item,
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

    # 6) itens extras = somente blocos plausíveis que NÃO estão no orçamento e NÃO são auxiliares detalhadas/referenciadas
    expected_and_found_keys = set(orc_by_codebank) | set(principais) | set(text_blocks) | referenced_aux_keys

    def _looks_like_truncated_extra(key: str, block: RawBlock) -> bool:
        codigo, banco = key.split("|", 1)
        digits = re.sub(r"[^0-9]", "", codigo)
        if digits and digits.startswith("0000"):
            return True
        if digits and len(digits) <= 4:
            return True
        if digits:
            for known in expected_and_found_keys:
                k_code, k_bank = known.split("|", 1)
                if k_bank != banco:
                    continue
                k_digits = re.sub(r"[^0-9]", "", k_code)
                if len(k_digits) > len(digits) and k_digits.startswith(digits):
                    return True
        if block.item:
            item_ref = orc_by_item.get(block.item)
            if item_ref is not None and str(item_ref.get("ref_id", "") or "") != key:
                return True
        if not block.item and key not in text_blocks:
            return True
        return False

    itens_extras = sorted(
        key
        for key, block in catalog_principals.items()
        if key not in orc_by_codebank
        and key not in referenced_aux_keys
        and (key in text_blocks or not _looks_like_truncated_extra(key, block))
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
