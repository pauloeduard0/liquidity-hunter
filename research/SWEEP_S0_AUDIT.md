# SWEEP — Liquidity Sweep Audit (S0)

**Data:** 2026-09-11 · **Producao nao foi alterada.** · Harness:
`research/sweep_audit.py`, `research/test_sweep_audit.py`,
`research/_offline.py` (provider offline sobre `.klines_cache`, sem rede).

Amostra: **112 simbolos x M15/H1/H4 x 2400 candles = 13.550 sweeps
confirmados** + 13.550 controles casados por simbolo/TF/direcao.
Braco causal: 40 simbolos x 3 TFs, replay por prefixo (**1.566 sweeps**,
120 combos).

---

## S0 — O mapa: seis objetos, uma palavra

Nao sao o mesmo fenomeno. Ordenados por quanto aparecem para o usuario.

| # | Objeto | Nasce em | Modelo / campo | Semantica |
|---|---|---|---|---|
| **A** | `StructureEvent.LIQUIDITY_SWEEP` | `liquidity/detectors/internal_structure.py:3074` e `:4249` (rota "pivo contra-tendencia") e a rota "persistencia falhou" (`is_sustained_break`) | `MarketStructure` | **Residuo da maquina de estados**: um pivo contra-tendencia quebrou o `active_high`/`active_low` trailing e nao virou CHoCH |
| **B** | sweep de `LiquidityZone` | `liquidity/mitigation.py` | `is_mitigated`, `invalidated_at`, `sweep_rejected`, `breached_at` | **Pavio atravessa a zona**. Aqui "sweep" = wick-through e "breach" = close-through — duas palavras, dois campos |
| **C** | `LiquidityGrab` | `app/liquidity_grabs.py` | `LiquidityGrab`, `outcome` (REJECTED/SPENT), `excursion_atr`, `rejection_confirmed` | **Momento de consumo** de pools (EQ + OB), agrupado por candle+lado |
| **D** | `raid` do HUNT | `app/liquidity_hunt.py:_raid_signals` | sinal ponderado, sem modelo | Candle que **atravessa pool EQ e fecha de volta** — a unica das seis que exige reclaim explicitamente |
| **E** | `SweepContext` | `app/sweep_context.py` | `SweepContext` | Anotacao *sobre* A: o extremo caiu num OB que encara e ja existia |
| **F** | `ConfluenceFactor.LIQUIDITY_SWEEP` | `app/structure_confluence.py:222` | fator de confluencia, peso 9.0 | "houve um A perto deste BOS" |

Satelites que reusam o vocabulario sem serem nenhum dos acima:
`NarrativeEventType.SWEEP` (narrativa, deriva de A), `VWAPAnchor.EVENT`
(ancora no ultimo A), `SupertrendBreakQuality.STOP_RUN` (sweep da banda),
`POIZoneKind.BREAKER_BLOCK` (definido por "o extremo varreu o anterior"),
`ManipulationPhase.MANIPULATION` (casa A com uma zona), e o
`sweep_rejected` do frontend.

---

## 1-2. Implementacao principal e definicao formal

O sweep que **aparece no grafico** e **A**. Trajeto completo:

- nasce: `InternalStructureDetector.detect` (producao: `swing_lookback=5`,
  `persistence_candles=2`, todos os TFs — `_INTERNAL_STRUCTURE_PARAMS`)
- entidade: `MarketStructure` · enum `StructureEvent.LIQUIDITY_SWEEP`
- API: `internal_structure_events` em `GET /api/dashboard`
- frontend: `MainChart.tsx:2113` (`STRUCTURE_EVENT_STYLES.liquidity_sweep`,
  branco, linha pontilhada curta, **teto de 3 sweeps por grafico**,
  `MAX_INTERNAL_SWEEPS`)
- testes de producao: `test_market_structure.py:443,674,724`

**Regra atual, so pelo codigo:**

> Um `LIQUIDITY_SWEEP` bullish ocorre quando um **pivo de maxima** (confirmado
> por `swing_lookback` candles de cada lado) tem preco **estritamente acima**
> do `active_high` trailing **enquanto o trend da maquina e BEARISH**; ou
> quando um fechamento cruza a referencia contraria mas **nao se sustenta**
> por `persistence_candles`. O evento e datado no **primeiro candle que cruzou
> o nivel com o pavio** (`find_wick_break_index`), com `direction` = lado que o
> pavio alcancou e `reference_price_level` = o nivel quebrado.

O que a regra **nao** exige, e essa e a descoberta central:

- **nenhuma condicao sobre o close** na rota principal;
- **nenhum reclaim**;
- nenhuma tolerancia, ATR, volume, delta ou OI;
- nenhum limite de profundidade.

---

## 3. Preco de referencia

| Objeto | n | % |
|---|---|---|
| swing pivot | 12.544 | 92,6% |
| EQH/EQL | 965 | 7,1% |
| nao casou com nada mapeado | 41 | 0,3% |

**O sweep varre o pivo trailing da propria maquina de estados** — nao um pool
de liquidez. Os 7,1% de EQ sao coincidencia posicional, nao intencao: o
detector nunca consulta `liquidity_zones`. Isso e consistente com o que o
docstring do `SweepContext` ja admite ("uma afirmacao sobre a maquina de
estados, nao sobre liquidez").

---

## 4. Wick vs close — **S2, o achado semantico**

| classe | descricao | n | % |
|---|---|---|---|
| **A** | pavio atravessa, close volta aquem (rejeicao real) | 7.618 | **56,2%** |
| **B** | close **alem** do nivel (abriu aquem) | 5.734 | **42,3%** |
| **C** | corpo inteiro alem | 198 | 1,5% |
| D | tocou sem atravessar | 0 | 0% |

**43,8% dos eventos rotulados "Sweep" fecharam alem do nivel varrido.** Nao
sao rejeicoes; sao quebras que a janela de persistencia (2 candles) reprovou.
Nenhum campo de `MarketStructure` distingue os dois casos — caracterizado em
`test_sweep_emitted_IDENTICALLY_when_the_close_is_BEYOND_the_level`.

Consequencia concreta e **factualmente errada** hoje em producao
(`app/narrative.py:159`):

```
f"Sweep {direction} — wick pierced {ref} {side} but failed to hold"
```

"failed to hold" e falso em 43,8% dos casos. Exemplo real: BTCUSDT M15
2026-08-22 05:00, classe B, **5,11 ATR** alem do nivel, reclaim −1,52 ATR —
isso e um breakout, rotulado Sweep.

---

## 5-6. Causalidade e timestamp — **S1, o achado mais grave**

O evento e datado no candle do **wick break**. Mas so e emitido quando o pivo
confirmador se forma. Replay por prefixo (a pipeline de producao inteira sobre
prefixos crescentes, 120 combos):

- **atraso mediano ate o evento existir: 15 candles** (p90 = 318, max = 733)
- **2,91%** dos sweeps vistos ao vivo **sumiram** da serie final (repaint) —
  e sweeps **nunca** carregam `provisional=True`, entao esse repaint nao tem
  marca nenhuma

Classificacao pedida no item 5: **(D) bug causal** para qualquer consumidor que
trate o timestamp como "momento em que o evento foi conhecido", e **(C)
window-dependent** para a cauda de p90=318 (o `_structural_anchor_index` e o
`mean_tr_pct` global — o problema ja registrado em
`project_atr_gates_window_dependent`).

**A consequencia quantitativa é o resultado principal desta auditoria.** O pivo
so confirma porque os `swing_lookback = 5` candles seguintes nao superaram o
extremo — ou seja, medir a reacao a partir do candle datado usa,
**por construcao**, o fato de que a excursao adversa dos 5 candles seguintes
foi limitada. Medindo a mesma coisa com a entrada deslocada:

| inicio da medicao | h=5 MFE>MAE | h=10 | h=20 | h=40 |
|---|---|---|---|---|
| candle datado (`offset=0`) | **62,3%** | 62,5% | 60,9% | 58,9% |
| `offset=5` (= swing_lookback) | 53,2% | 53,1% | 52,5% | 52,1% |
| `offset=10` | **50,3%** | 50,7% | 50,4% | 50,6% |
| controle casado | 50,9% | 50,4% | 50,8% | 50,6% |

Confirmado de forma independente pelo replay real (n=1566, indice de
nascimento medido, nao estimado):

| braco | h=5 | h=10 | h=20 | h=40 |
|---|---|---|---|---|
| naive (candle datado) | 64,4% | 63,4% | 60,6% | 58,8% |
| **conhecivel (replay)** | **53,2%** | **49,2%** | 50,3% | 50,1% |
| controle | 52,9% | 51,7% | 50,8% | 49,8% |

> **Todo o "edge" aparente do sweep e o lookahead da confirmacao do pivo.**
> No instante em que o evento realmente existe, ele e indistinguivel de um
> candle aleatorio da mesma serie na mesma direcao.

Isso replica exatamente a licao ja registrada em
`project_provisional_marks_edge` e `project_raid_reversal_measurement`.

---

## 7. Direcao

Convencao unica e **consistente** em todos os modulos: `direction` = **lado que
o pavio alcancou** (BULLISH = tomou maximas). Verificado em
`sweep_context.py`, `manipulation_cycle.py:_find_swept_zone`,
`liquidity_hunt.py` (`capture_direction`), `oi_regime.py`, `narrative.py`,
`MainChart.tsx` (`TREND_ICONS`). **Nenhuma inversao semantica encontrada.**

Ressalva de leitura, nao de codigo: o rotulo `Sweep ▲` significa "varreu para
cima", isto e, implicacao **baixista** — a seta aponta ao contrario da leitura.
O projeto ja resolveu o mesmo problema para `CHoCH ▲ ✕`; aqui nao resolveu.

---

## 8. Duplicidade — **S3**

| outro detector | mesmo candle | ±1 | ±2 | ±5 |
|---|---|---|---|---|
| **invalidacao de `LiquidityZone` (B)** | **97,4%** | 98,7% | 99,1% | 99,7% |
| `LiquidityGrab` (C) | 12,4% | 15,2% | 16,9% | 21,3% |
| `raid` do HUNT (D) | 8,5% | — | — | — |

**97,4% dos sweeps sao literalmente o mesmo pavio que a camada de mitigacao ja
marcou** como `invalidated_at` da zona de swing correspondente. Isso e
tautologico (o nivel varrido *e* um pivo, e pivos viram `SwingHigh`/`SwingLow`
zones), mas significa que A e B sao a **mesma observacao contada duas vezes**,
com vocabularios incompativeis: em B, "sweep" e o wick-through e o close-through
chama-se "breach"; em A, os dois sao "sweep".

O grab (C) so coincide em 12,4% porque C exige **pool agrupado** (EQ com
`min_touches=3`) ou order block — e uma regra mais exigente sobre o mesmo wick.

---

## 9. Multi-level

Niveis mapeados **vivos** que o mesmo pavio cruzou: 1 nivel 58,2%, 2 niveis
28,6%, 3+ 13,2%. Um sweep emite **um** evento por pivo, nao um por nivel —
nao ha cluster nem multiplicacao. **Sem poluicao visual por esse eixo**, e o
teto de `MAX_INTERNAL_SWEEPS = 3` no `MainChart` fecha a questao.

---

## 10-13. Geometria (h=5, **medida no braco naive — ver a ressalva abaixo**)

| penetracao (ATR) | n | MFE>MAE | | reclaim (ATR) | n | MFE>MAE |
|---|---|---|---|---|---|---|
| 0–0,1 | 2.705 | 58,7% | | ≤0 (fechou alem) | 5.932 | 65,0% |
| 0,1–0,25 | 3.279 | 60,0% | | 0–0,25 | 3.732 | 61,0% |
| 0,25–0,5 | 3.166 | 62,2% | | 0,25–0,5 | 1.773 | 61,6% |
| 0,5–1,0 | 2.565 | 64,8% | | 0,5–1,0 | 1.302 | 60,1% |
| >1,0 | 1.835 | **68,5%** | | >1,0 | 811 | **53,9%** |

| wick/range | n | MFE>MAE | | idade do nivel | n | MFE>MAE |
|---|---|---|---|---|---|---|
| 0–0,25 | 5.475 | 63,6% | | 0–10 | 6.020 | 60,9% |
| 0,25–0,5 | 4.439 | 62,5% | | 11–30 | 5.668 | 62,0% |
| 0,5–0,75 | 2.883 | 60,1% | | 31–100 | 746 | **70,1%** |
| >0,75 | 752 | 60,6% | | >100 | 1.075 | 66,9% |

Leitura honesta destas quatro tabelas: **penetracao e monotonica e reclaim e
monotonico ao contrario** — quanto mais fundo o pavio foi e quanto *menos* o
close voltou, melhor a "reacao". Isso e o oposto da intuicao SMC, e e
exatamente o que se espera se a metrica esta medindo o **tamanho do pavio**
(que e o proprio MFE potencial) e nao uma rejeicao. **Wick/range nao separa
nada** (63,6% → 60,6%): o Sweep bom **nao** precisa de pinbar.

**Ressalva obrigatoria:** todas as quatro tabelas herdam o vies do item 5. Nao
foram re-medidas no braco conhecivel porque, naquele braco, o nivel agregado ja
e o controle — nao ha o que decompor. Sao diagnostico da forma do evento, nao
evidencia de qualidade.

---

## 14-16. Lifecycle, re-sweep, idade

- **Lifecycle (S6):** o `active_high`/`active_low` varrido e estado interno da
  maquina, **nao** tem lifecycle proprio — nao existe `alive` / `already
  invalidated` / `swept before` para ele. Os 92,6% que casam com um
  `SwingHigh`/`SwingLow` casam com uma zona que **a propria varredura
  invalida** (item 8). A pergunta "o nivel estava vivo?" **nao tem resposta na
  implementacao atual**. Nao foi filtrado nada; foi medido e registrado.
- **Re-sweep:** 1o 91,0% (n=12.326), 2o 8,2%, 3o+ 0,9% (n=117). O 3o+ mede
  melhor (70,9% vs 62,1%) mas com n=117 e no braco enviesado — nao e evidencia.
- **Idade:** niveis com 31–100 candles medem melhor (70,1%). Mesmo vies.

---

## 17-18. Contexto estrutural e consequencia

Sweeps sao **estruturalmente contra-tendencia por construcao**: 92,1% da
amostra contra o trend, 7,1% a favor, 0,8% sem trend estabelecido. Nao ha o que
comparar — o eixo e degenerado. (A que rota do detector pertencem os 7,1% nao
foi medido; a auditoria nao instrumenta a rota de emissao.)

Depois do sweep, primeiro evento estrutural:

| h | nenhum | BOS | CHoCH |
|---|---|---|---|
| 5 | **96%** | 3% | 1% |
| 10 | 88% | 7% | 3% |
| 20 | 73% | 15% | 10% |
| 40 | 51% | 26% | 23% |

**O sweep nao e precursor de mudanca estrutural.** Em 5 candles nada acontece
em 96% dos casos, e em 40 candles a distribuicao BOS/CHoCH (26/23) esta
proxima de simetrica — a taxa-base da serie, nao um sinal.

---

## 19-21. EQH/EQL, Grab e HUNT

**19 — EQ nao e diferente:** 61,2% (n=965) vs 62,4% para swing. O sweep de um
equal-level nao mede diferente de um sweep de pivo solto.

**20 — Sweep vs Grab, diferenca formal:**

| | Sweep (A) | Liquidity Grab (C) |
|---|---|---|
| gatilho | pivo quebra o nivel trailing da maquina | pool mapeado e consumido |
| exige reclaim? | **nao** | so para rotular `REJECTED` |
| exige pool agrupado? | nao | **sim** (EQ `min_touches=3`, ou OB) |
| unidade | por pivo | por candle+lado (agrupa pools empilhados) |
| depende do trend? | **sim** | nao |
| confirmado depois? | pelo pivo (mediana 15 candles) | `rejection_confirmed` em 2 candles |
| profundidade | `SweepContext.excursion_atr` | `LiquidityGrab.excursion_atr` |

Nao sao estagios um do outro e **coexistem em 12,4% dos casos**. Redundancia
real: os dois carregam um `excursion_atr` calculado com a mesma formula e o
mesmo ATR de janela, sobre o mesmo candle, em dois modelos diferentes.

**21 — HUNT:** o sweep entra como fonte `"sweep"` com peso `_WEIGHT_SWEEP` em
`_collect_capture_signals`. O mesmo wick pode pontuar **tres vezes**:
`sweep` (A) + `zone` (B, via `zone.invalidated_at`) + `raid` (D). Medido:
8,5% dos sweeps tem um `raid` no mesmo candle, e 97,4% tem a invalidacao de
zona. O `_FLOOR_SIGNATURE_SOURCES` ja trata `raid` e `supertrend` como
"raid-shaped" para evitar dupla contagem; **`sweep` e `zone` nao recebem o
mesmo tratamento**. Diagnostico apenas — o HUNT nao foi tocado.

---

## 22. Funil

```
pivo confirmado (swing_lookback=5)          -> a origem de tudo
  quebra o active_high/low trailing         -> 100% (definicao)
    trend contrario                         -> LIQUIDITY_SWEEP
    persistencia falha                      -> LIQUIDITY_SWEEP
    (a divisao entre as duas rotas nao foi instrumentada)
  datado em find_wick_break_index           -> retroativo, mediana 15 candles
  close volta aquem                         -> 56,2%   (A)
  close alem                                -> 43,8%   (B+C)
  cai em OB que encara (SweepContext)       -> anotacao aditiva
  3 mais recentes                           -> unicos desenhados
```

Nao ha etapa de "reclaim" e nao ha etapa de "nivel vivo" no funil real. As
duas etapas que a auditoria procurava **nao existem no codigo**.

---

## 23-28. Qualidade, direcao, TF, robustez

Ja no item 5. Resumindo o que sobra depois de corrigir a causalidade:

- **por TF:** M15 63,0% / H1 62,8% / H4 61,0% no braco naive — sem separacao
  material. Nenhum TF sobrevive ao braco conhecivel.
- **por direcao:** bullish 62,0% vs bearish 62,9%. **Sem a assimetria do
  PISO H4.**
- **temporal:** 4 blocos em 63,0 / 61,1 / 61,9 / 63,3% — estavel, e estavel
  tambem no vies (um artefato de construcao *deveria* ser estavel).
- **por simbolo:** 110 simbolos com n≥20, **110 positivos (100%)**, mediana
  62,7%. Um resultado positivo em 100% dos simbolos, num projeto onde nada
  jamais foi positivo em 100% dos simbolos, e o proprio sinal de artefato.

---

## 29. Casos reais

| caso | exemplo | leitura |
|---|---|---|
| A) sweep limpo | BTCUSDT H1 2026-06-24 11:00 · pen 0,30 ATR · reclaim +0,52 · MFE5 9,32 / MAE5 0,13 | o que o usuario imagina que e um Sweep |
| B) sweep que falha | BTCUSDT H1 2026-08-28 14:00 · pen 1,19 · reclaim +1,67 · MFE5 0,84 / MAE5 6,83 | reclaim **forte** e resultado pessimo — o eixo nao separa |
| C) muito raso | BTCUSDT M15 2026-08-18 22:15 · pen **0,00 ATR** · idade **0** | nivel formado no mesmo candle em que foi varrido |
| D) profundo | BTCUSDT M15 2026-08-22 05:00 · pen **5,11 ATR** · classe B | isso e um breakout rotulado Sweep |
| E) re-sweep | ETHUSDT M15 2026-09-08 01:15 · nth=3 · classe B | o 3o sweep tambem fechou alem |
| F) EQ | BTCUSDT M15 2026-08-18 22:15 | o mesmo evento do caso C |
| G) sweep+grab+raid | BTCUSDT M15 2026-08-19 00:15 · co_grab=0, co_raid=0 | tres nomes, um pavio |
| H) duplicata | BTCUSDT M15 2026-09-06 18:00 · 3 niveis, grab+zone no mesmo candle | idem, com 3 niveis consumidos |

---

## 30. Testes — cobertura e lacunas

Existe hoje (`test_market_structure.py`, `test_mitigation.py`,
`test_sweep_context.py`, `test_liquidity_grabs.py`): bullish/bearish, wick-only
vs close (**em B, nao em A**), rota de persistencia, re-anchor do candidate
CHoCH, block pre-existente/aposentado.

**Lacunas, por prioridade de "protege a semantica atual":**

1. **nenhum teste em A distinguia reclaim de close-through** — preenchido aqui
   (`test_sweep_emitted_IDENTICALLY_...`), como caracterizacao
2. **nenhum teste de truncation/replay** para o timestamp de A — a lacuna que
   deixou o item 5 passar; preenchida parcialmente pelo `--causal`
3. fronteira de igualdade em A — preenchida (`test_a_close_exactly_at_the_level...`)
4. **nao preenchidas** (deliberadamente, exigem decisao de produto): duplicata
   no mesmo candle entre A e B; multiplos niveis no mesmo wick; ausencia de
   `provisional` em A apesar do repaint de 2,91%

`research/test_sweep_audit.py`: **16 testes, todos passando.**

---

## 31. Hipoteses, ordenadas

| # | hipotese | evidencia | impacto | risco | facil de testar |
|---|---|---|---|---|---|
| **S1** | **bug causal**: timestamp e retroativo (mediana 15 candles, p90 318) e 2,91% repinta sem marca `provisional` | **forte, medida 2x** | alto | baixo | ja testado |
| **S2** | **semantica wick vs close**: 43,8% dos "Sweep" fecharam alem; a narrativa afirma "failed to hold" | **forte, 13.550 eventos** | alto | baixo | ja testado |
| **S3** | **duplicidade A×B**: 97,4% mesmo pavio, vocabularios conflitantes | **forte** | medio | medio | facil |
| **S10** | **redundancia no HUNT**: `sweep`+`zone`+`raid` podem pontuar o mesmo wick; so `raid`/`supertrend` sao desduplicados | media | medio | alto | medio |
| **S6** | **lifecycle inexistente**: o nivel varrido nao tem estado de vida | forte (por leitura de codigo) | medio | medio | dificil |
| **S9** | **visual**: `Sweep ▲` aponta ao contrario da implicacao | fraca (leitura) | baixo | baixo | trivial |
| **S4** | penetracao/reclaim: monotonicos ao **contrario** da intuicao | fraca (enviesada) | baixo | alto | ja medido |
| **S7** | confluencia direction-agnostic (`structure_confluence.py:222`, peso 9.0 — um sweep de maximas credita um BOS de alta) | media (leitura + comentario do proprio codigo) | medio | medio | facil |
| **S5** | re-sweep (n=117) | fraca | baixo | alto | medio |
| **S8** | assimetria TF/direcao | **ausente** | — | — | — |

---

## 34. Entrega

1. **Quantos tipos de Sweep?** Seis objetos distintos (A-F) + 5 satelites.
2. **Qual e o principal?** A — `StructureEvent.LIQUIDITY_SWEEP`.
3. **Definicao formal?** Item 2. Pivo contra-tendencia que quebra o nivel
   trailing e nao vira CHoCH. Sem close, sem reclaim, sem ATR, sem volume.
4. **Que nivel varre?** O pivo trailing da maquina (92,6%), nao um pool.
5. **Wick e close tratados corretamente?** **Nao.** 43,8% fecham alem e saem
   com o mesmo rotulo; a narrativa afirma o contrario do que aconteceu.
6. **E causal?** **Nao** como o timestamp sugere. Mediana de 15 candles de
   atraso ate existir.
7. **Existe repaint?** Sim, 2,91%, **sem marca `provisional`**.
8. **Timestamp semanticamente correto?** Correto como "quando o nivel foi
   cruzado"; **incorreto** para qualquer leitura de "quando isso foi sabido".
9. **Bullish/bearish consistentes?** **Sim**, em todos os modulos.
10. **Duplicidade com Grab/HUNT?** Com o Grab 12,4%; com a invalidacao de zona
    **97,4%**; com o raid 8,5%.
11. **Quantos eventos por wick?** Ate 4 nomes (A+B+C+D) para um pavio.
12. **Multi-level gera ruido?** **Nao** — um evento por pivo, teto de 3 no chart.
13. **Penetracao importa?** Monotonica, mas na direcao errada e no braco
    enviesado. Nao e evidencia.
14. **Reclaim importa?** Idem, invertido: quem **nao** recuperou mede melhor.
15. **Wick/body importa?** **Nao.** 63,6% → 60,6% ao longo de todo o eixo.
16. **Primeiro vs re-sweep?** n insuficiente (117 no 3o+) e enviesado.
17. **Idade importa?** Aparentemente sim (31-100 candles), mesmo vies.
18. **Lifecycle correto?** **Nao existe** para o nivel que A varre.
19. **Sweep de EQH/EQL e diferente?** **Nao.** 61,2% vs 62,4%.
20. **Antecede BOS/CHoCH?** **Nao.** 96% sem evento em 5 candles; simetrico em 40.
21. **Qualidade por TF?** Sem separacao; nada sobrevive ao braco causal.
22. **Assimetria por direcao?** **Nenhuma.**
23. **Estabilidade temporal?** Estavel — o artefato tambem e.
24. **Robustez por simbolo?** 110/110 positivos: o sinal de artefato.
25. **Bugs concretos?** Tres: (a) narrativa afirma "failed to hold" em 43,8%
    de casos onde nao houve reclaim; (b) repaint de 2,91% sem `provisional`;
    (c) confluencia soma peso 9,0 de um sweep de **qualquer** direcao.
26. **Redundancias concretas?** A×B (97,4%), `excursion_atr` duplicado entre
    `SweepContext` e `LiquidityGrab`, e `sweep`+`zone` nao-desduplicados no HUNT.
27. **O que esta bom e deve ficar?** A convencao de direcao (unica em 6
    modulos); o `SweepContext` como anotacao **aditiva** e o honesto docstring
    que ja chamava o sweep de "residuo da maquina de estados"; o teto de 3
    sweeps no chart; o `LiquidityGrab` como a regra mais exigente das seis.
28. **O que parece mais promissor melhorar?** **S2** — separar reclaim de
    close-through. E a unica mudanca que corrige um rotulo factualmente errado
    sem tocar na maquina de estados, e o projeto ja fez exatamente isso uma vez
    (`rejection_confirmed` no `LiquidityGrab`, `project_grab_rejection_confirm`:
    corrigiu o **rotulo**, nao gerou edge). A licao se repete aqui.
29. **Qual hipotese testar primeiro?** **S2**, com S1 como pre-condicao de
    metodo (qualquer medicao futura de sweep tem que rodar no braco conhecivel).
30. **Ha evidencia suficiente para mudar producao agora?** **NAO.**

**30 = NAO → parado aqui.** Nenhum threshold, cor, label, lifecycle, pivo, UI,
HUNT, grab ou EQH/EQL foi alterado.

---

## Arquivos

```
research/sweep_audit.py            # harness (painel + --causal + --replay + --offset)
research/test_sweep_audit.py       # 16 testes: helpers + caracterizacao da semantica
research/_offline.py               # provider offline sobre .klines_cache
research/sweep_audit_baseline.json # gitignored (research/.gitignore: *_baseline.json)
```

Reproducao:

```bash
poetry run python research/sweep_audit.py --limit 112 --timeframes 15m 1h 4h
poetry run python research/sweep_audit.py --limit 112 --timeframes 15m 1h 4h --offset 5
poetry run python research/sweep_audit.py --causal --limit 40 --replay-step 4
poetry run pytest research/test_sweep_audit.py -q
```
