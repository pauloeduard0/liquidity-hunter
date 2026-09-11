# EQ E2 — qualidade no holdout

O painel usa o mesmo baseline E1, restringe-se a BTCUSDT, ETHUSDT e SOLUSDT e
seleciona o bloco temporal final (`block=3`) no horizonte 10. A métrica é
`P(MFE > MAE)` menos o controle casado já calculado no E1; não é retorno nem
prova de edge.

Os números ficam em `research/eq_levels_e2_quality_baseline.json`. A leitura é
descritiva: R e N são comparados sem alterar parâmetros, e o resultado não é
usado para escolher uma configuração. Se o sinal não repetir nos seis grupos
de TF/lado e nos três símbolos, não há base para promoção.

Este passo mede qualidade fora da amostra do contrato causal. Persistência,
thresholds, clustering, lifecycle e UI continuam intocados.

No painel completo (71 símbolos, 211 charts), bloco 3/h10, os deltas R/N foram:

| TF/lado | R (n) | N (n) |
| --- | ---: | ---: |
| 15m/EQH | 0,000 (29) | 0,000 (30) |
| 15m/EQL | -0,001 (113) | -0,001 (113) |
| 1h/EQH | -0,014 (73) | 0,000 (74) |
| 1h/EQL | 0,000 (46) | 0,000 (46) |
| 4h/EQH | +0,052 (71) | +0,052 (71) |
| 4h/EQL | +0,017 (109) | +0,017 (108) |

As diferenças entre R e N são residuais e a direção muda por timeframe. O
holdout completo não demonstra ganho consistente de qualidade para N; ele apenas
mantém a vantagem mecânica de causalidade já verificada em E2.

Artefatos: [analisador](eq_levels_e2_quality.py) e [teste](test_eq_levels_e2_quality.py).
