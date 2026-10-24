"""Servidor MCP: as tres tools, o resource da politica e o ciclo de MRTR.

A ponte com o agente acontece aqui, do lado do servidor: quando o intervalo
pedido conflita com uma reserva, a tool nao responde com um erro nem pergunta
por um canal de volta (o transporte stateless nao tem um). Ela termina a
resposta pedindo informacao — `InputRequiredResult`, com a elicitation e um
`requestState` selado — e o cliente volta com um request novo.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Elicit, Resolve
from mcp.server.request_state import RequestStateSecurity
from pydantic import BaseModel, create_model

from .dados import ERRO_SEM_ALTERNATIVA, Acervo, ErroDeUso

MENSAGEM_DA_ELICITACAO = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."
URI_DA_POLITICA = "politica://uso"
NOME_DO_SERVIDOR = "central-de-salas"
VERSAO_DO_SERVIDOR = "1.0.0"
SEGREDO_MINIMO_EM_BYTES = 32
VALIDADE_DO_ESTADO_EM_SEGUNDOS = 900.0

_registro = logging.getLogger(NOME_DO_SERVIDOR)

# Preenchido por `criar_servidor`. Os resolvers sao funcoes de modulo para que a
# chave de `inputRequests` (`module:qualname`) seja estavel entre reinicios —
# o `requestState` atravessa o restart e precisa casar com a mesma pergunta.
_acervo: Acervo | None = None


def _dados() -> Acervo:
    if _acervo is None:  # pragma: no cover - so acontece se criar_servidor nao rodou
        raise RuntimeError("acervo nao inicializado: chame criar_servidor() antes de servir")
    return _acervo


# --- contratos das tools -------------------------------------------------


class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ConflitoOut(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class Disponibilidade(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoOut]


class ReservaOut(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


class EscolhaDaSala(BaseModel):
    """Sala que sera efetivamente reservada."""

    sala: str


# --- regras compartilhadas ----------------------------------------------


def _intervalo(sala: str, inicio: str, fim: str) -> tuple[datetime, datetime]:
    """Valida sala, intervalo, janela e duracao, na ordem fixada.

    Converte a violacao de uso em `ToolError`, que o SDK devolve como erro de
    execucao (`isError: true`) com a mensagem exata exigida pelo enunciado.
    """
    acervo = _dados()
    try:
        quando_inicio, quando_fim = datetime.fromisoformat(inicio), datetime.fromisoformat(fim)
    except ValueError as erro:
        raise ToolError(f"Intervalo invalido: {erro}") from erro
    try:
        acervo.validar(sala, quando_inicio, quando_fim)
    except ErroDeUso as erro:
        raise ToolError(str(erro)) from erro
    return quando_inicio, quando_fim


def _schema_das_alternativas(alternativas: list[str]) -> type[BaseModel]:
    """Modelo pydantic com a escolha restrita as alternativas.

    Com duas ou mais alternativas o schema sai como `enum`; com uma so, o
    gerador emite `const`. O enunciado aceita as duas formas.
    """
    return create_model("EscolhaDeSala", sala=(Literal[tuple(alternativas)], ...))


# --- o resolver que vira pergunta ---------------------------------------


def escolha_de_sala(sala: str, inicio: str, fim: str) -> "Elicit[EscolhaDaSala] | EscolhaDaSala":
    """Decide qual sala sera reservada, pedindo a escolha quando ha conflito.

    Devolver um marcador `Elicit` e o que faz o framework montar o
    `InputRequiredResult` — e, junto com ele, checar a capability do cliente e
    selar o `requestState`. Devolver um valor simples segue o caminho normal.
    """
    acervo = _dados()
    quando_inicio, quando_fim = _intervalo(sala, inicio, fim)
    if not acervo.conflitos(sala, quando_inicio, quando_fim):
        return EscolhaDaSala(sala=sala)
    alternativas = acervo.alternativas(sala, quando_inicio, quando_fim)
    if not alternativas:
        raise ToolError(ERRO_SEM_ALTERNATIVA)
    return Elicit(MENSAGEM_DA_ELICITACAO, _schema_das_alternativas(alternativas))


# --- registro de requests em stderr -------------------------------------


class RegistroDeRequests:
    """Registra no stderr o metodo, o id e o traceparent de cada request."""

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        _registro.info(
            "request method=%s id=%s traceparent=%s",
            ctx.method,
            ctx.request_id,
            _traceparent_do(ctx.params) or "-",
        )
        return await call_next(ctx)


def _traceparent_do(params: Mapping[str, Any] | None) -> str:
    if not isinstance(params, Mapping):
        return ""
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        return ""
    return str(meta.get("traceparent", ""))


# --- montagem ------------------------------------------------------------


def segredo_do_ambiente() -> bytes:
    """Le `REQUEST_STATE_SECRET` do ambiente, exigindo tamanho minimo.

    Nao ha fallback para a chave efemera do SDK: ela e process-local e faria o
    `requestState` selado antes de um restart ser rejeitado depois dele.
    """
    valor = os.environ.get("REQUEST_STATE_SECRET", "")
    if len(valor.encode("utf-8")) < SEGREDO_MINIMO_EM_BYTES:
        raise SystemExit(
            "REQUEST_STATE_SECRET ausente ou com menos de "
            f"{SEGREDO_MINIMO_EM_BYTES} bytes.\n"
            "Gere uma e exporte antes de subir o servidor:\n"
            '  export REQUEST_STATE_SECRET="$(python3 -c '
            "'import secrets; print(secrets.token_hex(32))')\""
        )
    return valor.encode("utf-8")


def criar_servidor(acervo: Acervo | None = None) -> MCPServer[Any]:
    """Monta o servidor MCP com as tres tools, o resource e o registro em stderr."""
    global _acervo
    _acervo = acervo or Acervo()

    servidor: MCPServer[Any] = MCPServer(
        name=NOME_DO_SERVIDOR,
        version=VERSAO_DO_SERVIDOR,
        request_state_security=RequestStateSecurity(
            keys=[segredo_do_ambiente()],
            ttl=VALIDADE_DO_ESTADO_EM_SEGUNDOS,
        ),
        middleware=[RegistroDeRequests()],
    )

    @servidor.tool(description="Lista todas as salas com capacidade e recursos.")
    def listar_salas() -> ListaDeSalas:
        return ListaDeSalas(salas=[SalaOut(**sala) for sala in _dados().salas])

    @servidor.tool(description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.")
    def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
        quando_inicio, quando_fim = _intervalo(sala, inicio, fim)
        conflitos = _dados().conflitos(sala, quando_inicio, quando_fim)
        return Disponibilidade(
            sala=sala,
            livre=not conflitos,
            conflitos=[ConflitoOut(**{campo: r[campo] for campo in ("id", "inicio", "fim", "responsavel")}) for r in conflitos],
        )

    @servidor.tool(description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.")
    def reservar_sala(
        sala: str,
        inicio: str,
        fim: str,
        responsavel: str,
        escolha: Annotated[ElicitationResult[Any], Resolve(escolha_de_sala)],
    ) -> ReservaOut:
        acervo_atual = _dados()
        if not isinstance(escolha, AcceptedElicitation):
            return ReservaOut(reservado=False, motivo="recusado")
        sala_escolhida = escolha.data.sala
        quando_inicio, quando_fim = datetime.fromisoformat(inicio), datetime.fromisoformat(fim)
        if acervo_atual.conflitos(sala_escolhida, quando_inicio, quando_fim):
            raise ToolError(ERRO_SEM_ALTERNATIVA)
        reserva = acervo_atual.reservar(sala_escolhida, quando_inicio, quando_fim, responsavel)
        return ReservaOut(
            reserva=reserva["id"],
            reservado=True,
            sala=sala_escolhida,
            inicio=inicio,
            fim=fim,
            responsavel=responsavel,
            politica=acervo_atual.versao_da_politica,
        )

    @servidor.resource(URI_DA_POLITICA, name="politica-de-uso", title="Politica de uso", mime_type="text/markdown")
    def politica_de_uso() -> str:
        return _dados().politica

    return servidor


def configurar_log() -> None:
    """Manda o log para o stderr, no lugar do canal depreciado."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
