# EQ E3.3 — atribuição das divergências

Objetivo: explicar a diferença de membership R/N do E3 por símbolo, timeframe,
lado e quatro blocos de mesmo número de candles. Os blocos são relativos a cada
chart, não períodos de calendário comuns a todos os ativos.

## Método

1. Usar exatamente as identidades do manifesto E0 e do shadow E3, sem duplicatas.
   Conferir SHA-256 de cada fixture antes de executar; excluir a última barra,
   como nos estudos anteriores.
2. Executar o Replay existente, sem alterar parâmetros nem políticas R/N.
   Verificar R contra o detector nativo a cada 100 barras e na barra final.
3. Observar a confirmação de pivôs separadamente em EQH/EQL. Guardar a composição
   R dessa confirmação como referência. N deve continuar igual à referência até
   a confirmação seguinte; no instante da confirmação, R e N devem coincidir.
4. Contar identidades apenas em R e apenas em N, com subconjuntos ativos
   (`not is_mitigated`). Uma exposição é uma identidade em uma barra/lado; ela
   não equivale a uma oportunidade independente ou trade.
5. Registrar mudanças de R sem novo pivô, início de episódios divergentes e
   maior distância, em barras, à última confirmação. Atribuir um snapshot ao
   relógio de pivôs apenas quando as invariantes da referência são satisfeitas.
6. Comparar semântica geométrica/lifecycle e strength das identidades comuns
   separadamente. A atribuição de membership não explica automaticamente essas
   outras diferenças.
7. Reconciliar todos os campos de `shadow_metrics` com o baseline salvo; qualquer
   diferença aborta a execução. Guardar um exemplo completo do início de episódio
   por chart/lado/bloco, incluindo tokens e timestamps dos pivôs.

## Limites

Esta é uma auditoria mecânica da implementação do N, não uma estimativa de
qualidade ou superioridade. A paridade nativa é amostrada em checkpoints; o
replay causal compartilha as primitivas do detector. A auditoria não mede
memória, não valida restart e não implementa rollback. Seu tempo inclui
instrumentação adicional e não substitui o orçamento do shadow E3.

Código: [eq_levels_e3_3.py](eq_levels_e3_3.py).
Testes: [test_eq_levels_e3_3.py](test_eq_levels_e3_3.py).
Execução: `poetry run python -m research.eq_levels_e3_3`.
Saída local: `research/.replay_cache/eq_e3_3.json` (ignorada pelo Git).
