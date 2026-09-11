# SWEEP S1.1 — impacto do gate de disponibilidade nos consumidores

**Pergunta:** o `timestamp` retroativo do `LIQUIDITY_SWEEP` (S1) custa alguma
coisa de concreto aos consumidores que o leem?

**Resposta curta: o bug e real, universal e *pequeno*.** Exigir
`known_at <= T` em todos os consumidores causais muda **entre 0,0% e 3,8%** dos
eventos de cada um. Nenhum consumidor chega a MEDIUM. O achado material desta
etapa nao e a causalidade — e o **S7**, que sobrevive intacto ao gate: 42,0%
dos CHoCH recebem confluencia de um sweep do lado errado da quebra, e isso nao
tem nada a ver com quando o sweep passou a existir.

**Nada foi implementado.** Nenhuma linha de `liquidity_hunter/` foi tocada.

---

## Metodo

O gate: um sweep so participa de uma decisao avaliada em `T` se
`known_at(sweep) <= T`.

`known_at` e reconstruido **fora de producao**, pelo piso analitico do S1 (o
pivo cujo extremo e o `price_level` do evento, mais o `swing_lookback` que o
confirma). O replay exato do S1 mediu o residual da maquina de estados sobre
esse piso em **0 candles no p50 e no p90** — o piso *e* o valor real na
esmagadora maioria dos casos. Onde nao for, este estudo **subestima** o
impacto; nunca o exagera.

Os consumidores sao os **reais**, reexecutados nos dois bracos a partir de um
`DashboardData` montado pelo `load_dashboard_data` de producao (offline, sobre
o cache de klines, sem rede). O braco causal difere do legacy em uma coisa so:
sweeps ainda nao conheciveis nao estao la.

**Painel:** 112 simbolos x {15m, 1h, 4h} x 1200 candles = 336 paineis,
**0 falhas**. 8.381 eventos de estrutura qualificados, 6.912 sweeps,
2.619 episodios de hunt, 523 ciclos de manipulacao.

Dois cuidados que mudam a leitura:

- **A janela forward do CHoCH e DESENHO, nao bug.** `_CHOCH_FORWARD_CAP=60`
  existe porque o combustivel de uma reversao se forma *antes* do candle que a
  confirma e o nivel e defendido *depois*. Entao o CHoCH tem duas leituras
  causais, reportadas separadamente: `known_at <= candle da quebra` (estrita) e
  `known_at <= fim da janela` (respeita o desenho). **A segunda e a que
  decide.**
- **Limiar de score e convencao deste relatorio.** O projeto **nao tem** um
  limiar de confluencia: o unico consumo visivel e a contagem de fatores
  (`✦N` no `MainChart`). Os cortes 30/40/50/60 estao aqui para dar tamanho ao
  efeito, nao porque alguma regra os use.

---

## 1. `structure_confluence` — o consumidor prioritario

Fator `LIQUIDITY_SWEEP`, peso 9,0 de 100. O teto do `min(100, ...)` **nunca
morde** (a soma de todos os pesos e exatamente 100), entao perder o fator custa
sempre exatamente **−9 pontos e −1 no `✦N`**.

### BOS — janela estritamente para tras (`ev-10 .. ev-1`)

| | n | % |
|---|---|---|
| eventos qualificados | 5.715 | |
| recebem o fator no legacy | 462 | 8,1% |
| **perdem exigindo `known_at <= quebra`** | **194** | **3,4% do total — 42,0% dos que recebem** |
| score medio legacy → causal | 16,25 → 15,94 | **−0,31** |

Os 42,0% do S1 se confirmam exatamente. Mas o denominador importa: **so 8,1%
dos BOS recebem o fator**, entao o anacronismo atinge 3,4% dos BOS.

Nos cortes de score, o efeito se concentra onde ha poucos eventos:

| corte | legacy | causal | deixam de cruzar |
|---|---|---|---|
| ≥30 | 235 | 195 | 40 (17,0%) |
| ≥40 | 90 | 67 | 23 (25,6%) |
| ≥50 | 24 | 2 | **22 (91,7%)** |
| ≥60 | 1 | 1 | 0 |

O 91,7% e chamativo e vale entender antes de citar: o BOS de score alto e raro
(24 em 5.715 = 0,4%), e 9 pontos e o que separa 50 de 41. **E um efeito grande
sobre uma populacao minuscula.** Nao e evidencia de que a confluencia esteja
quebrada; e evidencia de que `≥50` nao e um corte estavel para BOS.

### CHoCH — janela da origem da reversao ate o proximo evento oposto

| | n | % |
|---|---|---|
| eventos qualificados | 2.666 | |
| recebem o fator no legacy | 1.717 | 64,4% |
| janela olha para frente (desenho) | 2.629 | 98,6% |
| perdem exigindo `known_at <= quebra` | 726 | 27,2% |
| **perdem exigindo `known_at <= janela`** | **101** | **3,8% — anacronismo PURO** |
| score medio legacy → causal | 33,99 → 33,64 | **−0,34** |

A diferenca entre 27,2% e 3,8% e a medida exata de **quanto do "anacronismo" do
CHoCH era o desenho funcionando**. Dos 726 creditos que nao existiam no candle
da quebra, 625 chegam dentro da janela que o engine deliberadamente mantem
aberta. Sobram 101 (3,8%) que nunca existem dentro da propria janela — esses
sim sao credito de nada.

| corte | legacy | causal | deixam de cruzar |
|---|---|---|---|
| ≥30 | 1.529 | 1.511 | 18 (1,2%) |
| ≥40 | 979 | 958 | 21 (2,1%) |
| ≥50 | 532 | 523 | 9 (1,7%) |
| ≥60 | 192 | 186 | 6 (3,1%) |

### Por timeframe e direcao

| corte | n | c/ fator | perde | % |
|---|---|---|---|---|
| TOTAL | 8.381 | 2.179 | 295 | 3,5% |
| 15m | 2.667 | 679 | 77 | 2,9% |
| 1h | 2.794 | 760 | 120 | 4,3% |
| 4h | 2.920 | 740 | 98 | 3,4% |
| bullish | 4.041 | 1.019 | 137 | 3,4% |
| bearish | 4.340 | 1.160 | 158 | 3,6% |
| bos/1h | 1.923 | 179 | 89 | **4,6%** |
| choch/4h | 944 | 600 | 44 | **4,7%** |

**Nao ha gradiente.** Isso e o esperado e vale dizer: o atraso em *candles* e
quase constante (o `swing_lookback` domina), entao a fracao de eventos afetados
nao depende do TF — so o atraso em **tempo de relogio** depende (1h30 no M15,
1 dia no H4, S1.5). Direcao nao separa nada (3,4% vs 3,6%).

---

## 2. S7 — direcao, ja no stream causal

Definicao corrigida no S1: **alinhado = sweep de direcao OPOSTA a quebra** (e o
que alimenta a ruptura; um sweep da mesma direcao tomou a liquidez do lado para
onde o preco depois foi — e alvo, nao combustivel).

| evento | braco | creditados | alinhado | lado errado |
|---|---|---|---|---|
| BOS | legacy | 462 | 95,5% | 4,5% |
| BOS | causal@quebra | 268 | 95,1% | 4,9% |
| CHoCH | legacy | 1.717 | 62,3% | 37,7% |
| CHoCH | causal@quebra | 991 | 1,8% | 98,2% |
| **CHoCH** | **causal@janela** | **1.616** | **58,0%** | **42,0%** |

Duas leituras:

- **Para o BOS, S7 nao e material** (4,9% de lado errado) e o gate nao mexe
  nisso. O engine nao testa direcao, mas na pratica a janela curta para tras ja
  entrega o sweep certo.
- **Para o CHoCH, S7 continua material e o gate nao muda quase nada**: 37,7% →
  42,0%. **O problema direction-agnostic e independente do problema causal.**
  Corrigir um nao corrige o outro.

O 98,2% da linha `causal@quebra` merece registro porque *explica o desenho*: o
sweep alinhado — o que varreu o extremo da perna — praticamente **nunca** e
conhecivel no candle em que o CHoCH confirma. E exatamente por isso que a
janela forward existe. Medir o CHoCH com gate estrito nao mede causalidade;
mede a distancia entre a origem da perna e a confirmacao.

---

## 3. HUNT

Gate aplicado em `_collect_capture_signals` (o sweep so conta se ja era
conhecido no fim da janela de captura) e em `_swept_since` (idem, contra o
candle vivo). Nenhuma outra regra do engine muda.

| stream | legacy | causal | removidos | adicionados | alterados | total mexido |
|---|---|---|---|---|---|---|
| `state` (ao vivo) | 336 | 336 | 0 | 0 | 0 | **0,0%** |
| `history` | 2.619 | 2.600 | 18 | 0 | 26 | **1,7%** |
| `continuation` | 2.172 | 2.170 | 2 | 0 | 20 | **1,0%** |

Campos que mudam quando mudam: `capture_sources`, `capture_score`,
`description` e, em 1 caso, `end_timestamp`. **Nenhuma mudanca de fase ou de
classificacao hunt/continuation.** Nenhum cluster novo ou perdido.

O `state` — que e a leitura **ao vivo**, a que uma pessoa olharia para decidir
— e **imune**: 0 de 336. Faz sentido: `_swept_since` pergunta se houve sweep
*durante a perna inteira*, e uma perna dura muito mais que os ~6 candles do
atraso, entao o sweep quase sempre ja e conhecido antes de a perna acabar.

> Nota de escopo: o S1 registrou que 6,6% dos sweeps sao datados antes de um
> BOS/CHoCH que so existiu depois deles. Sob o **modelo A** isso deixa de ser
> erro: `event_time` e o candle fisico, e "durante esta perna" *deve* ser
> julgado por ele. A leitura alternativa (fatiar a perna por `known_at`) e uma
> mudanca de semantica do HUNT, nao um conserto de causalidade, e fica fora
> desta etapa.

---

## 4. `manipulation_cycle`

| | legacy | causal |
|---|---|---|
| ciclos | 523 | 523 |
| removidos / adicionados / alterados | 0 / 0 / 0 | **0,0%** |

**Conteudo: zero impacto.** Mas a pergunta do protocolo tem outra resposta:

> *Sweep retroativo inicia ciclo antes de poder ser conhecido?* **Sim, quase
> sempre.** 412 dos 523 ciclos (78,8%) sao disparados por um sweep, e herdam a
> data dele: o ciclo e carimbado **p50=6, p90=14, max=75 candles** antes de
> poder existir.

Ou seja: o detector de ciclos nao *decide* diferente, mas todo `sweep_timestamp`
que ele publica e retroativo. E um problema de **datacao**, nao de valor — o
mesmo diagnostico do S1, herdado inteiro.

---

## 5. `oi_regime` — FLUSH

`FLUSH` e exclusivo de sweep (`oi_regime.py:227`: so um `LIQUIDITY_SWEEP` com
queda de OI vira FLUSH). Sem futuros no cache offline nao ha `oi_analysis` para
diffar, mas **o que decide aqui nao precisa dele**: o rotulo FLUSH e uma
*requalificacao do proprio evento de sweep, no candle do sweep*. Logo:

- a **disponibilidade** de um FLUSH e, por construcao, **identica** a do sweep
  que o origina — e essa e 100% retroativa. Sobre 6.912 sweeps elegiveis, o
  atraso herdado e **p50=6, p90=13, max=75 candles**;
- o **conteudo** (o `oi_delta` lido no candle do sweep) e **inalterado** sob o
  modelo A, que mantem `event_time` onde esta.

**0% de mudanca de valor, 100% de datacao retroativa.** O consumidor a jusante
(`_last_flush_ts >= flip_timestamp`, no HUNT) herda a mesma questao de lado da
perna tratada no item 3.

---

## 6. VWAP ancorada no ultimo sweep

| | n | % |
|---|---|---|
| paineis com ancora de sweep | 334 | |
| **a ancora escolhida MUDA sob o gate** | **0** | **0,0%** |
| candles desenhados antes de a ancora poder ser conhecida | p50=**6**, p90=12, max=31 | |

**A escolha da ancora e imune** — e por um motivo que ja estava no codigo:
`_build_anchored_vwaps` ja exige `_VWAP_MIN_ANCHOR_CANDLES = 3` candles atras
do sweep, e o ultimo sweep elegivel costuma estar bem mais longe que o atraso.
Como a ancora nunca muda, **VWAP e sigma sao identicos nos dois bracos**.

O que existe e puramente visual: a fita e desenhada cobrindo, em mediana, **6
candles anteriores ao momento em que a propria ancora poderia ser conhecida**.
Ninguem opera aqueles 6 candles retroativamente — a fita so e lida no ponto
vivo. E cosmetico, e sob o modelo A e **correto**: a ancora *deve* ficar no
candle fisico do sweep.

---

## 7. narrative / UI — classificacao

| consumidor | natureza | pode usar `event_time`? | precisa de `known_at`? |
|---|---|---|---|
| `MainChart` (marcador de sweep) | VISUAL | **sim** — o marcador tem de tocar o pavio | nao |
| `MultiTimeframePanel` | VISUAL | sim | nao |
| `narrative` (linha do tempo) | VISUAL | sim | nao |
| VWAP ancorada | VISUAL (geometria historica) | **sim** — a ancora e o candle fisico | nao |
| `structure_confluence` | DECISAO | nao | **sim** |
| `LiquidityHuntEngine` | DECISAO | nao | **sim** |
| `manipulation_cycle` | DECISAO | nao | **sim** (datacao) |
| `oi_regime` FLUSH | DECISAO | nao | **sim** (datacao) |
| `overview.py`, `tideRibbon.ts`, `structureRelevance.ts` | — | **ja excluem sweep de proposito** | — |

Nenhuma apresentacao foi alterada.

---

## 8. Modelo A como hipotese

```
event_time = candle fisico em que o pavio atravessou o nivel  (o timestamp de hoje)
known_at   = primeiro candle em que a pipeline afirma que o evento existe
```

Consumidores historicos/visuais usam `event_time`. Consumidores que simulam
decisao em `T` exigem `known_at <= T`.

**Resolve semanticamente? Sim, e e o unico dos tres modelos que resolve.** O
S1.3 e o argumento: o evento e **estavel depois de nascer** (97,6%), e um
evento estavel com dois tempos distintos e exatamente o caso em que separar os
dois e a representacao correta. O modelo B (mover o `timestamp`) troca um erro
por outro — o marcador deixaria de tocar o pavio — e o modelo C (`provisional`)
e semanticamente impossivel (S1.8: no candle do sweep a *geometria* existe, mas
a **classificacao** ainda nao).

Este estudo acrescenta a isso uma constatacao que nao estava no S1: **as duas
leituras coincidem em 96,2% a 100% dos casos, dependendo do consumidor.** O
modelo esta certo *e* quase nunca faz diferenca.

---

## 9. Magnitude

| consumidor | eventos | usos legacy | anacronicos | usos causais | eventos alterados | % | severidade |
|---|---|---|---|---|---|---|---|
| `confluence/BOS` | 5.715 | 462 | 194 | 268 | 194 | 3,4% | LOW |
| `confluence/CHoCH` | 2.666 | 1.717 | 101 | 1.616 | 101 | 3,8% | LOW |
| `hunt/state` | 336 | — | — | — | 0 | 0,0% | LOW |
| `hunt/history` | 2.619 | — | — | — | 44 | 1,7% | LOW |
| `hunt/continuation` | 2.172 | — | — | — | 22 | 1,0% | LOW |
| `manipulation_cycle` | 523 | — | — | — | 0 | 0,0% | LOW |
| `oi_regime/FLUSH` | 6.912 | 6.912 | 6.912 | 0 | 0 | 0,0%¹ | LOW¹ |
| `vwap_anchor` | 334 | — | — | — | 0 | 0,0% | LOW |

**Criterio:** HIGH = ≥20% dos eventos do consumidor mudam de **valor** sob o
gate; MEDIUM = ≥5%; LOW abaixo disso.

¹ O OI e o caso limite do criterio: **0% do conteudo muda, 100% da datacao e
retroativa.** A severidade dele e de datacao, nao de valor, e o criterio acima
so mede valor. Registrado como tal, nao promovido.

**Nenhum consumidor chega a MEDIUM.**

---

## 14. BTC / ETH / SOL

| simbolo | eventos | c/ fator | perdem | % |
|---|---|---|---|---|
| BTCUSDT | 83 | 31 | 4 | 4,8% |
| ETHUSDT | 77 | 24 | 5 | 6,5% |
| SOLUSDT | 83 | 28 | 5 | 6,0% |

Ligeiramente acima da media do painel (3,5%), mas com n pequeno por simbolo
(~80 eventos) as tres estao dentro do ruido. **Nao ha caso "claro" de major
para mostrar** — nenhum dos tres tem um evento cuja leitura mude de forma
qualitativa. O que muda e sempre o mesmo: `✦N → ✦N-1` e −9 no score.

---

## Respostas

1. **Usos anacronicos por consumidor:** confluence/BOS 194 (42,0% dos creditos);
   confluence/CHoCH 101 puros (3,8%) — 726 se ignorarmos o desenho forward;
   hunt/history 44; hunt/continuation 22; hunt/state 0; manipulation_cycle 0 de
   valor mas 412 de datacao; oi_regime 6.912 de datacao, 0 de valor; vwap 0.
2. **Confluencia muda quanto:** score medio −0,31 (BOS) e −0,34 (CHoCH). 295 de
   8.381 eventos (3,5%) mudam, sempre em −9 e −1 fator.
3. **BOS que mudam:** 194 de 5.715 (3,4%).
4. **CHoCH que mudam:** 101 de 2.666 (3,8%) pelo criterio que respeita a janela.
5. **S7 continua material?** **Sim, e praticamente inalterado pelo gate.** CHoCH
   vai de 37,7% para 42,0% de lado errado. BOS nao e material (4,9%). **E um
   problema independente do causal.**
6. **HUNT muda quanto:** 1,7% do historico, 1,0% da continuacao, **0,0% do
   estado ao vivo**. Nenhuma classificacao ou cluster alterado.
7. **Manipulation cycle:** 0,0% de conteudo; 78,8% dos ciclos carregam data
   retroativa (p50 6 candles).
8. **OI regime:** 0,0% de conteudo; 100% de datacao retroativa, herdada inteira
   do sweep.
9. **VWAP anchor:** 0,0% — a ancora nunca muda; VWAP e sigma identicos.
10. **Mais afetado:** `confluence/CHoCH` (3,8%), com `confluence/BOS` logo
    atras (3,4%) — e o BOS e o mais afetado *relativamente aos creditos que
    recebe* (42,0%).
11. **Praticamente imune:** `hunt/state` (0/336), `manipulation_cycle` (0/523) e
    `vwap_anchor` (0/334). Os tres pelo mesmo motivo: operam sobre janelas
    muito mais longas que o atraso, ou ja tem folga propria no codigo.
12. **Por TF:** sem gradiente — 2,9% (15m), 4,3% (1h), 3,4% (4h). O atraso e
    quase constante em candles; so o tempo de relogio escala.
13. **Por direcao:** sem diferenca — 3,4% bullish, 3,6% bearish.
14. **Casos claros em BTC/ETH/SOL:** nao. 4,8% / 6,5% / 6,0%, dentro do ruido
    de n≈80, e nenhuma mudanca qualitativa de leitura.
15. **`event_time` + `known_at` resolve?** **Sim**, e e o unico dos tres modelos
    que resolve — apoiado no S1.3 (evento estavel) e no S1.8 (`provisional`
    impossivel).
16. **Risco de regressao grande?** Sob o modelo A, **nao**: e aditivo, nada
    existente muda de valor. Sob o modelo B, **sim** — 5 consumidores de
    decisao e 3 fixtures de estrutura.
17. **O bug causal e material em producao?** **Nao.** E real, e universal, e
    esta corretamente diagnosticado — e custa menos de 4% dos eventos em todo
    consumidor medido, sem mudar uma unica classificacao.
18. **Patch minimo que faria sentido:** exigir `known_at <= ev_idx` no
    `sweep_idxs` de `structure_confluence.py:222`, com `known_at` derivado na
    propria camada de composicao. Uma condicao, um arquivo, sem dominio e sem
    API. **Nao implementado.**
19. **Deve ser corrigido agora?** **NAO.** O patch e pequeno, mas o beneficio
    medido e menor ainda: −0,31 ponto num score sem limiar, sobre 3,4% dos BOS.
    Nao paga nem o risco de mexer, nem o custo de manter a derivacao de
    `known_at` em duas camadas. **O registro basta.**
20. **Proxima etapa:** **S7**, nao S1. O unico numero desta medicao que e
    material — 42,0% dos CHoCH creditados por um sweep do lado errado da
    quebra — nao e causal, sobrevive intacto ao gate, e o engine nao testa
    direcao em lugar nenhum. Medir o que muda ao exigir direcao oposta e a
    pergunta que vale a pena.

---

## Arquivos

```
research/sweep_availability_impact.py        # o estudo
research/test_sweep_availability_impact.py   # 22 testes, incl. o invariante S1.1-10
research/SWEEP_S1_1_AVAILABILITY.md          # este relatorio
research/sweep_s1_1_baseline.json            # gitignored (research/.gitignore: *_baseline.json)
```

```bash
poetry run python research/sweep_availability_impact.py --limit 112
poetry run pytest research/test_sweep_availability_impact.py -q
```
