#!/usr/bin/env bash
#
# Sobe o agente A2A, na porta 7300.
#
# O agente fala com o servidor MCP por HTTP: suba o servidor antes, com
# `./subir-servidor-mcp.sh`, em outro terminal.
#
# Uso:
#   ./subir-agente.sh

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

if [[ ! -x agente/.venv/bin/python ]]; then
  echo "Ambiente do agente nao encontrado. Rode primeiro:" >&2
  echo "  cd agente && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

export A2A_PORT="${A2A_PORT:-7300}"
export A2A_URL="${A2A_URL:-http://127.0.0.1:${A2A_PORT}}"
export MCP_URL="${MCP_URL:-http://127.0.0.1:7301/mcp}"

cd agente
exec .venv/bin/python -m agente_central.main
