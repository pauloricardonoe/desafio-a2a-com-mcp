"""Sobe o agente A2A.

    python3 -m agente_central.main

Porta, URL publica e URL do servidor MCP vem do ambiente, com os valores que o
validador assume como padrao.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .a2a import criar_app

PORTA_PADRAO = 7300
HOST_PADRAO = "127.0.0.1"
URL_DO_MCP_PADRAO = "http://127.0.0.1:7301/mcp"
URL_PUBLICA_PADRAO = "http://127.0.0.1:7300"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    porta = int(os.environ.get("A2A_PORT", PORTA_PADRAO))
    host = os.environ.get("A2A_HOST", HOST_PADRAO)
    url_do_mcp = os.environ.get("MCP_URL", URL_DO_MCP_PADRAO)
    url_publica = os.environ.get("A2A_URL", URL_PUBLICA_PADRAO)
    uvicorn.run(
        criar_app(url_do_mcp, url_publica), host=host, port=porta, log_level="warning"
    )


if __name__ == "__main__":
    main()
