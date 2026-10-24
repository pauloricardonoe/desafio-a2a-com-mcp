"""A Task A2A: identidade, estado e produto.

O estado da ponte — o `requestState` e o pedido original — vive aqui, ligado a
uma Task especifica, e nunca sai em resposta ao cliente A2A. E o que permite
duas Tasks pausadas ao mesmo tempo sem trocarem de estado entre si.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

SUBMETIDA = "TASK_STATE_SUBMITTED"
TRABALHANDO = "TASK_STATE_WORKING"
AGUARDANDO_ENTRADA = "TASK_STATE_INPUT_REQUIRED"
CONCLUIDA = "TASK_STATE_COMPLETED"
CANCELADA = "TASK_STATE_CANCELED"
FALHOU = "TASK_STATE_FAILED"

TERMINAIS = frozenset({CONCLUIDA, CANCELADA, FALHOU})

PAPEL_DO_USUARIO = "ROLE_USER"
PAPEL_DO_AGENTE = "ROLE_AGENT"


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{secrets.token_hex(6)}"


def mensagem(
    texto: str,
    papel: str,
    task_id: str | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    """Uma mensagem A2A. `taskId` e `contextId` so entram quando a mensagem e do agente."""
    corpo: dict[str, Any] = {
        "messageId": novo_id("msg"),
        "role": papel,
        "parts": [{"text": texto}],
    }
    if task_id is not None:
        corpo["taskId"] = task_id
    if context_id is not None:
        corpo["contextId"] = context_id
    return corpo


@dataclass
class Tarefa:
    id: str
    context_id: str
    estado: str = SUBMETIDA
    historico: list[dict[str, Any]] = field(default_factory=list)
    artefatos: list[dict[str, Any]] = field(default_factory=list)
    ultima_mensagem: dict[str, Any] | None = None

    # --- estado da ponte, opaco para o cliente A2A ---
    argumentos: dict[str, str] = field(default_factory=dict)
    chave_do_input: str = ""
    alternativas: list[str] = field(default_factory=list)
    request_state: str = ""
    traceparent: str = ""

    def como_resposta(self) -> dict[str, Any]:
        """A Task como o cliente A2A a ve — sem nada do estado da ponte."""
        status: dict[str, Any] = {"state": self.estado}
        if self.ultima_mensagem is not None:
            status["message"] = self.ultima_mensagem
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": list(self.historico),
            "artifacts": list(self.artefatos),
        }

    def falar(self, texto: str, papel: str) -> None:
        """Registra uma mensagem no historico e a marca como a ultima da Task."""
        self.ultima_mensagem = mensagem(texto, papel, self.id, self.context_id)
        self.historico.append(self.ultima_mensagem)

    def receber(self, texto: str) -> None:
        """Registra a mensagem do cliente no historico."""
        self.historico.append(mensagem(texto, PAPEL_DO_USUARIO))

    def concluir_com(self, conteudo: dict[str, Any]) -> None:
        self.artefatos.append(
            {
                "artifactId": novo_id("art"),
                "name": "reserva",
                "parts": [{"text": _json(conteudo)}],
            }
        )
        self.estado = CONCLUIDA


class AcervoDeTarefas:
    """Guarda as Tasks do processo, indexadas por id."""

    def __init__(self) -> None:
        self._tarefas: dict[str, Tarefa] = {}

    def abrir(self, traceparent: str = "") -> Tarefa:
        tarefa = Tarefa(id=novo_id("task"), context_id=novo_id("ctx"), traceparent=traceparent)
        self._tarefas[tarefa.id] = tarefa
        return tarefa

    def obter(self, task_id: str) -> Tarefa | None:
        return self._tarefas.get(task_id)


def _json(conteudo: dict[str, Any]) -> str:
    import json

    return json.dumps(conteudo, ensure_ascii=False)
