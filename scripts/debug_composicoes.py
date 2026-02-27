import json
from pathlib import Path

from app.core.config_loader import load_base_config
from app.bases.sinapi.composicoes_parser import parse_composicoes_sinapi

PDF_PATH = Path("../documento/sintético.pdf")  # ajuste se estiver em outro lugar

# chute inicial: coloque um range que você SABE que pega o ANEXO 3
START = 15
END = 78

def main():
    config_all = load_base_config()
    config = config_all["sinapi"]

    pdf_bytes = PDF_PATH.read_bytes()

    comp, avisos, erros, faltando, extras = parse_composicoes_sinapi(
        pdf_bytes=pdf_bytes,
        start_1based=START,
        end_1based=END,
        config=config,
        item_refs=None,
        context={},
    )

    print("AVISOS:")
    for a in avisos[:30]:
        print("-", a)

    print("\nERROS:")
    for e in erros[:30]:
        print("-", e)

    print("\nRESUMO:")
    print("principais:", len(comp.principais))
    print("auxiliares_globais:", len(comp.auxiliares_globais))
    print("faltando:", len(faltando))
    print("extras:", len(extras))

    # mostra 3 exemplos
    keys = list(comp.principais.keys())[:3]
    for k in keys:
        bloco = comp.principais[k]
        print("\nEXEMPLO:", k, "item:", bloco.item)
        print("  principal:", bloco.principal.codigo, bloco.principal.banco, bloco.principal.descricao[:60])
        print("  aux:", len(bloco.composicoes_auxiliares), "insumos:", len(bloco.insumos))

    # salva para inspeção
    out = {
        "principais": {k: v.model_dump() for k, v in comp.principais.items()},
        "auxiliares_globais": {k: v.model_dump() for k, v in comp.auxiliares_globais.items()},
        "avisos": avisos,
        "erros": erros,
        "faltando": faltando,
        "extras": extras,
    }
    Path("debug_composicoes.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSalvo: debug_composicoes.json")

if __name__ == "__main__":
    main()