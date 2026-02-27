print("Executandooooooo")
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.openapi.utils import get_openapi

from app.bases.base_loader import parse_document
from app.core.config_loader import load_base_config
from app.core.schemas import ParseResponse

app = FastAPI()


def custom_openapi():
    app.openapi_schema = get_openapi(
        title="PDF Import API",
        version="0.1.0",
        routes=app.routes,
    )
    return app.openapi_schema


app.openapi = custom_openapi


@app.post("/parse", response_model=ParseResponse)
async def parse_endpoint(
    base_id: str = Form(...),
    orcamento_inicio: int = Form(...),
    orcamento_fim: int = Form(...),
    composicoes_inicio: int = Form(...),
    composicoes_fim: int = Form(...),
    obra_nome: str | None = Form(None),
    obra_localizacao: str | None = Form(None),
    pdf: UploadFile = File(...),
):
    pdf_bytes = await pdf.read()

    config_all = load_base_config()
    base_cfg = config_all.get(base_id)
    if not base_cfg:
        raise HTTPException(status_code=400, detail=f"Base '{base_id}' não cadastrada em db/base_config.json")

    context = {"obra_nome": obra_nome, "obra_localizacao": obra_localizacao}

    # ranges com skip profissional
    ranges = {"orcamento": (orcamento_inicio, orcamento_fim)}
    if composicoes_inicio >= 1 and composicoes_fim >= composicoes_inicio:
        ranges["composicoes"] = (composicoes_inicio, composicoes_fim)
    else:
        ranges["composicoes"] = (0, 0)

    result = parse_document(
        base_id=base_id,
        pdf_bytes=pdf_bytes,
        ranges=ranges,
        config=base_cfg,
        context=context,
    )

    strict = bool(base_cfg.get("validation", {}).get("strict", True))
    v = result.get("validacao", {})
    if strict and v.get("erros"):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Falha de validação.",
                "erros": v.get("erros", []),
                "avisos": v.get("avisos", []),
                "divergencias": v.get("divergencias", []),
            },
        )

    return result