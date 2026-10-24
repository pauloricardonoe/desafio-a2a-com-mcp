#!/usr/bin/env bash
#
# Reinicia os dois processos do zero e roda o validador.
#
# O validador nao limpa estado: as reservas criadas por uma execucao mudam o
# resultado da seguinte. Rodar duas vezes contra os mesmos processos da falso
# negativo — as verificacoes 13 a 20 param de pedir input porque as salas
# alternativas ja foram ocupadas. Este script mata o que estiver nas portas,
# sobe tudo de novo, espera os dois responderem e so entao valida.
#
# O stderr dos dois processos fica em `servidor-mcp.log` e `agente.log`, na
# raiz, para conferir a propagacao do traceparent e o par de tools/call.
#
# Uso:
#   ./rodar-validador.sh              # reinicia e roda o validador
#   ./rodar-validador.sh --manter     # reinicia e deixa rodando, sem validar
#   ./rodar-validador.sh --parar      # so derruba os dois processos

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

PORTA_AGENTE="${A2A_PORT:-7300}"
PORTA_MCP="${MCP_PORT:-7301}"
LOG_MCP="$RAIZ/servidor-mcp.log"
LOG_AGENTE="$RAIZ/agente.log"

parar() {
  lsof -nP -iTCP:"$PORTA_AGENTE","$PORTA_MCP" -sTCP:LISTEN -t 2>/dev/null | xargs -r kill 2>/dev/null || true
}

esperar() {
  local descricao="$1" url="$2"
  shift 2
  # `"$@"` com zero argumentos e seguro sob `set -u`; um array nomeado vazio
  # nao e, no bash 3.2 que o macOS ainda entrega.
  for _ in $(seq 1 40); do
    if curl -s -o /dev/null --max-time 2 "$@" "$url" 2>/dev/null; then
      echo "  $descricao pronto"
      return 0
    fi
    sleep 0.5
  done
  echo "  $descricao NAO respondeu em 20s — veja o log" >&2
  return 1
}

case "${1:-}" in
  --parar)
    parar
    echo "processos derrubados"
    exit 0
    ;;
esac

echo "== derrubando o que estiver nas portas $PORTA_AGENTE e $PORTA_MCP =="
parar
sleep 2

echo "== subindo o servidor MCP =="
nohup ./subir-servidor-mcp.sh > "$LOG_MCP" 2>&1 < /dev/null &
disown
esperar "servidor MCP" "http://127.0.0.1:$PORTA_MCP/mcp" -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

echo "== subindo o agente =="
nohup ./subir-agente.sh > "$LOG_AGENTE" 2>&1 < /dev/null &
disown
esperar "agente" "http://127.0.0.1:$PORTA_AGENTE/.well-known/agent-card.json"

if [[ "${1:-}" == "--manter" ]]; then
  echo
  echo "os dois processos estao rodando."
  echo "  card:      curl http://127.0.0.1:$PORTA_AGENTE/.well-known/agent-card.json"
  echo "  stderr:    tail -f $LOG_MCP"
  echo "  derrubar:  ./rodar-validador.sh --parar"
  exit 0
fi

echo "== validador =="
echo
set +e
python3 validador/validar.py \
  --agente "http://localhost:$PORTA_AGENTE" \
  --mcp "http://localhost:$PORTA_MCP"
codigo=$?
set -e

echo
echo "os dois processos continuam rodando — o stderr esta em:"
echo "  $LOG_MCP"
echo "  $LOG_AGENTE"
echo "para derrubar: ./rodar-validador.sh --parar"
exit "$codigo"
