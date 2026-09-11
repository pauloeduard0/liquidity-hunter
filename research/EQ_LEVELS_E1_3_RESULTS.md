# EQ E1.3 — políticas de retenção

E1.3 mediu o custo de preservar causalidade depois de E1.2. O detector e os parâmetros permanecem intocados.

## Políticas

- **Full:** checkpoint com toda a origem dos candles. É o oráculo de restart.
- **Compact-ledger:** versões publicadas, pivôs, ancestrais, consumo, acumuladores e onze candles finais. Foi medido como formato, mas ainda não tem engine de restore.
- **Tail:** últimos 30, 100 ou 300 candles. É um controle deliberadamente inválido para medir perda de contexto.

No piloto BTC M15/H1/H4, o full restore reproduziu a execução contínua. O ledger compacto é menor que o checkpoint completo nos três casos testados (aproximadamente 20–26% do tamanho), mas não pode ser promovido para produção até haver um restaurador que reconstrua exatamente os mesmos memberships e lifecycle. O estudo amplo de 211 charts fica para depois de validar o restaurador, evitando gastar o painel em um formato ainda não executável.

O tail curto perde a origem necessária para confirmação e linhagem. A divergência é medida em IDs de níveis adicionados e removidos no baseline; não foi usada para selecionar um tamanho “bom”. A janela de 300 é uma conveniência de diagnóstico, não uma recomendação de retenção.

Em BTC com 1.199 candles, o estado completo ocupa aproximadamente 214–216 KB e o payload EQ 8–9 KB. O tamanho do ledger e a divergência de cada janela estão no JSON gitignored.

## Decisão

Não usar janela limitada como solução causal. Não implementar ainda o ledger compacto: faltam restauração determinística de acumuladores, pivôs confirmados, membership ativo, histórico de eventos e migração de schema.

A próxima etapa é E1.4: construir um restaurador compacto de pesquisa e exigir igualdade byte a byte dos snapshots N após restart, append, gap, duplicação e rollover de cache. Só depois medir custo de storage em escala e preparar uma proposta produtiva.

Artefatos: [harness](eq_levels_e1_3.py), [testes](test_eq_levels_e1_3.py) e `research/eq_levels_e1_3_baseline.json`.
