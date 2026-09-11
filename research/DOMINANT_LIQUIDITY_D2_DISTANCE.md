# Dominant Liquidity D2 — escala de distância em ATR

Pergunta: uma escala de distância em ATR é comparável entre timeframes, sem
ajustar curva à amostra de diagnóstico e sem assumir que a `strength` deve
ficar? Nada de produção foi alterado.

Amostra: as mesmas 211 fixtures, mesma grade causal (início 200, passo 100, 40
barras futuras exigidas) — 2.030 observações, 41.597 candidatos, 11 braços.

## Conclusão

Resultado **C** da D2.24, com uma correção importante ao enquadramento: das
três curvas pré-registradas **só a `LINEAR_5_ATR` sobrevive**, e ela conserta
exatamente o que se propôs a consertar — a saturação desigual entre TFs —
**sem** consertar a patologia do vencedor distante, que continua sendo, 112 em
112 casos, a `strength` de EQ. As duas outras curvas falham (a `LINEAR_3_ATR`
satura mais que a atual; a `SOFT_DECAY` *piora* a patologia em 73%). E o braço
que zera a patologia (`ATR_NO_STRENGTH`) é, demonstradamente, a regra "o nível
mais próximo" — um card degenerado que o D0 já havia proibido adotar por
maximizar contato.

**Não há evidência suficiente para substituir o ranking** (pergunta 23 = NÃO).

## D2.0 — Pré-registro

O D1 pré-registrou a *família* da hipótese (distância em ATR, não percentual
fixo) e deixou curva e limiar deliberadamente por escolher. As três curvas
foram fixadas por argumento, antes de qualquer resultado desta rodada, e não
foram tocadas depois:

| Curva | Definição | Por que esta |
|---|---|---|
| `LINEAR_5_ATR` | 100 em 0 ATR, linear até 0 em 5 ATR | 5 ATR já era o marco de relatório do D0/D1 (o bucket `>5`), não um número ajustado |
| `LINEAR_3_ATR` | 100 em 0, zero em 3 ATR | 3 ATR é o outro marco do D0 — é nele que a patologia do vencedor distante é definida |
| `SOFT_DECAY` | `100 / (1 + d_atr)` | Sem parâmetro nenhum e monotônica; nunca chega a zero, então saturação é impossível por construção, não por calibração |

Nenhuma varredura de 2,5 / 3,5 / 4 / 6 ATR foi feita, e não será feita nesta
amostra (D2.23).

## D2.1 — Braços

LEGACY (pct + strength atual), e para cada curva ATR três modos de strength:
`ATR_CURRENT_STRENGTH`, `ATR_EQ_STRENGTH_ONLY`, `ATR_NO_STRENGTH`; mais
`DISTANCE_ONLY` (só geometria em ATR). Pesos 0,4 / 0,4 / 0,2 preservados em
todos: **só a curva de distância e quais famílias mantêm `touch_score` mudam**.

## D2.4 — Intenção do card, lida da documentação

`docs/scoring.md`: o engine ordena zonas "by how relevant they are as
**liquidity targets** relative to the current price", explicitamente "a
research metric, not a trading signal". O card (`KpiRow.tsx`) mostra o preço do
primeiro colocado e o tipo da zona. Isso é a intenção **(A)** — o nível mais
provável de ser *visitado*.

E é precisamente por isso que a alcançabilidade **não pode ser a métrica a
maximizar**: o nível mais próximo a vence por construção. Os braços são
julgados pelos critérios D2.22; reachability e reação aparecem lado a lado, sem
serem fundidas em um número.

## D2.6 — Saturação: o que cada curva faz com o canal de distância

| Curva | TF | candidatos zerados | observações com TUDO zerado | score p50 |
|---|---|---:|---:|---:|
| LEGACY_PCT | 15m | 46,98% | 1,47% | 7,31 |
| LEGACY_PCT | 1h | 62,68% | 2,65% | 0,00 |
| LEGACY_PCT | 4h | 82,95% | **14,48%** | 0,00 |
| LINEAR_5_ATR | 15m | 65,84% | **0,88%** | 0,00 |
| LINEAR_5_ATR | 1h | 67,30% | **1,03%** | 0,00 |
| LINEAR_5_ATR | 4h | 63,48% | **1,19%** | 0,00 |
| LINEAR_3_ATR | 15m | 81,38% | 5,00% | 0,00 |
| LINEAR_3_ATR | 1h | 80,83% | 6,62% | 0,00 |
| LINEAR_3_ATR | 4h | 79,50% | 5,22% | 0,00 |
| SOFT_DECAY | 15m/1h/4h | 0,00% | 0,00% | 10,9–12,3 |

Esta é a tabela que responde à pergunta original da D2. **A `LINEAR_5_ATR`
iguala os três TFs** (0,88 / 1,03 / 1,19%) e elimina o defeito do H4 — de
14,48% das observações sem qualquer informação de distância para 1,19%. A
`LINEAR_3_ATR` também iguala, mas num patamar *pior* que o atual no 15m e no
1h (5,0% e 6,6% contra 1,5% e 2,7%): trocar um defeito de um TF por um defeito
em três não é conserto. A `SOFT_DECAY` não satura nunca — e é justamente por
isso que ela falha adiante.

## D2.5/D2.14/D2.15 — Distância dos vencedores

Recorte nos braços que importam (a tabela completa dos 11 está na saída do
script):

| Braço | TF | p50 | p75 | p90 | >1 ATR | >3 ATR | >5 ATR |
|---|---|---:|---:|---:|---:|---:|---:|
| LEGACY | 15m | 1,52 | 2,98 | 5,99 | 70,9% | 24,9% | 13,4% |
| LEGACY | 1h | 1,45 | 2,88 | 7,57 | 66,9% | 23,8% | 15,6% |
| LEGACY | 4h | 1,68 | **7,51** | **16,65** | 67,5% | **40,6%** | **32,7%** |
| ATR_CURRENT_STRENGTH · L5 | 15m | 1,40 | 2,65 | 6,53 | 66,9% | 20,6% | 13,1% |
| ATR_CURRENT_STRENGTH · L5 | 1h | 1,37 | 2,60 | 7,57 | 65,7% | 20,4% | 12,9% |
| ATR_CURRENT_STRENGTH · L5 | 4h | 1,47 | **3,28** | **12,81** | 67,9% | **26,3%** | 18,4% |
| ATR_CURRENT_STRENGTH · SOFT | 4h | 2,88 | 8,50 | 19,63 | 76,9% | **49,0%** | 37,3% |
| ATR_NO_STRENGTH (qualquer curva) | 4h | 0,93 | 1,50 | 2,26 | 45,7% | 5,2% | 1,2% |

**Sim (D2.14): a escala em ATR corrige a disparidade entre TFs.** Com
`LINEAR_5_ATR`, o p75 do H4 cai de 7,51 para 3,28 ATR e o `>3 ATR` de 40,6%
para 26,3%, enquanto M15 e H1 também melhoram (24,9→20,6% e 23,8→20,4%) —
**nenhum TF piora** (D2.15). Com `SOFT_DECAY` acontece o contrário em todos.

## D2.7/D2.8 — Identidade e família do vencedor

| Braço | muda vs LEGACY | EQ→SW | SW→EQ | troca de lado | share EQ | d_atr mediano |
|---|---:|---:|---:|---:|---:|---:|
| LEGACY | — | — | — | — | 52,6% | 1,53 |
| ATR_CURRENT_STRENGTH · L5 | 15,07% | 145 | 60 | 141 | **48,4%** | 1,42 |
| ATR_EQ_STRENGTH_ONLY · L5 | 16,01% | 135 | 73 | 152 | 49,6% | 1,43 |
| ATR_NO_STRENGTH · L5 | 47,49% | 889 | 0 | 424 | **8,8%** | 1,03 |
| ATR_CURRENT_STRENGTH · SOFT | 23,94% | 38 | 305 | 247 | **65,8%** | 2,40 |
| DISTANCE_ONLY | 48,42% | 900 | 0 | 424 | **8,3%** | 1,03 |

Resposta a (D2.8): **remover a strength vira o ranking swing-only** — 8,8% de
EQ, com 889 trocas EQ→SW e **zero** SW→EQ. Isso reprova o critério 6 (não criar
domínio artificial de uma família) pelo lado oposto ao do defeito atual. A
`SOFT_DECAY` cria o viés na direção contrária ainda mais forte (65,8% EQ): uma
curva que decai devagar deixa a strength *mais* poderosa em termos relativos,
não menos. Só a `LINEAR_5_ATR` com a strength atual fica equilibrada (48,4%).

## D2.9 — A patologia: vencedor >3 ATR existindo candidato ≤1 ATR

Sobre as 959 observações em que existia um nível a ≤1 ATR:

| Braço | vencedores distantes | % das elegíveis | EQ | SW |
|---|---:|---:|---:|---:|
| LEGACY | 173 | 18,04% | 172 | 1 |
| ATR_CURRENT_STRENGTH · L5 | 112 | 11,68% | **112** | 0 |
| ATR_EQ_STRENGTH_ONLY · L5 | 114 | 11,89% | 114 | 0 |
| ATR_CURRENT_STRENGTH · L3 | 137 | 14,29% | 137 | 0 |
| ATR_CURRENT_STRENGTH · SOFT | **299** | **31,18%** | 297 | 2 |
| ATR_NO_STRENGTH (qualquer curva) | 0 | 0,00% | 0 | 0 |
| DISTANCE_ONLY | 0 | 0,00% | 0 | 0 |

Leitura central da rodada: a melhor curva reduz a patologia de 18,0% para
11,7%, **mas os 112 casos restantes são 112 EQ**. A escala de distância não é o
mecanismo da patologia — a `strength` incomparável é, exatamente como o D1
concluiu. Uma curva de distância não pode consertar um canal que não é dela.

## D2.10 — Reachability (pareada, mesmas observações)

| Braço | h5 | h10 | h20 | h40 | h80 |
|---|---:|---:|---:|---:|---:|
| LEGACY | 30,84% | 41,03% | 49,21% | 56,65% | 63,79% |
| ATR_CURRENT_STRENGTH · L5 | 33,15% | 44,19% | 52,86% | 60,84% | 67,93% |
| ATR_CURRENT_STRENGTH · SOFT | 24,58% | 33,25% | 40,10% | 46,35% | 53,69% |
| ATR_NO_STRENGTH · L5 | 41,23% | 55,02% | 64,68% | 74,43% | 81,72% |
| DISTANCE_ONLY | 41,28% | 55,07% | 64,73% | 74,48% | 81,72% |

**`ATR_NO_STRENGTH` e `DISTANCE_ONLY` são a mesma coisa até o terceiro decimal**
— e isso não é coincidência, é aritmética: `timeframe_score` é constante dentro
do chart, então qualquer curva monotônica sem strength ordena por proximidade.
O teste `test_no_strength_arm_reduces_to_the_nearest_level_when_nothing_is_zeroed`
registra isso. A vantagem de contato desses braços é geométrica e **não é
evidência** (D0 já havia dito isso).

## D2.2/D2.18 — Holdout temporal e causalidade

Discovery = blocos 0–1 (1.015 observações), holdout = blocos 2–3 (1.015).
**Limitação declarada:** é um corte temporal *dentro das mesmas séries*, não um
período independente — as fixtures são a única janela disponível sem nova
coleta. Nenhuma curva foi escolhida olhando o holdout; as três estavam fixadas
antes.

No holdout, a ordenação dos braços é a mesma do discovery: `LINEAR_5_ATR` +
strength atual melhora a alcançabilidade sobre a LEGACY (41,58% → 45,52% em
h20) e a `SOFT_DECAY` piora (28,67%). A patologia por bloco (D2.19) é
monotônica e replica: LEGACY b0 1,14% / b1 11,99% / b2 25,58% / b3 31,05%
contra L5 0,00% / 4,45% / 20,93% / 18,95% — **a curva melhora em todos os
quatro blocos**, e a `SOFT_DECAY` piora em todos os quatro (3,98 / 21,58 /
46,84 / 46,32%).

Causalidade: o ATR14 é calculado só no prefixo, e o teste
`test_atr_is_causal_future_bars_cannot_change_it` mais o
`test_selection_is_fixed_before_the_future_is_read` fixam isso — anexar barras
futuras não muda nem o ATR nem a seleção.

## D2.11/D2.12 — Reação depois do contato (ao lado, nunca multiplicada)

Vencedores efetivamente tocados, 10 candles após o 1º toque:

| Braço | Família | n | through | rejeição (ATR) | excursão além (ATR) | rej/thr |
|---|---|---:|---:|---:|---:|---:|
| LEGACY | EQ | 477 | 57,2% | 1,490 | 0,980 | 0,743 |
| LEGACY | SW | 772 | 74,7% | 1,359 | 1,335 | 0,844 |
| ATR_CURRENT_STRENGTH · L5 | EQ | 491 | 59,9% | 1,392 | 1,030 | 0,667 |
| ATR_CURRENT_STRENGTH · L5 | SW | 840 | 73,3% | 1,414 | 1,304 | 0,865 |
| ATR_NO_STRENGTH · L5 | EQ | 153 | 64,1% | 1,317 | 1,193 | 0,631 |
| ATR_NO_STRENGTH · L5 | SW | 1.469 | 74,1% | 1,371 | 1,325 | 0,809 |

A qualidade da reação **não degrada** com a `LINEAR_5_ATR` (critério 3): o
swing fica igual, e no EQ a diferença é uma rejeição mediana de 1,392 contra
1,490 ATR com through 59,9% contra 57,2% — pequena e a favor do status quo,
não o suficiente para reprovar nem para aprovar. Lembrando o confundidor do
D1: a banda EQ tem largura e a do swing é um ponto, então `through` não compara
famílias.

## D2.13 — Vencedor vs o nível mais próximo do mesmo lado

LEGACY é o mais próximo do próprio lado em 56,60% das observações;
`LINEAR_5_ATR` + strength em 61,82%; `ATR_NO_STRENGTH` em 99,06% e
`DISTANCE_ONLY` em 100% (por definição). Sem mudança de semântica do card.

## D2.16 — Tie-break

Simulado nos dois modos, sem tocar em produção: a ordem de entrada contra a
proximidade move 0,34% dos vencedores na LEGACY, 0,20% na `LINEAR_5_ATR` +
strength, 1,13% na `ATR_NO_STRENGTH · L5` e 4,53% na `ATR_NO_STRENGTH · L3`
(onde a zona saturada em zero é grande). Continua baixo nos braços relevantes:
**fica para depois**, como previsto.

## D2.17 — Dependência de janela da strength de swing

Mesmo pivô, uma etapa de crescimento da série (+100 barras) depois. A
proeminência já está fixa; só o denominador (`price_range` da série inteira)
pode ter mudado:

| TF | pivôs revistos | strength mudou | mediana \|Δ\| | p90 \|Δ\| |
|---|---:|---:|---:|---:|
| 15m | 90.951 | 43,29% | 0,00% | 32,84% |
| 1h | 90.939 | 43,18% | 0,00% | 41,59% |
| 4h | 94.182 | 49,71% | 0,00% | 23,79% |

Entre 43% e 50% dos pivôs mudam de nota sem que nada no pivô tenha mudado, com
p90 de 24 a 42% de variação relativa. **Sim (17): tirar a strength elimina o
problema por completo** — os braços `mode="none"` não leem o campo. Mas, como a
§D2.8 mostra, o preço disso é um card swing-only.

## D2.20 — Por símbolo (variação da taxa de vencedor distante vs LEGACY)

| Braço | símbolos | melhoram | pioram | mediana | pior caso |
|---|---:|---:|---:|---:|---:|
| ATR_CURRENT_STRENGTH · L5 | 68 | **41** | 2 | −6,67pp | +10,00pp |
| ATR_EQ_STRENGTH_ONLY · L5 | 68 | 41 | 2 | −6,67pp | +13,33pp |
| ATR_CURRENT_STRENGTH · L3 | 68 | 32 | 5 | +0,00pp | +20,00pp |
| ATR_CURRENT_STRENGTH · SOFT | 68 | 1 | **59** | +11,11pp | +46,15pp |
| ATR_NO_STRENGTH · L5 | 68 | 61 | 0 | −16,03pp | +0,00pp |

A `LINEAR_5_ATR` melhora em 41 de 68 símbolos e piora em 2 — não é um efeito
concentrado em poucos ativos. A `SOFT_DECAY` piora em 59 de 68; está reprovada
por unanimidade, não por média.

## D2.21 — Casos concretos (braço `ATR_CURRENT_STRENGTH · LINEAR_5_ATR`)

- **(A) LEGACY escolhe EQ distante, ATR escolhe perto** — BTCUSDT 15m
  2026-09-03 14:00, preço 78.955, ATR 262,79: LEGACY `equal_lows` a 5,75 ATR
  (strength 0,640) → braço `equal_highs` a 0,89 ATR (strength 0,058).
- **(B) LEGACY está certo e o ATR troca para pior** — BTCUSDT 1h 2026-08-29
  09:00: LEGACY `swing_low` a **1,12 ATR** → braço `equal_lows` a **56,63 ATR**
  (strength 0,890). O mecanismo importa: com ATR baixo em relação a 5% do
  preço, a curva em ATR dá ao nível próximo uma nota *menor* que a curva
  percentual dava (1,12 ATR ≈ 78 pontos em vez de ~94), e a strength de EQ
  passa por cima. **A troca de escala pode piorar casos pontuais**, e a média
  favorável não apaga isso.
- **(C) H4 com todos os `distance_score` legacy zerados** — ETHUSDT 4h
  2026-04-13 20:00: LEGACY `equal_lows` a 7,31 ATR → braço `swing_low` a 4,03
  ATR. E SOLUSDT 1h 2026-08-25 05:00, onde nem o braço muda a escolha
  (`equal_lows` a 15,33 ATR nos dois): quando não há nada perto, nenhuma curva
  inventa um alvo.
- **(D) Empate resolvido diferente** — nenhum caso entre BTC/ETH/SOL; empates
  são 0,20% no braço (§D2.16).
- **(E) Remover strength muda a família** — BTCUSDT 15m 2026-09-05 16:00:
  LEGACY `equal_lows` a 13,41 ATR (strength 0,509) → `swing_high` a 1,08 ATR
  (strength 0,004).

## D2.22 — Critérios, um a um (`LINEAR_5_ATR` + strength atual)

| # | Critério | Veredito |
|---|---|---|
| 1 | reduz fortemente observações com distância zerada | **passa** (H4 14,48% → 1,19%; os três TFs em ~1%) |
| 2 | reduz vencedores >3 ATR havendo nível ≤1 ATR | **parcial** (18,04% → 11,68%; os 112 restantes são 112 EQ) |
| 3 | não degrada reaction-after-contact | **passa** (swing igual; EQ −0,098 ATR de rejeição) |
| 4 | funciona em M15/H1/H4 | **passa** (nenhum TF piora) |
| 5 | replica temporalmente | **passa** (4 blocos e holdout) |
| 6 | não cria domínio artificial de família | **passa** (EQ 52,6% → 48,4%) |
| 7 | não depende de strength incomparável | **falha** (carrega a strength que o D1 condenou) |
| 8 | causal | **passa** (testado) |
| 9 | comportamento interpretável | **passa** (reta, um parâmetro, marco pré-existente) |
| 10 | passa no holdout independente | **parcial** (bloco temporal das mesmas séries, não período independente) |

E, para registro, os outros dois pré-registrados: **`LINEAR_3_ATR` reprovada**
no critério 1 (satura mais que a atual no 15m e no 1h) e **`SOFT_DECAY`
reprovada** nos critérios 2, 4, 5 e 6 (piora a patologia em 73%, em todos os
TFs, em todos os blocos e em 59 de 68 símbolos).

## Decisão

**23. Existe evidência suficiente para substituir o ranking atual? NÃO.**

A `LINEAR_5_ATR` passa em 7 critérios, fica parcial em 2 e **falha no 7**, que
não é um detalhe: ela conserta o canal de distância e deixa intacto o canal
que o D1 mostrou ser a causa. O único braço que zera a patologia é a regra do
nível mais próximo, com card swing-only — trocar um viés de família por outro,
com o agravante de maximizar justamente a métrica que o D0 proibiu usar como
prova. E as duas outras curvas pré-registradas estão reprovadas.

Conforme a D2.23, a rodada **para aqui**: nenhuma varredura de limiar nova
nesta amostra, e a hipótese fica registrada como **parcialmente resolvida** —
a escala em ATR é comprovadamente a escala certa para *comparar timeframes*,
e comprovadamente insuficiente sozinha para consertar o ranking.

**25. Deve ir para produção? Não.** Nada foi alterado: nem pesos,
`distance_score`, `touch_score`, desempate, EQ, swing ou frontend.

Fica em aberto, para quando houver decisão de mexer: a escala de distância e a
`strength` são **um problema só** e não se decidem em separado — a `LINEAR_5_ATR`
só mostra o seu valor real num desenho em que a `strength` já tenha sido
resolvida (removida, recalibrada por família, ou reduzida a um critério de
desempate). Medir de novo a curva antes disso mede a strength de novo.

## Limites

Contato é interseção geométrica com a banda original — não é sweep, retorno nem
trade; níveis próximos e bandas largas o vencem por construção. O holdout é um
bloco temporal das mesmas séries. As observações compartilham histórico e
níveis, não são independentes. `through` não compara famílias (largura de banda
desigual). Nada aqui mede resultado financeiro.

## Validação e artefatos

15 testes novos do D2 passam; `ruff check` e `git diff --check` limpos;
`git status` sem nenhum arquivo de produção modificado.

[auditor](dominant_liquidity_distance_audit.py),
[testes](test_dominant_liquidity_distance_audit.py),
[D1](DOMINANT_LIQUIDITY_D1_STRENGTH.md).
Execução: `poetry run python -m research.dominant_liquidity_distance_audit`
(replay) e `python -c "from research.dominant_liquidity_distance_audit import
report; report()"` (tabelas).
Baseline local: `research/.replay_cache/dominant_liquidity_d2_baseline.json`
(36,6 MB, gitignored).
