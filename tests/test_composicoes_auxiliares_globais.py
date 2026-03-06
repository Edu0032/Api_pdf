from __future__ import annotations

from types import SimpleNamespace

from app.bases.sinapi.composicoes_parser import parse_composicoes_sinapi


class FakePage:
    def __init__(self, tables):
        self._tables = tables

    def extract_tables(self, table_settings=None):
        return self._tables


class FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_auxiliar_global_sem_item_nao_vira_extra(monkeypatch):
    tables = [
        [
            ["4.3.4", "Código", "Banco", "Descrição", "Tipo", "Und", "Quant.", "Valor Unit", "Total"],
            ["Composição", "96536", "SINAPI", "FORMA VIGA BALDRAME", "Estruturas", "m²", "1,0000000", "67,99", "67,99"],
            ["Composição Auxiliar", "88239", "SINAPI", "AJUDANTE DE CARPINTEIRO COM ENCARGOS COMPLEMENTARES", "Livro SINAPI: Cálculos e Parâmetros", "H", "0,4770000", "25,71", "12,26"],
            ["Composição Auxiliar", "91692", "SINAPI", "SERRA CIRCULAR DE BANCADA COM MOTOR ELÉTRICO POTÊNCIA DE 5HP - CHP DIURNO", "Custos Horários", "CHP", "0,0130000", "33,56", "0,43"],
            ["Composição", "91693", "SINAPI", "SERRA CIRCULAR DE BANCADA COM MOTOR ELÉTRICO POTÊNCIA DE 5HP - CHI DIURNO", "Custos Horários", "CHI", "1,0000000", "31,96", "31,96"],
            ["Composição Auxiliar", "88297", "SINAPI", "OPERADOR DE MÁQUINAS E EQUIPAMENTOS COM ENCARGOS COMPLEMENTARES", "Livro SINAPI: Cálculos e Parâmetros", "H", "1,0000000", "31,83", "31,83"],
            ["Composição Auxiliar", "91688", "SINAPI", "SERRA CIRCULAR DE BANCADA - DEPRECIAÇÃO", "Custos Horários", "H", "1,0000000", "0,11", "0,11"],
        ]
    ]

    def fake_open(_):
        return FakePDF([FakePage(tables)])

    monkeypatch.setattr("app.bases.sinapi.composicoes_parser.pdfplumber.open", fake_open)

    item_refs = [
        {"item": "4.3.4", "ref_id": "96536|SINAPI", "sem_bdi": 67.99, "com_bdi": 84.91},
    ]

    comp, avisos, erros, itens_faltando, itens_extras = parse_composicoes_sinapi(
        pdf_bytes=b"dummy",
        start_1based=1,
        end_1based=1,
        config={"page_indexing": "1-based"},
        item_refs=item_refs,
    )

    assert erros == []
    assert itens_faltando == []
    assert "96536|SINAPI" in comp.principais
    assert comp.principais["96536|SINAPI"].item == "4.3.4"

    assert "91693|SINAPI" not in comp.principais
    assert "91693|SINAPI" in comp.auxiliares_globais_blocos
    assert comp.auxiliares_globais_blocos["91693|SINAPI"].item == ""

    assert "91693|SINAPI" in comp.auxiliares_globais
    assert "88297|SINAPI" in comp.auxiliares_globais
    assert "91688|SINAPI" in comp.auxiliares_globais

    assert itens_extras == []
    assert any("principais=1" in aviso for aviso in avisos)


def test_principal_com_item_sem_correspondencia_no_orcamento_permanece_extra(monkeypatch):
    tables = [
        [
            ["99.9", "Código", "Banco", "Descrição", "Tipo", "Und", "Quant.", "Valor Unit", "Total"],
            ["Composição", "53786", "SINAPI", "RETROESCAVADEIRA SOBRE RODAS COM CARREGADEIRA - MATERIAIS NA OPERAÇÃO", "Equipamentos", "H", "1,0000000", "66,96", "66,96"],
            ["Insumo", "00004221", "SINAPI", "OLEO DIESEL", "Material", "L", "8,5300000", "7,85", "66,96"],
        ]
    ]

    def fake_open(_):
        return FakePDF([FakePage(tables)])

    monkeypatch.setattr("app.bases.sinapi.composicoes_parser.pdfplumber.open", fake_open)

    item_refs = [
        {"item": "4.3.4", "ref_id": "96536|SINAPI", "sem_bdi": 67.99, "com_bdi": 84.91},
    ]

    comp, _avisos, erros, itens_faltando, itens_extras = parse_composicoes_sinapi(
        pdf_bytes=b"dummy",
        start_1based=1,
        end_1based=1,
        config={"page_indexing": "1-based"},
        item_refs=item_refs,
    )

    assert erros == []
    assert "53786|SINAPI" in comp.principais
    assert comp.principais["53786|SINAPI"].item == "99.9"
    assert "53786|SINAPI" in itens_extras
    assert itens_faltando == ["96536|SINAPI"]
