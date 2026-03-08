# Api_pdf — PDF Import API (SINAPI/SICRO-ready)

API em FastAPI para importar PDFs de orçamento (Orçamento Sintético) e Anexo de Composições (ex.: SINAPI),
gerando um JSON estruturado com árvore hierárquica, composições analíticas e validações.

> Status atual: SINAPI implementado (orçamento + composições), com tolerância a quebras de linha/truncamento
> e validações matemáticas.

## Recursos

### Orçamento Sintético (árvore)
- Detecta e monta hierarquia: `meta` → `submeta` → `item`
- Extrai para itens folha:
  - `item`, `codigo`, `fonte`, `especificacao`, `und`, `quant`
  - `custo_unitario_sem_bdi`, `custo_unitario_com_bdi`, `custo_parcial`
- Mantém `itens_plano` (lista simples de itens folha em ordem)

### Composições (Anexo 3)
- Detecta blocos por item do orçamento
- Extrai:
  - composição principal
  - composições auxiliares
  - insumos
- Recupera códigos truncados usando referência do orçamento

### Validação
- `itens_faltando`: códigos esperados (do orçamento) não encontrados no anexo
- `itens_extras`: composições encontradas no anexo que não estão no orçamento
- Validação matemática por item:
  - `quant * custo_unitario_com_bdi ≈ custo_parcial`
- Modo `strict` (422 quando houver erros)

## Estrutura do projeto

- `app/main.py`
- `app/bases/base_loader.py`
- `app/bases/sinapi/parser.py`
- `app/bases/sinapi/composicoes_parser.py`
- `app/core/schemas.py`
- `app/core/config_loader.py`
- `app/core/pdf_text.py`
- `app/core/money.py`
- `app/core/sanitizer.py`
- `db/base_config.json`

## Como rodar

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger:
- `http://127.0.0.1:8000/docs`

## Endpoint

### `POST /parse`

Form-data:
- `base_id`
- `orcamento_inicio`
- `orcamento_fim`
- `composicoes_inicio`
- `composicoes_fim`
- `obra_nome` (opcional)
- `obra_localizacao` (opcional)
- `pdf`
