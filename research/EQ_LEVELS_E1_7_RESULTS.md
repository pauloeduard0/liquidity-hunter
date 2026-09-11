# EQ E1.7 — holdout temporal

E1.7 usa o baseline E1 já congelado e reserva o quarto bloco temporal (`block=3`)
como holdout. O script não recalcula thresholds nem escolhe símbolos; ele apenas
extrai, por TF/lado e braço R/N/L, qualidade em horizonte 10, densidade e sinais
de restart.

O holdout é necessário porque os sinais de E1 variam por TF e lado. A decisão
deve usar o bloco final e a mediana por símbolo, além do agregado. O resultado
serve para confirmar ou rejeitar a hipótese N antes de qualquer alteração
produtiva. Um ganho isolado em um TF, lado ou período não passa o critério.

No bloco final, R e N continuam praticamente iguais: a diferença média em
`P(MFE > MAE)` foi 0,000 em M15/EQH, 0,000 em M15/EQL, +0,014 em H1/EQH,
0,000 em H1/EQL, 0,000 em H4/EQH e +0,0002 em H4/EQL. As medianas por símbolo
foram 0,0 em todos os grupos. L variou de -0,090 a +0,077, sem direção
consistente. Portanto o holdout não fornece evidência robusta para ativar N ou L
em produção.

O artefato JSON é gitignored. O teste garante que R, N e L permanecem juntos e
que a seleção do holdout é fixa. Thresholds, lifecycle, clustering e UI não são
alterados.

Artefatos: [analisador](eq_levels_e1_7.py), [teste](test_eq_levels_e1_7.py) e
`research/eq_levels_e1_7_baseline.json`.
