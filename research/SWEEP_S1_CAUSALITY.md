# SWEEP — S1: Causal Availability / Timestamp Audit

**Data:** 2026-09-11 · **Produção não foi alterada.** · S0 fechado em `39b23dc`.
Harness: `research/sweep_causality.py`, `research/test_sweep_causality.py`
(20 testes), baseline `research/sweep_s1_baseline.json` (gitignored).

Amostra analítica: **112 símbolos × M15/H1/H4 × 2400 candles = 13.550 sweeps**.
Replay exato: **12 símbolos × 3 TFs × 800 candles, passo 1** (uma passada da
pipeline de produção por candle).

---

## S1.4 — O que o timestamp representa hoje

Lendo o código, não o docstring:

```python
# internal_structure.py:3074 (e o espelho em :4249)
sweep_candle = candles[
    find_wick_break_index(candles, prev_high_pivot_index + 1,
                          current_index, active_high.price, bullish=True)
]
emit(sweep_candle.timestamp, StructureEvent.LIQUIDITY_SWEEP, ...)
```

O timestamp é o **primeiro candle, desde o pivô anterior, cujo pavio cruzou o
nível** — não o candle do pivô, não o da confirmação. É deliberado: `find_wick_break_index`
existe só para isso, e o comentário do detector chama esse candle de "a quebra".

O mesmo timestamp atravessa a pilha inteira sem tradução:
`MarketStructure.timestamp` → `internal_structure_events` em
`GET /api/dashboard` → `toChartTime(event.timestamp)` no `MainChart.tsx:2103`.
**Em nenhum ponto existe um campo que diga quando o evento passou a ser
conhecido.**

---

## S1.0 / S1.1 — Os três tempos, e de onde vem o atraso

O pivô foi localizado por casamento **exato** de extremo em **13.550/13.550
(100%)** dos sweeps — a decomposição abaixo é aritmética, não estimativa.

| componente | p50 | p75 | p90 | max |
|---|---|---|---|---|
| back-dating (`timestamp` → candle do pivô) | 1 | 4 | 8 | 63 |
| `swing_lookback` (fixo, todos os TFs) | 5 | 5 | 5 | 5 |
| **piso analítico** (soma) | **6** | 9 | 13 | 68 |

Histograma do back-dating: 39,4% em 0 candles, 17,2% em 1, 8,9% em 2 — mas
uma cauda longa, com 1,9% acima de 20 candles e um máximo de 63.

**Resposta ao "por que 15 se o lookback é 5?":** o lookback é apenas **um dos
três** componentes, e o menor deles na cauda. Mesmo somando back-dating +
lookback, o piso mediano é **6** — ainda menos da metade do atraso real. O que
sobra é a máquina de estados: o branch de sweep só executa quando o *próximo*
pivô contrário é processado, e isso pode levar uma perna inteira.

---

## S1.9 — O número crucial: o nível já era conhecido?

| | n | % |
|---|---|---|
| **A) nível já conhecível no candle do sweep** | 12.982 | **95,8%** |
| B) nível só conhecível depois (p50 = 4 candles) | 529 | 3,9% |
| – nível não localizado | 39 | 0,3% |

**O atraso não vem do nível varrido — vem do pivô que dispara a emissão.** Em
95,8% dos casos, no instante em que o pavio atravessou, um observador causal
já sabia que aquele nível existia. O que ele não podia saber era que aquele
extremo viraria pivô, e que a máquina classificaria o episódio como SWEEP.

Isso é o que separa S1.8 de S1.7 abaixo — a geometria é causal, a
**classificação** não é.

---

## S1.5 — O gráfico mostra informação retroativa?

**Sim, em 100% dos casos, sem exceção medida.**

| corte | n | retroativos | p50 | p75 | p90 | max |
|---|---|---|---|---|---|---|
| TOTAL | 13.550 | **100,0%** | 6 | 9 | 13 | 68 |
| 15m | 4.398 | 100,0% | 6 | 9 | 14 | 68 |
| 1h | 4.802 | 100,0% | 6 | 9 | 13 | 50 |
| 4h | 4.350 | 100,0% | 6 | 9 | 13 | 52 |
| bullish | 7.871 | 100,0% | 6 | 9 | 13 | 68 |
| bearish | 5.679 | 100,0% | 6 | 9 | 13 | 52 |

Por símbolo: **110 símbolos com n≥20, todos os 110 com 100% dos sweeps
retroativos**; a mediana do atraso varia só entre 5 e 8 candles (piores:
EOSUSDT 8, ATOMUSDT 7). Não é propriedade de mercado nenhum — é do algoritmo.

Em tempo de relógio, a mediana do **piso analítico**:

| TF | mediana |
|---|---|
| M15 | 1h30 |
| H1 | 6h |
| H4 | **1 dia** |

O marcador `Sweep` no `MainChart` é desenhado em `toChartTime(event.timestamp)`,
num candle onde, ao vivo, ele não existia. **Em nenhuma leitura da amostra o
sweep existia no candle que o data.**

---

## S1.6 — Quem consome, e como

| consumidor | arquivo | usa o timestamp para | classe |
|---|---|---|---|
| `LiquidityHuntEngine._collect_capture_signals` | `app/liquidity_hunt.py:897` | filtrar `start <= ts <= end` e pontuar (`_WEIGHT_SWEEP`) | **decisão** |
| `LiquidityHuntEngine._swept_since` | `app/liquidity_hunt.py:1304` | `ts >= flip_timestamp` → gate de fase | **decisão** |
| `StructureConfluenceEngine` | `app/structure_confluence.py:222` | janela de evidência, peso 9,0 | **decisão** |
| `ManipulationCycleDetector` | `psychology/.../manipulation_cycle.py:267` | início do ciclo + janela de expansão (30–50 candles) | **decisão** |
| `OIRegimeAnalyzer` | `psychology/.../oi_regime.py:227` | `FLUSH` é exclusivo de `LIQUIDITY_SWEEP` | **decisão** |
| `_build_anchored_vwaps` | `app/dashboard_data.py:2680` | âncora da VWAP no último sweep | **decisão** (linha desenhada) |
| `MainChart` | `MainChart.tsx:2113` | posição do marcador (3 mais recentes) | visual |
| `MultiTimeframePanel` | `.tsx:32` | rótulo `SWEEP` | visual |
| `NarrativeEngine` | `app/narrative.py:159` | texto do evento | visual |

**Excluem sweep de propósito** (e por isso não são afetados): a escada de trend
do `app/overview.py:75`, a linha de fase do `tideRibbon.ts`, e a referência
permanente de `structureRelevance.ts:171`.

Nota sobre a âncora de VWAP: o guarda é `_VWAP_MIN_ANCHOR_CANDLES = 3`, menor
que o piso mediano de 6 — a linha pode ancorar num sweep que ainda não existia.
Nota sobre o ciclo de manipulação: a janela de expansão é de 30–50 candles, e
o próprio sweep consome ~15 dela antes de ser conhecido.

---

## S1.11 / S1.12 — Impacto medido nos consumidores

4.204 eventos BOS/CHoCH recebem crédito de sweep na amostra completa.

| | n | % |
|---|---|---|
| **sweep só existiu DEPOIS do evento que credita** | 1.727 | **41,1%** |
| janela olha para frente (só CHoCH, por desenho) | 3.264 | 77,6% |
| crédito só de sweep do mesmo lado da quebra (S7) | 1.310 | 31,2% |

Por tipo, que é onde a leitura muda:

| evento | n | lado errado | anacrônico |
|---|---|---|---|
| `break_of_structure` | 927 | **4,5%** | **42,0%** |
| `change_of_character` | 3.277 | **38,7%** | 40,8% |

**O BOS é o caso limpo e é o problema real.** A janela do BOS é estritamente
para trás (`ev_idx - _SWEEP_LOOKBACK .. ev_idx - 1`), então não há desenho
forward para desculpar nada: **42,0% dos BOS que recebem peso 9,0 de "um
stop-hunt precedeu esta quebra" recebem esse peso de um sweep que ninguém
podia ter visto quando a quebra imprimiu.**

O CHoCH tem 40,8% de anacronismo, mas 77,6% das janelas de CHoCH olham para
frente **deliberadamente** (`_CHOCH_FORWARD_CAP = 60`, documentado como "a
CHoCH accretes confluence as its level is defended"). Lá o anacronismo é a
regra escrita, não um bug.

**S7, com a definição corrigida.** Minha primeira medição inverteu o lado.
O alinhamento certo é o que o próprio comentário do engine descreve — *"a
bullish reversal sweeps the lows, a wick-down/bearish-labeled sweep"* — ou
seja, o sweep que **alimenta** uma quebra é o de direção **oposta** a ela.
Com isso: BOS **4,5%** de crédito exclusivamente do lado errado, CHoCH
**38,7%**. O engine continua sem testar direção nenhuma; o que muda é o
tamanho do problema — pequeno no BOS (a janela curta só pega o sweep
contra-tendência natural), grande no CHoCH (a janela de 60 candles pega os
dois lados).

**E o caso que o HUNT lê torto:** **6,6% dos sweeps (897/13.550) são datados
antes de um BOS/CHoCH que só passou a existir depois deles.** Esses aparecem
do lado errado da perna para `_swept_since` (`ts >= flip_timestamp`) e para o
fatiamento de episódios por `grab_ts`. Com o piso analítico; o número real é
maior.

---

## S1.13 — O que prepara o S2

`app/narrative.py:159` afirma *"wick pierced X but failed to hold"* em 100%
dos sweeps, enquanto 43,8% fecharam além do nível (S0). Pergunta desta etapa:
dá para distinguir **sem tocar no detector**?

**Dá.** `reference_price_level` está presente em **13.550/13.550** dos sweeps
confirmados, e o candle do evento está em `DashboardData.candles`. A
comparação `close` vs `reference_price_level` é um cálculo de camada de
composição — exatamente o que `app/sweep_context.py` já faz hoje ao ler
`candle.high`/`candle.low` para o `excursion_atr`.

E o canal já existe fim-a-fim: `SweepContext` trafega
`core/domain` → `DashboardData` → `api/schemas.py:82` → `types/dashboard.ts:736`
→ `MainChart.tsx:2040`. **Zero encanamento novo.**

---
## S1.2 / S1.3 — Replay exato: o evento nasce e depois fica quieto

12 símbolos × 3 TFs × 800 candles, **passo 1** — a pipeline de produção
inteira reexecutada a cada candle, 400 passadas por combo.

| | n | % |
|---|---|---|
| identidades vistas em algum prefixo | 502 | |
| sobreviveram até a série final | 494 | 98,4% |
| **apareceram e sumiram (repaint)** | **8** | **1,59%** |

Identidade física = `(direction, price_level)`. O `price_level` é o extremo do
pivô — um high/low de candle, exato em float e imutável — então um timestamp
reescrito aparece como *reescrita*, não como "sumiu e nasceu outro".

Depois de nascer:

| classe | n | % |
|---|---|---|
| **A) atrasado mas estável** | 482 | **97,6%** |
| B) piscou (sumiu e voltou) | 2 | 0,40% |
| C) timestamp reescrito | 5 | 1,01% |
| C) `reference_price_level` reescrito | 5 | 1,01% |
| direção/tipo reescritos | 0 | — |

**Este é o achado tranquilizador do S1.** O sweep não é um evento instável: em
97,6% dos casos ele nasce tarde e nunca mais muda. O problema não é repaint —
é **latência com data falsa**. Os 1,59% de repaint (o S0 mediu 2,91% com uma
amostra diferente) são a exceção, e mesmo eles não vêm marcados: sweeps nunca
carregam `provisional=True`.

---

## S1.8 — `provisional` é semanticamente possível?

**Não para o evento que existe hoje. Sim para uma afirmação mais fraca —
que o projeto já tem.**

O S1.9 mostra que, no candle do sweep, a **geometria** é inteiramente causal:
em 95,8% dos casos o nível já era conhecido, e "o pavio atravessou este nível
e (em 56,2% dos casos) o close voltou" é computável ali mesmo, sem lag nenhum.

O que **não** é causal é a **classificação**. No candle do sweep não se sabe:

- se aquele extremo virará pivô (o candle seguinte pode superá-lo);
- se a máquina chamará o episódio de `LIQUIDITY_SWEEP`, `CHANGE_OF_CHARACTER`
  ou `BREAK_OF_STRUCTURE` — a distinção depende da persistência e do que o
  trend estava fazendo, nenhum dos dois resolvido ainda.

Ou seja: um `Sweep?` prometeria uma classificação que o detector não pode
prometer. Isso o separa do `BOS?` e do `CHoCH?`, que são provisionais
legítimos — lá o **nível já fechou-quebrou** e só faltam os pivôs
confirmadores; aqui falta o próprio fato que define o evento.

E a afirmação fraca que *é* causal já está implementada duas vezes:
`liquidity/mitigation.py` (`is_mitigated` / `sweep_rejected`, lag zero) e
`LiquidityHuntEngine._raid_signals` (pavio atravessa pool e fecha de volta,
lag zero). Criar um terceiro objeto para dizer a mesma coisa seria repetir o
S3 do S0 — 97,4% de duplicidade — de propósito.

**MODEL C está descartado por semântica, não por estética.**

---

## S1.7 — Os três modelos

| | **A** — manter `timestamp`, somar `known_at` | **B** — mover `timestamp` para `known_at` | **C** — `provisional` até `known_at` |
|---|---|---|---|
| fidelidade histórica | **alta**: os dois fatos ficam separados e ambos verdadeiros | **baixa**: perde o candle onde o pavio de fato cruzou; a linha do sweep deixa de tocar o nível | — |
| impacto visual | nenhum obrigatório; habilita "sweep conhecido há N candles" | **alto**: marcadores migram até 68 candles (1 dia no H4); o desenho deixa de casar com o pavio | — |
| impacto API | aditivo (campo opcional; `from_attributes` já serializa) | **quebra** consumidores que casam por timestamp (`SweepContext.event_timestamp`, `OIQualifiedEvent`, âncora de VWAP) | — |
| impacto HUNT/confluence | permite corrigir cada janela **por consumidor**, sem mexer nos outros | muda todas as janelas de uma vez, inclusive as que estavam certas | — |
| risco de regressão | **baixo**: nada existente muda de valor | **alto**: 5 consumidores de decisão + 3 fixtures de estrutura | — |
| possível? | sim | sim | **não** (S1.8) |

O que decide é o S1.3: o evento é **estável depois de nascer** (97,6%). Um
evento estável com dois tempos distintos é exatamente o caso em que separar
`event_time` de `known_at` é a representação correta — e é o mesmo padrão que
o projeto já usa quando o "quando" e o "quando se soube" divergem
(`LiquidityGrab.rejection_confirmed`, que é `None` enquanto a janela não
fechou, em vez de chutar).

MODEL B trocaria um erro por outro: hoje o marcador mente sobre *quando se
soube*; movido, mentiria sobre *onde o preço esteve* — e o desenho do sweep,
que é uma linha curta no extremo do pavio, deixaria de tocar o pavio.

---
## S1.5-replay / S1.1 — **Correção de um número do S0**

O replay do S0 (passo 4) abria a janela na metade da série e contava como
"nasceu no primeiro prefixo" todo evento que **já estava vivo quando a janela
abriu**. Isso é censura à esquerda, e inflava o atraso. O replay desta etapa
descarta esses casos (229 de 494) e mede só nascimentos observados:

| | S0 (censurado) | **S1 (sem censura, n=265)** |
|---|---|---|
| p50 | 15 | **6** |
| p75 | — | **9** |
| p90 | 318 | **14** |
| max | 733 | 246 |

| componente | p50 | p75 | p90 | max |
|---|---|---|---|---|
| piso analítico (back-dating + lookback) | 6 | 9 | 13 | 28 |
| **resíduo da máquina de estados** | **0** | **0** | **0** | 240 |
| known_at − timestamp (total) | 6 | 9 | 14 | 246 |

**A resposta à pergunta 3 é que a premissa estava errada: o atraso mediano não
é 15, é 6 — e o `swing_lookback` explica 5 dos 6.** O terceiro componente,
back-dating, explica o outro (p50 = 1). A máquina de estados **não contribui
nada** na mediana, no p75 nem no p90; ela só aparece numa cauda fina (max 240
candles), que é o caso em que o branch de sweep espera uma perna inteira até o
próximo pivô contrário ser processado.

Os p90 = 318 e max = 733 que o S0 reportou eram artefato de medição, não
comportamento do detector. **O que não muda:** a retroatividade continua sendo
100%, e a magnitude (6 candles = 1h30 no M15, 6h no H1, 1 dia no H4) continua
sendo a mesma do painel analítico de 13.550 eventos, que nunca dependeu do
replay.

Por TF, sem censura: M15 p50=6, H1 p50=5, H4 p50=6 — sem separação.
Por direção: bearish 6, bullish 7 — sem separação.

---

## S1.10 — Baseline causal definitivo

| braço | h=5 | h=10 | h=20 | h=40 |
|---|---|---|---|---|
| medindo do `timestamp` | 63,4% | 65,2% | 64,6% | 66,8% |
| **medindo do `known_at`** | **53,0%** | **50,2%** | 54,3% | 53,9% |

n = 243–265 (replay exato, passo 1, sem censura).

Bate com o braço `--offset 5` do S0 sobre o painel completo (53,2% em h=5,
n=13.537) — o que é esperado, já que a mediana do atraso é 6. E o braço com
controle casado e n grande do S0 continua sendo a evidência forte:
`--offset 10` dá **50,3% contra um controle de 50,9%**, com n = 13.520.

**O baseline causal do Sweep é o controle.** Os 53–54% em h=20/40 aqui são
n≈250 sem braço de controle nesta execução — não são um achado, e não devem
ser tratados como um.

---

## S1.15 / Resultado

O resultado desta etapa é **A + D**, com um pedaço de B:

- **A** — o timestamp é retroativo (100% dos casos) **e** consumidores de
  decisão o usam cedo demais: 42,0% dos BOS recebem confluência de um sweep
  que ainda não existia; 6,6% dos sweeps caem do lado errado de uma perna para
  o HUNT.
- **D** — `known_at` **não** pode ser representado por `provisional` (S1.8):
  no candle do sweep a classificação ainda não existe, só a geometria. Separar
  `event_time` de `known_at` é a única representação correta.
- **B** (parcial) — parte do uso é puramente visual (`MainChart`,
  `MultiTimeframePanel`, `narrative`), e três consumidores já excluem sweep de
  propósito.
- **Não é E**: o stream não é instável (97,6% estável após nascer), então o
  impacto é de *datação*, não de *conteúdo*.

---

## Entrega — 20 respostas

1. **O que o timestamp representa hoje?** O primeiro candle, desde o pivô
   anterior, cujo **pavio cruzou o nível** (`find_wick_break_index`). É o
   momento físico da travessia — não o pivô, não a confirmação.
2. **Qual é o `known_at` real?** O candle em que o pivô que dispara a emissão
   confirma: `timestamp + back-dating + swing_lookback`. Mediana **6 candles**
   depois do timestamp.
3. **Por que o atraso mediano é 15 se o lookback é 5?** **Não é 15.** Os 15 do
   S0 eram censura à esquerda no replay. O atraso real é **6 = 1 (back-dating)
   + 5 (lookback)**, e a máquina de estados contribui **zero** até o p90.
4. **Quantos eventos são retroativos?** **100%** — 13.550/13.550 no painel
   analítico, 265/265 no replay exato, 110/110 símbolos.
5. **Quantos níveis já eram conhecidos antes do sweep?** **95,8%**. 3,9% só
   depois (mediana de 4 candles), 0,3% não localizados.
6. **Quantos aparecem e somem?** 8 de 502 = **1,59%** (o S0 mediu 2,91% numa
   amostra e passo diferentes).
7. **Quantos mudam preço/direção?** Timestamp reescrito **1,01%**,
   `reference_price_level` reescrito **1,01%**, direção/tipo **0**.
8. **Existe repaint real?** Sim, mas é a exceção: **97,6% nascem tarde e nunca
   mudam**. E nenhum vem marcado — sweeps nunca carregam `provisional`.
9. **`provisional` é semanticamente possível?** **Não** para este evento. A
   geometria é causal no candle, a **classificação** não é (o extremo ainda
   pode não virar pivô; SWEEP vs CHoCH vs BOS ainda não está decidido). A
   afirmação fraca que *é* causal já existe duas vezes (`mitigation.py`,
   `_raid_signals`).
10. **`event_time` e `known_at` devem ser separados?** **Sim** — é o MODEL A, e
    o S1.3 é o argumento: um evento estável com dois tempos distintos é
    exatamente o caso em que dois campos são a representação correta.
11. **Edge causal definitivo?** **Nenhum.** 53,0% em h=5 e 50,2% em h=10
    medindo do `known_at`; 50,3% contra controle de 50,9% no braço de n grande
    do S0. O Sweep é o controle.
12. **Resultado por TF?** Sem separação: atraso p50 de 6/5/6 candles
    (M15/H1/H4); a única diferença material é em **tempo de relógio** —
    1h30 no M15 contra **1 dia no H4**.
13. **Resultado por direção?** Sem separação (bearish 6, bullish 7 candles).
14. **HUNT recebe Sweep cedo demais?** Sim. **6,6% dos sweeps são datados antes
    de um BOS/CHoCH que só existiu depois deles** — caem do lado errado da
    perna para `_swept_since` (`ts >= flip_timestamp`) e para o fatiamento de
    episódios. Piso analítico; o real é maior.
15. **`structure_confluence` recebe Sweep cedo demais?** Sim, e é o caso mais
    limpo: **42,0% dos BOS** que recebem peso 9,0 recebem de um sweep que não
    existia quando a quebra imprimiu — e a janela do BOS é estritamente para
    trás, então não há desenho forward para desculpar.
16. **Impacto material nos consumidores?** Cinco consumidores de decisão usam o
    timestamp (HUNT ×2, confluence, ciclo de manipulação, `FLUSH` de OI, âncora
    de VWAP). Dois têm folga menor que o atraso: a âncora de VWAP exige só 3
    candles (atraso 6), e a janela de expansão do ciclo é de 30–50 candles com
    ~6 já consumidos pela latência.
17. **S7 afeta quantos casos?** BOS **4,5%**, CHoCH **38,7%** de crédito
    exclusivamente do lado semanticamente errado. (Minha primeira medição
    inverteu a definição de lado; alinhado é o sweep de direção **oposta** à
    quebra, como o próprio comentário do engine descreve.)
18. **S2 pode ser corrigido só por label?** **Sim.**
    `reference_price_level` existe em 100% dos sweeps, o candle está em
    `DashboardData.candles`, e `SweepContext` já trafega domínio → API →
    frontend. Cálculo de composição, zero toque no detector, zero encanamento.
19. **Existe bug causal concreto?** **SIM.**
20. **Correção mínima recomendada?** Abaixo.

---

## 19 = SIM → correção mínima proposta (não implementada)

Duas coisas, nesta ordem, e **nenhuma delas toca no detector**:

**(1) Consertar o consumidor, não o evento.** O bug concreto e isolado é
`structure_confluence.py:221`: a janela do BOS credita um sweep que ainda não
existia em 42,0% dos casos. A correção mínima é **exigir que o sweep já fosse
conhecido no candle do BOS** — e `known_at` é derivável na própria camada de
composição, do mesmo jeito que este estudo o derivou (o pivô é localizável por
casamento exato de extremo em 100% dos casos, e `swing_lookback` é um
parâmetro que `app/` já conhece). Nada de novo no domínio, nada de novo na API.

**(2) Só depois, se (1) confirmar que vale:** adicionar `known_at` ao
`SweepContext` — o modelo A, no objeto que já existe para anotar sweeps depois
do fato, e que já trafega fim-a-fim. Aditivo, opcional, sem quebrar nada.

**O que NÃO fazer:** mover o `timestamp` (MODEL B — troca um erro por outro e
quebra 5 consumidores), e marcar `provisional` (MODEL C — semanticamente
impossível, S1.8).

**Nada disso deve ser implementado agora.** A próxima etapa é medir o impacto
de (1): recomputar a confluência com e sem o gate de disponibilidade e ver
quantos scores mudam, e de quanto. Se o score mudar pouco, o bug é real mas
irrelevante, e o registro basta.

---

## Arquivos

```
research/sweep_causality.py        # --times (padrao) | --consumers | --replay
research/test_sweep_causality.py   # 20 testes; os de disponibilidade pulam sem cache
research/SWEEP_S1_CAUSALITY.md     # este relatorio
research/sweep_s1_baseline.json    # gitignored (research/.gitignore: *_baseline.json)
```

```bash
poetry run python research/sweep_causality.py --limit 112 --consumers
poetry run python research/sweep_causality.py --replay --limit 12 --step 1
poetry run pytest research/test_sweep_causality.py -q
```
