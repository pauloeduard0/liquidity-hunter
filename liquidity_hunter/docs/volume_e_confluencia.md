# Volume (VSA) e Confluência de estrutura (selos)

Este documento explica dois mecanismos descritivos da plataforma, na ordem em
que se encaixam:

1. **VSA (Volume-Spread-Analysis)** — lê a *anatomia de volume* de cada candle
   e emite sinais como *Climax*, *Thrust* e *No Supply/Demand*.
2. **Confluência de estrutura (selos `✦N`)** — para cada BOS/CHoCH, conta
   quantas camadas de evidência independentes concordam com a quebra (VSA é uma
   delas), e estampa um selo no rótulo do evento.

Ambos são **observacionais**: descrevem o mercado, não recomendam operação. O
VSA é uma camada de `psychology/`; a confluência é um sintetizador de nível de
composição em `app/` (roda por último sobre o `DashboardData` já montado, como
o `NarrativeEngine` e o `LiquidityHuntEngine`).

---

## Parte 1 — VSA (camada de volume)

Arquivos:
- `core/domain/volume_spread.py` — entidade `VolumeSpreadSignal`.
- `core/domain/enums.py` — enum `VSAPattern`.
- `psychology/analyzers/volume_spread.py` — `VolumeSpreadAnalyzer`.

### Volume bruto vs. volume delta

A distinção central do VSA ("esforço vs. resultado"):

- **Volume bruto = esforço.** Quanta atividade houve no candle. É o que define
  se um movimento é *climático* (volume extremo) ou *quieto* (No Supply/Demand).
- **`volume_delta` = quem ganhou.** `2 · taker_buy_volume − volume`, a agressão
  líquida de compra/venda. Usado como **confirmação direcional** do sinal.

Ou seja: o volume bruto diz *quanto* aconteceu; o delta diz *pra que lado*.

### O que o VSA cobre (e o que já era coberto)

O `BehaviorDivergenceAnalyzer` (janela agregada) já cobre distribuição,
acumulação, exaustão e absorção. O VSA preenche os **três buracos de anatomia
de candle único** que faltavam:

| Padrão (`VSAPattern`) | Direção | Anatomia |
|---|---|---|
| `NO_SUPPLY` | bullish | barra de baixa estreita, volume baixo — vendedores sumiram |
| `NO_DEMAND` | bearish | barra de alta estreita, volume baixo — compradores sumiram |
| `SELLING_CLIMAX` | bullish | barra larga, volume extremo, pavio inferior — capitulação no fundo |
| `BUYING_CLIMAX` | bearish | barra larga, volume extremo, pavio superior — clímax no topo |
| `DOWN_THRUST` | bullish | rejeição de pavio inferior, fecha no alto, volume acima da média (o "pin bar" de compra) |
| `UP_THRUST` | bearish | rejeição de pavio superior, fecha embaixo, volume acima da média (up-thrust clássico) |

> Cuidado com o nome: `DOWN_THRUST` é o pin **bullish** do vídeo (rejeição no
> fundo); `UP_THRUST` é o clássico **bearish** (rejeição no topo).

### Como cada candle é classificado (`_classify`)

Cada candle é medido contra uma linha-base de janela *trailing*
(`_TIMEFRAME_LOOKBACK`, espelha o `BehaviorDivergenceAnalyzer`: M1=20, M5=15,
M15=10, M30=7, H1=7, H4=5, D1=5, W1=3). Calcula-se:

- `spread_ratio` = spread do candle ÷ spread médio da janela,
- `volume_ratio` = volume ÷ volume médio da janela,
- `close_position` = posição do fechamento dentro do range (0 = na mínima, 1 = na máxima),
- pavios superior/inferior.

Prioridade de classificação: **climax > thrust > quiet**. Limiares (parâmetros
do construtor, todos calibráveis):

| Parâmetro | Default | Papel |
|---|---|---|
| `narrow_spread_ratio` | 0.7 | teto de spread p/ No Supply/Demand |
| `low_volume_ratio` | 0.7 | teto de volume p/ No Supply/Demand |
| `wide_spread_ratio` | 1.8 | piso de spread p/ climax |
| `climax_volume_ratio` | 2.0 | piso de volume p/ climax |
| `thrust_volume_ratio` | 1.2 | piso de volume p/ thrust |
| `wick_dominance` | 1.5 | quão maior o pavio de rejeição deve ser vs. o oposto |

### Filtro de extremo local (o "gate")

As famílias quietas/rejeição (`NO_SUPPLY`/`NO_DEMAND` e `DOWN_THRUST`/`UP_THRUST`)
disparam em quase toda barra quieta/rejeição da fita (medido ~89% dos sinais
crus, ~214 por 1000 candles). Sem filtro, poluem o gráfico. **Todos os seis
padrões** (`_GATED_PATTERNS`, os climaxes incluídos desde 2026-07-21) são
**restritos a um extremo local fresco**:

- padrão **bullish** só vale se o candle faz (ou empata) a **mínima** da janela
  `gate_extreme_lookback` (default 20) — um teste de suporte genuíno;
- padrão **bearish** só vale no topo da janela.

`gate_extreme_tolerance` (default 0.0005) é a folga do empate. O climax passa
pelo gate como **rede de segurança quase-grátis** (barra larga de volume extremo
quase sempre já faz o próprio extremo; só barra o climax raro que aparece no
meio da perna). Medido: corta as famílias ruidosas ~86% (densidade ~54 por 1000
candles).

> **Nota (gate de pivô confirmado — testado e descartado 2026-07-21):** um gate
> *centrado* (`gate_pivot_lookforward`, exigir que o candle siga sendo o extremo
> por N barras **depois** dele = pivô de swing) foi medido (−13% a −30% conforme
> N) mas na revisão visual derrubava fundos/topos importantes junto com o ruído,
> sem ganho que compensasse. Ficou só o gate *trailing* acima.

### Deduplicação

`_deduplicate` agrupa por índice de candle e mantém o sinal de **maior
confiança por padrão** dentro de uma janela (`dedup_window`, resolvido por
timeframe). Evita repetir o mesmo padrão em candles consecutivos.

### Como aparece no gráfico

- **Markers** no pane principal (`MainChart.buildVsaMarkers`, cores em
  `theme.VSA_STYLES`): climax = magenta, thrust = âmbar, no-supply/demand =
  cinza; seta pra cima/baixo conforme a direção. Rótulos curtos: `S.Climax ▲`,
  `B.Climax ▼`, `D.Thrust ▲`, `U.Thrust ▼`.
- **Coloração das barras do pane de volume delta** — a barra do candle que
  disparou um VSA recebe o tint do padrão (`vsaColorByTs`).
- Botão **`≈ VSA`** na toolbar (`App.tsx`, `vsaVisible`, ligado por default).

O sinal trafega em `DashboardData.volume_spread_signals` → API
(`schemas.py`) → tipo TS `VolumeSpreadSignal`.

---

## Parte 2 — Confluência de estrutura (selos `✦N`)

Arquivos:
- `core/domain/enums.py` — enum `ConfluenceFactor`.
- `core/domain/structure_confluence.py` — entidade `StructureConfluence`.
- `app/structure_confluence.py` — `StructureConfluenceEngine`.

### A ideia

Para **cada BOS/CHoCH não-provisional** que o gráfico desenha, o engine conta
as observações independentes que **concordam com a direção da quebra** perto
dela. Uma quebra com quatro camadas confirmando lê como estrutura forte; uma
sozinha lê como fraca. É **por-evento** e **por-timeframe** (cada snapshot é um
símbolo + um TF; só cruza TF via os dois fatores HTF).

Sinais provisórios (`BOS?`/`CHoCH?`), rótulos de pivô (HH/HL/LH/LL) e
`LIQUIDITY_SWEEP`/`CHOCH_FAILED` **não** são qualificados.

### Os 7 fatores e seus pesos

`_FACTOR_WEIGHTS` (somam 100). Os dois fatores de timeframe superior pesam mais
— contexto HTF é a leitura de confiança mais forte no SMC.

| Fator (`ConfluenceFactor`) | Peso | Dispara quando… |
|---|---|---|
| `HTF_ALIGNMENT` | 20 | a direção da quebra == tendência do TF superior (≠ neutro) |
| `HTF_ORDER_BLOCK` | 20 | a quebra reagiu num OB do TF superior |
| `VSA_VOLUME` | 15 | um sinal VSA alinhado à direção está na janela do evento |
| `ORDER_BLOCK` | 15 | a quebra lançou de / reagiu num OB do TF atual |
| `OI_PARTICIPATION` | 12 | dinheiro novo entrou na quebra (OI = `NEW_MONEY`) |
| `VOLUME_DELTA` | 9 | agressão líquida do candle da quebra alinhada |
| `LIQUIDITY_SWEEP` | 9 | um stop-hunt precedeu a quebra |

`score = min(100, Σ pesos dos fatores presentes)`.

### Como VSA entra no selo (contexto, não candle exato)

Ponto que costuma confundir: o **sinal VSA** é um candle único, mas o fator
`VSA_VOLUME` é creditado por uma **janela de vizinhança**, não pelo candle
exato da quebra. Basta **um** sinal VSA alinhado à direção na janela para o
fator disparar — é **binário** (presente/ausente), adiciona o peso fixo (15)
uma vez, não escala com a quantidade nem com a confiança dos sinais.

### Janelas de evidência: BOS vs. CHoCH

Aqui está a diferença de desenho mais importante. Um **BOS** tem a ação *no*
candle da quebra, então usa uma janela apertada. Um **CHoCH** (reversão) não: o
candle que confirma fica lá em cima no nível quebrado, mas o **combustível**
(sweep, climax, thrust) se formou dezenas de candles antes, no **fundo da
perna**, e o nível é **retestado/defendido** candles depois.

**BOS — janela apertada e fixa:**
- VSA: `[ev−5, ev+2]`
- Sweep: `[ev−10, ev)`
- OB confirmado até o candle da quebra (`created_at ≤ evento`).

**CHoCH — janela ancorada no nível, que cresce:**
- **Início** = origem da reversão (`_reversal_origin`): o extremo da perna (menor
  low p/ CHoCH de alta, maior high p/ baixa) entre o pivô-referência e a
  confirmação. É onde mora o combustível.
- **Fim** = próximo evento oposto/falho (`_choch_forward_bound`): o primeiro
  BOS/CHoCH de direção contrária, ou um `CHOCH_FAILED` da mesma direção (a
  reversão morreu), com teto de `_CHOCH_FORWARD_CAP` = 60 candles, grampeado ao
  último candle.
- VSA e sweep contam em qualquer ponto dessa janela (só direção + tempo escopado
  à perna; sweep sem checar direção, pois uma reversão de alta varre as
  **mínimas** = sweep rotulado bearish).
- OB do CHoCH: range contém o nível **ou** a origem da reversão, e o MSB
  confirmou **até o fim da janela** — então o OB de demanda confirmado *depois*
  que defendeu o nível também conta.

**O selo cresce com o tempo.** Como o dashboard faz polling, o fim-para-frente
da janela do CHoCH avança a cada refresh enquanto o nível é defendido. Ou seja,
um CHoCH **ganha selos conforme a situação se desenvolve** (repinta pra cima),
até vir o evento oposto ou bater o teto, quando congela. Isso é deliberado e
coerente com o caráter descritivo: o valor de um CHoCH é justamente "este nível
virou o pivô da reversão e está sendo defendido".

### Order block: ativo vs. breaker retest

`_order_block_match` classifica o OB que respalda a quebra:

- **`active`** — um OB ativo cujo range contém o nível → peso cheio.
- **`retest`** — um OB **recém-invalidado** no nível (um *breaker* que o preço
  voltou pra quebrar de novo) → peso reduzido (`_BREAKER_RETEST_WEIGHT_FACTOR`
  = 0.5), desde que a invalidação seja ≤ `_OB_INVALIDATION_LOOKBACK` = 50
  candles antes (senão é nível velho coincidente).
- Ativo vence retest. O selo conta o fator cheio de qualquer forma (o retest é
  uma camada de confluência distinta); só o *score* reflete a força menor.

### Como aparece no gráfico

- Selo **`✦N`** anexado ao rótulo do BOS/CHoCH (`MainChart.confluenceByEvent`),
  onde **N = número de fatores**, mostrado só quando **N ≥ 2** (1 fator é fraco
  demais pra valer marca). Keyed por `timestamp|event_type`, a mesma chave do
  sufixo de participação de OI.
- Trafega em `DashboardData.structure_confluence` → API → tipo TS
  `StructureConfluence` (com `factors[]`, `score`, `description`).

> A **ausência** de selo também é informação: significa < 2 camadas
> confirmando — uma quebra fraca ou contra-tendência. Ex.: um CHoCH contra a
> HTF já perde o `HTF_ALIGNMENT` por definição.

### Calibração pendente

Constantes são pontos de partida, a afinar na revisão visual do gráfico:
limiar do selo (≥2), pesos dos fatores, janelas do BOS (±5/10), teto do CHoCH
(`_CHOCH_FORWARD_CAP` = 60), janela do breaker retest (50 candles / fator 0.5),
proximidade do OB, e os limiares do próprio VSA + `gate_extreme_lookback`.
