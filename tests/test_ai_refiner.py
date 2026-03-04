from app.core.schemas import BlocoComposicao, Composicoes, LinhaComposicao
from app.postprocess.ai_refiner import refine_composicoes_with_ai


def test_ai_refiner_corrige_codigo_com_baixa_confianca():
    comp = Composicoes(
        principais={
            "CP_SEE_0|SINAPI": BlocoComposicao(
                item="1.1",
                principal=LinhaComposicao(
                    codigo="CP_SEE_0",
                    banco="SINAPI",
                    descricao="",
                    tipo="Composição",
                    und="UN",
                ),
                composicoes_auxiliares=[],
                insumos=[],
            )
        },
        auxiliares_globais={},
        aliases_auxiliares={},
    )

    refs = [{"item": "1.1", "ref_id": "CP_SEE_04|SINAPI"}]

    refined, avisos = refine_composicoes_with_ai(comp, refs, min_confidence=0.99, min_correction_similarity=0.6)

    assert "CP_SEE_04|SINAPI" in refined.principais
    assert any("Código principal ajustado" in a for a in avisos)
