"""O agente como servidor A2A, e a ponte entre os dois protocolos.

Alcance por fora: o Agent Card publica a identidade no well-known URI, e o
endpoint aceita `SendMessage` e `GetTask` no binding JSON-RPC.

**Onde a ponte acontece.** Em `Ponte._aplicar`: quando o servidor MCP responde
`InputRequiredResult`, a Task vai para `TASK_STATE_INPUT_REQUIRED` e o
`requestState` fica guardado na propria Task. Em `Ponte._continuar`: a
continuacao com `escolha=<id>` repete o `tools/call` — com id de JSON-RPC novo,
porque e um request independente — levando `inputResponses` e o `requestState`
ecoado sem modificacao. O agente nunca abre o `requestState`: guarda e ecoa.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import types
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .mcp_host import ServidorDeSalas, alternativas_de, texto_do_resultado
from .tarefas import (
    AGUARDANDO_ENTRADA,
    CANCELADA,
    CONCLUIDA,
    FALHOU,
    PAPEL_DO_AGENTE,
    TERMINAIS,
    TRABALHANDO,
    AcervoDeTarefas,
    Tarefa,
)

_registro = logging.getLogger("agente-central-de-salas")

CAMPOS_DO_ARTEFATO = ("reserva", "sala", "inicio", "fim", "responsavel", "politica")
PEDIDO_RECUSADO = "recusar"


class ErroJsonRpc(Exception):
    """Erro de protocolo devolvido como `error` de JSON-RPC."""

    def __init__(self, codigo: int, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


# --- o dialogo em formato fixo ------------------------------------------


def interpretar_pedido(texto: str) -> dict[str, str]:
    """Le `reservar sala=<id> inicio=<iso> fim=<iso> responsavel=<nome>`."""
    partes = texto.split()
    if not partes or partes[0] != "reservar":
        raise ErroJsonRpc(-32602, f"Pedido fora do formato esperado: {texto!r}")
    campos = dict(parte.split("=", 1) for parte in partes[1:] if "=" in parte)
    faltando = [c for c in ("sala", "inicio", "fim", "responsavel") if c not in campos]
    if faltando:
        raise ErroJsonRpc(-32602, f"Pedido sem os campos: {', '.join(faltando)}")
    return {campo: campos[campo] for campo in ("sala", "inicio", "fim", "responsavel")}


def interpretar_escolha(texto: str) -> str:
    """Le `escolha=<id>` ou `escolha=recusar`."""
    texto = texto.strip()
    if not texto.startswith("escolha="):
        raise ErroJsonRpc(-32602, f"Resposta fora do formato esperado: {texto!r}")
    return texto.split("=", 1)[1]


def _texto_de(mensagem: dict[str, Any]) -> str:
    partes = mensagem.get("parts") or []
    return " ".join(p.get("text", "") for p in partes if isinstance(p, dict)).strip()


# --- a ponte ------------------------------------------------------------


class Ponte:
    """Traduz `input_required` do MCP em pausa da Task A2A, e a volta."""

    def __init__(self, salas: ServidorDeSalas) -> None:
        self._salas = salas
        self._tarefas = AcervoDeTarefas()

    async def send_message(self, params: dict[str, Any], traceparent: str = "") -> dict[str, Any]:
        mensagem_recebida = params.get("message") or {}
        texto = _texto_de(mensagem_recebida)
        task_id = mensagem_recebida.get("taskId")
        if task_id:
            return await self._continuar(str(task_id), texto)
        return await self._abrir(texto, traceparent)

    async def get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        tarefa = self._tarefas.obter(str(params.get("id", "")))
        if tarefa is None:
            raise ErroJsonRpc(-32602, f"Task desconhecida: {params.get('id')}")
        return {"task": tarefa.como_resposta()}

    async def _abrir(self, texto: str, traceparent: str) -> dict[str, Any]:
        tarefa = self._tarefas.abrir(traceparent)
        tarefa.receber(texto)
        tarefa.argumentos = interpretar_pedido(texto)
        tarefa.estado = TRABALHANDO
        resultado = await self._salas.reservar(tarefa.argumentos, tarefa.traceparent)
        return self._aplicar(tarefa, resultado)

    async def _continuar(self, task_id: str, texto: str) -> dict[str, Any]:
        tarefa = self._tarefas.obter(task_id)
        if tarefa is None:
            raise ErroJsonRpc(-32602, f"Task desconhecida: {task_id}")
        if tarefa.estado in TERMINAIS:
            raise ErroJsonRpc(
                -32600,
                f"Task {task_id} esta em estado terminal ({tarefa.estado}) e nao aceita novas mensagens",
            )
        tarefa.receber(texto)
        escolha = interpretar_escolha(texto)

        if escolha == PEDIDO_RECUSADO:
            # `escolha=recusar` vira `action: decline`; o servidor conclui sem
            # reservar e a Task termina cancelada.
            await self._salas.reservar(
                tarefa.argumentos,
                tarefa.traceparent,
                input_responses={tarefa.chave_do_input: {"action": "decline"}},
                request_state=tarefa.request_state,
            )
            tarefa.estado = CANCELADA
            tarefa.falar("Reserva cancelada a pedido do cliente.", PAPEL_DO_AGENTE)
            return {"task": tarefa.como_resposta()}

        if escolha not in tarefa.alternativas:
            # Escolha fora do enum mantem a pausa e repete a lista, na mesma ordem.
            tarefa.estado = AGUARDANDO_ENTRADA
            tarefa.falar(_linha_das_alternativas(tarefa.alternativas), PAPEL_DO_AGENTE)
            return {"task": tarefa.como_resposta()}

        resultado = await self._salas.reservar(
            tarefa.argumentos,
            tarefa.traceparent,
            input_responses={
                tarefa.chave_do_input: {"action": "accept", "content": {"sala": escolha}}
            },
            request_state=tarefa.request_state,
        )
        return self._aplicar(tarefa, resultado)

    def _aplicar(
        self, tarefa: Tarefa, resultado: types.CallToolResult | types.InputRequiredResult
    ) -> dict[str, Any]:
        """O ponto exato em que o `input_required` do MCP vira pausa da Task."""
        if isinstance(resultado, types.InputRequiredResult):
            chave, alternativas = alternativas_de(resultado)
            tarefa.chave_do_input = chave
            tarefa.alternativas = alternativas
            tarefa.request_state = resultado.request_state or ""
            tarefa.estado = AGUARDANDO_ENTRADA
            tarefa.falar(_linha_das_alternativas(alternativas), PAPEL_DO_AGENTE)
            _registro.info(
                "task %s pausada em %s com alternativas: %s",
                tarefa.id,
                AGUARDANDO_ENTRADA,
                ", ".join(alternativas),
            )
            return {"task": tarefa.como_resposta()}

        if resultado.is_error:
            # Erro de execucao da tool: a Task falha com a mensagem exata visivel.
            tarefa.estado = FALHOU
            tarefa.falar(texto_do_resultado(resultado), PAPEL_DO_AGENTE)
            return {"task": tarefa.como_resposta()}

        return {"task": self._concluir(tarefa, resultado).como_resposta()}

    def _concluir(self, tarefa: Tarefa, resultado: types.CallToolResult) -> Tarefa:
        estruturado = resultado.structured_content or {}
        if estruturado.get("reservado") is False:
            tarefa.estado = CANCELADA
            tarefa.falar("Reserva nao efetivada.", PAPEL_DO_AGENTE)
            return tarefa
        conteudo = {campo: estruturado.get(campo) for campo in CAMPOS_DO_ARTEFATO}
        tarefa.concluir_com(conteudo)
        tarefa.falar(
            f"Reserva {conteudo['reserva']} confirmada na {conteudo['sala']}.", PAPEL_DO_AGENTE
        )
        return tarefa


def _linha_das_alternativas(alternativas: list[str]) -> str:
    """A linha exata que o cliente A2A recebe — sem prefixo e sem saudacao."""
    return "alternativas: " + ", ".join(alternativas)


# --- Agent Card e endpoint ---------------------------------------------


def cartao_do_agente(url_do_endpoint: str) -> dict[str, Any]:
    """O Agent Card na forma da v1.0."""
    return {
        "name": "Central de Salas",
        "description": "Reserva salas de reuniao da Hill Valley Tech.",
        "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": url_do_endpoint, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reservar-sala",
                "name": "Reservar sala",
                "description": (
                    "Reserva uma sala em um intervalo. Se houver conflito, "
                    "pergunta qual alternativa usar."
                ),
                "tags": ["salas", "agenda"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
                "examples": [
                    "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 "
                    "fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
                ],
            }
        ],
    }


async def _cartao(request: Request) -> JSONResponse:
    return JSONResponse(cartao_do_agente(f"{request.app.state.url_publica}/a2a"))


async def _jsonrpc(request: Request) -> JSONResponse:
    corpo = await request.json()
    identificador = corpo.get("id")
    metodo = corpo.get("method")
    params = corpo.get("params") or {}
    ponte: Ponte = request.app.state.ponte
    try:
        if metodo == "SendMessage":
            resultado = await ponte.send_message(params, request.headers.get("traceparent", ""))
        elif metodo == "GetTask":
            resultado = await ponte.get_task(params)
        else:
            raise ErroJsonRpc(-32601, f"Metodo desconhecido: {metodo}")
    except ErroJsonRpc as erro:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": identificador,
                "error": {"code": erro.codigo, "message": erro.mensagem},
            }
        )
    return JSONResponse({"jsonrpc": "2.0", "id": identificador, "result": resultado})


@asynccontextmanager
async def _ciclo(app: Starlette) -> Any:
    salas = ServidorDeSalas(app.state.url_do_mcp)
    await salas.abrir()
    app.state.ponte = Ponte(salas)
    try:
        yield
    finally:
        await salas.fechar()


def criar_app(url_do_mcp: str, url_publica: str) -> Starlette:
    """Monta o agente: card no well-known e JSON-RPC em `/a2a`."""
    app = Starlette(
        routes=[
            Route("/.well-known/agent-card.json", _cartao, methods=["GET"]),
            Route("/a2a", _jsonrpc, methods=["POST"]),
        ],
        lifespan=_ciclo,
    )
    app.state.url_do_mcp = url_do_mcp
    app.state.url_publica = url_publica
    return app
