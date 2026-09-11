# Dominant Liquidity D3 — calibração relativa por família

Pergunta: a `strength` sobrevive se for lida como **posição relativa dentro da
própria família**, em vez de número bruto comparado entre famílias
incomensuráveis? Nada de produção foi alterado.

Amostra: as mesmas 211 fixtures, mesma grade causal — 2.030 observações, 41.597
candidatos. Curva de distância congelada na sobrevivente do D2
(`LINEAR_5_ATR`), para isolar o canal de strength (D3.3). Peso não otimizado: o
valor normalizado entra no mesmo canal de 0,4 (D3.5).

## Conclusão

Resultado **B da D3.18, encostando no D**. O `FAMILY_RANK` conserta tudo que é
*comportamento* — mata a dependência de janela (0,00%), reduz a patologia de
18,04% para 6,36% e mantém as duas famílias vivas (21,5% de EQ) — **e não
acrescenta nada que seja *informação***: com distância controlada, o rank
separa o desfecho tão mal quanto a strength bruta já separava no D1. Pelo
critério pré-registrado da D3.11, isso significa que **a strength deve sair**;
mas tirá-la entrega um card swing-only (8,8% de EQ), o que joga a pergunta para
a arquitetura do card, não para a calibração.

**20 = NÃO**: não há evidência para mudar produção.

## D3.0/D3.1 — Pré-registro

Nenhuma tentativa de inventar uma strength absoluta nova. As três variantes
foram fixadas antes de qualquer desfecho desta rodada:

| Variante | Definição | Parâmetros livres |
|---|---|---|
| `FAMILY_RANK` | percentil midrank do candidato entre os candidatos **da própria família visíveis naquela observação**, em [0, 100] | nenhum, exceto a regra de família unitária |
| `FAMILY_ROBUST` | o mesmo percentil, contra o histórico causal em expansão daquela família naquele símbolo/TF (candidatos de observações estritamente anteriores) | aquecimento de 20 amostras |
| `NO_STRENGTH` | canal desligado | — |

**Família de um único membro pontua 50 (neutro), não 100.** Um membro sozinho
não carrega informação relativa, e dar-lhe o canal inteiro reconstruiria
exatamente o viés que a rodada existe para evitar. Foram 332 casos.
EQ e swing nunca são normalizados juntos.

## D3.2 — Causalidade

O `FAMILY_RANK` usa apenas os candidatos daquela observação; o `FAMILY_ROBUST`
apenas observações anteriores — a história é atualizada **depois** de pontuar,
nunca antes. Testes: `test_robust_rank_is_neutral_until_the_warmup_is_met`,
`test_robust_rank_reads_history_without_writing_to_it`,
`test_observe_leaves_history_to_the_caller_so_it_stays_strictly_past` e
`test_ranks_of_a_prefix_do_not_depend_on_later_observations`.

## Os canais, como distribuições (0–100)

| Família | Canal | p10 | p50 | p90 | média | em 100 | em 50 (neutro) |
|---|---|---:|---:|---:|---:|---:|---:|
| EQ | atual | 5,68 | 30,24 | 80,82 | 36,83 | 5,3% | 0,0% |
| EQ | rank | 0,00 | 50,00 | 100,00 | 50,00 | 23,2% | 16,9% |
| EQ | robust | 40,91 | 50,00 | 77,78 | 52,12 | 1,9% | 69,5% |
| SW | atual | 0,16 | **0,85** | 3,68 | **1,68** | 0,0% | 0,0% |
| SW | rank | 7,89 | 50,00 | 92,31 | 50,00 | 5,6% | 2,9% |
| SW | robust | 4,17 | 46,60 | 81,58 | 42,15 | 0,5% | 15,8% |

O rank resolve a incomensurabilidade por construção: as duas famílias passam a
ter média 50 e a mesma amplitude. O preço disso também está na tabela — 23,2%
dos EQ ficam em 100 (são o melhor EQ da tela, muitas vezes entre dois), e o
`robust` fica 69,5% neutro no EQ, porque o aquecimento raramente é atingido em
uma família pequena.

## D3.6 — Share de família do vencedor

| Braço | 15m | 1h | 4h | geral |
|---|---:|---:|---:|---:|
| LEGACY | 51,3% | 43,5% | 63,1% | 52,6% |
| L5_CURRENT | 45,9% | 41,5% | 58,1% | 48,4% |
| L5_NO_STRENGTH | 8,4% | 6,6% | 11,5% | **8,8%** |
| L5_FAMILY_RANK | 19,3% | 21,0% | 24,3% | **21,5%** |
| L5_FAMILY_ROBUST | 11,5% | 8,1% | 16,6% | 12,0% |

Sem exigir 50/50: o `FAMILY_RANK` é o único braço que não colapsa nem para EQ
(LEGACY, 63,1% no H4) nem para swing (`NO_STRENGTH`, 8,8%), e é o mais uniforme
entre TFs (19–24%).

## D3.7 — Distância dos vencedores

| Braço | TF | p50 | p75 | p90 | >1 | >3 | >5 |
|---|---|---:|---:|---:|---:|---:|---:|
| LEGACY | 4h | 1,68 | 7,51 | 16,65 | 67,5% | 40,6% | 32,7% |
| L5_CURRENT | 4h | 1,47 | 3,28 | 12,81 | 67,9% | 26,3% | 18,4% |
| L5_FAMILY_RANK | 15m | 1,68 | 2,57 | 3,63 | 80,7% | 15,3% | 5,6% |
| L5_FAMILY_RANK | 1h | 1,68 | 2,61 | 3,86 | 78,5% | 18,7% | 8,4% |
| L5_FAMILY_RANK | 4h | 1,62 | 2,56 | **4,16** | 78,5% | **17,0%** | 8,4% |
| L5_FAMILY_ROBUST | 4h | 1,41 | 2,17 | 3,67 | 71,0% | 13,6% | 7,2% |
| L5_NO_STRENGTH | 4h | 0,93 | 1,50 | 2,26 | 45,7% | 5,2% | 1,2% |

O `FAMILY_RANK` é o primeiro braço com strength cujo p90 é **da mesma ordem nos
três TFs** (3,63 / 3,86 / 4,16 ATR, contra 5,99 / 7,57 / 16,65 da LEGACY).
Ele fica ligeiramente mais longe que o `NO_STRENGTH` (p50 1,65 contra 1,03),
que é o preço de não ser a regra do nível mais próximo.

## D3.8 — A patologia (≤1 ATR disponível, vencedor >3 ATR)

Sobre as 959 observações elegíveis:

| Braço | distantes | % | EQ | SW |
|---|---:|---:|---:|---:|
| LEGACY | 173 | 18,04% | **172** | 1 |
| L5_CURRENT | 112 | 11,68% | **112** | 0 |
| L5_FAMILY_RANK | 61 | **6,36%** | 19 | 42 |
| L5_FAMILY_ROBUST | 48 | **5,01%** | 7 | 41 |
| L5_NO_STRENGTH | 0 | 0,00% | 0 | 0 |

Duas leituras. A patologia cai por dois terços **e deixa de ser um fenômeno de
uma família só** — 19 EQ contra 42 swings, contra 172/1 na LEGACY. Ou seja: o
que sobra não é mais "um EQ forte e distante ganhou de um swing perto", é
"o melhor da família estava longe", que é o comportamento que a regra declara.

## D3.9 — Reachability (pareada)

| Braço | h5 | h10 | h20 | h40 | h80 |
|---|---:|---:|---:|---:|---:|
| LEGACY | 30,84% | 41,03% | 49,21% | 56,65% | 63,79% |
| L5_CURRENT | 33,15% | 44,19% | 52,86% | 60,84% | 67,93% |
| L5_FAMILY_RANK | 25,27% | 37,68% | 48,52% | 59,06% | **68,37%** |
| L5_FAMILY_ROBUST | 30,05% | 43,10% | 52,76% | 63,69% | 72,61% |
| L5_NO_STRENGTH | 41,23% | 55,02% | 64,68% | 74,43% | 81,72% |

No holdout (blocos 2–3) a ordem se mantém: LEGACY 41,58% em h20, `FAMILY_RANK`
45,22%, `NO_STRENGTH` 63,84%. Como sempre, **a vantagem do `NO_STRENGTH` é
geométrica** (ele é o nível mais próximo) e não é evidência.

## D3.10 — Reação depois do contato (MFE = rejeição, MAE = excursão além)

| Braço | Família | n | through | MFE | MAE | MFE/MAE |
|---|---|---:|---:|---:|---:|---:|
| LEGACY | EQ | 477 | 57,2% | 1,490 | 0,980 | 0,743 |
| LEGACY | SW | 772 | 74,7% | 1,359 | 1,335 | 0,844 |
| L5_FAMILY_RANK | EQ | 264 | 59,1% | 1,358 | 0,905 | 0,667 |
| L5_FAMILY_RANK | SW | 1.063 | 72,4% | 1,495 | 1,360 | 0,831 |
| L5_FAMILY_ROBUST | EQ | 195 | 58,5% | 1,480 | 0,955 | 0,760 |
| L5_NO_STRENGTH | SW | 1.469 | 74,1% | 1,371 | 1,325 | 0,809 |

Nada se move materialmente. Nenhum braço degrada a reação; nenhum a melhora.
(`through` não compara famílias: a banda EQ tem largura e o swing é um ponto.)

## D3.11 — O teste decisivo: o rank agrega sobre não ter strength nenhuma?

**Não.**

**(a) Nível de candidato, com distância controlada** — mesma estratificação do
D1 (símbolo × TF × lado × bucket de ATR × bloco), split pela mediana da célula,
desfecho contato@20:

| Canal | Família | n | alto | baixo | gap | células | ganha/perde |
|---|---|---:|---:|---:|---:|---:|---:|
| bruto (D1) | EQ | 6.181 | 1,48% | 1,27% | +0,21pp | 268 | 10/7 |
| bruto (D1) | SW | 35.416 | 2,88% | 3,48% | −0,60pp | 1.372 | 108/139 |
| family rank | EQ | 6.181 | 1,27% | 1,41% | **−0,14pp** | 274 | 8/10 |
| family rank | SW | 35.416 | 2,95% | 3,42% | **−0,48pp** | 1.374 | 111/127 |
| robust | EQ | 6.181 | 1,14% | 1,01% | +0,14pp | 145 | 4/3 |
| robust | SW | 35.416 | 2,90% | 3,56% | −0,66pp | 1.185 | 108/121 |

Renormalizar não cria informação onde não havia: o rank separa o desfecho
exatamente tão mal quanto a strength bruta separava.

**(b) Nível de vencedor, com distância casada** (pares em que o braço e o
`NO_STRENGTH` escolhem níveis a menos de 0,25 ATR um do outro):

| Braço | pares | contato@20 (braço / sem strength) | through | MFE |
|---|---:|---|---|---|
| L5_FAMILY_RANK | 216 | 68,98% / 67,59% | 73,7% / 71,7% | 1,406 / 1,415 |
| L5_FAMILY_ROBUST | 157 | 64,97% / 66,88% | 71,5% / 72,4% | 1,583 / 1,433 |
| L5_CURRENT | 152 | 73,03% / 67,11% | 60,0% / 69,2% | 1,522 / 1,555 |

Empate dentro do ruído para o rank. (A comparação **não casada** — braço a 2,00
ATR contra 1,00 ATR — dá 41,40% contra 66,59% e não significa nada: é a
vantagem geométrica de novo.)

## D3.12 — Qualidade do vencedor dentro da própria família

| Braço | percentil de família mediano | é o topo da família | d_atr mediano |
|---|---:|---:|---:|
| LEGACY | 75,00 | 37,1% | 1,53 |
| L5_CURRENT | 66,67 | 32,2% | 1,42 |
| L5_FAMILY_RANK | **87,50** | 32,2% | 1,65 |
| L5_FAMILY_ROBUST | 77,78 | 18,5% | 1,47 |
| L5_NO_STRENGTH | 45,00 | 7,3% | 1,03 |

O `FAMILY_RANK` entrega o que promete: vencedores bem colocados dentro da
própria família (percentil mediano 87,5), sem ficarem muito mais distantes
(1,65 contra 1,53 ATR da LEGACY) e sem degradar a reação. **O que ele não tem é
prova de que isso importe para o desfecho** (§D3.11).

## D3.13 — Dependência de janela: resolvida

Mesmos pivôs, +100 barras, **população mantida fixa** (só os pivôs presentes
nos dois prefixos, para que um rank que se mexe signifique normalização e não
população nova):

| TF | passos | pivôs | strength bruta mudou | rank de família mudou |
|---|---:|---:|---:|---:|
| 15m | 612 | 90.951 | 43,29% | **0,00%** |
| 1h | 612 | 90.939 | 43,18% | **0,00%** |
| 4h | 603 | 94.182 | 49,71% | **0,00%** |

Total: bruta 45,44%, rank **0,00%**. Não é sorte amostral, é álgebra: todos os
swings de um prefixo dividem a proeminência pelo *mesmo* `price_range`, e um
rank é invariante a rescala comum. O teste
`test_family_rank_is_invariant_to_a_shared_rescaling` fixa a propriedade.
**Sim (13): o family-rank elimina o defeito de janela por completo.**

## D3.14 — Tie-break (diagnóstico, nada misturado)

Desempate mantido como está em todos os braços. Só medindo: proximidade moveria
0,34% na LEGACY, 0,20% no `L5_CURRENT`, 1,28% no `FAMILY_RANK`, 1,13% no
`NO_STRENGTH`. Continua irrelevante; fica para depois.

## D3.15/D3.16 — Holdout e robustez

Holdout = blocos 2–3 (1.015 observações), **limitação declarada**: é corte
temporal dentro das mesmas séries, não período independente — é o único dado
disponível sem nova coleta. Nenhuma variante foi escolhida olhando o holdout;
as três estavam fixadas antes.

Taxa de vencedor distante por bloco e por símbolo (contra a LEGACY):

| Braço | símbolos melhores | piores | mediana | pior caso | b0 | b1 | b2 | b3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| L5_CURRENT | 41 | 2 | −6,67pp | +10,00pp | 0,00% | 4,45% | 20,93% | 18,95% |
| L5_FAMILY_RANK | **52** | 7 | −11,44pp | +16,67pp | 9,09% | 3,08% | 9,63% | 3,68% |
| L5_FAMILY_ROBUST | 54 | 4 | −12,92pp | +13,33pp | 0,00% | 2,74% | 9,97% | 5,26% |
| L5_NO_STRENGTH | 61 | 0 | −16,03pp | +0,00pp | 0,00% | 0,00% | 0,00% | 0,00% |

O `FAMILY_RANK` melhora em 52 de 68 símbolos e é o único braço com strength
cuja taxa não explode nos blocos tardios (3,7–9,6% contra 18,9–20,9% do
`L5_CURRENT`). Lado: vencedor acima do preço em 45,9%, sem viés direcional novo.

## D3.17 — Interpretabilidade

Contribuição média de cada canal para o vencedor (em pontos do composite):

| Braço | distância | strength | venceu o mais próximo **na strength** |
|---|---:|---:|---:|
| LEGACY | 22,14 | 13,53 | 42,86% |
| L5_CURRENT | 23,98 | 12,53 | 41,97% |
| L5_FAMILY_RANK | 24,19 | **33,14** | **63,99%** |
| L5_FAMILY_ROBUST | 25,75 | 26,45 | 46,50% |
| L5_NO_STRENGTH | 29,77 | 0,00 | 0,00% |

A frase do `FAMILY_RANK` é legível — "é o melhor EQ (ou o melhor swing) da tela
entre os que estão perto" — e essa é a sua vantagem de explicação sobre o
número bruto atual, que não tem frase nenhuma. Mas a tabela também mostra um
efeito colateral que a D3.5 mandou registrar: **com o canal normalizado para
média 50, a strength passa a contribuir mais que a distância** (33,1 contra
24,2 pontos), e o vencedor bate o nível mais próximo *na strength* em 64% das
observações, contra 43% hoje. Com o peso atual, o rank domina. Registrado, não
ajustado.

## Respostas às perguntas

1. **Rank midrank dentro da própria família**, entre os candidatos visíveis
   naquela observação; família unitária = 50.
2. **Sim**, causal, com quatro testes.
3. EQ 52,6% (LEGACY) → 48,4% (L5) → **21,5%** (rank) → 12,0% (robust) → 8,8%
   (sem strength).
4. Rank: p50 ~1,65 ATR e p90 3,63/3,86/4,16 — a primeira vez que os três TFs
   ficam na mesma ordem de grandeza.
5. Patologia 18,04% → **6,36%**, e sem dominância de família (19 EQ / 42 SW).
6. Rank ≈ LEGACY em h20 (48,5% vs 49,2%) e melhor em h80 (68,4% vs 63,8%).
7. Reação não muda materialmente em nenhum braço.
8. **Não agrega** (§D3.11, a e b).
9. EQ deixa de dominar. 10. Swing não passa a dominar (21,5% ainda é EQ).
11. H4 melhora (>3 ATR 40,6% → 17,0%; p90 16,65 → 4,16 ATR).
12. M15/H1 preservados e melhorados (24,9% → 15,3% e 23,8% → 18,7%).
13. **Sim**, dependência de janela zerada (0,00% contra 45,44%).
14. Tie-break move 1,28% no rank: continua irrelevante.
15. Holdout confirma a ordenação; limitação declarada.
16. Robusto: 52 de 68 símbolos melhoram, 7 pioram, mediana −11,44pp.
17. Mais interpretável: o `FAMILY_RANK` (tem frase; o número bruto não tem).
18. **Sim, um viés novo**: com o peso atual, o canal normalizado passa a pesar
    mais que a distância (33,1 contra 24,2 pontos). Não foi ajustado.
19. **Provavelmente sim** — ver abaixo.
20. **NÃO.** 21. Ver abaixo.

## 19/21 — A arquitetura, e a mudança mínima

Três rodadas apontam para o mesmo lugar: **não existe arbitragem entre um EQ e
um swing que se justifique por medição.** O D1 mostrou que as escalas são
incomensuráveis; o D2, que nenhuma curva de distância conserta isso; o D3, que
nem a normalização por família cria informação — ela só *deixa de destruir*.
Qualquer ranking único precisa escolher entre famílias em algum ponto, e nenhum
dado desta pesquisa diz como escolher.

A alternativa que o próprio card sugere é **não escolher**: mostrar o melhor
nível de cada família em vez de um único vencedor. Isso dissolve a pergunta
incomensurável em vez de respondê-la, e é a única saída que nenhuma das três
rodadas contradiz. **Não está medida** — é uma observação de arquitetura, não um
achado, e mudar o card é decisão de produto.

Mudança mínima, se e quando houver decisão de mexer: trocar a curva de
distância pela `LINEAR_5_ATR` (D2: ganho limpo, sem contrapartida medida) e
tratar a strength como **desempate**, não como canal de 40 pontos — porque é
isso que os dados sustentam: ela não prevê nada, mas ordena dentro da família
sem repaint. Ambas dependem de um D4 com critério de utilidade definido antes,
e nenhuma delas está proposta aqui.

## Decisão

**20. Existe evidência suficiente para mudar produção? NÃO.**

O `FAMILY_RANK` passa em todos os critérios *comportamentais* (família,
patologia, TF, janela, robustez, interpretabilidade) e falha no único critério
de *informação* que a rodada pré-registrou (D3.11). Promovê-lo seria promover
uma regra mais bonita, não uma regra mais certa — e ela ainda traz um viés novo
de peso (§D3.17) que só seria resolvido otimizando o peso, o que esta rodada
proíbe. Nada foi alterado: nem pesos, `distance_score`, `touch_score`,
desempate, EQ, swing ou frontend.

## Limites

Contato é interseção geométrica com a banda original; níveis próximos e bandas
largas o vencem por construção. O holdout é bloco temporal das mesmas séries.
As observações compartilham histórico e níveis. `through` não compara famílias.
Nada aqui mede resultado financeiro.

## Validação e artefatos

15 testes novos do D3 passam; `ruff check` e `git diff --check` limpos;
`git status` sem nenhum arquivo de produção modificado.

[auditor](dominant_liquidity_calibration_audit.py),
[testes](test_dominant_liquidity_calibration_audit.py),
[D2](DOMINANT_LIQUIDITY_D2_DISTANCE.md), [D1](DOMINANT_LIQUIDITY_D1_STRENGTH.md).
Execução: `poetry run python -m research.dominant_liquidity_calibration_audit`
e `python -c "from research.dominant_liquidity_calibration_audit import report;
report()"`.
Baseline local: `research/.replay_cache/dominant_liquidity_d3_baseline.json`
(15,7 MB, gitignored).
