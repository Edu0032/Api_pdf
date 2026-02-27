from __future__ import annotations

from typing import Dict, Tuple, Callable, Any

from app.bases.sinapi.parser import parse_sinapi


# Registro simples (expande fácil pra SICRO depois)
_PARSERS: dict[str, Callable[..., dict]] = {
    "sinapi": parse_sinapi,
}


def parse_document(
    base_id: str,
    pdf_bytes: bytes,
    ranges: Dict[str, Tuple[int, int]],
    config: dict,
    context: dict | None = None,
) -> dict:
    """
    Roteador por base (SINAPI, SICRO, etc.)
    - config: configuração da base (db/base_config.json[base_id])
    - context: dados dinâmicos (obra_nome, obra_localizacao, etc.)
    """
    base_id = (base_id or "").lower().strip()
    context = context or {}

    parser = _PARSERS.get(base_id)
    if not parser:
        raise ValueError(f"Base '{base_id}' não suportada.")

    return parser(pdf_bytes=pdf_bytes, ranges=ranges, config=config, context=context)