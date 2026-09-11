# EQ E3.2 — decisão operacional no baseline real

Aplicando E3.1 ao baseline completo de 211 charts, com orçamento operacional
de 300 segundos, o painel termina em 217,75 segundos e contém divergências R/N
em todos os grupos observados. A decisão resultante é `shadow-only`, como
exigido pelo contrato.

Isso confirma que a instrumentação não promoveria automaticamente o candidato
quando a superfície causal diverge do detector atual. O orçamento de tempo é
operacional e não altera qualquer parâmetro do indicador.

Artefatos: [aplicador](eq_levels_e3_2.py) e [teste](test_eq_levels_e3_2.py).
