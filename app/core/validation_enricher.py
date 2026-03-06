from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


_RE_ITEM = re.compile(r"(?P<item>\d+(?:\.\d+)*)")
_RE_REF_ID = re.compile(r"(?P<ref>[0-9A-Z_]+\|[A-ZÀ-Úa-zà-ú]+)")
_RE_GROUP_MISSING_TOTAL = re.compile(r"Grupo (?P<item>\d+(?:\.\d+)*) sem CUSTO TOTAL")
_RE_GROUP_MATH = re.compile(
    r"Divergência matemática no grupo (?P<item>\d+(?:\.\d+)*)"
    r"\s+—\s+soma_filhos=(?P<soma>-?\d+(?:\.\d+)?)"
    r"\s+vs\s+custo_total=(?P<total>-?\d+(?:\.\d+)?)"
    r"\s+\(tol=(?P<tol>-?\d+(?:\.\d+)?)\)"
)
_RE_NUMERIC_TOTAL = re.compile(r"custo_total não numérico em (?P<item>[^:]+): '(?P<valor>.*)'")
_RE_LINE_IGNORED = re.compile(r"Linha ignorada \(não casou com item/grupo\): (?P<linha>.*)")
_RE_SUSPICIOUS_GROUP = re.compile(r"Grupo suspeito incluído \(revisar\): (?P<linha>.*)")
_RE_TRUNCATION_MATCH = re.compile(
    r"\[validacao\] match por truncamento: esperado '(?P<esperado>[^']+)' ~ detectado '(?P<detectado>[^']+)'"
)
_RE_RECOVERED_CODE = re.compile(
    r"\[composicoes\] código truncado recuperado: '(?P<origem>[^']+)' -> '(?P<destino>[^']+)' \(banco=(?P<banco>[^)]+)\)"
)
_RE_COMPOSICOES_SUMMARY = re.compile(
    r"Composições: processadas páginas (?P<ini>\d+)-(?P<fim>\d+); "
    r"principais=(?P<principais>\d+); auxiliares_globais=(?P<aux>\d+); aliases=(?P<aliases>\d+)\."
)
_RE_COMPOSICOES_SUMMARY_INTERNAL = re.compile(
    r"\[composicoes\] processadas páginas (?P<ini>\d+)-(?P<fim>\d+); "
    r"principais=(?P<principais>\d+); auxiliares=(?P<aux>\d+); insumos=(?P<insumos>\d+); "
    r"auxiliares_globais=(?P<aux_g>\d+); aliases=(?P<aliases>\d+)\."
)


def enrich_validation_payload(
    result: Dict[str, Any],
    *,
    base_id: str,
    ranges: Dict[str, Tuple[int, int]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Enriquece a seção `validacao` sem alterar o parser base.

    Objetivo:
    - manter os campos antigos (`avisos`, `erros`, `divergencias`, etc.)
    - adicionar `ocorrencias` estruturadas para o Lovable consumir
    - adicionar `resumo` com totais e agrupamentos simples
    """
    enriched = copy.deepcopy(result or {})
    validacao = enriched.setdefault("validacao", {})

    avisos = _safe_list(validacao.get("avisos"))
    erros = _safe_list(validacao.get("erros"))
    divergencias = _safe_list(validacao.get("divergencias"))
    itens_faltando = _safe_list(validacao.get("itens_faltando"))
    itens_extras = _safe_list(validacao.get("itens_extras"))

    ocorrencias: List[Dict[str, Any]] = []

    for idx, div in enumerate(divergencias):
        ocorrencias.append(_build_divergence_occurrence(div, idx, ranges))

    for idx, msg in enumerate(avisos):
        ocorrencias.append(
            _build_message_occurrence(
                message=msg,
                severity="aviso",
                origin="avisos",
                index=idx,
                ranges=ranges,
                context=context,
            )
        )

    for idx, msg in enumerate(erros):
        ocorrencias.append(
            _build_message_occurrence(
                message=msg,
                severity="erro",
                origin="erros",
                index=idx,
                ranges=ranges,
                context=context,
            )
        )

    for idx, ref_id in enumerate(itens_faltando):
        ocorrencias.append(
            _build_reference_occurrence(
                ref_id=ref_id,
                severity="erro",
                code="COMPOSICAO_FALTANDO_NO_ANEXO",
                category="composicoes",
                message=f"Composição esperada no orçamento não encontrada no anexo: {ref_id}",
                origin="itens_faltando",
                index=idx,
                ranges=ranges,
                cause="O orçamento sintético referencia a composição, mas ela não foi detectada nas páginas de composições informadas.",
                suggestion=(
                    "Verifique o intervalo de páginas do anexo, possíveis truncamentos de código e "
                    "se o PDF contém a composição em outra página."
                ),
            )
        )

    for idx, ref_id in enumerate(itens_extras):
        ocorrencias.append(
            _build_reference_occurrence(
                ref_id=ref_id,
                severity="aviso",
                code="COMPOSICAO_EXTRA_NO_ANEXO",
                category="composicoes",
                message=f"Composição encontrada no anexo sem correspondência direta no orçamento: {ref_id}",
                origin="itens_extras",
                index=idx,
                ranges=ranges,
                cause="O anexo trouxe uma composição que não bateu com as referências coletadas do orçamento sintético.",
                suggestion=(
                    "Confirme se a composição é realmente extra, se há código quebrado no orçamento, "
                    "ou se o documento mistura composições auxiliares fora da lista principal."
                ),
            )
        )

    ocorrencias = _deduplicate_occurrences(ocorrencias)
    validacao["ocorrencias"] = ocorrencias
    validacao["resumo"] = _build_summary(ocorrencias)

    enriched["base_id"] = base_id
    return enriched


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return []


def _build_divergence_occurrence(
    divergence: Any,
    index: int,
    ranges: Dict[str, Tuple[int, int]],
) -> Dict[str, Any]:
    divergence = divergence if isinstance(divergence, dict) else {}
    item = str(divergence.get("item") or "").strip()
    category = "validacao"
    stage = "orcamento"
    page_start, page_end = _get_pages(stage, ranges)

    diff = _to_float(divergence.get("diferenca"))
    tol = _to_float(divergence.get("tolerancia"))
    severity = "erro" if (diff is not None and tol is not None and abs(diff) > tol) else "aviso"

    return {
        "codigo": "VALIDACAO_DIVERGENCIA_GRUPO",
        "severidade": severity,
        "categoria": category,
        "mensagem": (
            f"Divergência matemática detectada no grupo {item or '?'}: "
            f"soma_filhos={divergence.get('soma_filhos')} vs custo_total={divergence.get('custo_total')}"
        ),
        "origem": "divergencias",
        "indice_origem": index,
        "etapa": stage,
        "item": item,
        "ref_id": "",
        "pagina_inicio": page_start,
        "pagina_fim": page_end,
        "linha_original": "",
        "causa": "A soma matemática dos filhos do grupo não bateu com o custo total informado no orçamento sintético.",
        "sugestao": (
            "Recalcule os filhos do grupo, confira arredondamentos e revise se algum item foi "
            "omitido ou classificado no grupo errado."
        ),
        "evidencia": {
            "tipo": divergence.get("tipo"),
            "custo_total": divergence.get("custo_total"),
            "soma_filhos": divergence.get("soma_filhos"),
            "diferenca": divergence.get("diferenca"),
            "tolerancia": divergence.get("tolerancia"),
        },
    }


def _build_message_occurrence(
    *,
    message: str,
    severity: str,
    origin: str,
    index: int,
    ranges: Dict[str, Tuple[int, int]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    message = str(message or "").strip()
    context = context or {}

    default_stage, default_category = _infer_stage_and_category(message)
    page_start, page_end = _get_pages(default_stage, ranges)

    occurrence = {
        "codigo": _default_code(origin, severity, default_stage),
        "severidade": severity,
        "categoria": default_category,
        "mensagem": message,
        "origem": origin,
        "indice_origem": index,
        "etapa": default_stage,
        "item": _extract_item(message),
        "ref_id": _extract_ref_id(message),
        "pagina_inicio": page_start,
        "pagina_fim": page_end,
        "linha_original": "",
        "causa": "",
        "sugestao": "",
        "evidencia": {
            "mensagem_original": message,
        },
    }

    if context.get("obra_nome"):
        occurrence["evidencia"]["obra_nome_informada"] = str(context["obra_nome"])
    if context.get("obra_localizacao"):
        occurrence["evidencia"]["obra_localizacao_informada"] = str(context["obra_localizacao"])

    if message.startswith("Orçamento: intervalo de páginas inválido"):
        occurrence.update(
            {
                "codigo": "ORCAMENTO_RANGE_INVALIDO",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "causa": "O intervalo informado para o orçamento sintético é inválido ou vazio.",
                "sugestao": "Confira orcamento_inicio e orcamento_fim antes de chamar a API.",
                "pagina_inicio": _get_pages("orcamento", ranges)[0],
                "pagina_fim": _get_pages("orcamento", ranges)[1],
            }
        )
        return occurrence

    if message.startswith("Composições: não processadas"):
        occurrence.update(
            {
                "codigo": "COMPOSICOES_RANGE_INVALIDO",
                "categoria": "composicoes",
                "etapa": "composicoes",
                "causa": "O intervalo informado para o anexo de composições está ausente, zerado ou incoerente.",
                "sugestao": "Envie composicoes_inicio e composicoes_fim válidos, usando a indexação esperada pela API.",
                "pagina_inicio": _get_pages("composicoes", ranges)[0],
                "pagina_fim": _get_pages("composicoes", ranges)[1],
            }
        )
        return occurrence

    if message.startswith("Falha ao abrir PDF (pdfplumber):"):
        occurrence.update(
            {
                "codigo": "PDF_NAO_ABERTO",
                "categoria": "sistema",
                "etapa": "composicoes",
                "causa": "A biblioteca de leitura não conseguiu abrir o PDF recebido.",
                "sugestao": "Verifique se o arquivo não está corrompido, protegido por senha ou fora do formato esperado.",
                "pagina_inicio": _get_pages("composicoes", ranges)[0],
                "pagina_fim": _get_pages("composicoes", ranges)[1],
            }
        )
        return occurrence

    recovered = _RE_RECOVERED_CODE.search(message)
    if recovered:
        occurrence.update(
            {
                "codigo": "CODIGO_TRUNCADO_RECUPERADO",
                "categoria": "composicoes",
                "etapa": "composicoes",
                "ref_id": f"{recovered.group('destino')}|{recovered.group('banco')}",
                "causa": "O código detectado no anexo parecia truncado e foi normalizado com base nas referências do orçamento.",
                "sugestao": "Use o código recuperado como valor preferencial ao refinar a saída no Lovable.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "codigo_detectado": recovered.group("origem"),
                    "codigo_recuperado": recovered.group("destino"),
                    "banco": recovered.group("banco"),
                },
            }
        )
        return occurrence

    trunc_match = _RE_TRUNCATION_MATCH.search(message)
    if trunc_match:
        occurrence.update(
            {
                "codigo": "MATCH_POR_TRUNCAMENTO",
                "categoria": "validacao",
                "etapa": "composicoes",
                "ref_id": trunc_match.group("esperado"),
                "causa": "O casamento entre orçamento e composições exigiu tolerância a truncamento de código.",
                "sugestao": "Considere o código detectado como evidência auxiliar, mas preserve a referência esperada do orçamento.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "esperado": trunc_match.group("esperado"),
                    "detectado": trunc_match.group("detectado"),
                },
            }
        )
        return occurrence

    if message.startswith("Insumo citado no orçamento como item de composição:"):
        occurrence.update(
            {
                "codigo": "ORCAMENTO_INSUMO_COMO_ITEM",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "causa": "Um código com aparência de insumo entrou no orçamento como se fosse composição principal.",
                "sugestao": "Revise o item no PDF/orçamento e, se necessário, reclasifique-o antes do refino final.",
            }
        )
        return occurrence

    if "COMPOSICAO placeholder" in message:
        occurrence.update(
            {
                "codigo": "ORCAMENTO_CODIGO_PLACEHOLDER",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "causa": "O parser encontrou item de orçamento sem código confiável e usou placeholder temporário.",
                "sugestao": "Busque o código real no trecho do PDF correspondente ao item citado nos exemplos.",
            }
        )
        return occurrence

    group_missing_total = _RE_GROUP_MISSING_TOTAL.search(message)
    if group_missing_total:
        occurrence.update(
            {
                "codigo": "ORCAMENTO_GRUPO_SEM_TOTAL",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "item": group_missing_total.group("item"),
                "causa": "O grupo foi detectado, mas o documento não trouxe CUSTO TOTAL parseável para ele.",
                "sugestao": "Procure o valor do grupo na linha original ou em linha quebrada logo abaixo/acima.",
            }
        )
        return occurrence

    suspicious_group = _RE_SUSPICIOUS_GROUP.search(message)
    if suspicious_group:
        occurrence.update(
            {
                "codigo": "ORCAMENTO_GRUPO_SUSPEITO",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "linha_original": suspicious_group.group("linha"),
                "causa": "Uma linha foi interpretada como grupo, mas o parser já a sinalizou como suspeita.",
                "sugestao": "Revise essa linha antes de confiar nela como meta/submeta.",
            }
        )
        return occurrence

    line_ignored = _RE_LINE_IGNORED.search(message)
    if line_ignored:
        occurrence.update(
            {
                "codigo": "ORCAMENTO_LINHA_IGNORADA",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "linha_original": line_ignored.group("linha"),
                "causa": "A linha não casou com item, grupo ou continuação segura durante o parsing do orçamento.",
                "sugestao": "Use essa linha como evidência para reconstrução manual/assistida no Lovable.",
            }
        )
        return occurrence

    numeric_total = _RE_NUMERIC_TOTAL.search(message)
    if numeric_total:
        occurrence.update(
            {
                "codigo": "ORCAMENTO_CUSTO_TOTAL_NAO_NUMERICO",
                "categoria": "orcamento",
                "etapa": "orcamento",
                "item": numeric_total.group("item"),
                "causa": "O custo total do grupo foi encontrado, mas não pôde ser convertido para número.",
                "sugestao": "Revise separadores decimais, ruído textual e quebras na linha do grupo.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "valor_bruto": numeric_total.group("valor"),
                },
            }
        )
        return occurrence

    group_math = _RE_GROUP_MATH.search(message)
    if group_math:
        occurrence.update(
            {
                "codigo": "VALIDACAO_DIVERGENCIA_GRUPO_MENSAGEM",
                "categoria": "validacao",
                "etapa": "orcamento",
                "item": group_math.group("item"),
                "causa": "O orçamento falhou na validação matemática de um grupo.",
                "sugestao": "Compare o custo total do grupo com a soma de seus filhos e revise arredondamentos/quebras.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "soma_filhos": group_math.group("soma"),
                    "custo_total": group_math.group("total"),
                    "tolerancia": group_math.group("tol"),
                },
            }
        )
        return occurrence

    summary_public = _RE_COMPOSICOES_SUMMARY.search(message)
    if summary_public:
        occurrence.update(
            {
                "codigo": "COMPOSICOES_RESUMO_PROCESSAMENTO",
                "severidade": "info",
                "categoria": "composicoes",
                "etapa": "composicoes",
                "pagina_inicio": int(summary_public.group("ini")),
                "pagina_fim": int(summary_public.group("fim")),
                "causa": "Resumo operacional do parser para o anexo de composições.",
                "sugestao": "Pode ser usado pelo Lovable para telemetria e decisão de refino, não como erro em si.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "principais": int(summary_public.group("principais")),
                    "auxiliares_globais": int(summary_public.group("aux")),
                    "aliases": int(summary_public.group("aliases")),
                },
            }
        )
        return occurrence

    summary_internal = _RE_COMPOSICOES_SUMMARY_INTERNAL.search(message)
    if summary_internal:
        occurrence.update(
            {
                "codigo": "COMPOSICOES_RESUMO_INTERNO",
                "severidade": "info",
                "categoria": "composicoes",
                "etapa": "composicoes",
                "pagina_inicio": int(summary_internal.group("ini")),
                "pagina_fim": int(summary_internal.group("fim")),
                "causa": "Resumo interno do parser de composições.",
                "sugestao": "Use apenas como evidência operacional; não representa uma falha.",
                "evidencia": {
                    **occurrence["evidencia"],
                    "principais": int(summary_internal.group("principais")),
                    "auxiliares": int(summary_internal.group("aux")),
                    "insumos": int(summary_internal.group("insumos")),
                    "auxiliares_globais": int(summary_internal.group("aux_g")),
                    "aliases": int(summary_internal.group("aliases")),
                },
            }
        )
        return occurrence

    if "Range suspeito" in message and "page_indexing=1-based" in message:
        occurrence.update(
            {
                "codigo": "COMPOSICOES_RANGE_SUSPEITO",
                "categoria": "composicoes",
                "etapa": "composicoes",
                "causa": "Os números de página enviados sugerem risco de offset duplo entre indexação 0-based e 1-based.",
                "sugestao": "Confirme como o Lovable está contando páginas antes de reenviar o PDF para a API.",
            }
        )
        return occurrence

    return occurrence


def _build_reference_occurrence(
    *,
    ref_id: str,
    severity: str,
    code: str,
    category: str,
    message: str,
    origin: str,
    index: int,
    ranges: Dict[str, Tuple[int, int]],
    cause: str,
    suggestion: str,
) -> Dict[str, Any]:
    item = ""
    page_start, page_end = _get_pages("composicoes", ranges)
    return {
        "codigo": code,
        "severidade": severity,
        "categoria": category,
        "mensagem": message,
        "origem": origin,
        "indice_origem": index,
        "etapa": "composicoes",
        "item": item,
        "ref_id": str(ref_id or "").strip(),
        "pagina_inicio": page_start,
        "pagina_fim": page_end,
        "linha_original": "",
        "causa": cause,
        "sugestao": suggestion,
        "evidencia": {
            "ref_id": str(ref_id or "").strip(),
        },
    }


def _get_pages(stage: str, ranges: Dict[str, Tuple[int, int]]) -> Tuple[Optional[int], Optional[int]]:
    stage = (stage or "").strip().lower()
    value = ranges.get(stage)
    if not value or not isinstance(value, tuple) or len(value) != 2:
        return None, None

    start, end = value
    start = start if isinstance(start, int) and start > 0 else None
    end = end if isinstance(end, int) and end > 0 else None
    return start, end


def _infer_stage_and_category(message: str) -> Tuple[str, str]:
    lowered = message.lower()

    if lowered.startswith("[composicoes]") or lowered.startswith("composições:") or "pdfplumber" in lowered:
        return "composicoes", "composicoes"

    if lowered.startswith("[orcamento]") or lowered.startswith("orçamento:"):
        return "orcamento", "orcamento"

    if lowered.startswith("[validacao]"):
        return "composicoes", "validacao"

    orcamento_hints = [
        "grupo ",
        "linha ignorada",
        "custo_total não numérico",
        "divergência matemática no grupo",
        "insumo citado no orçamento",
    ]
    if any(hint in lowered for hint in orcamento_hints):
        return "orcamento", "orcamento" if "divergência matemática" not in lowered else "validacao"

    composicoes_hints = [
        "código truncado recuperado",
        "range suspeito",
    ]
    if any(hint in lowered for hint in composicoes_hints):
        return "composicoes", "composicoes"

    return "validacao", "validacao"


def _default_code(origin: str, severity: str, stage: str) -> str:
    origin = (origin or "origem").upper()
    severity = (severity or "aviso").upper()
    stage = (stage or "validacao").upper()
    return f"{stage}_{origin}_{severity}"


def _extract_item(message: str) -> str:
    match = _RE_ITEM.search(message or "")
    return match.group("item") if match else ""


def _extract_ref_id(message: str) -> str:
    match = _RE_REF_ID.search(message or "")
    return match.group("ref") if match else ""


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(".", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _deduplicate_occurrences(occurrences: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()

    for occ in occurrences:
        key = (
            occ.get("codigo"),
            occ.get("severidade"),
            occ.get("categoria"),
            occ.get("origem"),
            occ.get("item"),
            occ.get("ref_id"),
            occ.get("linha_original"),
            occ.get("mensagem"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(occ)

    return deduped


def _build_summary(occurrences: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, int] = {}
    by_code: Dict[str, int] = {}
    total_errors = 0
    total_warnings = 0
    total_infos = 0

    for occ in occurrences:
        category = str(occ.get("categoria") or "desconhecido")
        code = str(occ.get("codigo") or "DESCONHECIDO")
        severity = str(occ.get("severidade") or "aviso")

        by_category[category] = by_category.get(category, 0) + 1
        by_code[code] = by_code.get(code, 0) + 1

        if severity == "erro":
            total_errors += 1
        elif severity == "info":
            total_infos += 1
        else:
            total_warnings += 1

    return {
        "total_ocorrencias": len(occurrences),
        "total_erros": total_errors,
        "total_avisos": total_warnings,
        "total_infos": total_infos,
        "por_categoria": dict(sorted(by_category.items())),
        "por_codigo": dict(sorted(by_code.items())),
        "tem_erros": total_errors > 0,
    }
