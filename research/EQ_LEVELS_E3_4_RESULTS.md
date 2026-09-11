# EQ E3.4 — resultado de continuidade/restart

## Resultado

Os 211 charts passaram pelas três reinicializações sucessivas em 25%, 50% e
75% do histórico fechado: **633 restarts sem diferença de estado**. A execução
final de cada chart foi idêntica à referência contínua e reproduziu todas as
métricas do baseline E3.

Foram comparados 252.003 snapshots barra a barra e, na igualdade do estado
final, 56.478 eventos ordenados, 12.762 registros de sweep e 13.142 registros de
publicação. A comparação também incluiu candles, zonas R/N, strength,
linhagens, memória de consumo e demais campos internos do Replay.

O painel levou 606,88 s (aproximadamente 10 min 7 s), incluindo a referência
contínua, a segunda trajetória e a reconstrução de histórico dos checkpoints.
Esse tempo não é o runtime do shadow simples e não deve ser aplicado ao seu
limite operacional de 300 s.

## Custos observados

O tempo por checkpoint abaixo inclui serialização/gravação, leitura,
restauração e comparação de estado. É uma medição local, sem garantia de SLA.
O tamanho é do JSON em disco, não da memória residente do processo.

| TF | Charts | Restarts | Maior checkpoint (bytes) | Mediana I/O + restore (s) | Máximo I/O + restore (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 15m | 71 | 213 | 189075 | 0,1820 | 0,7202 |
| 1h | 70 | 210 | 188558 | 0,1887 | 0,9832 |
| 4h | 70 | 210 | 186268 | 0,2225 | 0,8679 |

| Fronteira | Mediana do tamanho (bytes) | Mediana I/O + restore (s) |
| --- | ---: | ---: |
| 25% | 58149 | 0,0405 |
| 50% | 116359 | 0,2007 |
| 75% | 174867 | 0,5386 |

O crescimento é compatível com o desenho atual: o checkpoint guarda todos os
candles e `restore()` reconstrói o estado reexecutando o histórico. Os números
não demonstram custo limitado para séries indefinidamente longas.

## Escopo da conclusão

A continuidade do checkpoint de pesquisa está validada nos três pontos por
chart, com arquivos JSON completos gravados e lidos do disco. O ensaio cria
novas instâncias no mesmo processo. Não simula encerramento abrupto do processo,
queda de energia durante a gravação, arquivos truncados, perda de disco ou
recuperação de serviço. Não houve teste de todas as fronteiras possíveis.

Não foi necessário modificar o Replay ou o formato de checkpoint. O novo
código é um auditor de pesquisa; não é uma implementação de persistência ou
rollback de produção.

O candidato permanece **shadow-only**. Este resultado atende à verificação de
continuidade nas fronteiras ensaiadas, mas ainda faltam orçamento de memória,
persistência tolerante a interrupções e rollback operacional. O holdout E2
continua sem ganho robusto de qualidade, independentemente da continuidade.

## Validação e reprodução

- 32 testes E2/E3 passaram em 11,54 s, incluindo quatro testes E3.4.
- O teste de falha detecta perda de um acumulador durante `restore()`.
- Ruff nos novos módulos e `git diff --check` passaram.
- Identidades do painel e hashes das fixtures foram conferidos; 211/211 charts
  reconciliaram integralmente com o shadow E3.

Execução: `poetry run python -m research.eq_levels_e3_4`.
Detalhes: [protocolo](EQ_LEVELS_E3_4_PROTOCOL.md),
[auditor](eq_levels_e3_4.py), [testes](test_eq_levels_e3_4.py).
Resultado local: `research/.replay_cache/eq_e3_4.json` (ignorado pelo Git).
O JSON registra hashes do auditor, Replay, shadow, manifesto, baseline e
fixtures. O commit HEAD registrado não inclui as alterações locais pendentes.
