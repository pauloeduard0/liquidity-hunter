# EQ E3 — resultado inicial do shadow

O shadow foi implementado em pesquisa e mantém R/N lado a lado no mesmo replay.
Ele não altera o detector, o lifecycle, a API ou o frontend. O teste de contrato
passa e registra divergências de densidade, eventos de publicação e estabilidade
de strength para uma futura revisão.

O próximo ensaio deve rodar o shadow nos 211 charts e medir custo de CPU/memória
antes de considerar qualquer feature flag. Uma flag de produção não é proposta
por este resultado.

Artefatos: [protocolo](EQ_LEVELS_E3_PROTOCOL.md), [shadow](eq_levels_e3_shadow.py)
e [teste](test_eq_levels_e3_shadow.py).
