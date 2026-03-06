from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------
# ORÇAMENTO SINTÉTICO
# --------------------
class OrcamentoItem(BaseModel):
    # "meta", "submeta", "item"
    tipo: str = ""
    item: str = ""

    # grupos (meta/submeta)
    descricao: str = ""
    custo_total: Optional[str] = None

    # itens (folha)
    codigo: str = ""
    fonte: str = ""
    especificacao: str = ""
    und: str = ""
    quant: Optional[str] = None
    custo_unitario_sem_bdi: Optional[str] = None
    custo_unitario_com_bdi: Optional[str] = None
    custo_parcial: Optional[str] = None
    filhos: List["OrcamentoItem"] = Field(default_factory=list)


class OrcamentoSintetico(BaseModel):
    descricao: str = ""
    total: Optional[float] = None

    # raízes da árvore
    itens_raiz: List[OrcamentoItem] = Field(default_factory=list)

    # lista “plano” (apenas os números de item, ex: ["9.4", "9.5", ...])
    itens_plano: List[str] = Field(default_factory=list)


OrcamentoItem.model_rebuild()


# --------------------
# COMPOSIÇÕES (ANEXO 3)
# --------------------
class LinhaComposicao(BaseModel):
    codigo: str
    banco: str
    descricao: str = ""
    tipo: str = ""  # ex: Provisórios / Armaduras / Material / Mão de Obra
    und: str = ""
    quant: Optional[float] = None
    valor_unit: Optional[float] = None
    total: Optional[float] = None

    # debug: o parser preenche isso quando banco vem “embutido” no código
    banco_coluna: str = ""


class LinhaInsumo(LinhaComposicao):
    pass


class BlocoComposicao(BaseModel):
    # item do orçamento (ex: "9.4") detectado do header ou via mapeamento
    item: str = ""
    principal: LinhaComposicao
    composicoes_auxiliares: List[LinhaComposicao] = Field(default_factory=list)
    insumos: List[LinhaInsumo] = Field(default_factory=list)


class Composicoes(BaseModel):
    # chave padrão: "CODIGO|BANCO"
    principais: Dict[str, BlocoComposicao] = Field(default_factory=dict)

    # auxiliares “órfãs” (quando o range começa no meio / ou auxiliar aparece fora do bloco)
    auxiliares_globais: Dict[str, LinhaComposicao] = Field(default_factory=dict)

    # aliases (ex: "883164|SINAPI" -> "88316|SINAPI")
    aliases_auxiliares: Dict[str, str] = Field(default_factory=dict)


# --------------------
# VALIDAÇÃO / RESPOSTA
# --------------------
class OcorrenciaValidacao(BaseModel):
    codigo: str
    severidade: str  # info | aviso | erro
    categoria: str  # orcamento | composicoes | validacao | sistema
    mensagem: str

    origem: str = ""
    indice_origem: Optional[int] = None
    etapa: str = ""
    item: str = ""
    ref_id: str = ""
    pagina_inicio: Optional[int] = None
    pagina_fim: Optional[int] = None
    linha_original: str = ""
    causa: str = ""
    sugestao: str = ""
    evidencia: Dict[str, Any] = Field(default_factory=dict)


class ResumoValidacao(BaseModel):
    total_ocorrencias: int = 0
    total_erros: int = 0
    total_avisos: int = 0
    total_infos: int = 0
    por_categoria: Dict[str, int] = Field(default_factory=dict)
    por_codigo: Dict[str, int] = Field(default_factory=dict)
    tem_erros: bool = False


class Validacao(BaseModel):
    itens_faltando: List[str] = Field(default_factory=list)
    itens_extras: List[str] = Field(default_factory=list)
    avisos: List[str] = Field(default_factory=list)
    erros: List[str] = Field(default_factory=list)
    divergencias: List[Dict[str, Any]] = Field(default_factory=list)

    # novos campos para consumo estruturado pelo Lovable
    ocorrencias: List[OcorrenciaValidacao] = Field(default_factory=list)
    resumo: ResumoValidacao = Field(default_factory=ResumoValidacao)


class ParseResponse(BaseModel):
    base_id: str
    orcamento_sintetico: OrcamentoSintetico
    composicoes: Optional[Composicoes] = None
    validacao: Validacao


# necessário por causa da recursão OrcamentoItem.filhos
OrcamentoItem.model_rebuild()