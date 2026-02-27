```markdown
# Api_pdf — PDF Import API (SINAPI/SICRO-ready)

API em FastAPI para importar PDFs de orçamento (Orçamento Sintético) e Anexo de Composições (ex.: SINAPI),
gerando um JSON estruturado com árvore hierárquica, composições analíticas e validações.

> Status atual: SINAPI implementado (orçamento + composições), com tolerância a quebras de linha/truncamento
> e validações matemáticas.

---

## ✅ Recursos

### Orçamento Sintético (árvore)
- Detecta e monta hierarquia:
  - `meta` → `submeta` → `item`
- Extrai para itens folha:
  - `item`, `codigo`, `fonte`, `especificacao`, `und`, `quant`,
    `custo_unitario_sem_bdi`, `custo_unitario_com_bdi`, `custo_parcial`
- Mantém `itens_plano` (lista simples de itens folha em ordem)

### Composições (Anexo 3)
- Detecta blocos por item do orçamento
- Extrai:
  - Composição principal
  - Composições auxiliares
  - Insumos
- Recupera códigos truncados (ex.: `CP_SEE_0` → `CP_SEE_04`) usando referência do orçamento

### Validação
- `itens_faltando`: códigos esperados (do orçamento) não encontrados no Anexo
- `itens_extras`: composições encontradas no Anexo que não estão no orçamento (opcional/esperado dependendo do PDF)
- Validação matemática por item:
  - `quant * custo_unitario_com_bdi ≈ custo_parcial` (com tolerâncias configuráveis)
- Modo `strict` (422 quando houver erros)

---

## 🧱 Estrutura do Projeto

```
app/
main.py
bases/
base_loader.py
sinapi/
parser.py
composicoes_parser.py
core/
schemas.py
config_loader.py
pdf_text.py
money.py
sanitizer.py
db/
base_config.json

````
---

## ⚙️ Configuração por Base (`db/base_config.json`)

Cada base (ex.: `sinapi`) possui chaves de configuração para:
- marcadores de header/linhas a ignorar
- sanitização do texto (quebrar marcadores colados, remover inline, etc.)
- tolerâncias de validação e modo `strict`

---

## ▶️ Como rodar localmente

### 1) Ambiente
Recomendado Python 3.11+.

Instale dependências:
```bash
pip install -r requirements.txt
````

### 2) Subir API

```bash
uvicorn app.main:app --reload
```

Acesse:

* Swagger: `http://127.0.0.1:8000/docs`

---

## 📌 Endpoint

### `POST /parse`

**Form-data:**

* `base_id` (ex.: `sinapi`)
* `orcamento_inicio` (int, 1-based)
* `orcamento_fim` (int, 1-based)
* `composicoes_inicio` (int, 1-based)
* `composicoes_fim` (int, 1-based)
* `obra_nome` (opcional)
* `obra_localizacao` (opcional)
* `pdf` (arquivo PDF)

**Exemplo (curl):**

```bash
curl -X POST "http://127.0.0.1:8000/parse" \
  -F "base_id=sinapi" \
  -F "orcamento_inicio=2" \
  -F "orcamento_fim=14" \
  -F "composicoes_inicio=15" \
  -F "composicoes_fim=78" \
  -F "obra_nome=Minha Obra" \
  -F "obra_localizacao=Minha Cidade" \
  -F "pdf=@meu_arquivo.pdf"
```

**Resposta (alto nível):**

* `base_id`
* `orcamento_sintetico`

  * `itens_raiz` (árvore)
  * `itens_plano`
* `composicoes`

  * `principais` (dict `COD|BANCO` → bloco)
  * `auxiliares_globais`
  * `aliases_auxiliares`
* `validacao`

  * `itens_faltando`, `itens_extras`, `avisos`, `erros`, `divergencias`

---

## 🧩 Como adicionar uma nova base (SICRO, etc.)

1. Criar pasta:
   `app/bases/sicro/`

2. Implementar:

* `app/bases/sicro/parser.py` com `parse_sicro(pdf_bytes, ranges, config, context) -> dict`

3. Registrar no `base_loader.py`:

* mapear `base_id == "sicro"` para `parse_sicro`

4. Adicionar config em `db/base_config.json`:

* chaves de `synthetic`, `sanitizer`, `validation`, `page_indexing`

---

## 🧪 Debug / Diagnóstico

* Verifique os `validacao.avisos` para:

  * código truncado recuperado
  * linha ignorada
  * insumo citado indevidamente no orçamento
* Se `validacao.erros` existir com `strict=true`, a API retorna 422

---

## 📈 Roadmap

* Implementar SICRO via configuração (mesmo pipeline do SINAPI)
* Modo opcional: “somente composições do orçamento” (remover `itens_extras`)
* Suite de testes com PDFs reais e snapshots de saída

---

## Licença

```