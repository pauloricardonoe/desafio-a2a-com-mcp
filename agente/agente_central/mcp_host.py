"""O agente como host MCP.

Profundidade por dentro: o agente descobre as ferramentas em runtime, le o
resource da politica e chama a reserva por HTTP, como um cliente MCP de verdade.
Nao importa a funcao da tool — a fronteira entre os dois processos e o protocolo.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client, types

_registro = logging.getLogger("agente-central-de-salas")

NOME_DA_TOOL_DE_RESERVA = "reservar_sala"
URI_DA_POLITICA = "politica://uso"
PAPEL_DO_USUARIO = "ROLE_USER"
PAPEL_DO_AGENTE = "ROLE_AGENT"


async def _fachada_de_elicitacao(contexto: Any, params: Any) -> Any:
    """Nunca roda: existe apenas para o cliente declarar a capability.

    O cliente monta `clientCapabilities` a partir dos callbacks registrados.
    Sem este callback ele nao anuncia elicitation em form mode, e o servidor
    responde `-32021`. Como as chamadas saem por `client.session.call_tool` com
    `allow_input_required=True`, o `input_required` volta cru para o agente e
    este callback nao chega a ser invocado.
    """
    raise RuntimeError(
        "o agente nao responde elicitation: a pergunta pertence ao cliente A2A"
    )


def alternativas_de(resultado: types.InputRequiredResult) -> tuple[str, list[str]]:
    """Devolve a chave do `inputRequests` e os ids das alternativas, na ordem do `enum`."""
    chave = next(iter(resultado.input_requests))
    pedido = resultado.input_requests[chave]
    schema = pedido.params.requested_schema
    campo = schema["properties"]["sala"]
    return chave, list(campo.get("enum") or [campo["const"]])


def texto_do_resultado(resultado: types.CallToolResult) -> str:
    """Concatena o texto das partes, que e onde a mensagem exata da tool vive."""
    return " ".join(parte.text for parte in resultado.content if getattr(parte, "text", None))


class ServidorDeSalas:
    """Cliente MCP do servidor de salas."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._pilha = AsyncExitStack()
        self._cliente: Client | None = None
        self.tools: list[str] = []
        self.versao_da_politica: str = ""

    async def abrir(self) -> None:
        """Conecta, descobre as ferramentas e le a versao declarada na politica."""
        # `mode="auto"` sonda `server/discover` e adota a era moderna. Sem isso o
        # cliente cairia na era de handshake e o servidor tomaria o caminho antigo,
        # sem `input_required` — a ponte nunca aconteceria.
        self._cliente = await self._pilha.enter_async_context(
            Client(self._url, mode="auto", elicitation_callback=_fachada_de_elicitacao)
        )
        listagem = await self._cliente.session.list_tools()
        self.tools = [tool.name for tool in listagem.tools]
        _registro.info("tools descobertas por tools/list: %s", ", ".join(self.tools))
        recurso = await self._cliente.session.read_resource(URI_DA_POLITICA)
        texto = recurso.contents[0].text
        self.versao_da_politica = texto.splitlines()[0].split(":", 1)[1].strip()
        _registro.info("politica lida, versao %s", self.versao_da_politica)

    async def fechar(self) -> None:
        await self._pilha.aclose()
        self._cliente = None

    async def reservar(
        self,
        argumentos: dict[str, str],
        traceparent: str = "",
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        """Chama a reserva e devolve o resultado cru.

        `allow_input_required=True` e o que impede o SDK de fechar o ciclo de
        MRTR sozinho. O `traceparent` viaja no `_meta`, preservando o trace-id
        que veio do cliente A2A.
        """
        if self._cliente is None:  # pragma: no cover - defensivo
            raise RuntimeError("cliente MCP nao aberto")
        meta: dict[str, Any] = {"traceparent": traceparent} if traceparent else {}
        return await self._cliente.session.call_tool(
            NOME_DA_TOOL_DE_RESERVA,
            argumentos,
            input_responses=input_responses,
            request_state=request_state,
            meta=meta,
            allow_input_required=True,
        )
