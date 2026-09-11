# SWEEP — S3 TRIPLE SEQUENCE AUDIT

**Hipótese (do usuário, visual):** três sweeps em sequência precedem uma
expansão forte na direção da tendência vigente.

**Resultado: NÃO se confirma.** O terceiro sweep não tem descontinuidade
nenhuma — e onde ele difere dos anteriores, difere *contra* a hipótese: fica
abaixo do controle casado em quase todo recorte, e a probabilidade de um BOS
pró-tendência **cai** monotonicamente com a contagem enquanto a de um CHoCH
contra-tendência **sobe**. O resultado é o **E + F** da grade S3.32 (a leitura
visual é hindsight, e a contagem carrega um viés de exaustão, não de expansão).

Reprodução:

```bash
poetry run python research/sweep_sequence_audit.py --limit 112 --cases
```

Painel: 112 símbolos × {15m, 1h, 4h} × 1200 candles = **336 painéis, 389.701
candles, 6.912 sweeps confirmados, 0 falhas**. 97,1% deles têm tendência causal
definida (os 2,9% restantes são o início de série, antes do primeiro
BOS/CHoCH conhecível — ficam fora do outcome e são reportados à parte).

---

## S3.0 — o que "três em sequência" significa nos dados

As quatro definições foram **pré-registradas no topo de
`research/sweep_sequence_audit.py`** (constante `DEFS`), com a PRIMARY
declarada antes de qualquer medição, exatamente como pedido. Nenhuma foi
escolhida depois de ver outcome.

| definição | quebra a run quando… | #1 | #2 | #3 | #4+ | ≥3 |
|---|---|---|---|---|---|---|
| `event` EVENT_CONSECUTIVE | nunca | 334 | 331 | 330 | 5917 | 90,4% |
| `leg` SAME_STRUCTURAL_LEG | há BOS ou CHoCH conhecido entre dois sweeps | 3334 | 1621 | 850 | 1107 | 28,3% |
| `side` SAME_PHYSICAL_SIDE | o lado **físico** varrido muda | 1659 | 1262 | 962 | 3029 | 57,7% |
| **`trend` TREND_CONTEXT (PRIMARY)** | a tendência causal muda | 1750 | 1286 | **975** | 2901 | 56,1% |

Concordância do rótulo "≥3" com a PRIMARY: `side` 90,8%, `leg` 72,2%,
`event` 65,7%.

Duas observações que mudam a leitura da pergunta:

1. **`event` é degenerada.** Sem quebra, 90,4% dos sweeps são "o terceiro de
   alguma coisa". Se o padrão visual for essa definição, ele não é um padrão —
   é o estado normal do gráfico.
2. **`#4+` é grande (2.901) sob a PRIMARY.** Uma tendência dura muito mais que
   três sweeps. Ou seja: *o normal dentro de uma tendência é haver mais de três
   sweeps*, e parar de contar no terceiro é uma escolha do observador, não uma
   fronteira do mercado. O S3.13 abaixo confirma que o mercado não trata 3
   diferente de 4+.

O **lado físico** (S3.7) é derivado da geometria (`price_level` vs
`reference_price_level`), nunca de `sweep.direction` — a lição do S7, de que
`direction` é a tendência vigente invertida e não um lado independente.

## S3.1/S3.2 — causalidade

Nada é medido do timestamp back-datado. Cada evento carrega um `known_at`:

- `LIQUIDITY_SWEEP` — o piso analítico do S1 (o pivô cujo extremo é o
  `price_level`, mais o `swing_lookback` que o confirma);
- `BOS`/`CHoCH` — o candle da quebra mais `persistence_candles` (2 em produção,
  a confirmação de `_common.is_sustained_break`).

A **ordem da sequência é a de `known_at`**, não a dos timestamps. O relógio de
outcome de cada estágio começa no `known_at` **daquele estágio** — no #1
ninguém sabe que haverá três (S3.4), então o #1 não recebe crédito nenhum por
informação futura. A tendência é reconstruída candle a candle a partir do
último BOS/CHoCH já conhecível; `final_trend` (o estado no *fim* da série)
nunca é usado.

## S3.26 — frequência

975 terceiros sweeps: **2,50 por 1000 candles**, ~2,9 por painel de 1200.
Por TF: 15m 2,33 · 1h 2,69 · 4h 2,48. Por direção: bearish 3,83 · bullish 3,48.

Markers históricos que existiriam por chart (mediana entre painéis):
últimos 100 candles p50=0 (p90=1) · 250 p50=0 (p90=1) · 500 p50=1 (p90=2) ·
1000 p50=2 (p90=4, max=5).

**A frequência não é o problema** — um rastro permanente caberia no gráfico sem
poluir. O problema é que não há o que marcar.

## S3.3/S3.12/S3.13 — o ladder 1 / 2 / 3 / 4+

Outcome na direção da tendência causal, medido do `known_at` do próprio
estágio. CONTROLE A = candle comum da mesma fita, **mesma tendência causal**,
deslocado 37 candles (a lição do `raid_reversal`: o controle casa a direção).

`MFE>MAE`, e entre parênteses o delta contra o controle:

| h | #1 | #2 | #3 | #4+ |
|---|---|---|---|---|
| 5 | 48,0% (−3,0) | 48,5% (−3,6) | 48,5% (−3,9) | 48,3% (−2,1) |
| 10 | 49,5% (−2,7) | 50,3% (−2,0) | **47,7% (−5,2)** | 48,8% (−2,2) |
| 20 | 50,4% (−2,0) | 51,3% (−3,4) | **49,5% (−4,6)** | 50,2% (+0,6) |
| 40 | 51,4% (−1,3) | 53,5% (−0,3) | **48,6% (−3,5)** | 49,4% (+0,7) |

**Não existe salto no terceiro.** Em 3 dos 4 horizontes o #3 é o *pior* estágio
do ladder, e em todos os quatro ele perde do controle casado. O contraste
direto que justificaria uma marca diferente (S3.12, #3 vs #2) é
**−1,8pp em h=20 e −4,8pp em h=40** — negativo, não incremental.

Por recorte (h=20, `MFE>MAE`): 15m 3−2 = +1,0pp · 1h −3,6pp · 4h −2,5pp ·
bullish −1,6pp · bearish −2,0pp. Um único recorte positivo, e pequeno.

**S3.13 (3 vs 4+):** +0,6pp em h=20, entre −0,2 e +1,1pp nos quatro horizontes.
São o mesmo número. **O número 3 não é especial.**

**S3.11 (CONTROLE D — terceiro evento qualquer na perna seguinte de mesma
tendência, casado por idade da perna):** #3 49,5% vs 52,0% do controle,
**−2,5pp**. #2 −0,5pp, #4+ +1,1pp. O triple também não bate esse controle.

## S3.10 — "explode"

Fração que alcança N ATR a favor **antes** de 1 ATR adverso ou da mudança
estrutural confirmada (o que vier primeiro). Descritivo, sem virar setup:

| estágio | n | ≥1 ATR | ≥2 ATR | ≥3 ATR | p50 t→MFE(h20) |
|---|---|---|---|---|---|
| #1 | 1652 | 47,5% | 29,2% | 18,5% | 9 |
| #2 | 1237 | 49,6% | 32,2% | 21,3% | 10 |
| #3 | 949 | **49,7%** | **31,1%** | **20,0%** | 9 |
| #4+ | 2874 | 49,0% | 31,6% | 21,2% | 9 |

Os quatro estágios são indistinguíveis. **Não há evidência de explosão** —
nem em frequência, nem em magnitude, nem em tempo até o extremo.

## S3.5/S3.6 — distância e compactação

Gap entre sweeps consecutivos (n=2261): p25=14 p50=29 p75=55 p90=97 candles.
Span #1→#3 (n=975): p25=45 **p50=70** p75=110 p90=168 candles.

O primeiro achado é sobre a própria observação visual: **"três sweeps em
sequência" tem uma mediana de 70 candles de span**. Isso não é um cluster
apertado — é meio gráfico. O que parece "seguidinho" na tela é uma sequência
esparsa.

Outcome por bucket **natural** da distribuição (h=20, delta vs controle):
span<45 −7,4pp · 45–70 −4,7pp · 70–110 −8,1pp · >110 +1,7pp.

Compactar **piora**, ao contrário da hipótese secundária do S3.6: o único
bucket que empata com o controle é o mais *espalhado*, e por 1,7pp em n=241.

## S3.7/S3.8 — lado físico e configuração

Padrões: HHH 46,3% · LLL 43,2% · mistos 10,5% (o maior misto, HLL, tem 2,8%).
**89,5% dos triples já são same-side por construção** — atacar o mesmo lado não
é uma condição a descobrir, é o caso comum.

| grupo | n | MFE>MAE | delta |
|---|---|---|---|
| same-side (HHH/LLL) | 837 | 50,2% | **−4,4pp** |
| mixed | 102 | 44,1% | **−5,9pp** |

Os dois perdem. Por configuração contra a tendência (h=20):
bullish/high +0,6pp (n=**31**) · bullish/low −7,7pp (n=417) ·
bearish/high −2,1pp (n=452) · bearish/low −4,0pp (n=39).

A única célula positiva tem n=31 e não replica no espelho bearish
(bearish/low, a configuração simétrica, é −4,0pp). Não é um achado.

## S3.14 — mesma perna estrutural

| | n | MFE>MAE | delta |
|---|---|---|---|
| A) mesma leg | 384 | 47,1% | **−8,6pp** |
| B) atravessa BOS | 361 | 50,4% | −3,8pp |
| C) atravessa CHoCH | 194 | 52,6% | +2,0pp |

**A hipótese do usuário previa que o fenômeno viveria em A** — e A é o pior
grupo dos três, com a maior perda de todo o estudo. O único grupo acima do
controle é C, o que *atravessou* um CHoCH, isto é, exatamente o que a hipótese
excluía. E +2,0pp em n=194 não é um achado, é ruído.

Idade da perna no terceiro sweep: p25=33 p50=54 p90=108 candles.

## S3.16/S3.17/S3.18 — geometria, níveis, progressão

Geometrias (R=reclaim, C=close-through): RRR 18,7% · CCC 7,2% · mistos 74,1%.
Outcome h=20: RRR −5,3pp (n=176) · CCC +1,7pp (n=69) · mistos −4,9pp (n=694).
O S2 já havia medido que as duas geometrias não diferem individualmente; **em
sequência elas continuam não diferindo** (o CCC positivo tem n=69 e 1,7pp).

Níveis distintos consumidos pelo trio: **3 níveis em 91,9%**, 2 em 8,0%,
1 em 0,1% (um único caso no painel inteiro). Ou seja, o padrão visual não é
"bateram três vezes no mesmo nível" — são **três pools diferentes**. Outcome
por nível: 2 níveis −9,1pp (n=75), 3 níveis −4,3pp (n=863).

S3.18 — deslocamento do nível #1→#3, em ATR com sinal pró-tendência:
p25=−0,70 p50=**+0,90** p75=+3,62; 62,1% avançam a favor. O mecanismo
*existe*: a liquidez é consumida progressivamente na direção da tendência. Mas
ele não se converte em expansão posterior — é a tendência andando, não um
sinal sobre o que vem depois.

## S3.20/S3.21 — o que a estrutura faz depois (o achado real)

| estágio | n | BOS pró ≤20 | CHoCH contra ≤20 | p50 candles →BOS |
|---|---|---|---|---|
| #1 | 1652 | **20,8%** | **13,1%** | 63 |
| #2 | 1237 | 17,9% | 13,6% | 68 |
| #3 | 949 | 16,5% | 16,4% | 76 |
| #4+ | 2874 | **12,9%** | **17,1%** | 98 |

Este é o resultado mais limpo do estudo, e é **monotônico nas duas colunas e na
direção oposta à hipótese**: quanto mais sweeps acumulados na tendência, *menos*
provável o BOS pró-tendência, *mais* provável o CHoCH contra, e mais longe fica
a próxima continuação (63 → 98 candles).

Contar sweeps dentro de uma tendência é contar **idade da perna**. Uma perna
que já precisou varrer três pools é uma perna velha, e pernas velhas continuam
menos. A leitura visual "três sweeps e explode" inverte o sinal: se há algum
conteúdo na contagem, ele é de **exaustão**, não de expansão — e nem esse
conteúdo é grande o suficiente para virar regra (16,5% vs 20,8% de BOS, com o
CHoCH contra subindo só 3,3pp).

## S3.24/S3.25 — estabilidade e robustez

Por bloco temporal (`MFE>MAE` do #3 em h=20): 45,3% · 53,9% · 49,0% · 49,0%.
Discovery (blocos 0–2, n=735) −5,0pp vs controle; holdout (bloco 3, n=204)
−2,5pp. **Negativo nas duas metades** — a definição não mudou depois de olhar o
holdout, e não precisou: não há efeito a preservar.

Por símbolo: 106 símbolos com ≥5 triples. O triple melhora o controle em
**41/106 (38,7%)** e piora em 65. Mediana do delta **−3,6pp** (p25 −25,0pp,
p75 +17,1pp). Top: TRXUSDT +46,7% (n=6), BNBUSDT +43,4% (n=11).
Bottom: JELLYJELLYUSDT −73,3% (n=12), MYXUSDT −69,0% (n=7).

A dispersão é de amostra pequena em ambas as pontas, centrada abaixo de zero.
Nada aqui é concentração em BTC/ETH puxando resultado — não há resultado.

## S3.15/S3.19 — diagnósticos não cobertos

Declarados no protocolo como *somente diagnóstico, sem gate*, e **não medidos**
aqui:

- **S3.15 (leg ACTIVE/STALE)** — o projeto não expõe um estado ACTIVE/STALE
  causal por candle no stream de estrutura. A idade da perna (S3.14, p50=54
  candles no terceiro sweep) é o proxy disponível, e o S3.20 já mostra o efeito
  dela. Não inventei um estado que produção não tem.
- **S3.19 (OI / volume / VSA)** — o painel roda com `NoFuturesProvider` (sem
  rede, sem orçamento de requisição da Binance), então não há OI causal
  disponível para 336 painéis. Fica registrado como não medido em vez de
  medido com dado ausente.

Nenhum dos dois mudaria a conclusão: o efeito principal não existe, e um
diagnóstico não ressuscita um efeito ausente.

## S3.31 — os dez critérios

| # | critério | resultado |
|---|---|---|
| 1 | #3 melhora materialmente sobre #2 | **NÃO** (−1,8pp h=20, −4,8pp h=40) |
| 2 | melhora sobre o sweep individual | **NÃO** (−0,9pp h=20 vs #1) |
| 3 | bate controle casado | **NÃO** (−4,6pp ctrl A; −2,5pp ctrl D) |
| 4 | existe em ≥2 TFs | **NÃO** (positivo só no 15m, +1,0pp) |
| 5 | existe nas duas direções | **NÃO** (bullish −1,6pp, bearish −2,0pp) |
| 6 | replica em blocos temporais | **NÃO** (negativo em discovery e holdout) |
| 7 | não depende de poucos símbolos | vácuo — 38,7% melhoram, mediana −3,6pp |
| 8 | permanece usando `known_at` | sim (foi medido só assim) — e é negativo |
| 9 | frequência compatível com UI | **SIM** (2,5/1000 candles, p50=2 por chart) |
| 10 | não é proxy de BOS já acontecendo | vácuo — é proxy de **idade da perna** |

**1 de 10.** O único critério satisfeito é o da frequência, que só importaria
se houvesse algo para marcar.

## Conclusão

**Não criar marcador `×3`, badge, evento `TRIPLE SWEEP` nem rastro permanente.**
Nada muda em produção: detector, domínio, API, narrativa, HUNT, confluência e
frontend ficam exatamente como estão.

O que o estudo estabeleceu, para não ser remedido:

1. "Três sweeps em sequência" precisa de definição, e as definições não são
   intercambiáveis (concordância de 65,7% a 90,8%). A mais frouxa rotula 90,4%
   dos sweeps.
2. Sob a definição PRIMARY, o terceiro sweep não é diferente do primeiro, do
   segundo nem do quarto — nem em outcome, nem em alcance em ATR, nem em tempo
   até o extremo.
3. O triple perde de dois controles casados independentes.
4. O span mediano de um trio é de 70 candles: o que parece adjacência visual
   não é adjacência temporal.
5. A contagem de sweeps dentro de uma tendência é um proxy de **idade da
   perna**, e aponta para exaustão (menos BOS pró, mais CHoCH contra), não
   para expansão.

**Hipótese rejeitada — resultado E + F da grade S3.32.** A observação visual é
hindsight: nota-se os trios que foram seguidos de expansão e não se nota os que
não foram, e 49,5% deles são seguidos de expansão contra 54,1% de um candle
qualquer da mesma tendência.

O achado 5 é o único material que sobra, e **não é um setup** — é uma leitura
sobre a perna, do mesmo tipo que `docs/confirmed_structure_limits.md` já
registra. Se algum dia virar pesquisa, é sobre *idade/desgaste de perna*, não
sobre sweep.

## Arquivos

| arquivo | conteúdo |
|---|---|
| `research/sweep_sequence_audit.py` | o estudo; as 4 definições pré-registradas no topo |
| `research/test_sweep_sequence_audit.py` | 27 testes (lado físico, `known_at`, tendência causal, as quebras de run, as métricas de "explode", invariantes causais ponta a ponta) |
| `research/SWEEP_S3_TRIPLE_SEQUENCE.md` | este documento |
| `research/sweep_s3_baseline.json` | baseline numérico (gitignored por `research/.gitignore`) |
