# SWEEP S2 — reclaim vs close-through

**Pergunta:** `narrative.py:164` descreve todo `LIQUIDITY_SWEEP` como
*"wick pierced {ref} {side} but failed to hold"*. O S0 mediu que ~43,8% desses
eventos **fecham além** do nível. O rótulo está misturando dois fenômenos?

**Resposta curta: misturando duas *geometrias*, sim; dois *fenômenos*, não.**

- **44,0%** são CLOSE_THROUGH (medido: 2.951 de 6.712), confirmando o S0.
- Não é ruído numérico: mediana de **0,27 ATR** além do nível, 79,4% acima de
  0,1 ATR, 10,8% acima de 1 ATR inteiro, máximo 11,23 ATR.
- **Mas os dois grupos não se comportam diferente.** Contra controle casado
  (mesmo símbolo/TF/direção/período), ambos ficam colados em zero: RECLAIM
  −1,1% a −2,9%, CLOSE_THROUGH −1,5% a +0,7% de MFE>MAE, em 4 horizontes.
- E **todo consumidor decisional quer "liquidez tomada", não "rejeição"** —
  então nenhum deles precisa da separação.

**O defeito real é a frase.** Para 44% dos eventos ela afirma algo falso sobre
o candle, e os dados para dizer a verdade já estão na camada de composição.

**Nada foi implementado.** Nenhuma linha de `liquidity_hunter/` foi tocada.

**Painel:** 112 símbolos × {15m, 1h, 4h} × 1200 candles, **0 falhas**, 6.912
sweeps confirmados.

---

## S2.1 — Por que um close-through vira `liquidity_sweep`

Lido do detector, não suposto. `internal_structure.py:66`:

> `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing reference
> but is not a confirmed reversal

E a confirmação (`_common.is_sustained_break`) é **persistência**:

```python
window = candles[pivot_index : pivot_index + 1 + persistence_candles]
return all(candle.close > active_price for candle in window)
```

Em produção `persistence_candles = 2`, ou seja **3 fechamentos consecutivos**
além da referência. Um candle pode fechar além do nível — e o seguinte também —
e o evento ainda ser um `LIQUIDITY_SWEEP`, bastando que o terceiro não segure.

**Das cinco hipóteses do protocolo, a correta é "Sweep é resíduo da state
machine".** O rótulo é uma afirmação sobre a máquina de estados (*o CHoCH não
confirmou*), não sobre a geometria do candle. O detector nunca olhou o close do
candle que ele data — olha o close dos **três** candles a partir do pivô.

A própria docstring do detector (`internal_structure.py:151`) simplifica isso
para *"a single candle that pokes through the reference and reverts (a 'false
break')"*, e o `SweepContext` (`core/domain/sweep_context.py`) diz a coisa certa
em voz alta: *"a sweep is emitted as a **residual** category ... That makes it a
statement about the state machine, not about liquidity"*.

**É essa simplificação que vazou para a narrativa.**

---

## S2.0 / S2.3 — Geometria real

Classificação do candle que o evento data, contra `reference_price_level`,
usando a leitura estrita de `mitigation.py` (`>` e `<`, tocar não é atravessar):

| classe | n | % |
|---|---|---|
| **RECLAIM** (pavio além, close de volta) | 3.761 | **54,4%** |
| **CLOSE_THROUGH** (close além) | 2.951 | **42,7%** |
| TOUCH_ONLY (extremo exatamente no nível) | 0 | 0,0% |
| AMBIGUOUS (close exatamente *no* nível) | 200 | 2,9% |
| NO_REF (sem `reference_price_level`) | **0** | 0,0% |

`NO_REF = 0` reconfirma o S1.13: **100%** dos sweeps confirmados carregam o
nível de referência. `TOUCH_ONLY = 0` também é informativo — o detector só
emite quando o pivô *supera* a referência, nunca quando a toca.

Entre RECLAIM e CLOSE_THROUGH (n=6.712), **44,0% são close-through** — o
número do S0 (43,8%) reproduzido.

| corte | n | RECLAIM | CLOSE_THROUGH |
|---|---|---|---|
| **TOTAL** | 6.712 | 56,0% | **44,0%** |
| 15m | 2.202 | 53,0% | 47,0% |
| 1h | 2.361 | 56,9% | 43,1% |
| 4h | 2.149 | 58,2% | 41,8% |
| bullish | 3.385 | 55,7% | 44,3% |
| bearish | 3.327 | 56,4% | 43,6% |
| bloco 0 → 3 | ~1.650 cada | 54,7% → 57,4% | 45,3% → 42,6% |

**Sem gradiente em nada**: 5 pp entre o TF extremo, 0,7 pp entre direções, 2,7
pp entre o bloco mais antigo e o mais recente. Por símbolo (111 com ≥10):
p25 39,2% · **p50 43,8%** · p75 49,3%, min 29,5%, max 59,6% —
**109 de 111 símbolos acima de 30%**. É propriedade do algoritmo.

---

## S2.4 — O close-through é marginal ou real?

| close além do nível | n | % |
|---|---|---|
| 0 – 0,1 ATR | 607 | 20,6% |
| 0,1 – 0,25 ATR | 771 | 26,1% |
| 0,25 – 0,5 ATR | 693 | 23,5% |
| 0,5 – 1 ATR | 561 | 19,0% |
| **> 1 ATR** | **319** | **10,8%** |

p50 = **0,27 ATR**, p90 = 1,07, **max = 11,23 ATR**.

**É aceitação real, não ruído numérico.** 79,4% clareiam o nível por mais de um
décimo de ATR; 29,8% por mais de meio; 10,8% por mais de um ATR inteiro. Os
casos extremos que o protocolo esperava aparecem: o máximo é **11,23 ATR** além
do nível — um candle que atravessou o nível de ponta a ponta e ainda assim é
descrito como "wick pierced ... but failed to hold".

Existe uma cauda marginal (20,6% abaixo de 0,1 ATR) que poderia ser tratada
como empate, mas ela é **minoria** e não muda nada: mesmo descartando-a, ~35%
de todos os sweeps continuam sendo close-through.

## S2.5 — Reclaim depth

| close de volta aquém | n | % |
|---|---|---|
| 0 – 0,1 ATR | 669 | 17,8% |
| 0,1 – 0,25 ATR | 956 | 25,4% |
| 0,25 – 0,5 ATR | 1.004 | 26,7% |
| **> 0,5 ATR** | **1.132** | **30,1%** |

p50 = 0,30 ATR, p90 = 1,03, max = 11,71.

**Sim, existem reclaims muito marginais e rejeições fortes**, e a distribuição é
quase a imagem espelhada da anterior. Conforme o protocolo, **nenhum limiar foi
criado**: a magnitude fica medida e separada da classe.

Penetração do extremo (todos os sweeps): p50 = 0,31 · p90 = 1,27 · max = 18,26
ATR.

---

## S2.2 — Contra a taxonomia de `mitigation.py`

`mitigation.py` já separa exatamente estas duas coisas, e já o faz com lag zero:

- **sweep**: o pavio atravessa a zona → `is_mitigated`, mais `sweep_rejected`
  gravando se *aquele mesmo candle fechou de volta dentro*;
- **breach**: um candle *fecha* além da zona → `breached_at`.

6.890 de 6.912 sweeps (99,7%) casam com uma zona mapeada (tolerância 0,1%):

| detector | `sweep_rejected` | `breached_at` | n |
|---|---|---|---|
| RECLAIM | True | sim | 2.937 |
| CLOSE_THROUGH | False | sim | 2.217 |
| CLOSE_THROUGH | True | sim | 721 |
| RECLAIM | False | sim | 653 |
| AMBIGUOUS | True | sim | 161 |
| RECLAIM | True | **não** | 159 |

**Concordância RECLAIM ↔ `sweep_rejected`: 77,6%** (5.348 de 6.890).

As duas leituras **não têm** de bater, e a discordância não é erro de nenhuma
das duas: `mitigation` lê a **primeira** vela que cruzou a *zona*, enquanto o
detector data o pavio que quebrou a **referência estrutural** — dois níveis
próximos mas distintos, em candles possivelmente diferentes.

**Resposta a Q10: sim, a semântica correta já existe no projeto**, em
`liquidity/mitigation.py`, ao nível de `LiquidityZone` — e o detector estrutural
não a usa porque está respondendo outra pergunta (persistência de 3 closes, não
geometria de 1 candle).

---

## S2.6 — Outcome causal

MFE/MAE em ATR **a partir do `known_at`** (o piso analítico do S1), nunca do
timestamp back-datado — a metodologia que o S0/S1 fixaram. Direção esperada: a
que um sweep argumenta (rejeição, contra o lado que o pavio alcançou). Controle:
**mesmo símbolo, TF, direção e período**, deslocado 37 candles — a lição do
`raid_reversal`, onde um controle sem casar direção fabricou um resultado.

| grupo | h | n | MFE/MAE | MFE>MAE | controle | delta |
|---|---|---|---|---|---|---|
| RECLAIM | 5 | 3.749 | 0,86 | 47,6% | 50,2% | **−2,5%** |
| RECLAIM | 10 | 3.732 | 0,90 | 48,2% | 51,1% | −2,9% |
| RECLAIM | 20 | 3.699 | 0,93 | 49,1% | 51,8% | −2,7% |
| RECLAIM | 40 | 3.637 | 0,96 | 49,4% | 50,5% | −1,1% |
| CLOSE_THROUGH | 5 | 2.947 | 0,85 | 47,8% | 49,3% | −1,5% |
| CLOSE_THROUGH | 10 | 2.939 | 0,90 | 48,5% | 49,8% | −1,2% |
| CLOSE_THROUGH | 20 | 2.919 | 1,00 | 50,4% | 50,2% | **+0,2%** |
| CLOSE_THROUGH | 40 | 2.866 | 1,00 | 50,2% | 49,5% | +0,7% |

**Os dois grupos são indistinguíveis, e ambos são o controle.** Nenhum passa de
±3 pp em nenhum horizonte. Isso reconfirma o S1.10 (o baseline causal do sweep é
o controle) e acrescenta: **a geometria não separa nada.**

Estratificado (h=20, MFE>MAE menos controle) — 6 estratos, sinais trocados e
sem padrão:

| estrato | RECLAIM | CLOSE_THROUGH |
|---|---|---|
| 15m/bullish | −0,3% (495) | −0,0% (477) |
| 15m/bearish | +1,3% (654) | +4,5% (545) |
| 1h/bullish | +5,1% (640) | +4,9% (499) |
| 1h/bearish | −2,3% (677) | +4,0% (510) |
| 4h/bullish | −9,7% (717) | −8,3% (511) |
| 4h/bearish | −10,3% (516) | −5,0% (377) |

Por bloco temporal, idem: RECLAIM −9,3% → +1,8%, CLOSE_THROUGH −3,9% → +9,4%.
**O sinal oscila com o bloco em ambos os grupos**, o que é a assinatura de
ruído, não de fenômeno. Conforme S2.17, nenhuma conclusão é promovida daqui.

---

## S2.7 — Estrutura posterior

| grupo | n | BOS ≤20 | CHoCH ≤20 | volta o nível ≤20 |
|---|---|---|---|---|
| RECLAIM | 3.761 | **18,8%** | 13,7% | 100,0%¹ |
| CLOSE_THROUGH | 2.951 | 13,6% | **15,1%** | **94,8%** |

Há uma diferença, e ela aponta na direção da hipótese — o close-through é
seguido de CHoCH um pouco mais (15,1% vs 13,7%) e de BOS um pouco menos (13,6%
vs 18,8%). Mas são **5,2 pp e 1,4 pp**, contra nenhuma diferença de outcome.

**Não é suficiente para chamar o close-through de "breach/continuation".** Pelo
contrário: **94,8% dos close-through voltam a atravessar o nível em até 20
candles**. Mesmo quando o preço fecha além, ele quase sempre volta — o "failed
to hold" da narrativa está **certo**; o que está errado é o "wick".

¹ Os 100% do RECLAIM são quase tautológicos (um reclaim já fecha do lado de cá),
e estão na tabela só para o contraste ser legível. O número que fala é o 94,8%.

---

## S2.8 — Consumidores: quem precisa de quê

| consumidor | precisa de | por quê |
|---|---|---|
| `structure_confluence` | **A** — qualquer wick-through | o fator é "houve um stop-hunt na janela"; S2.10 mostra os dois creditando igual |
| `LiquidityHuntEngine` | **A** | `_collect_capture_signals`/`_swept_since` perguntam se a **liquidez foi tomada**, não se foi devolvida |
| `manipulation_cycle` | **A** | o ciclo é `Accumulation → Manipulation (sweep) → Expansion (BOS)`: o sweep é a *tomada*, e a devolução é a fase seguinte |
| `oi_regime` FLUSH | **A** | `flush_oi_drop_pct`: "leveraged positions force-closed" — posições liquidadas é **liquidez tomada**, independente do close |
| VWAP ancorada | **D** — visual | âncora no candle físico; a geometria não muda a âncora |
| `MainChart` | **D** | marcador |
| `MultiTimeframePanel` | **D** | leitura resumida |
| **`narrative`** | **B/C** — precisa dos dois | **é o único que faz uma afirmação geométrica sobre o candle** |

**Nenhum consumidor decisional precisa da separação.** O único que precisa é o
que escreve uma frase sobre o candle.

## S2.9 — HUNT

Conforme o protocolo, não assumi que o close-through devesse sair do HUNT. A
semântica que o código espera, lida dele:

- `_swept_since(events, capture_direction, flip_timestamp)` — *"whether a
  capture-side liquidity sweep fired during this leg"*;
- `_collect_capture_signals` — o sweep entra como sinal de **captura**, com peso
  `_WEIGHT_SWEEP`, ao lado de pools tocados e raids.

Em ambos a pergunta é **"a liquidez daquele lado foi tomada?"**. Um close-through
toma a liquidez tanto quanto um reclaim — toma *mais*, inclusive. **O HUNT não
deve distinguir**, e remover o close-through dele seria uma regressão.

## S2.10 — Confluência: quem carrega o crédito

| grupo | n | credita BOS | credita CHoCH | nenhum |
|---|---|---|---|---|
| RECLAIM | 3.761 | 5,1% | **47,4%** | 47,5% |
| CLOSE_THROUGH | 2.951 | 2,9% | **51,0%** | 46,1% |

**Os dois carregam o crédito praticamente na mesma proporção** (47,4% vs 51,0%).
A associação que o S7 achou no CHoCH **não vem de um dos dois grupos** — vem dos
dois. Separar o crédito por geometria não isolaria nada.

## S2.11 — Manipulation cycle

O ciclo é `Accumulation → Manipulation (sweep) → Expansion (BOS)`. A fase de
manipulação exige que os stops sejam **tomados**; a expansão que vem depois é
onde a direção se decide. Um close-through satisfaz a fase de manipulação
exatamente como um reclaim. **Faz sentido como está.**

## S2.12 — OI regime

`FLUSH` quer dizer **liquidez tomada** (`flush_oi_drop_pct`: "leveraged
positions force-closed"), não rejeição. Um close-through com queda de OI é um
flush tão legítimo quanto um reclaim — e provavelmente mais, já que o preço
aceitou o nível. **Close-through continua válido. Não distinguir.**

---

## S2.13 — Narrative: os dados já existem

Hoje, para **44% dos eventos**, `narrative.py:164` afirma algo falso:

```python
f"Sweep {ms.direction.value}{scope_label} — "
f"wick pierced {ref} {side} but failed to hold"
```

O "failed to hold" está certo (94,8% voltam pelo nível). O **"wick"** está
errado: foi o corpo.

**Os dados necessários já estão na camada de composição, por duas rotas
independentes** — e é isso que este item pedia para provar:

1. **Direta.** `NarrativeEngine.build(data)` já recebe `data.candles` e o
   próprio `ms` com `reference_price_level` (presente em 100% dos sweeps,
   S1.13). A classificação é uma comparação entre `candle.close` e
   `ms.reference_price_level` — os dois já em mãos, zero fetch, zero estado.
2. **Pela anotação que já existe.** `SweepContext` já é construído em
   composição por sweep confirmado, já é chaveado por `event_timestamp`, já
   carrega `excursion_atr` (a mesma aritmética contra o mesmo nível) e já
   trafega domínio → API → frontend.

Textos semanticamente verdadeiros (**não implementados**):

- **RECLAIM:** `"wick pierced {ref} {side} and closed back through it"`
- **CLOSE_THROUGH:** `"closed {d:.2f} ATR beyond {ref} {side} without holding"`
- **AMBIGUOUS:** `"pierced {ref} {side} and closed exactly on it"`

---

## S2.14 — Os três modelos

| | **A** — evento único + contexto | **B** — renomear para `liquidity_take` | **C** — separar tipos no domínio |
|---|---|---|---|
| compatibilidade | **total**: nada existente muda | quebra: enum é persistido, comparado e serializado | quebra todo `is LIQUIDITY_SWEEP` do projeto |
| risco | **baixo**: campo opcional numa anotação que já existe | alto | alto |
| impacto em API | aditivo (`SweepContext` já serializa) | valor do enum muda no JSON | novo membro de enum, frontend precisa tratar |
| impacto em histórico | nenhum | fixtures e docs reescritos | fixtures de estrutura reescritas |
| clareza semântica | boa: o evento continua sendo o que é (resíduo da máquina), a geometria vira anotação | melhor *no nome*, mas troca um nome impreciso por outro — `take` não diz que a persistência falhou | máxima, e desnecessária: **nenhum consumidor decisional precisa** |

**A é o mais seguro, e a medição diz que é o suficiente.** B e C pagam um custo
de migração por uma distinção que S2.6 mostra não separar comportamento e S2.8
mostra que nenhum consumidor decisional consome.

Um argumento contra B que vale registrar: o nome `liquidity_sweep` não é o
problema. O evento **é** um sweep no sentido do projeto (a liquidez foi tomada e
a estrutura não confirmou). O problema é uma frase que promete geometria.

---

## S2.16 — Casos (BTC / ETH / SOL)

- **A) reclaim limpo** — 68 casos. `BTCUSDT 4h 2026-03-23T08:00`, bullish,
  penetração 0,14 ATR, **reclaim 1,40 ATR**. Credita um CHoCH.
- **B) close-through pequeno** — 17 casos. `BTCUSDT 4h 2026-08-28T16:00`,
  bearish, penetração 0,84 ATR, **close além 0,06 ATR**. É a cauda marginal.
- **C) close-through grande** — 12 casos. `BTCUSDT 1h 2026-07-26T22:00`,
  bullish, penetração 1,62 ATR, **close além 1,35 ATR**. Aqui "wick pierced" é
  simplesmente falso.
- **D) creditado a um BOS** — 8 casos. `BTCUSDT 4h 2026-05-26T12:00`, RECLAIM,
  BOS em +9.
- **E) creditado a um CHoCH** — 110 casos, a rota dominante (S2.10).
- **F) narrative descreve errado** — **100 casos só nos majors**.
  `BTCUSDT 4h 2026-04-27T12:00`, bearish, close **0,38 ATR** além do nível,
  descrito como "wick pierced ... but failed to hold".

---

## S2.17 — Robustez

A fração de close-through é estável em **tudo**: 41,8%–47,0% entre TFs, 43,6%
vs 44,3% entre direções, 42,6%–45,3% entre os 4 blocos, e **109 de 111 símbolos
acima de 30%** (p25 39,2% · p50 43,8% · p75 49,3%).

A *diferença de comportamento* entre os dois grupos, por outro lado, **não é
robusta**: troca de sinal entre estratos e entre blocos, sempre dentro de ±10 pp
do controle. Nenhuma conclusão é promovida dela.

## S2.18 — Qual resultado

**D, com um pedaço de A.**

- **D — não há diferença útil, mas a narrativa está semanticamente errada.**
  É o resultado principal: outcome indistinguível e ambos no controle (S2.6),
  diferença estrutural de 1,4–5,2 pp sem robustez (S2.7/S2.17).
- **A — a taxonomia/contexto pode ser corrigida, o detector não.** Os
  consumidores precisam dos dois, e o detector está respondendo a pergunta certa
  (persistência), só não a que a frase anuncia.
- **NÃO é B.** Nenhum consumidor melhora removendo close-through; HUNT, OI e
  manipulation ficariam piores (S2.9/S2.11/S2.12).
- **NÃO é C.** Não há consumidor que queira só reclaim.

---

## Respostas

1. **Por que close-through vira Sweep?** Porque o sweep é o **resíduo da máquina
   de estados**: um pivô contra-tendência que quebrou a referência e *falhou a
   persistência do CHoCH* — 3 fechamentos consecutivos além do nível
   (`persistence_candles = 2`). O detector nunca olha o close do candle que ele
   data.
2. **RECLAIM:** 3.761 (54,4% de todos; 56,0% do par).
3. **CLOSE_THROUGH:** 2.951 (42,7% de todos; **44,0%** do par) — confirma os
   43,8% do S0.
4. **Por TF:** 47,0% (15m), 43,1% (1h), 41,8% (4h) de close-through.
5. **Por direção:** 44,3% bullish vs 43,6% bearish. Sem diferença.
6. **Marginal ou real?** **Real.** p50 = 0,27 ATR, 79,4% acima de 0,1 ATR,
   10,8% acima de 1 ATR, max 11,23 ATR. Há uma cauda marginal (20,6% < 0,1 ATR),
   minoritária.
7. **Reclaim depth importa?** Existe dispersão grande (17,8% abaixo de 0,1 ATR,
   30,1% acima de 0,5), mas ela **não se traduz em comportamento** — e nenhum
   limiar foi criado.
8. **Comportamento diferente?** **Não.** Ambos colados no controle em 4/4
   horizontes (−2,9% a +0,7%), sem robustez por estrato ou bloco.
9. **Close-through parece breach?** **Não.** 94,8% voltam a atravessar o nível
   em até 20 candles. Há um viés fraco (+1,4 pp de CHoCH, −5,2 pp de BOS) que
   não sobrevive à estratificação.
10. **Mitigation já tem a taxonomia?** **Sim** — `sweep`/`sweep_rejected` vs
    `breach`, com lag zero, em `liquidity/mitigation.py`. Concorda com esta
    classificação em 77,6%; a diferença é de *nível* (zona vs referência
    estrutural), não de definição.
11. **HUNT precisa distinguir?** **Não** — quer "liquidez tomada", e o
    close-through toma mais.
12. **Confluence precisa distinguir?** **Não** — os dois creditam CHoCH quase
    igual (47,4% vs 51,0%).
13. **Manipulation cycle?** **Não** — a fase de manipulação é a *tomada*; a
    devolução é a fase seguinte.
14. **OI regime?** **Não** — FLUSH é posição liquidada, não rejeição.
15. **Narrative está errada?** **Sim, e é o único achado concreto do S2.** Para
    44% dos eventos "wick pierced" é falso.
16. **UI deveria distinguir?** Opcionalmente, e só como anotação — não é
    necessário e não é o defeito.
17. **Precisa mudar domínio?** **Não.** `SweepContext` já existe, já é chaveado
    por `event_timestamp`, já carrega a mesma aritmética contra o mesmo nível e
    já trafega até o frontend.
18. **Modelo mais seguro?** **A** — evento único, geometria como contexto.
    Compatível, aditivo, sem migração.
19. **Existe bug concreto?** **Sim**: `narrative.py:164` afirma "wick pierced"
    para eventos cujo corpo fechou além do nível — 44% deles, com mediana de
    0,27 ATR e máximo de 11,23 ATR de fechamento além.
20. **Deve mudar produção?** **SIM — e apenas a frase.**

---

## Patch mínimo proposto (não implementado)

Um arquivo, um ramo, zero risco. Em `app/narrative.py`, no ramo
`LIQUIDITY_SWEEP` (linha 159), escolher o texto pela comparação que já é
possível ali — `candle.close` contra `ms.reference_price_level`:

```
close volta aquém do nível  → "wick pierced {ref} {side} but failed to hold"   (hoje, correto)
close além do nível         → "closed beyond {ref} {side} but failed to hold"
close exatamente no nível   → "pierced {ref} {side} and closed on it"
```

O `NarrativeEngine` já recebe `data.candles`; falta só o mapa
`timestamp → candle`, que outros ramos do mesmo arquivo já constroem.

**O que não fazer:** mexer no detector, no enum, na API ou no `SweepContext`.
O evento está certo; a frase é que promete uma geometria que ele nunca mediu.

Se um dia a geometria precisar viajar (para a UI, por exemplo), o lugar é um
campo opcional no `SweepContext` — modelo A — e não um tipo novo de evento.

---

## Arquivos

```
research/sweep_reclaim_audit.py        # o estudo
research/test_sweep_reclaim_audit.py   # 19 testes, com as fronteiras do S2.15
research/SWEEP_S2_RECLAIM.md           # este relatorio
research/sweep_s2_baseline.json        # gitignored (research/.gitignore)
```

```bash
poetry run python research/sweep_reclaim_audit.py --limit 112 --cases
poetry run pytest research/test_sweep_reclaim_audit.py -q
```
