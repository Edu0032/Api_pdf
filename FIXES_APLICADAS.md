# Correções aplicadas

## 1. Vínculo orçamento ↔ composições
A função `_collect_item_refs()` retorna `item`, `codigo`, `fonte` e `ref_id`,
alinhando o orçamento sintético com `parse_composicoes_sinapi()`.

## 2. Compatibilidade retroativa das refs
Mesmo quando chega referência antiga só com `ref_id`, o parser reconstrói
`codigo` e `fonte` automaticamente.

## 3. Filtro mais forte para códigos de composição
O extrator de código/banco passou a priorizar as colunas explícitas `Código` e `Banco`
e deixou de aceitar quantidades quebradas como se fossem códigos.

Na prática, isso corrige casos como:
- `0,0003025` sendo interpretado como `00003025|SINAPI`
- códigos truncados gerando falsos `itens_extras`

## 4. Fallback por texto corrido nas composições
Foi adicionado um segundo caminho de leitura do anexo de composições usando o texto
extraído da página, para recuperar blocos quando a tabela vem quebrada.

Isso melhora especialmente casos como:
- `Composiçã` em uma linha e o resto da composição na linha seguinte
- códigos próprios (`SEE...`, `COMP...`) separados do restante da linha
- linhas auxiliares quebradas em `o Auxiliar`

## 5. Priorização por chave real do orçamento
Na associação dos blocos principais, o parser agora prioriza o casamento por
`codigo|banco` antes de tentar qualquer associação por `item`.

Isso evita que uma composição válida seja anexada ao item errado quando o PDF
começa a página com uma linha órfã ou quebrada.

## 6. Filtro de extras truncados/espúrios
A etapa de `itens_extras` passou a eliminar blocos claramente truncados,
blocos sem item confiável e blocos cujo item aponta para outra composição do orçamento.

## 7. Expansão automática curta do orçamento sintético
Quando o usuário informa um `orcamento_fim` curto demais e o parser percebe que
não encontrou o fechamento do anexo sintético, ele tenta avançar automaticamente
1–2 páginas para capturar o restante.

Isso evita casos como o item `15.3 SEE12798`, que estava ficando fora do orçamento
por causa de um corte de página no intervalo informado.

## 8. Health check
Foi adicionada a rota `GET /health` para alinhar a aplicação com o teste smoke.

## 9. Estabilidade de imports nos testes
Foram adicionados `__init__.py` e `tests/conftest.py` para estabilizar os imports
no ambiente de teste.

## Resultado observado no PDF enviado
Usando o PDF `sintético.pdf` enviado na conversa, com orçamento `3-6` e composições `15-78`:

- antes: muitos `itens_extras` falsos e muitos `itens_faltando`
- depois: `0 itens_extras`
- depois: restou `1 item_faltando` → `CP_SEE_04|PRÓPRIO`

Esse último caso parece ser ausência real da composição correspondente no anexo,
não um falso positivo de truncamento.

## Validação local
- `pytest -q` → `1 passed`
