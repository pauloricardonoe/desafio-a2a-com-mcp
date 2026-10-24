"""Dados do dominio e as regras de uso das salas.

Nao ha banco nem ORM: o desafio entrega `dados/*.json` prontos e a persistencia
pode ser em memoria. As reservas criadas durante a execucao ficam visiveis para
as consultas seguintes do mesmo processo e nao sobrevivem a um restart.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

# A politica e fixa e esta transcrita em `dados/politica-de-uso.md`.
ABERTURA = time(8, 0)
FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)
MAXIMO_DE_ALTERNATIVAS = 3

ERRO_SALA = "Sala inexistente: {sala}"
ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"


class ErroDeUso(Exception):
    """Violacao de uma regra de uso, devolvida como erro de execucao da tool."""


def _diretorio_dados() -> Path:
    """Localiza `dados/` a partir da raiz do repositorio, ou de `DADOS_DIR`."""
    if override := os.environ.get("DADOS_DIR"):
        return Path(override)
    # .../<raiz>/servidor-mcp/servidor_mcp/dados.py -> <raiz>/dados
    return Path(__file__).resolve().parents[2] / "dados"


class Acervo:
    """Salas, reservas e politica de uso, carregados uma vez na subida."""

    def __init__(self, diretorio: Path | None = None) -> None:
        self._diretorio = diretorio or _diretorio_dados()
        self.salas: list[dict[str, Any]] = self._ler("salas.json")
        self.reservas: list[dict[str, Any]] = self._ler("reservas.json")
        self.politica: str = (self._diretorio / "politica-de-uso.md").read_text(encoding="utf-8")
        self.versao_da_politica: str = self.politica.splitlines()[0].split(":", 1)[1].strip()
        self._proxima_reserva = self._maior_numero_de_reserva() + 1

    def _ler(self, nome: str) -> list[dict[str, Any]]:
        return json.loads((self._diretorio / nome).read_text(encoding="utf-8"))

    def _maior_numero_de_reserva(self) -> int:
        numeros = [
            int(r["id"].removeprefix("res-"))
            for r in self.reservas
            if r["id"].startswith("res-") and r["id"].removeprefix("res-").isdigit()
        ]
        return max(numeros, default=0)

    # --- consultas -------------------------------------------------------

    def sala(self, sala_id: str) -> dict[str, Any]:
        for sala in self.salas:
            if sala["id"] == sala_id:
                return sala
        raise ErroDeUso(ERRO_SALA.format(sala=sala_id))

    def conflitos(self, sala_id: str, inicio: datetime, fim: datetime) -> list[dict[str, Any]]:
        """Reservas da sala que sobrepoem o intervalo."""
        return [
            r
            for r in self.reservas
            if r["sala"] == sala_id
            and _quando(r["inicio"]) < fim
            and _quando(r["fim"]) > inicio
        ]

    def alternativas(self, sala_id: str, inicio: datetime, fim: datetime) -> list[str]:
        """Salas livres no intervalo, com capacidade igual ou maior, na ordem da regra."""
        capacidade = self.sala(sala_id)["capacidade"]
        candidatas = [
            s
            for s in self.salas
            if s["id"] != sala_id
            and s["capacidade"] >= capacidade
            and not self.conflitos(s["id"], inicio, fim)
        ]
        candidatas.sort(key=lambda s: (s["capacidade"], s["id"]))
        return [s["id"] for s in candidatas[:MAXIMO_DE_ALTERNATIVAS]]

    # --- comandos --------------------------------------------------------

    def validar(self, sala_id: str, inicio: datetime, fim: datetime) -> None:
        """Aplica as regras na ordem fixada: sala, intervalo, janela, duracao."""
        self.sala(sala_id)
        if fim <= inicio:
            raise ErroDeUso(ERRO_INTERVALO)
        if inicio.time() < ABERTURA or fim.time() > FECHAMENTO:
            raise ErroDeUso(ERRO_JANELA)
        if fim - inicio > DURACAO_MAXIMA:
            raise ErroDeUso(ERRO_DURACAO)

    def reservar(self, sala_id: str, inicio: datetime, fim: datetime, responsavel: str) -> dict[str, Any]:
        """Cria a reserva. O chamador ja validou politica e conflito."""
        reserva = {
            "id": f"res-{self._proxima_reserva:04d}",
            "sala": sala_id,
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
            "responsavel": responsavel,
        }
        self._proxima_reserva += 1
        self.reservas.append(reserva)
        return reserva


def _quando(texto: str) -> datetime:
    return datetime.fromisoformat(texto)
