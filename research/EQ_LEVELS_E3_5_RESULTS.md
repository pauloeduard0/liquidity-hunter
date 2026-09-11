# EQ E3.5 — memória retida e decisão sobre integração

## Resultado

Foram medidos 211 charts em quatro fronteiras de histórico, totalizando 844
amostras. Todos reproduziram integralmente o baseline E3. O ensaio levou
257,43 s, incluindo as travessias para estimar memória. As fixtures têm entre
213 e 1.199 barras fechadas; os quartos são relativos ao tamanho de cada chart.

O tamanho estimado mediano do grafo Python do Replay ao final foi de
4.731.496 bytes (4,51 MiB). O estado cresceu entre o primeiro quarto e o final
em todos os 211 charts. O valor inclui R/N/L e a instrumentação de pesquisa;
não é a memória incremental de N em produção nem o RSS do serviço.

| TF | Charts | Mediana final MiB | Máximo final MiB | Mediana final sem logs MiB | Mediana crescimento 25→100% |
| --- | ---: | ---: | ---: | ---: | ---: |
| 15m | 71 | 4,451 | 5,019 | 1,931 | 5,16x |
| 1h | 70 | 4,457 | 4,952 | 1,958 | 5,13x |
| 4h | 70 | 4,633 | 5,001 | 1,982 | 5,04x |

## Crescimento e origem do custo

| Fronteira | Mediana estado Python (bytes) | Mediana projeção sem logs (bytes) | Mediana checkpoint JSON (bytes) |
| --- | ---: | ---: | ---: |
| 25% | 918211 | 491236 | 58149 |
| 50% | 2040796 | 1007633 | 116359 |
| 75% | 3319799 | 1537593 | 174867 |
| 100% | 4731496 | 2050890 | 233350 |

Excluir `snapshots`, `events` e `sweeps` da projeção reduz a estimativa final
em mediana de 56,52%. Porém, mesmo essa projeção cresce 4,18x em mediana entre
25% e 100% do histórico. Portanto, o crescimento não se limita aos logs de
saída: candles, índices, pivôs, versões e linhagens continuam retidos.

A projeção não foi executada como candidato nem submetida a restart; retirar
logs em produção requer definir retenção e consumidores. O estimador não
modificou o Replay. O estudo E1.4 já mostrou que reter apenas onze candles
impede calcular a strength de novas memberships nos casos reais do piloto.
Essa limitação não foi resolvida aqui.

O checkpoint JSON mediano final, com 233.350 bytes, é muito menor que o grafo
Python retido. Seu tamanho em disco não é uma estimativa de memória do Replay.
Os snapshots retêm memberships de cada barra, enquanto o conjunto de
memberships pode crescer; a curva observada não deve ser extrapolada como uma
lei linear ou como um limite para séries mais longas.

## Parecer: não investir na integração neste momento

A recomendação é manter o detector atual e encerrar esta rodada de engenharia
do candidato no estado `shadow-only`.

O motivo decisivo continua sendo a qualidade: o holdout E2 não mostrou ganho
robusto de N. E3.3 e E3.4 demonstraram explicação mecânica das divergências e
continuidade de checkpoints completos; isso reduz incerteza de correção, mas
não cria benefício para o usuário. Integrar agora exigiria investimento em
retenção de estado, persistência e rollback sem benefício demonstrado.

A medição não permite afirmar que o custo é inviável: falta um orçamento
operacional e não foi medido o RSS/pico do serviço. Ela demonstra que o Replay
atual não valida memória limitada, nem mesmo ao projetar o estado sem logs.
Não há justificativa para definir um limite retrospectivo e declarar aprovação.

Só reabrir a integração diante de um benefício concreto, por exemplo uma
necessidade de produto de identidade/publicação estável ou nova evidência de
qualidade em período independente com critérios definidos antes da execução.
Nesse caso, definir primeiro carga e orçamento de memória/latência; depois
medir RSS e picos no ambiente alvo, validar retenção/compactação preservando a
strength, recuperação de interrupções e rollback operacional.

Essas tarefas ficam registradas como condições de retomada, não como uma nova
rodada já iniciada. Não foi alterado detector, API, UI ou configuração produtiva.

## Validação e reprodução

Seis testes do estimador e do shadow passaram em 2,14 s. Os testes verificam
compartilhamento, ciclos, slots e ausência de mutação. Ruff e
`git diff --check` passaram. As 211 reconciliações com o baseline passaram.

Protocolo e limites do estimador: [E3.5](EQ_LEVELS_E3_5_PROTOCOL.md).
Código: [auditor](eq_levels_e3_5.py), [testes](test_eq_levels_e3_5.py).
Execução: `poetry run python -m research.eq_levels_e3_5`.
Dados detalhados locais: `research/.replay_cache/eq_e3_5.json` (ignorado pelo Git).
O JSON registra versão do Python, plataforma e hashes dos códigos e dados.
