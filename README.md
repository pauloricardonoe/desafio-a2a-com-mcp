# A Ponte: um agente A2A com MCP por dentro

Reserva de salas da Hill Valley Tech, em dois processos separados: um **servidor MCP** que expõe as capacidades do domínio, e um **agente** que consome esse servidor por dentro (como host MCP) e se oferece ao mundo por fora (como servidor A2A v1.0).

O que este projeto entrega, além dos dois protocolos, é a costura entre eles. Nenhum dos dois tem sessão: o MCP resolve com o `requestState` do MRTR, que volta pelas mãos do cliente, e o A2A resolve com a Task, que tem identidade, estado e produto. No meio do agente esses dois mecanismos se encontram, e é isso que o validador cobra.

## Como rodar

Requisitos: **Python 3.10 ou superior**. Nenhuma outra dependência de sistema.

A partir de um clone limpo, na raiz do repositório:

```bash
# 1. Ambiente do servidor MCP
cd servidor-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
cd ..

# 2. Ambiente do agente
cd agente
python3 -m venv .venv
.venv/bin/pip install -e .
cd ..
```

As dependências estão travadas em `mcp==2.3.0` no `pyproject.toml` de cada processo.

**Terminal 1** — servidor MCP (porta `7301`):

```bash
./subir-servidor-mcp.sh
```

Na primeira subida o script gera `REQUEST_STATE_SECRET`, guarda em `.env` (que está no `.gitignore`) e reutiliza nas subidas seguintes. Isso é o que faz o `requestState` continuar válido depois de reiniciar o processo — o avaliador testa exatamente isso. Para gerar a sua à mão:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

e exporte antes de subir: `export REQUEST_STATE_SECRET="<valor gerado>"`. O servidor recusa a subida se a variável estiver ausente ou tiver menos de 32 bytes — não há fallback silencioso para uma chave efêmera.

**Terminal 2** — agente A2A (porta `7300`):

```bash
./subir-agente.sh
```

**Terminal 3** — validador:

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

### O caminho de um comando só

O validador **não limpa estado**: as reservas criadas por uma execução mudam o resultado da seguinte. Rodar duas vezes contra os mesmos processos dá falso negativo — as verificações 13 a 20 param de pedir input, porque as salas alternativas já foram ocupadas. São 16 falhas de uma vez, e a leitura enganosa é "o agente quebrou", quando na verdade o servidor MCP e o agente estão íntegros.

Por isso há um script que faz o ciclo completo — derruba o que estiver nas portas, sobe os dois de novo, espera cada um responder e só então valida:

```bash
./rodar-validador.sh
```

Rodar duas, três vezes seguidas dá 36/36 em todas. O stderr dos dois processos fica em `servidor-mcp.log` e `agente.log`, na raiz, para conferir a propagação do `traceparent` e o par de `tools/call`:

```bash
./rodar-validador.sh --manter   # sobe e deixa rodando, sem validar
tail -f servidor-mcp.log        # acompanha o stderr do servidor
./rodar-validador.sh --parar    # derruba os dois
```

> Se preferir subir à mão nos terminais, **mate e suba os dois processos antes de cada execução** — é a mesma regra, feita por você.

> **Isso vale também para os passos manuais do Fluxo do avaliador.** Uma execução do validador deixa a `sala-fusca` reservada das 14h às 15h (verificação 30), e o passo 7 do fluxo espera que um pedido para a `sala-garagem` nesse intervalo ofereça exatamente `sala-fusca, sala-mirante`. Com o estado do validador ainda vivo, a `sala-fusca` já está ocupada e sobra só a `sala-mirante` — correto pelo domínio, mas diferente do que o passo descreve. **Reinicie os dois processos entre rodar o validador e fazer os passos manuais.**

Conferência rápida do card:

```bash
curl http://localhost:7300/.well-known/agent-card.json
```

Portas e URLs são parametrizáveis: `MCP_PORT`, `MCP_PATH`, `MCP_HOST`, `A2A_PORT`, `A2A_HOST`, `A2A_URL`, `MCP_URL`. Os valores padrão são os que o validador assume.

## Onde a ponte acontece

A ponte tem dois pontos, um de ida e um de volta.

**Ida — o `input_required` do MCP vira pausa da Task.** Em `agente/agente_central/a2a.py`, no método `Ponte._aplicar`. Quando o servidor MCP responde com `InputRequiredResult` em vez de um resultado de tool, o agente **não** responde pelo cliente nem espera: ele coloca a Task em `TASK_STATE_INPUT_REQUIRED`, extrai as alternativas do `enum` da elicitation, publica a linha exata `alternativas: <ids>` como mensagem da Task, e guarda na própria Task o `requestState`, a chave do `inputRequests`, o pedido original e o `traceparent`. É o ponto em que "o servidor precisa de mais informação" atravessa a fronteira e vira "a Task está pausada esperando o cliente A2A".

**Volta — o `requestState` retorna ao servidor.** Em `Ponte._continuar`, no mesmo arquivo. O `SendMessage` de continuação traz `taskId` e `escolha=<valor>`; o agente recupera o estado guardado daquela Task e repete o `tools/call` — com **id de JSON-RPC novo**, porque são requests independentes — levando `inputResponses` com a mesma chave que veio no `inputRequests` e o `requestState` ecoado sem modificação.

O agente trata o `requestState` como **opaco**: guarda e ecoa, nunca abre, interpreta ou reconstrói. Ele não aparece no card, no artifact nem em nenhuma resposta A2A — o validador verifica isso varrendo todas as respostas do agente (verificação 34).

O lado do servidor, em `servidor-mcp/servidor_mcp/servidor.py`, tem o outro extremo da costura: a função `escolha_de_sala` é o resolver que devolve um marcador `Elicit` quando o intervalo está ocupado. Devolver esse marcador é o que faz o framework montar o `InputRequiredResult` e selar o `requestState` — o servidor não guarda nada entre a pergunta e o retry.

## Decisões técnicas

**Como o `requestState` é protegido.** Pelo codec do próprio SDK: `RequestStateSecurity` com `AESGCMRequestStateCodec` — **AES-256-GCM** sobre uma chave derivada por HKDF-SHA256 — e o `RequestStateBoundary`, que sela e verifica no limite do wire. O token sai como `v1.<base64url sem padding>`, um blob único, a mesma forma dos exemplos em `exemplos/wire/`. Um caractere alterado muda o tag de autenticação e a verificação falha com `-32602` e a mensagem congelada `Invalid or expired requestState`.

Vale registrar por que não há HMAC próprio: além de duplicar um mecanismo já testado, o boundary embute no estado um **digest dos argumentos** e o binding de método e alvo. É isso que faz a verificação 18 passar — argumentos adulterados no retry divergem do que foi selado e a requisição é recusada, sem que o servidor precise comparar campo a campo.

**Por quanto tempo ele vale.** 900 segundos, 15 minutos, dentro da faixa de 5 a 30 que o enunciado exige. O padrão do SDK são 600; o valor está explícito em `VALIDADE_DO_ESTADO_EM_SEGUNDOS`.

**A chave vem do ambiente, e isso é o que faz o restart funcionar.** O SDK recebe a chave por parâmetro, e quando o parâmetro é omitido o padrão é `RequestStateSecurity.ephemeral()`: uma chave aleatória **por processo**. Isso passa em tudo que roda num processo só e quebra silenciosamente a única verificação que depende de reinício — cada subida geraria uma chave nova e todo estado selado antes do restart seria rejeitado. Por isso `REQUEST_STATE_SECRET` é obrigatória e a subida falha sem ela.

**Onde fica o estado das Tasks.** Em memória, no processo do agente, num `AcervoDeTarefas` indexado por `taskId` (`agente/agente_central/tarefas.py`). O `requestState` vive dentro do objeto `Tarefa`, junto do pedido original e das alternativas. Isso é o que permite **duas Tasks pausadas ao mesmo tempo** concluírem cada uma com a sua reserva, sem trocar de estado entre si — uma variável única de estado pausado não passaria na verificação 33. As reservas também ficam em memória, no servidor MCP, e não precisam sobreviver a um restart; o `requestState` precisa, e é autocontido.

**O agente usa `ClientSession`, nunca `Client.call_tool`.** Este é o ponto que decide se a entrega funciona. O SDK tem duas camadas de cliente com comportamentos opostos diante do `input_required`:

- `Client` (wrapper alto nível) **fecha o ciclo sozinho**: despacha a pergunta para os callbacks registrados e re-tenta, até 10 rodadas.
- `ClientSession` é *raw-opt-in*: por padrão **levanta `RuntimeError`**, e só devolve o `InputRequiredResult` cru com `allow_input_required=True`.

O caminho de menor resistência é justamente o que quebra o desafio, e ele **falha parecendo funcionar**: a reserva é criada, a Task conclui, e a pausa simplesmente nunca existiu. Por isso o agente conecta com `Client(url, mode="auto")` — para negociar a era moderna e declarar as capabilities — mas chama sempre `client.session.call_tool(..., allow_input_required=True)`.

**A capability de elicitation exige um `elicitation_callback` de fachada.** O cliente monta `clientCapabilities` a partir dos callbacks registrados; sem callback ele não anuncia elicitation e o servidor responde `-32021`. O agente registra um callback que nunca roda — com `allow_input_required=True` o `input_required` volta cru — e que existe apenas para anunciar a capability.

**Validação de protocolo: nada escrito à mão.** O SDK cobre sozinho o `_meta` obrigatório (`-32602` com HTTP 400) e os headers espelhados divergentes (`-32020` com HTTP 400), pelo `classify_inbound_request`. O `-32021` sai pelo caminho do `Elicit`, que dispara a checagem de capability no momento em que o resolver tenta perguntar. O único erro que construímos é a distinção entre os dois tipos: `ToolError` para erro de execução (`isError: true` dentro de um resultado `complete`) e `MCPError` para erro de protocolo.

**`stateless_http=True`, e por quê.** Na revisão `2026-07-28` o caminho moderno já é estateless por construção: o gerenciador de transporte roteia qualquer request moderno para um caminho sem `Mcp-Session-Id` e sem canal de volta, com um contexto novo por request. O `stateless_http=True` é reforço explícito, alinhado à proibição de sessão do enunciado — verificado funcionando no ciclo de MRTR, que atravessa dois requests independentes.

**Sem LLM, por decisão.** O agente interpreta um formato fixo (`reservar sala=… inicio=… fim=… responsavel=…`) e responde por regra. Não há dependência de SDK de provedor de modelo em nenhum dos `pyproject.toml`, e o mesmo pedido produz sempre a mesma pausa.

### Divergências do SDK, documentadas

O enunciado pede que limitações reais sejam registradas em vez de contornadas reescrevendo o protocolo. Duas observações:

- **O cliente anuncia `{"elicitation": {"form": {}, "url": {}}}`**, um superconjunto da forma `{"elicitation": {"form": {}}}` citada no enunciado. O SDK não permite declarar modos isoladamente — a capability é derivada da presença do callback. `form` está presente, que é o que a verificação exige; o `url` extra não altera comportamento nenhum neste fluxo.
- **O `traceparent` é propagado por passagem direta do header** recebido do cliente A2A para o `_meta` de cada request MCP da Task. O enunciado permite span-id novo ou reusado; o que não pode mudar é o trace-id, e ele é preservado.

## Saída do validador

Execução completa, com os dois processos recém-iniciados:

```text
trace-id desta execucao: af1d7715bd428946ff915d17ebf33445
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```

### As duas verificações que ficam fora do validador

O validador não reinicia processos nem lê log, então duas exigências do enunciado se conferem à mão. Ambas foram verificadas nesta entrega:

**A propagação do `traceparent`.** O validador imprime na primeira linha o trace-id que vai usar. No stderr do servidor MCP, com o trace-id `af1d7715bd428946ff915d17ebf33445`:

```text
request method=tools/call id=38aaf0bd777d traceparent=00-af1d7715bd428946ff915d17ebf33445-ed720c9e7c6ef021-01
```

O trace-id é o mesmo do cliente A2A; só o span-id é do agente.

**O `requestState` sobrevive ao reinício.** Conflito no processo A, estado selado, processo A morto, processo B subido com o mesmo `REQUEST_STATE_SECRET`, retry contra o processo B:

```text
resultType: complete isError: False
structuredContent: {'reserva': 'res-0003', 'reservado': True, 'sala': 'sala-fusca', ...}
```

**O par de `tools/call` da pausa**, no mesmo stderr, com ids de JSON-RPC diferentes entre o request inicial e o retry:

```text
request method=tools/call id=38aaf0bb...   # request inicial, responde input_required
request method=tools/call id=4f2c91e0...   # retry, id novo, conclui a reserva
```

## Estrutura

```
.
├── README.md                    este arquivo
├── subir-servidor-mcp.sh        sobe o servidor MCP (7301)
├── subir-agente.sh              sobe o agente A2A (7300)
├── rodar-validador.sh           reinicia os dois e roda o validador
├── dados/                       nao alterado
├── validador/                   nao alterado
├── exemplos/                    nao alterado
├── servidor-mcp/
│   ├── pyproject.toml           mcp==2.3.0
│   └── servidor_mcp/
│       ├── dados.py             dominio: salas, reservas, politica, regras
│       ├── servidor.py          as tres tools, o resource e o resolver do MRTR
│       └── main.py              sobe em Streamable HTTP
└── agente/
    ├── pyproject.toml           mcp==2.3.0
    └── agente_central/
        ├── mcp_host.py          o agente como host MCP
        ├── tarefas.py           a Task A2A e o estado da ponte
        ├── a2a.py               o Agent Card, o endpoint e a ponte
        └── main.py              sobe o agente
```
