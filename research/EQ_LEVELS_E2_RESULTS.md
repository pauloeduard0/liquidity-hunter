# EQ E2 — resultado inicial do candidato causal

O candidato E2 foi iniciado sobre o replay N de E1. Ele preserva os parâmetros
atuais e testa apenas identidade estável e `strength` congelado por membership.

Em BTCUSDT H1, 500 candles produziram publicações com strength estável por
versão. Um restart no candle 160, seguido da mesma cauda, reproduziu exatamente
eventos e snapshots da execução contínua.

Os dois testes E2 passam. Isto valida o contrato mecânico do candidato, não
qualidade de mercado. Ainda falta executar o painel de holdout com essa versão,
comparar R/N sob o mesmo recorte e medir custo/compatibilidade antes de qualquer
proposta produtiva.

O holdout fixo BTC/ETH/SOL × M15/H1/H4 também foi executado: os nove charts
mantiveram `stable_strength_versions == published_versions` e todos reproduziram
eventos e snapshots após restart no meio da série. Isso confirma a invariância
de estado, mas não demonstra melhoria de reação, densidade ou edge.

Artefatos: [protocolo](EQ_LEVELS_E2_PROTOCOL.md), [harness](eq_levels_e2.py) e
[testes](test_eq_levels_e2.py), [holdout](eq_levels_e2_holdout.py) e
`research/eq_levels_e2_baseline.json`.
