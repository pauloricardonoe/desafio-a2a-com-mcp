"""Sobe o servidor MCP em Streamable HTTP.

    python3 -m servidor_mcp.main

Porta e caminho vem do ambiente, com os valores que o validador assume como
padrao. `REQUEST_STATE_SECRET` e obrigatoria: sem ela o processo nao sobe.
"""

from __future__ import annotations

import os

import uvicorn

from .servidor import configurar_log, criar_servidor

PORTA_PADRAO = 7301
CAMINHO_PADRAO = "/mcp"
HOST_PADRAO = "127.0.0.1"


def main() -> None:
    configurar_log()
    porta = int(os.environ.get("MCP_PORT", PORTA_PADRAO))
    caminho = os.environ.get("MCP_PATH", CAMINHO_PADRAO)
    host = os.environ.get("MCP_HOST", HOST_PADRAO)

    # Sem sessao de transporte: cada request carrega o proprio envelope em
    # `_meta`, que e o modelo da revisao 2026-07-28. O ciclo de MRTR atravessa
    # dois requests independentes e nao depende de conexao aberta.
    app = criar_servidor().streamable_http_app(streamable_http_path=caminho, stateless_http=True)
    uvicorn.run(app, host=host, port=porta, log_level="warning")


if __name__ == "__main__":
    main()
