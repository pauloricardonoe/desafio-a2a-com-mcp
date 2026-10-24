#!/usr/bin/env bash
#
# Sobe o servidor MCP em Streamable HTTP, na porta 7301.
#
# O segredo do `requestState` vem de `REQUEST_STATE_SECRET`. Se a variavel nao
# estiver no ambiente, este script gera uma e guarda em `.env` — que esta no
# .gitignore — para que reinicios continuem validando o estado selado antes
# deles. Sem esse segredo estavel, o requisito de o `requestState` sobreviver a
# um restart nao passa.
#
# Uso:
#   ./subir-servidor-mcp.sh

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

if [[ ! -x servidor-mcp/.venv/bin/python ]]; then
  echo "Ambiente do servidor nao encontrado. Rode primeiro:" >&2
  echo "  cd servidor-mcp && python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${REQUEST_STATE_SECRET:-}" ]]; then
  REQUEST_STATE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'REQUEST_STATE_SECRET=%s\n' "$REQUEST_STATE_SECRET" > .env
  echo "Segredo novo gerado e guardado em .env (nao versionado)." >&2
fi

export REQUEST_STATE_SECRET
export MCP_PORT="${MCP_PORT:-7301}"
cd servidor-mcp
exec .venv/bin/python -m servidor_mcp.main
