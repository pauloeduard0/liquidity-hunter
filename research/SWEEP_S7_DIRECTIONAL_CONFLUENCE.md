# SWEEP S7 — o fator de confluência testa o lado certo?

**Pergunta:** `StructureConfluenceEngine` credita 9,0 pontos a qualquer sweep
na janela, sem olhar de que lado ele foi. Os 42,0% de "lado errado" nos CHoCH
que o S1.1 deixou em aberto são um bug semântico?

**Resposta curta: não — e exigir alinhamento tornaria a confluência pior.**

A medição derrubou a hipótese em dois passos. Primeiro, `direction` de um sweep
não é um fato independente: o detector só rotula como sweep um pivô
**contra-tendência** (93,9% medido), então `direction` é a tendência vigente
invertida. Consequência: num CHoCH, "alinhado" concorda **96,6%** com "veio
depois do flip". Os dois testes são quase o mesmo teste.

Segundo — e é o que decide — restringindo à parte da janela que uma decisão em
tempo real poderia usar (só para trás), o grupo "lado errado" é o que **carrega
informação** (h=20: 71,0% MFE>MAE, n=994) e o "alinhado" fica **abaixo de não
ter sweep nenhum** (57,6%, n=59, contra 64,9% sem sweep).

O ganho aparente do "alinhado" na janela cheia (72,2%) é **artefato de seleção
para frente**: 95,4% dos créditos alinhados são posteriores ao CHoCH, e
condicionar num pivô contra-tendência futuro é condicionar em a perna ter dado
certo.

**Nada foi implementado.** Nenhuma linha de `liquidity_hunter/` foi tocada.

**Painel:** 112 símbolos × {15m, 1h, 4h} × 1200 candles, **0 falhas**.
8.381 eventos qualificados, 3.953 créditos de sweep.

---

## S7.0 — A regra de alinhamento, lida do código

`direction` de um `LIQUIDITY_SWEEP` é **o lado que o pavio alcançou**, não a
tendência. `internal_structure.py:3074` emite `BULLISH` para o sweep que
atravessa um topo (`find_wick_break_index(..., bullish=True)`), e o detector
diz isso explicitamente em `internal_structure.py:1096` — "whose `direction` is
the pivot/wick side, not the trend".

O próprio engine declara a regra que quer (`structure_confluence.py:219`):

```
# A stop-hunt sweep within the window (any side — a bullish reversal
# sweeps the lows, a wick-down/bearish-labeled sweep).
```

"uma reversão de **alta** varre os **mínimos**, um sweep rotulado **bearish**".
Formalizado:

| quebra | sweep alinhado | leitura |
|---|---|---|
| BOS bullish | **BEARISH** (varreu low) | liquidez vendedora vira combustível |
| BOS bearish | **BULLISH** (varreu high) | liquidez compradora vira combustível |
| CHoCH bullish | **BEARISH** (varreu low) | o stop-hunt que lançou a reversão |
| CHoCH bearish | **BULLISH** (varreu high) | idem, espelhado |

**Alinhado = direção OPOSTA à da quebra.** A linha seguinte do engine —
`any(sweep_lo <= s <= sweep_hi for s in sweep_idxs)` — não consulta `direction`
em lugar nenhum.

---

## S7.1 — Matriz de confusão

Sobre os 3.953 créditos individuais:

| corte | créditos | aligned | wrong | missing |
|---|---|---|---|---|
| **TOTAL** | 3.953 | 48,7% | 51,3% | 0 |
| **BOS** | 483 | **95,7%** | 4,3% | 0 |
| **CHoCH** | 3.470 | 42,2% | **57,8%** | 0 |
| bos/15m · 1h · 4h | 152 · 187 · 144 | 94,1% · 96,3% · 96,5% | | |
| choch/15m · 1h · 4h | 1.123 · 1.172 · 1.175 | 39,7% · 38,9% · 47,8% | | |
| choch/bullish | 1.654 | 40,0% | 60,0% | 0 |
| choch/bearish | 1.816 | 44,2% | 55,8% | 0 |

**`missing direction` é zero em 3.953 créditos** — o detector nunca emite um
sweep neutro. A classe existe no código para não ser suposta; a medição diz que
está vazia.

Classe do **evento** (a janela inteira, que é o que o fator enxerga):

| corte | eventos | aligned | wrong | ambíguo | sem sweep |
|---|---|---|---|---|---|
| TOTAL | 8.381 | 13,1% | 8,0% | 4,9% | 74,0% |
| BOS | 5.715 | 7,7% | **0,4%** | 0,0% | 91,9% |
| CHoCH | 2.666 | 24,8% | **24,3%** | 15,3% | 35,6% |

---

## S7.2 / S7.3 — BOS: não é materialmente afetado

| | n | % |
|---|---|---|
| eventos qualificados | 5.715 | |
| com sweep na janela | 462 | 8,1% |
| **perdem o fator (só wrong-side)** | **21** | **0,4% do total — 4,5% dos que recebem** |
| score médio legacy → directional | 16,25 → 16,22 | **−0,03** |

Sem gradiente: 0,5% (15m), 0,4% (1h), 0,3% (4h); 0,4% bullish, 0,3% bearish.

**Confirmado como esperado.** A janela do BOS é estritamente para trás
(`ev-10 .. ev-1`) e a tendência no sweep é a mesma da quebra, então o rótulo
sai alinhado por construção em 95,7% dos casos.

## S7.4 — CHoCH: é aqui que está tudo

| | n | % |
|---|---|---|
| eventos qualificados | 2.666 | |
| com sweep na janela | 1.717 | 64,4% |
| **perdem o fator (só wrong-side)** | **648** | **24,3% do total — 37,7% dos que recebem** |
| score médio legacy → directional | 33,99 → 31,80 | **−2,19** |
| ✦N alterado | 648 | sempre −1 fator e −9 pontos |

Distribuição do score: p25=24, p50=33, p75=44, max=88. Por TF: 24,7% (15m),
28,5% (1h), 20,1% (4h). Por direção: 25,5% bullish, 23,2% bearish.

Para comparação: o gate causal do S1.1 mexia em **3,8%** dos CHoCH. O gate
direcional mexe em **24,3%** — **seis vezes mais**. Se a regra estivesse certa,
seria o achado mais material dos três estudos.

---

## S7.5 / S7.6 — Timing: onde cada lado se concentra

| quando | créditos | % | aligned | wrong |
|---|---|---|---|---|
| **antes** do CHoCH | 2.014 | 58,0% | **3,0%** | **97,0%** |
| mesmo candle | 9 | 0,3% | 88,9% | 11,1% |
| **depois** (janela forward) | 1.447 | 41,7% | **96,5%** | 3,5% |

| lado | before | same | after | offset p50 |
|---|---|---|---|---|
| wrong-side | **97,4%** | 0,0% | 2,5% | **−26** |
| aligned | 4,1% | 0,5% | **95,4%** | **+36** |

E o corte que o protocolo pediu explicitamente (S7.5), **dentro da própria
janela forward**:

| parte da janela | créditos | aligned | wrong |
|---|---|---|---|
| forward (`> ev`) | 1.447 | **96,5%** | 3,5% |
| backward (`≤ ev`) | 2.023 | 3,4% | **96,6%** |

O protocolo mandou não confundir temporalidade futura intencional com lado
errado. A medição responde que, **para este detector, as duas coisas são a
mesma coisa em 96,6% dos casos**.

---

## S7.7 — O que `direction` realmente codifica

| | n | |
|---|---|---|
| sweeps emitidos **contra a tendência vigente** | 3.668 / 3.906 | **93,9%** |

O detector só rotula como `LIQUIDITY_SWEEP` um pivô contra-tendência (em
tendência de baixa, o pavio que ultrapassa o topo vigente vira sweep
`BULLISH`). Logo `direction` **é a tendência vigente, invertida** — não um fato
independente sobre o sweep.

Daí decorre tudo:

| | aligned | wrong |
|---|---|---|
| antes do CHoCH | 60 | 1.954 |
| mesmo candle | 8 | 1 |
| depois do CHoCH | 1.396 | 51 |

**Concordância entre "alinhado" e "depois do flip": 96,6%** de 3.470 créditos.

Mecanicamente: antes de um CHoCH bullish a tendência ainda é de baixa, então os
sweeps daquele trecho são `BULLISH` (pavios para cima) — mesma direção da
quebra que vem, logo "lado errado". Depois do flip a tendência é de alta e os
sweeps viram `BEARISH` — "alinhados".

**O sweep que o comentário do engine descreve — o que varre os mínimos e lança
a reversão de alta — não pode ser rotulado assim antes do flip**, porque
naquele momento uma nova mínima é um BOS de continuação, não um sweep. A regra
escrita no comentário descreve um mecanismo que a própria máquina de rótulos
não consegue produzir a tempo.

---

## S7.8 — Outcome descritivo

MFE/MAE em ATR, do close do candle da quebra, na direção da quebra. **Não é
estratégia**: não há entrada, stop, custo nem controle aleatório. É o contraste
entre populações de CHoCH do mesmo painel.

### Janela cheia (contaminada — mostrada para expor a contaminação)

| grupo | h=5 | h=10 | h=20 | h=40 | n (h=20) |
|---|---|---|---|---|---|
| aligned | 73,8% | 72,8% | **72,2%** | 70,5% | 661 |
| wrong_side | 79,8% | 72,1% | 64,7% | 56,4% | 621 |
| ambiguous | 83,8% | 81,4% | 77,5% | 75,2% | 408 |
| sem sweep | 72,4% | 64,2% | 59,8% | 56,0% | 914 |

Lida assim, a tese confirma-se: alinhado > wrong-side em h=20/40. **Mas 95,4%
dos créditos alinhados vêm depois do CHoCH**, então "ter sweep alinhado"
significa "a perna continuou e produziu um pivô contra-tendência". É seleção
pelo futuro.

### S7.8b — Só a parte para trás da janela (a que uma decisão teria)

Classes do CHoCH recalculadas com `s_idx ≤ ev_idx`: `none` 59,3%,
`wrong_side` 38,3%, `aligned` **2,2%**, `ambiguous` 0,2%.

| grupo | h=5 | h=10 | h=20 | h=40 | n (h=20) |
|---|---|---|---|---|---|
| **wrong_side** | **82,2%** | **76,6%** | **71,0%** | **65,5%** | 994 |
| sem sweep | 73,6% | 68,3% | 64,9% | 61,5% | 1.545 |
| **aligned** | 54,2% | 50,8% | **57,6%** | 59,3% | **59** |

**A ordem inverte por completo.** O grupo "lado errado" bate o "sem sweep" em
todos os quatro horizontes (+6,1 pp em h=20), e o "alinhado" fica **abaixo do
sem-sweep**, com uma população de 59 eventos — 2,2% dos CHoCH.

Um sweep alinhado causalmente utilizável antes de um CHoCH **praticamente não
existe**.

### Estratificado (h=20, MFE>MAE), janela cheia

| estrato | aligned | wrong | sem sweep |
|---|---|---|---|
| 15m/bullish | 78,8% (85) | 62,1% (103) | 60,1% (138) |
| 15m/bearish | 72,6% (113) | 67,0% (103) | 58,2% (170) |
| 1h/bullish | 75,3% (89) | 70,5% (112) | 64,7% (150) |
| 1h/bearish | 71,3% (108) | 67,2% (116) | 61,6% (125) |
| 4h/bullish | 67,7% (124) | 58,9% (112) | 58,3% (168) |
| 4h/bearish | 70,4% (142) | 61,3% (75) | 57,1% (163) |

Em **6 de 6** estratos, os dois grupos com sweep batem o sem-sweep. O fator tem
sinal; o que não tem é a distinção de lado.

### BOS

| grupo | h=20 MFE>MAE | n |
|---|---|---|
| aligned | 46,1% | 436 |
| wrong_side | 42,9% | 21 |
| **sem sweep** | **49,8%** | 5.139 |

No BOS o fator de sweep é **pior que não ter sweep**, nos dois lados. Achado
lateral, mas registrado: o peso 9,0 no BOS não está comprado por nada.

---

## S7.9 — Score incremental

O score dos **outros** fatores já difere entre os grupos?

| grupo | n | base (score − sweep) |
|---|---|---|
| aligned | 661 | 29,99 |
| ambiguous | 408 | 29,85 |
| wrong_side | 648 | 28,63 |
| sem sweep | 949 | **25,92** |

Fatores médios além do sweep: aligned 2,12 · ambiguous 2,04 · wrong_side 1,98 ·
sem sweep 1,83.

Os três grupos com sweep são parecidos entre si (28,6–30,0) e todos acima do
sem-sweep (25,9). **A presença de um sweep marca um evento mais bem
acompanhado; o lado dele não separa quase nada.** É coerente com o S7.8b: o
peso 9,0 está comprando a *presença*, não a *direção*.

---

## S7.10 — Thresholds: não existem

`StructureConfluence.score` é lido por **nada**. Verificado:

- **Python:** só `dashboard_data.py:3170` (constrói) e `api/schemas.py:83`
  (serializa). Nenhum módulo decisional lê.
- **Frontend:** um único ponto, `MainChart.tsx:2051`, e ele usa
  `conf.factors.length` — o selo `✦N`. O campo `score` não aparece em
  `frontend/src/` em lugar nenhum.

**Não há threshold de produção.** Conforme o protocolo, não inventei cortes
(o `≥50` do S1.1 foi convenção declarada e não se repete aqui). A grandeza
observável é ✦N, e ela muda em **648 CHoCH (24,3%)** e **21 BOS (0,4%)**.

## S7.11 — Impacto downstream

`structure_confluence` é computado **por último** no composition root
(`dashboard_data.py:3170`, depois de narrative/hunt/stall), justamente para não
poder realimentar nada. Nenhum detector, passe ou sintetizador o lê.

**O impacto downstream é 100% visual: um número num rótulo do gráfico.**

---

## S7.12 — Redundância (CHoCH, ±20 candles)

| sinal | aligned | wrong_side | sem sweep |
|---|---|---|---|
| BOS | 51,9% | **30,1%** | 54,5% |
| liquidity grab | 53,1% | **68,4%** | 53,8% |
| hunt raid | **50,7%** | 38,4% | 36,7% |
| supertrend break | 85,6% | **69,9%** | 86,6% |
| VSA | 88,0% | **91,2%** | 87,4% |

O wrong-side **não** é um duplicado de movimento já ocorrido — se fosse,
apareceria *mais* colado a BOS e supertrend break, e aparece **menos** (30,1%
vs 54,5%; 69,9% vs 86,6%). O que ele acompanha é **liquidity grab** (68,4% vs
53,8%), que é exatamente o objeto "pavio tomou liquidez e voltou". É uma
assinatura de exaustão da perna que morre, não o eco da perna que nasce.

---

## S7.13 — Robustez temporal

| bloco | créditos CHoCH | wrong | eventos | perdem fator |
|---|---|---|---|---|
| 0 (mais antigo) | 815 | 44,8% | 750 | 19,6% |
| 1 | 954 | 58,2% | 614 | 24,4% |
| 2 | 827 | 60,6% | 638 | 26,5% |
| 3 (mais recente) | 874 | **66,9%** | 664 | 27,4% |

Presente nos **4 blocos**, com tendência crescente (44,8% → 66,9%). **Não vem
de um regime único**, então a conclusão é global — mas a deriva é real e vale
registrar: a fração cresce monotonicamente com o tempo.

## S7.14 — Robustez por símbolo

109 símbolos com ≥10 créditos de CHoCH.

| | |
|---|---|
| wrong-side por símbolo | p25 **50,0%** · p50 **57,6%** · p75 **65,7%** |
| extremos | min 20,0% · max 86,7% |
| símbolos com wrong-side >50% | **76 / 109** |
| símbolos com wrong-side >25% | **108 / 109** |

Piores 5: COWUSDT 87%, SUIUSDT 79%, ACUUSDT 78%, YFIUSDT 76%, GALAUSDT 75%.

**Não há concentração**: 108 de 109 símbolos passam de 25%. É propriedade do
algoritmo, não de um punhado de ativos — o mesmo padrão que o S1.5 achou para a
retroatividade.

---

## S7.15 — Casos (BTC / ETH / SOL)

- **A) CHoCH + sweep alinhado** — 23 casos. `BTCUSDT 4h 2026-03-19T04:00`,
  CHoCH bearish, score 33, ✦3 `[vsa_volume, volume_delta, liquidity_sweep]`,
  2 créditos ambos alinhados. h=5 MFE 1,57 / MAE 0,71 ATR.
- **B) CHoCH + wrong-side já visível ANTES da quebra** — 38 casos.
  `BTCUSDT 4h 2026-06-13T12:00`, CHoCH bullish, score 29, ✦2
  `[htf_alignment, liquidity_sweep]`. h=20 MFE **3,37** / MAE 0,69 ATR — o
  crédito "errado" precede a quebra e a leitura foi boa.
- **C) wrong-side que só ocorre DEPOIS do CHoCH** — **2 casos** nos majors.
  `ETHUSDT 1h 2026-07-27T22:00`. A raridade é o dado: o wrong-side é
  quase sempre *anterior* (97,4%), o que desmonta a hipótese "é consequência da
  reversão já em andamento".
- **D) wrong-side infla ✦N sem outro fator** — 6 casos. O mesmo BTC 4h acima:
  ✦2 em que um dos dois é o sweep. É o caso que a hipótese previa — e é raro.
- **E) remover seria INCORRETO** — 17 casos ambíguos (têm os dois lados).
  `BTCUSDT 1h 2026-08-10T21:00`, ✦3, 1 alinhado + 1 wrong. Sob a regra
  direcional o fator sobrevive por `any()`, então o ✦N não muda — mas a
  justificativa dele passaria a vir do crédito que o S7.8b mostra ser o pior
  dos dois.

---

## S7.16 — Qual dos resultados possíveis

**B, com um pedaço de C, e definitivamente não A.**

- **B — wrong-side carrega informação própria.** 71,0% vs 64,9% (sem sweep) em
  h=20 na janela causal, em 6/6 estratos, 4/4 blocos, 108/109 símbolos.
- **C — parte do efeito é semântica de janela.** 96,6% de concordância entre
  "alinhado" e "depois do flip": a distinção de lado e a distinção temporal são
  o mesmo eixo.
- **D — impacto apenas visual.** Confirmado por S7.10/S7.11: o consumo é um
  rótulo de gráfico.
- **NÃO é A.** O wrong-side não é crédito semântico sem valor: é o único grupo
  causalmente utilizável **e** o que melhor separa.
- **NÃO é E.** Não há consumidor decisional.

---

## Respostas

1. **Regra correta de alinhamento:** alinhado = sweep de direção **oposta** à
   da quebra, para BOS e CHoCH igualmente (S7.0, do comentário e da emissão do
   detector). Mas a medição mostra que a regra, aplicada a *estes* rótulos, não
   mede o que ela pretende medir.
2. **Wrong-side:** 2.029 de 3.953 créditos (51,3%) — 4,3% no BOS, **57,8%** no
   CHoCH.
3. **BOS é materialmente afetado?** **Não.** 21 eventos (0,4%), score −0,03.
4. **CHoCH é materialmente afetado?** **Sim, muito**: 648 eventos (24,3%),
   score −2,19, ✦N −1. Seis vezes o gate causal do S1.1.
5. **Wrong-side aparece antes ou depois?** **Antes, esmagadoramente**: 97,4%,
   offset mediano −26 candles.
6. **É consequência da forward window?** **Não da forma esperada** — o
   wrong-side é anterior. Mas o *eixo* é temporal: 96,6% de concordância entre
   "alinhado" e "posterior ao flip". A janela forward não cria o wrong-side;
   ela cria o **aligned**.
7. **Score muda quanto?** CHoCH 33,99 → 31,80 (−2,19); BOS 16,25 → 16,22
   (−0,03).
8. **✦N muda quanto?** 648 CHoCH e 21 BOS perdem exatamente 1 fator. 669 de
   8.381 eventos (8,0%).
9. **Thresholds reais afetados?** **Não existem.** `score` não é lido por
   nada; só `factors.length` chega ao `✦N`.
10. **Wrong-side agrega outcome?** **Sim.** Na janela causal, 71,0% vs 64,9%
    (h=20), consistente em 4/4 horizontes, 6/6 estratos, 4/4 blocos.
11. **Aligned agrega outcome?** **Não, quando medido sem lookahead**: 57,6% em
    h=20, **abaixo** do sem-sweep, com n=59. Na janela cheia parece o melhor
    (72,2%), mas 95,4% desses créditos são posteriores ao evento.
12. **Sem sweep se comporta como?** O piso: 64,9% em h=20, e o menor score-base
    (25,92 vs 28,6–30,0). A **presença** de sweep marca evento melhor
    acompanhado.
13. **Por TF:** CHoCH perde 24,7% (15m), 28,5% (1h), 20,1% (4h). BOS ≤0,5% em
    todos.
14. **Por direção:** CHoCH 25,5% bullish vs 23,2% bearish; BOS 0,4% vs 0,3%.
    Sem separação.
15. **Estável no tempo?** Presente nos 4 blocos, mas **crescente**: 44,8% →
    66,9% de wrong-side do bloco mais antigo ao mais recente.
16. **Robusto por símbolo?** **Sim**, e é o oposto de concentrado: 108/109
    símbolos acima de 25%, mediana 57,6%.
17. **Impacto downstream?** **Nenhum decisional.** `structure_confluence` roda
    por último, não realimenta nada, e o único consumo é o selo `✦N`.
18. **É bug semântico real?** **O comentário está certo e o código está
    errado — mas a correção óbvia estaria mais errada que os dois.** O engine
    descreve um mecanismo (o sweep que varre os mínimos e lança a reversão de
    alta) que a máquina de rótulos **não consegue emitir antes do flip**. O
    `any()` sem direção não é descuido premiado: é a única forma de o fator
    continuar vendo a população que carrega sinal.
19. **Deve corrigir produção?** **NÃO.** Exigir alinhamento removeria o fator de
    24,3% dos CHoCH — e o grupo removido é justamente o que bate o sem-sweep em
    todos os horizontes, em favor de um grupo de 59 eventos que fica abaixo
    dele. Seria trocar um rótulo impreciso por uma perda medida de informação.
20. **Patch mínimo:** nenhum no comportamento. O único defeito real e barato de
    corrigir é o **comentário** em `structure_confluence.py:219`, que promete um
    teste de direção que o código não faz e que a medição mostra que não deve
    fazer. Trocar por algo como *"qualquer sweep na janela — o lado é redundante
    com a tendência vigente (S7.7) e exigir alinhamento remove a população
    informativa (S7.8b)"* documenta a decisão onde ela é lida. **Não
    implementado.**

---

## Próxima etapa

Duas linhas abertas, nenhuma delas "consertar o S7":

1. **O peso 9,0 no BOS não está comprado.** O BOS com sweep sai *pior* que o
   BOS sem sweep (46,1% vs 49,8% em h=20, n=436 vs 5.139). O fator vale no
   CHoCH e parece negativo no BOS — e o engine aplica o mesmo peso aos dois.
2. **A deriva temporal do wrong-side** (44,8% → 66,9%) não foi explicada. Como
   `direction` é a tendência invertida, isso pode ser deriva do próprio detector
   de tendência, e aí não é sobre sweep nenhum.

Ambas são sobre o *peso e a calibração* da confluência — a Fase 2 do score
multi-TF —, não sobre causalidade nem sobre lado.

---

## Arquivos

```
research/sweep_directional_confluence.py        # o estudo
research/test_sweep_directional_confluence.py   # 18 testes
research/SWEEP_S7_DIRECTIONAL_CONFLUENCE.md     # este relatorio
research/sweep_s7_baseline.json                 # gitignored (research/.gitignore)
```

```bash
poetry run python research/sweep_directional_confluence.py --limit 112 --cases
poetry run pytest research/test_sweep_directional_confluence.py -q
```
