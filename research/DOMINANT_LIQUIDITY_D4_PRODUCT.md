# Dominant Liquidity D4 — arquitetura do card

Pergunta: os dados demonstram que EQ e swing devem ser reduzidos a **uma única
escala de dominância**? Nada de produção nem do frontend foi alterado.

Amostra: as mesmas 211 fixtures, mesma grade causal — 2.030 observações.
Nenhuma strength nova, nenhum peso otimizado, nenhuma curva nova: a distância é
a `LINEAR_5_ATR` do D2 e a strength só aparece como desempate exato.

## Conclusão

Resultado **C da D4.22, com um achado positivo inédito**. Pela primeira vez em
quatro rodadas alguma coisa separa: **com distância casada e medida neutra de
largura, um EQ é atravessado 9 a 11 pontos percentuais menos que um swing, e
isso replica nos quatro blocos**. Ou seja, as duas famílias não são duas
amostras da mesma coisa — e reduzi-las a uma escala de dominância destrói uma
distinção que existe, em troca de uma arbitragem que nenhuma medição justifica.

Isso **não** é evidência para mudar produção (21 = NÃO): a escolha entre um
vencedor único mais simples e dois níveis separados é decisão de produto, e a
medição só diz o que *não* fazer — comparar strengths entre famílias.

## D4.0 — O que "Dominant Liquidity" pretende significar hoje

Resposta: **(E), mistura histórica sem definição explícita.** A evidência está
no próprio projeto, e é contraditória em três pontos:

1. **A documentação descreve um mecanismo que não existe mais.**
   `docs/scoring.md` §2 diz que `touch_score` é "a proxy for number of touches"
   e que, no EQ, `strength` "increases with the number of swing points grouped
   into the zone (more touches → higher strength)". O detector diz o oposto,
   com medição: *"Touch count did not survive. It saturated at 1.0 for any
   group of 3+ touches"* (`liquidity/detectors/equal_levels.py`), e foi
   substituído por volume de área. O card é explicado por uma regra aposentada.
2. **Um quinto do composite não ordena nada.** `timeframe_score` vale 20% do
   score, mas o card é de um timeframe só: dentro de um chart a parcela é
   constante para todos os candidatos. A documentação a apresenta como fator de
   ranking; na prática o ranking é decidido só por distância e strength.
3. **A própria UI já discorda de si mesma.** O `KpiRow.tsx` mostra o primeiro
   colocado do composite. O `MainChart.tsx` pega o *mesmo* `ranked_zones`,
   **descarta o score**, filtra só os pools EQ, mantém os que estão à frente do
   preço no lado de onde são caçados, ordena **por proximidade** e desenha os
   dois mais próximos **de cada lado** (`byProximity`, `balancedTake`,
   `NEAREST_POOLS_PER_SIDE = 2`), com o comentário de que swings isolados "just
   clutter the chart". Isto é: o gráfico já roda, em produção, uma arquitetura
   por lado e por família, enquanto o card roda o vencedor único.

Nenhuma das definições possíveis (A alcance, B importância, C ímã, D melhor de
cada lado) está escrita em lugar nenhum. **Registrado como problema de
arquitetura**, e não escolhido retrospectivamente por desempenho.

## D4.1/D4.2 — Braços e desempate

`LEGACY` (produção), `SINGLE` (distância `LINEAR_5_ATR`, strength só em empate
exato) e `DUAL` (melhor EQ + melhor swing, cada um dentro da própria família,
pela mesma regra). Ordem determinística: (1) maior `distance_score`; (2) menor
distância em ATR — é o passo que decide dentro da zona saturada; (3) empate
exato de distância → maior strength **da própria família**; (4) fallback
estável por (formação, banda), nunca por ordem de emissão dos detectores.

**O passo 3 foi consultado em 61 observações (3,0%).** Exemplo real: ADAUSDT 1h
2026-07-31 05:00, um `swing_high` (strength 0,0769) venceu um `swing_low`
(0,0128) exatamente a 4,5439 ATR — os dois em **lados opostos** do preço, o que
já é um comentário sobre a pergunta que o ranking único faz.

## D4.4/D4.11 — SINGLE contra LEGACY

| Braço | TF | p50 | p75 | p90 | >3 ATR | >5 ATR | share EQ |
|---|---|---:|---:|---:|---:|---:|---:|
| LEGACY | 15m | 1,52 | 2,98 | 5,99 | 24,9% | 13,4% | 51,3% |
| SINGLE | 15m | 1,08 | 1,58 | 2,31 | 5,0% | 0,9% | 7,6% |
| LEGACY | 1h | 1,45 | 2,88 | 7,57 | 23,8% | 15,6% | 43,5% |
| SINGLE | 1h | 1,09 | 1,63 | 2,62 | 6,6% | 1,0% | 5,9% |
| LEGACY | 4h | 1,68 | 7,51 | **16,65** | **40,6%** | 32,7% | 63,1% |
| SINGLE | 4h | 0,93 | 1,50 | **2,26** | **5,2%** | 1,2% | 11,0% |

Muda o vencedor em 48,77% das observações (902 EQ→swing, **zero** swing→EQ, 426
trocas de lado), com a distância mediana dos casos alterados caindo de 3,93 para
1,23 ATR. Patologia (havia nível ≤1 ATR, venceu um >3 ATR): **18,04% → 0,00%**.

**Sim, o ranking simples vira quase swing-only** (8,2% de EQ no conjunto; 6 de
68 símbolos nunca elegem um EQ). E o efeito colateral conceitual é maior que o
numérico: um card chamado *Dominant Liquidity* que mostra sempre o nível mais
próximo está descrevendo geometria, não dominância.

## D4.5/D4.13 — DUAL: disponibilidade e densidade

Ambos existem em **90,25%** das observações (15m 88,8%, 1h 89,3%, 4h 92,7%);
só swing em 9,75%; **nunca só EQ e nunca nenhum**. O card dual mostraria dois
níveis em 9 de cada 10 leituras, um em 1 de cada 10 — previsível, mas é preciso
dizer sem eufemismo: **na prática o card vira sempre dois números**, e isso é o
custo real do desenho, não "0 a 2 níveis conforme a situação".

## D4.6 — Mesmo lado ou lados opostos

Entre as 1.832 observações com os dois: **mesmo lado 55,95%, lados opostos
44,05%**. No mesmo lado, o EQ é o mais próximo em apenas 14,93%, e a distância
mediana entre os dois é 1,34 ATR. Em lados opostos, o EQ está a 4,49 ATR e o
swing a 1,00 ATR (medianas).

Ou seja: **em 44% das leituras o ranking único está elegendo "o nível dominante"
entre dois níveis em direções opostas** — respondendo se a liquidez acima é
mais dominante que a abaixo, pergunta que nenhuma parte do projeto define.

## D4.7 — Diagnóstico: o melhor acima e o melhor abaixo

| TF | acima: disponível / mediana / EQ | abaixo: disponível / mediana / EQ |
|---|---|---|
| 15m | 96,9% / 1,56 ATR / 7,1% | 99,4% / 1,60 ATR / 7,8% |
| 1h | 95,7% / 1,76 ATR / 9,7% | 97,2% / 1,53 ATR / 6,4% |
| 4h | 99,6% / 1,64 ATR / 9,6% | 93,0% / 1,24 ATR / 14,6% |

Os dois lados quase sempre existem e ficam a distâncias parecidas. Um mapa de
dois lados é factível; **nenhuma UI é proposta aqui**.

## D4.8 — Reachability (geometria favorece o braço mais próximo)

| Braço | h5 | h10 | h20 | h40 | h80 |
|---|---:|---:|---:|---:|---:|
| LEGACY | 30,84% | 41,03% | 49,21% | 56,65% | 63,79% |
| SINGLE | 41,28% | 55,12% | 64,68% | 74,43% | 81,67% |
| BEST_SWING | 39,95% | 53,99% | 63,84% | 73,99% | 81,43% |
| BEST_EQ | 15,01% | 23,03% | 29,80% | 36,46% | 46,23% |

O EQ é muito menos alcançável — e isso é quase todo distância (o melhor EQ está
tipicamente 2,8 ATR mais longe que o melhor swing). **Não decide arquitetura.**

## D4.9/D4.12 — Reação: os papéis são diferentes?

O D1 registrou que `through` na borda da banda não compara famílias (o pool EQ
tem 0,3–0,5 ATR de largura, o swing é um ponto). Esta rodada mede também uma
versão **neutra de largura**: os dois julgados a partir do **ponto médio**, com
uma folga fixa de 0,25 ATR para contar como rompido.

Pares dentro da mesma observação, casados por bucket de distância:

| Bucket | Família | n | through (borda) | through (ponto médio) | MFE | MAE | MFE/MAE |
|---|---|---:|---:|---:|---:|---:|---:|
| 0–1 ATR | EQ | 137 | 62,0% | **62,0%** | 1,617 | 1,339 | 0,839 |
| 0–1 ATR | swing | 133 | 75,2% | **69,9%** | 1,551 | 1,517 | 0,746 |
| 1–2 ATR | EQ | 144 | 61,1% | **59,7%** | 1,568 | 1,073 | 0,993 |
| 1–2 ATR | swing | 152 | 74,3% | **69,1%** | 1,344 | 1,497 | 0,676 |

E replica nos quatro blocos (through, ambos ≤2 ATR, neutro de largura):

| Bloco | n | EQ | swing | gap |
|---|---:|---:|---:|---:|
| b0 | 71 | 64,8% | 76,1% | **−11,3pp** |
| b1 | 122 | 63,9% | 73,0% | **−9,0pp** |
| b2 | 96 | 57,3% | 66,7% | **−9,4pp** |
| b3 | 107 | 57,9% | 69,2% | **−11,2pp** |

**Sim (12): os papéis são diferentes.** O contato é praticamente igual quando a
distância é igual (0–1 ATR: 94,4% contra 88,7%; 1–2 ATR: 62,8% contra 63,4%),
mas **o que acontece depois não é**: o pool EQ segura, o swing é atravessado.
Mesmo sinal, mesma ordem de grandeza, nos quatro blocos, com a métrica corrigida
para o confundidor que o D1 identificou. É o primeiro achado positivo da série
— e continua sendo uma métrica só, com n de 71 a 152 por bloco.

## D4.10/D4.12 — O que um vencedor único esconde

Quando o `SINGLE` escolhe um swing (1.666 casos), o melhor EQ está a 4,12 ATR
(mediana), +2,78 ATR além do escolhido — normalmente longe, e esconder isso
custa pouco. Quando escolhe um EQ (166 casos), o melhor swing está a 1,08 ATR,
apenas +0,14 ATR atrás: aí o card está escolhendo entre dois níveis
praticamente equivalentes em distância.

Os dois têm candidato dentro de **1 ATR em 7,00%** das observações, **2 ATR em
25,17%** e **3 ATR em 38,62%**. E o vencedor da LEGACY é o melhor EQ em 35,57%
das vezes, o melhor swing em 44,63% e **nenhum dos dois em 19,80%** — um quinto
das leituras atuais elege um nível que não é o melhor da própria família.

## D4.14/D4.15/D4.16/D4.17 — H4, janela, tempo, símbolos

**H4:** corrigido nos dois desenhos — p90 do vencedor cai de 16,65 para 2,26
ATR e a patologia de 40,6% para 5,2%; o H4 passa a se parecer com M15/H1 em vez
de ser o caso patológico.

**Janela (D4.15):** a mesma observação lida com uma janela carregada mais curta
(600 barras) muda a escolha em **0,39%** na LEGACY e em **0,00%** no SINGLE e no
DUAL. Uma correção honesta ao que o D3 sugeriu: a strength de swing muda de
valor em 45% dos pivôs, mas isso raramente chega a mudar *quem vence* — o
defeito de janela contamina o número exibido muito mais do que a escolha. Nos
desenhos novos ele é zero por construção, porque uma rescala comum não altera
ordem (teste `test_a_shared_rescale_cannot_move_the_single_or_dual_pick`).

**Tempo (D4.16):** dual-both 65,8% / 95,7% / 95,4% / 98,8% por bloco (o b0 é
menor porque os prefixos curtos ainda têm poucos pools); patologia LEGACY
13,3% → 41,4% ao longo dos blocos contra SINGLE 6,7% → 3,9%; share EQ do SINGLE
entre 5,7% e 11,1%. Estável.

**Símbolos (D4.17):** share EQ do SINGLE por símbolo — mediana 6,67%, p10 3,33%,
p90 16,67%, e **6 de 68 símbolos nunca elegem um EQ**. DUAL com os dois
disponíveis — mediana 90,00%, p10 80,00%, p90 96,67%. Sem concentração.

## D4.18 — Casos concretos

- **(A)** BTCUSDT 15m 2026-09-04 15:00: LEGACY `equal_lows` a 3,39 ATR (strength
  0,517); SINGLE `swing_high` a 1,00 ATR; DUAL mostra os dois — e eles estão em
  lados opostos.
- **(B)** BTCUSDT 4h 2026-06-02 20:00: LEGACY acerta um `equal_lows` a 0,83 ATR,
  SINGLE concorda, e o melhor swing está a 0,86 ATR do mesmo lado — um caso em
  que o vencedor único é quase uma moeda ao ar.
- **(C)** BTCUSDT 15m 2026-09-02 13:00: EQ a 4,14 ATR e swing a 1,30 ATR, ambos
  acima — o DUAL mostra a escada; o card atual mostra um degrau.
- **(D)** BTCUSDT 15m 2026-08-31 11:00: `equal_lows` a 2,89 ATR abaixo e
  `swing_high` a 0,77 ATR acima. A LEGACY declara o de baixo "dominante".
- **(E)** ETHUSDT 4h 2026-04-13 20:00: todos os `distance_score` legacy zerados;
  LEGACY escolhe a 7,31 ATR, SINGLE a 4,00 ATR.
- **(F)** ADAUSDT 1h 2026-07-31 05:00: o desempate por strength realmente usado
  (ver §D4.2).

## Respostas

1. Não há definição única: **(E)**, mistura histórica — doc desatualizada,
   componente de timeframe inerte no card, e UI que já contradiz a si mesma.
2. **Não é coerente** (três contradições documentadas acima).
3. SINGLE: 8,2% de EQ (7,6 / 5,9 / 11,0% por TF).
4. **Sim**, patologia 18,04% → 0,00%.
5. **Sim**, estável entre TFs (p90 2,31 / 2,62 / 2,26 ATR).
6. DUAL mostraria 2 níveis em **90,25%** das observações.
7. Mesmo lado: **55,95%**. 8. Lados opostos: **44,05%**.
9. Dois candidatos dentro de 2 ATR em 25,17%; dentro de 3 ATR em 38,62%; e em
   19,80% a LEGACY elege quem não é o melhor de nenhuma família.
10. Contato: EQ 29,8% contra swing 63,8% em h20 — mas quase tudo é distância;
    casados por bucket, empatam.
11. Reação: EQ atravessado 9–11pp menos, nos quatro blocos, com métrica neutra
    de largura.
12. **Sim, papéis diferentes.**
13. SINGLE perde pouco quando escolhe swing (o EQ estava a +2,78 ATR) e perde
    informação real quando escolhe EQ (o swing estava a +0,14 ATR).
14. DUAL não é poluição, mas é **sempre dois números** em 90% das leituras.
15. **Sim**, H4 corrigido nos dois desenhos.
16. Dependência de janela: 0,00% no SINGLE/DUAL contra 0,39% na LEGACY — e o
    efeito real sobre a *escolha* sempre foi pequeno.
17. Temporalmente estável. 18. Sem concentração por símbolo.
19. **Não há justificativa medida para a arbitragem EQ-vs-swing.**
20. Ver abaixo. 21. **NÃO.** 22. Ver abaixo.

## 19/20 — A pergunta principal

**Não.** Quatro rodadas tentaram achar a escala comum e nenhuma achou: as
escalas brutas são incomensuráveis (D1), nenhuma curva de distância conserta
(D2), normalizar por família não cria informação (D3) — e agora o D4 mostra que
as duas famílias **se comportam de modo diferente depois do contato**, o que é o
argumento mais forte contra fundi-las: a fusão não está perdendo apenas
precisão, está apagando uma distinção real.

Conceitualmente, a ordem é **DUAL > SINGLE > LEGACY**:

- LEGACY é indefensável como está — arbitra com um canal que não prevê nada,
  numa escala que a própria documentação descreve errado.
- SINGLE é honesto e simples, mas o nome do card deixa de bater com o que ele
  faz: seria "nível mais próximo", não "liquidez dominante".
- DUAL é o único que não precisa responder a pergunta impossível. O custo é
  mostrar dois níveis quase sempre, e o benefício está apoiado no único achado
  positivo da série.

Mas isso é **superioridade conceitual, não superioridade medida**: nenhuma
métrica desta rodada diz que o usuário decide melhor com dois níveis. É decisão
de produto — resultado **C** da D4.22.

## 21/22 — Decisão

**21. Existe evidência suficiente para mudar produção? NÃO.**

A medição fecha o que *não* se deve fazer (comparar strengths entre famílias) e
não determina o que se deve mostrar. Nada foi alterado: nem ranking, nem pesos,
nem `distance_score`, nem desempate, nem EQ, nem swing, nem frontend.

**22. A mudança mínima**, se e quando houver decisão de produto — em ordem de
custo crescente, nenhuma implementada aqui:

1. **Corrigir a documentação** (`docs/scoring.md`): o `touch_score` não é proxy
   de contagem de toques desde 2026-08-19, e o `timeframe_score` não ordena nada
   dentro de um chart. Custo zero, risco zero, e é uma correção de fato errado.
2. Trocar a curva de distância pela `LINEAR_5_ATR` — o único ganho limpo e sem
   contrapartida medida de toda a série (D2).
3. Rebaixar a strength a desempate exato (é consultada em 3% das observações) —
   sustentado pelo D1/D3, e elimina a dependência de janela da escolha.
4. Só então, e como decisão de produto, discutir um vencedor único honesto
   (renomear o card) ou dois níveis por família — alinhando o card com o que o
   `MainChart` já faz.

## Limites

Contato é interseção geométrica com a banda original; níveis próximos o vencem
por construção. `through` neutro de largura usa uma folga fixa de 0,25 ATR
escolhida por argumento, não ajustada. O achado de papéis distintos é **uma
métrica**, com n de 71 a 152 por bloco, sobre pares selecionados como "melhor de
cada família" — replica em quatro blocos, mas não foi confirmado em amostra
independente. As observações compartilham histórico e níveis. Nada aqui mede
resultado financeiro.

## Validação e artefatos

16 testes novos do D4 passam; `ruff check` e `git diff --check` limpos;
`git status` sem nenhum arquivo de produção ou do frontend modificado.

[auditor](dominant_liquidity_product_audit.py),
[testes](test_dominant_liquidity_product_audit.py),
[D3](DOMINANT_LIQUIDITY_D3_CALIBRATION.md),
[D2](DOMINANT_LIQUIDITY_D2_DISTANCE.md),
[D1](DOMINANT_LIQUIDITY_D1_STRENGTH.md).
Execução: `poetry run python -m research.dominant_liquidity_product_audit` e
`python -c "from research.dominant_liquidity_product_audit import report;
report()"`.
Baseline local: `research/.replay_cache/dominant_liquidity_d4_baseline.json`
(21,1 MB, gitignored).
