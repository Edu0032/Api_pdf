# PDF Import API (MVP)

API para importar PDF (SINAPI agora; pronta para expandir SICRO depois).

## Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
```

## Rodar
```bash
uvicorn app.main:app --reload
```

## Teste rápido
```bash
pytest -q
```

## Endpoint principal
`POST /parse` (multipart/form-data):
- base_id: sinapi
- orcamento_inicio, orcamento_fim: int (1-based)
- composicoes_inicio, composicoes_fim: int (1-based)
- pdf: arquivo


## Sanitização
O arquivo `app/core/sanitizer.py` contém a lógica para cortar cabeçalhos/rodapés e corrigir casos de texto "colado" sem espaço.


## Campo não preenchido
Quando um grupo (meta/submeta) não possui valor na coluna **CUSTO TOTAL**, o parser define `custo_total` como **"Não preenchido"**.
