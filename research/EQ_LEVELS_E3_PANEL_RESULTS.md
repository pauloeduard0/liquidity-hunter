# EQ E3 — shadow completo

O shadow completo executa R/N nos 211 charts do baseline E1, sem modificar
produção. Cada linha registra snapshots divergentes, máximo/soma da diferença,
publicações e contagem de eventos; o baseline também registra tempo total.

Este painel mede custo e superfície de divergência. Uma divergência não é um
erro por si só: N foi desenhado para preservar identidade e strength enquanto
R acompanha a recomposição nativa. Só uma comparação posterior de payload,
qualidade e operação poderia autorizar uma flag.

Resultado: 211 charts em 217,75 s. A média de snapshots divergentes por chart
foi 124,2 no M15, 181,8 no H1 e 154,2 no H4. O maior delta instantâneo foi 17,
18 e 16 níveis, respectivamente; houve 13.142 versões publicadas no conjunto.
O custo é mensurável e a superfície de divergência é material, portanto uma
feature flag futura exigiria observabilidade própria e rollout reversível.

Artefato: `research/eq_levels_e3_panel_baseline.json` (gitignored), gerado por
`eq_levels_e3_shadow.py`.

Na divisão em quatro blocos, a soma de snapshots divergentes por TF foi:

| TF | bloco 0 | bloco 1 | bloco 2 | bloco 3 |
| --- | ---: | ---: | ---: | ---: |
| M15 | 1.031 | 1.865 | 2.679 | 2.706 |
| H1 | 646 | 2.127 | 5.199 | 3.493 |
| H4 | 1.075 | 2.385 | 3.123 | 3.652 |

A divergência não existe apenas no último período; ela cresce e muda de
concentração ao longo do histórico. Isso mantém o candidato no gate
`shadow-only` e impede justificar a diferença como um acidente de uma janela.
