# EQ E3.3 — divergências por identidade causal e período

## Resultado

O replay dos 211 charts reproduziu integralmente as métricas do baseline E3.
Foram feitas 5.468 checagens de paridade de R com o detector nativo (por lado,
a cada 100 barras e na barra final), sem falhas. A auditoria levou 252,09 s;
esse tempo inclui instrumentação adicional e não substitui a medição do E3.

Das 504.006 observações barra/lado, 32.338 apresentaram diferença de membership.
Todas obedeceram à referência da última confirmação de pivô: N reteve aquela
composição e R se recompôs entre confirmações. Nenhuma observação violou essa
regra, inclusive nos instantes de confirmação. Houve 5.165 mudanças de R sem
novo pivô e 3.690 inícios de episódios divergentes. A maior distância à última
confirmação durante uma divergência foi de 90 barras.

A atribuição é mecânica, pelo estado do replay: os pivôs disponíveis permanecem
iguais entre confirmações, mas a tolerância do agrupamento de R acompanha a
série. N publica a composição apenas no relógio de pivôs. Isso explica as
diferenças observadas neste baseline; não demonstra superioridade de N.

## Por timeframe e lado

“Só R” e “Só N” são exposições de identidade por barra, inclusive níveis já
consumidos. As colunas “ativos” restringem a `not is_mitigated`. Não são trades
nem amostras independentes. “Snapshots divergentes” aqui conta barra/lado.

| TF/lado | Snapshots divergentes | Só R | Só N | Só R ativos | Só N ativos | Não explicados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m/EQH | 4413 | 6117 | 5884 | 758 | 692 | 0 |
| 15m/EQL | 4404 | 7219 | 6931 | 3458 | 3184 | 0 |
| 1h/EQH | 6009 | 10103 | 9560 | 1287 | 1338 | 0 |
| 1h/EQL | 6718 | 11568 | 11230 | 3549 | 3574 | 0 |
| 4h/EQH | 5352 | 8805 | 8858 | 2598 | 2726 | 0 |
| 4h/EQL | 5442 | 8605 | 8524 | 1698 | 1642 | 0 |

São 103.404 exposições exclusivas ao todo; 26.504 estão ativas. Logo, o número
agregado de diferenças de membership não pode ser interpretado diretamente
como diferença de níveis operáveis.

## Por período relativo

Os blocos dividem as barras de cada chart em quatro partes; não representam
as mesmas datas em todos os timeframes. A saída JSON também cruza TF/lado/bloco.

| Bloco | Snapshots/lado divergentes | Só R | Só N | Não explicados |
| --- | ---: | ---: | ---: | ---: |
| 0 | 2805 | 2394 | 2432 | 0 |
| 1 | 6734 | 9055 | 9155 | 0 |
| 2 | 12162 | 21366 | 20132 | 0 |
| 3 | 10637 | 19602 | 19268 | 0 |

A concentração nos blocos finais não introduziu uma nova classe de falha do
relógio de publicação neste ensaio.

## Identidades compartilhadas e exemplo

Nas 2.460.379 exposições de identidades comuns, não houve diferenças na
semântica comparada (formação, banda, sweep, breach, mitigação e rejeição).
Houve 2.362.024 diferenças de strength acima de 1e-10. Essa comparação é
separada da composição e é compatível com a política de congelamento de N;
este ensaio não avalia o efeito dessas diferenças no score ou na qualidade.

Exemplo auditável: BTCUSDT/H1, EQH, bloco 1, barra 399
(2026-08-08 13:00 UTC). A última confirmação ocorreu na barra 394. R incluiu a
identidade `a3b40ce661eb97b1427b`, composta pelos pivôs de 2026-07-24 07:00,
2026-07-27 06:00 e 2026-07-27 13:00 UTC; N ainda não a publicou nessa barra.
O JSON guarda exemplos completos por chart/lado/bloco.

## Decisão e limites

O E3.3 explica a diferença de membership observada, mas não muda a decisão
`shadow-only` do contrato E3.1/E3.2, que continua conservador diante de qualquer
divergência. O holdout E2 continua sem ganho robusto. Restart no painel completo,
memória e rollback operacional ainda precisam de validação. Nenhuma alteração
foi feita no detector de produção, thresholds, lifecycle, API ou UI.

O manifesto esperado foi confrontado com o painel e os hashes das fixtures
foram verificados. A paridade nativa é amostrada; a atribuição compartilha o
Replay existente e não constitui uma implementação independente do candidato.

Validação: 26 testes E3 passaram, incluindo 5 da nova auditoria; Ruff e
`git diff --check` passaram.

Reprodução e definições: [protocolo E3.3](EQ_LEVELS_E3_3_PROTOCOL.md).
Código: [auditor](eq_levels_e3_3.py), [testes](test_eq_levels_e3_3.py).
Resultado detalhado local: `research/.replay_cache/eq_e3_3.json`.
O JSON registra o commit HEAD e hashes do manifesto, shadow e fixtures; HEAD
não inclui as alterações locais ainda sem commit desta etapa.
