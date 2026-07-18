# Como o BOS e o CHoCH funcionam (guia em português)

*Atualizado em 2026-07-16.*

Este documento explica, de forma direta, como o **`InternalStructureDetector`**
(`liquidity_hunter/liquidity/detectors/internal_structure.py`) — o detector que
**o gráfico React usa em todos os timeframes** — identifica e marca:

- **BOS** (Break of Structure / Rompimento de Estrutura)
- **CHoCH** (Change of Character / Mudança de Caráter)
- **CHOCH_FAILED** (CHoCH que falhou) — incluindo o **marcador de fizzle**
- **LIQUIDITY_SWEEP** (varredura de liquidez)
- **HH/HL/LH/LL** (rótulos descritivos de pivôs)
- **Marcas provisórias de live edge** (`BOS?`, `CHoCH?`, `CHoCH?*`)

A ideia é você ler isto e conseguir bater o olho no gráfico e saber **por que**
cada marca apareceu — e decidir se algo precisa de ajuste.

> Observação: existe também o `SwingStructureDetector` (estrutura "maior"). Ele
> compartilha a mesma arquitetura base, mas **não é desenhado no gráfico** hoje
> (alimenta o `market_structure_events` da API). Tudo que você vê no chart vem
> do **internal**. Este guia foca no internal.

> Filosofia que atravessa tudo: **marca faltando se conserta de forma aditiva**
> (staging mesclado no final), nunca afrouxando confirmação dentro da máquina de
> estados — afrouxar cascateia no `trend` e corrompe a sequência de CHoCH
> (lição medida mais de uma vez). Toda flag comportamental é `off` por padrão,
> byte a byte inerte quando desligada, e tem fixture de regressão com dados
> reais do caso que a motivou (`tests/liquidity/detectors/data/`).

---

## 1. Os blocos básicos

### 1.1 Pivôs (swing points)

Tudo começa com **pivôs**: topos e fundos locais.

- Um **swing high** (pivô de topo) é uma vela cujo topo é maior que o das
  `lookback` velas de cada lado.
- Um **swing low** (pivô de fundo) é o espelho disso.

O parâmetro que controla isso é o **`swing_lookback`**. Quanto menor, mais
sensível (mais pivôs, estrutura mais "fina"); quanto maior, mais grosso. Em
produção hoje é **5 em todos os timeframes**, com `persistence_candles = 2`
(ver seção 12). Os pivôs são coletados e percorridos em **ordem cronológica** —
o detector é uma máquina de estados que processa um pivô de cada vez.

Um efeito colateral importante do lookback: um pivô só "existe" `lookback`
velas depois do seu extremo. Esse **atraso do live edge** é o que as marcas
provisórias (seção 7) compensam.

### 1.2 Referências "trailing" (active_high / active_low)

A diferença central do detector interno: ele guarda

- **`active_high`** = o **último** pivô de topo formado
- **`active_low`** = o **último** pivô de fundo formado

São referências **móveis (trailing)**: cada novo pivô de topo atualiza o
`active_high`, cada novo fundo atualiza o `active_low`. (Isso é diferente do
detector "maior", que segura a referência até o lado oposto romper.)

> Por que trailing? Porque para a estrutura interna, segurar uma referência
> velha por muito tempo "congela" um lado e deixa de marcar movimentos grandes.
> A referência trailing acompanha a ação de preço recente.

### 1.3 A tendência (`trend`)

O detector mantém uma `trend` que é `NEUTRAL`, `BULLISH` ou `BEARISH`. Ela
começa em `NEUTRAL` (bootstrap) e muda só num **CHoCH** ou **CHOCH_FAILED**. O
`trend` decide se um rompimento é BOS (a favor) ou candidato a CHoCH/sweep
(contra).

### 1.4 Referência *estrutural* vs. *fraca* — o conceito que organiza tudo

Desde julho, quase todas as regras novas giram em torno desta distinção:

- **Referência estrutural**: um nível de onde uma perna *de verdade* partiu —
  o fundinho/topo da pernada de um BOS confirmado por fechamento, um pullback
  promovido por continuação, a origem de uma pending BOS viva, ou a origem
  blind-spot de um CHoCH. É o CHoCH "conservador".
- **Referência fraca**: um nível *sintético* — escrito por um re-anchor
  (staleness/chain/displacement), uma promoção cujo rompimento do degrau foi
  só por pavio, ou o fallback trailing do cold-start.

A distinção é exposta no evento (`MarketStructure.reference_structural`) e
governa: a persistência exigida (seção 3), como o CHoCH pode falhar (seção 4),
e como a marca é desenhada — um CHoCH de referência fraca aparece **pontilhado
e apagado com sufixo `*`** (`CHoCH* ▼`); o estrutural é a marca sólida normal.

---

## 2. BOS — Break of Structure (continuação)

Um **BOS** diz: "a tendência **continuou** e fez um novo extremo estrutural."

Num movimento de baixa, cada BOS é um **fundo mais baixo** confirmado. Num
movimento de alta, um **topo mais alto**. Os BOS de uma mesma perna formam uma
**escada** (staircase) descendo (baixa) ou subindo (alta).

Para um pivô virar BOS, ele precisa passar por **3 filtros em sequência**.

### Filtro 1 — Rompimento por *fechamento* (não por pavio)

O estado só avança quando **uma vela da perna FECHA além da referência**
(`find_close_break_index`). Um pavio que fura mas fecha de volta **não conta**:
a referência fica *congelada* e espera uma vela fechar de verdade.

```
   referência (active_low) ─────┐
                                │   só pavio fura → NÃO é BOS (congela)
         ┌──┐      ┌──┐         │   ┌──┐
         │  │      │  │    ╷    │   │  │   ← fecha abaixo → avança o estado
         └──┘      └──┘    ╵wick     └──┘
                          (rejeitado)   (confirma)
```

### Filtro 2 — A "escada" (staircase)

Um BOS de continuação tem que **estender a perna além do BOS anterior**:

- baixa: o novo fundo precisa ser **menor** que `last_bear_bos_low`
- alta: o novo topo precisa ser **maior** que `last_bull_bos_high`

Romper um fundo *mais alto* (formado num repique) **não** é BOS — é só a
referência trailing acompanhando. Isso garante que os BOS de baixa façam
fundos cada vez mais baixos (e os de alta, topos cada vez mais altos).

A escada é **semeada no CHoCH com o próprio nível do CHoCH**. Ou seja: o
**primeiro BOS da nova perna tem que romper além do nível que o CHoCH rompeu**.
Isso impede um BOS de aparecer "do lado errado" do CHoCH. (Só o primeiríssimo
BOS saindo do bootstrap `NEUTRAL` é livre.)

Há **dois** rastreadores de escada, e a diferença importa:

- o **gate** (`last_bear_bos_low`/`last_bull_bos_high`) — decide se um avanço
  é estrutural; ratcheta inclusive em extremos varridos só por pavio;
- o **piso reportado** (`prev_bear_bos_extreme`/`prev_bull_bos_extreme`) — o
  nível que o próximo BOS *desenha e referencia*. Com
  `bos_floor_require_close_break=True` (produção), ele **só ratcheta em
  extremos confirmados por fechamento**: um avanço que apenas *varreu por
  pavio* o piso não vira o novo degrau reportado (caso AAVE H1: o BOS de
  breakout reportava o pavio 77.94 em vez do topo fechado 77.70).

### Filtro 3 — Confirmação por *pullback*

Mesmo com o estado avançado, o BOS só é **emitido** (vira marca no gráfico)
quando um **pullback** aparece: um pivô na direção oposta.

- BOS de baixa → confirma quando aparece um **topo (LH)** abaixo do topo anterior
- BOS de alta → confirma quando aparece um **fundo (HL)** acima do fundo anterior

Ou seja, o preço cai, faz o fundo (avança o estado), **repica** formando um topo
menor — e *aí* o BOS é confirmado e desenhado. Esse pullback é importante porque
ele também vira a **semente da referência do CHoCH** (ver seção 3).

#### Filtro de pavio no pullback (`bos_pullback_max_wick_pct = 0.4`)

O pullback que confirma pode ser um **pavio de uma vela só** — a vela espeta o
extremo intrabar mas o corpo fecha longe. Isso é um "pullback" que nunca
repicou de verdade.

O filtro exige que o **pavio do lado do pivô** seja no máximo **40% do range da
vela** (corpo + lado oposto ≥ 60%). Se for só pavio, **o BOS não confirma ali** —
o pending BOS fica vivo esperando um pullback de verdade.

```
  pullback BOM (confirma)        pullback PAVIO (rejeitado)
        ╷                              ╷  ← pavio gigante
       ┌┴┐  corpo real               ╷ ╵
       │ │                            ╷       corpo minúsculo, fecha lá embaixo
       └┬┘                           ┌┴┐
        ╵                            └─┘
```

### O que o BOS reporta (níveis e linha no gráfico)

- **`price_level`** = o extremo do pivô que disparou (o novo fundo/topo da perna)
- **`reference_price_level`** = o **nível formado que ele rompeu** (o degrau
  anterior da escada — o *piso reportado*). É por isso que a linha do BOS é
  desenhada no **extremo do swing anterior**, formando a escada limpa — e não
  no extremo novo.
- **`reference_timestamp`** = a vela que *formou* aquele nível rompido (origem da
  linha), preenchido depois pelo passo de composição (seção 10).

---

## 3. CHoCH — Change of Character (reversão)

Um **CHoCH** diz: "a tendência **mudou de caráter** — pode estar revertendo."

É um rompimento **contra** a tendência atual. Num movimento de baixa, um CHoCH de
alta acontece quando o preço rompe (e *sustenta*) acima de uma referência de
reversão. É o evento que **vira a `trend`**.

### A pergunta-chave: qual nível o CHoCH precisa romper?

Esse é o coração do detector. A cadeia de referência de reversão, em ordem de
prioridade:

```
validated_choch_<lado>  OU  pending_bos.pullback_ref  OU  choch_origin_<lado>  OU  active_<lado>
```

#### `validated_choch_<lado>` — a referência principal (fundinho/topo da pernada)

Com `bos_leg_origin_choch_ref` (ligado em produção), a regra principal é
direta: **o fundinho/topo da pernada do último BOS emitido é a referência de
CHoCH**. Quando um BOS de alta confirma, o **fundo de onde a perna do
rompimento saiu** (o `pullback_ref` da pending BOS) vira `validated_choch_low`
**na hora da emissão** — marcado como *estrutural*. Cada novo BOS emitido
**renova** a referência para o fundinho da sua própria pernada (substitui
sempre, mesmo que o novo nível seja mais distante: estrutura manda). Espelhado
no lado de baixa (o topo da pernada vira `validated_choch_high`).

Três refinamentos importantes na promoção:

- **Estrutural só com fechamento no degrau**
  (`bos_leg_origin_require_close_break=True`): a origem promovida só é marcada
  *estrutural* se alguma vela de fato **fechou** além do piso da escada que o
  BOS reportou. Se a continuação só *pavou* o degrau anterior (exatamente o
  BOS que o passo de composição esconde do gráfico), a origem ainda é
  promovida, mas como referência **fraca** — a barreira de persistência (abaixo)
  governa o CHoCH resultante. O mesmo teste vale para a promoção por
  continuação do candidato.
- **Pullback raso sobe para o extremo da correção**
  (`bos_leg_origin_min_pullback_atr = 1.5`, M15/M30/H1): quando o pullback
  imediato é raso (altura < 1.5 × ATR médio da série), a origem promovida é o
  **extremo acumulado da correção** (`pending_high`/`pending_low`) em vez do
  pivô trailing — a linha do CHoCH cai no topo/fundo visível da correção, e um
  furo prematuro no nível raso vira sweep (caso AAVE H1: ref 86.59 → 87.82).
- **Reclaim-kill promove a origem**: uma pending BOS descartada porque o pivô
  oposto seguinte já está **além da origem da pernada** promove essa origem
  (estrutural) antes de morrer — o próprio reclaim é a reversão conservadora, e
  o CHoCH naquele pivô já avalia contra ela.

Por cima disso ainda roda o pipeline clássico de candidato/continuação (que pode
*apertar* a referência para o pullback pós-BOS quando a perna faz novo extremo):

1. **Candidato**: quando um BOS de baixa confirma, o topo (LH) que o confirmou
   vira `candidate_choch_high` — **provisório**, ainda não é a referência.

2. **Promoção com trava de continuação**: o candidato vira
   `validated_choch_high` **se um próximo fundo fizer um novo extremo da perna**
   (abaixo de `bear_leg_low`). Isso prova que a perna realmente continuou. Se o
   rompimento do degrau nesse avanço foi só por pavio, a promoção sai **fraca**.

   - **2b. Re-anchor por sweep**: se, enquanto a perna se desenrola, uma
     varredura (sweep) fura acima do candidato atual, o candidato é re-ancorado
     para o **extremo varrido** (só para mais extremo). Razão: depois que o preço
     pega a liquidez acima e volta a cair, é desse topo varrido que a reversão
     vai partir. Isso só mexe no *candidato*, nunca na referência validada.

3. **Estrutural é protegida enquanto alcançável**: uma referência estrutural
   não pode ser deslizada por re-anchor enquanto estiver a menos de
   **3 × ATR médio** do preço (`bos_leg_origin_release_gap_atr = 3.0` — o gap
   é normalizado por volatilidade; o antigo 4% fixo fica de fallback para
   séries curtas demais). Além do gap, a perna "fugiu" da origem e o staleness
   volta a poder agir. *Por que ATR e não %:* 4% fixos valiam 8.5 ATR no BTC
   30m (guarda quase absoluta → três pares de whipsaw na queda de junho) mas
   0.6 ATR no SOL D1 (guarda nenhuma).

#### `pending_bos.pullback_ref` — a origem de uma pending BOS viva

Enquanto uma pending BOS está viva (avançou por fechamento mas todos os
pullbacks foram rejeitados por pavio), a origem da pernada dela participa da
cadeia — senão o CHoCH caía no trailing e disparava no meio do range enquanto a
origem genuína estava guardada na pending (caso ETH H1 de 25/06).

#### `choch_origin_<lado>` — o fallback "one-shot"

No instante em que um CHoCH dispara, **todo** o estado validado/candidato é
zerado. Reconstruir a referência do lado oposto leva tempo (precisa de um BOS +
continuação). Nessa janela, se a reversão falhasse, a tendência ficaria
"presa". O `choch_origin` é o extremo da perna que o CHoCH acabou de reverter, e
serve de **fallback** até uma referência validada nova ser construída. É
**one-shot** (não arma o lado oposto), para não criar ping-pong.

#### `active_<lado>` — o fallback de bootstrap (com duas supressões)

No comecinho (`trend = NEUTRAL`, nada construído ainda), a referência trailing
serve de fallback para o detector conseguir "virar" a primeira tendência se o
chute inicial estiver errado. Duas supressões evitam que ele atropele os
mecanismos desenhados:

- **Dentro da janela de CHoCH não confirmado** (origem armada): a saída de
  reversão ali é o `CHOCH_FAILED` na origem, e o fallback a minava num nível
  bem mais fraco (caso SOL H1 de 23/06: CHoCH prematuro no LH trailing 69.63
  enquanto a origem estava em 74.97).
- **Por N velas depois de um `CHOCH_FAILED` na mesma direção**
  (`choch_failed_fallback_suppress_candles = 20`): uma falha não arma origem
  (one-shot), então o fallback voltava a valer na hora e re-disparava o mesmo
  whipsaw um dia depois (caso BTC H1 de 25/06). Referências estruturais não são
  afetadas — uma reversão genuína (que promove origem via BOS) dispara normal.

### A confirmação do CHoCH: *persistência* (não pavio)

Um furo de uma vela que volta na hora **não** é CHoCH — é sweep. Para ser CHoCH,
o rompimento precisa **sustentar**: a vela do rompimento **mais** as velas
seguintes têm que **fechar** todas além da referência (`is_sustained_break`),
por `persistence_candles` velas (produção: **2**, todos os TFs — recalibração
de 2026-07-16, base 12 → 2 para identificação de tendência mais rápida,
compensada pela histerese abaixo).

```
   referência de reversão ──────────────────────
                    ╷ volta na hora → SWEEP
   ┌──┐  ┌──┐      ╷╵
   │  │  │  │   ┌──┐                  ┌──┐┌──┐┌──┐ ... ┌──┐  → persistência de
   └──┘  └──┘   └──┘  (sweep)         └──┘└──┘└──┘     └──┘    fechamentos → CHoCH
```

Se rompe mas **não** sustenta → vira `LIQUIDITY_SWEEP` (tendência inalterada).

**Persistência de referência fraca** (`choch_weak_ref_persistence_candles = 4`,
M5–H1): um CHoCH contra uma referência **fraca** usa essa contagem **no lugar**
da base; referências **estruturais** usam a base. Com a base em 2, um furo
breve num nível fraco bastaria para virar o trend e começar um ciclo sujo — a
barreira endurece exatamente isso, o papel original dela (nos TFs graúdos,
H4+, quem protege é a barreira de trend confirmado abaixo). O check de
`CHOCH_FAILED` por origem continua usando a base (a válvula de escape que
desfaz um ciclo errado não pode ser atrasada).

**Barreira de trend confirmado**
(`choch_confirmed_trend_persistence_candles = 4`, todos os TFs, 2026-07-16):
**histerese** nos flips de tendência — flipar um trend *pendente* é barato,
invalidar um trend *confirmado* exige sustentação. Um trend setado por um CHoCH
é **pendente** até um **BOS emitido** na direção dele confirmá-lo (o mesmo
momento em que a origem do CHoCH aposenta; a aposentadoria por
displacement-success, seção 4, conta como confirmação). Enquanto pendente, o
CHoCH reverso usa a persistência base (2) e o `CHOCH_FAILED` segue sendo a
válvula de escape barata. Uma vez **confirmado**, um CHoCH contra o trend
precisa sustentar `max(4, barreira de ref fraca)` fechamentos: um stop-hunt de
uma vela pela referência de reversão imprime como `LIQUIDITY_SWEEP` (o ramo
não-sustentado que já existia — nenhuma semântica nova de evento), ou o CHoCH
simplesmente confirma umas velas depois, quando o rompimento é real. As marcas
`CHoCH?` de live edge não são afetadas — continuam mostrando a *tentativa* se
formando enquanto o evento confirmado espera a barreira.

> Medição (2026-07-16, 5 símbolos × 5m..1d, barreira 4/6/8 vs off, base 2): a
> assinatura em todos os níveis é a pretendida — pares whipsaw CHoCH+✕
> reclassificados como sweep com a continuação preservada, CHoCH genuíno
> re-confirmando poucas velas depois na mesma referência. Com 4: −68/+36
> CHoCH, ✕ 7→4, +58 sweeps, **1** mudança de conclusão de pé (um whipsaw de
> live edge no BTC 15m, corretamente morto). 6 dobra o churn e mexe numa
> segunda conclusão; 8 reescreve histórico graúdo. Subir depois de revisão
> visual se stop hunts ainda fliparem estruturas confirmadas.

### O que o CHoCH reporta

- **`price_level`** = o extremo do pivô que disparou
- **`reference_price_level`** = a referência que foi rompida
- **`reference_timestamp`** = o timestamp do pivô da referência (origem da
  linha) — por isso a linha do CHoCH começa na origem real, não na vela do
  rompimento.
- **`reference_structural`** = se a referência rompida era estrutural ou fraca
  (fraca → renderiza `CHoCH*`, pontilhado/apagado).

---

## 4. CHOCH_FAILED — quando a reversão não vinga

Um CHoCH é **provisório** até um **BOS na nova direção** confirmá-lo. Enquanto
não confirma, ele carrega uma **origem** — o nível cujo rompimento de volta
invalida a reversão.

### A origem é o extremo mais fundo da perna (`choch_origin_leg_extreme`)

A origem é o **extremo mais fundo da perna revertida** —
`_extreme(active_<lado>, pending_<lado>)` — e não o pivô trailing sozinho. O
trailing ratcheta em direção ao novo extremo pelos pivôs intermediários da
perna de reversão; na hora do CHoCH ele podia estar colado no topo, armando uma
falha *instantânea* no primeiro pullback raso (caso NEAR M5: o fundo real era
1.967, o `active_low` tinha subido para 2.004 — a reversão genuína "falhava" na
hora e a linha corria até a borda). Com a regra, `CHOCH_FAILED` caiu **~33%**
na matriz de medição, convertendo pares de whipsaw em sweeps ou CHoCHs que
seguram.

### A falha clássica (origem rompida)

Se o preço **rompe de volta** a origem (sustentado, persistência base) **antes**
de um BOS confirmar, a reversão falhou:

- dispara um **`CHOCH_FAILED`** (direção = a do CHoCH que falhou,
  `reference_price_level` = a origem rompida)
- a `trend` **volta** para a anterior

Como a tendência original nunca terminou de verdade, a escada de BOS dela
**retoma do último BOS genuíno** (guardado em stash quando o CHoCH disparou),
não da origem do CHoCH. Também é **one-shot** — uma falha não arma a origem
oposta, então não há ping-pong.

### CHoCH de referência fraca falha no próprio nível rompido
(`choch_weak_ref_fail_at_broken_level = True`)

Um CHoCH que disparou contra referência **fraca** arma, além da origem
distante, **o próprio nível que rompeu** como referência de invalidação: aquele
rompimento era a única evidência da reversão, então um fechamento sustentado de
volta por ele emite um `CHOCH_FAILED` real (trend volta) no nível *mais
apertado* dos dois. CHoCHs estruturais mantêm só a origem. (Caso BTC D1: o
CHoCH bullish de 30/04 contra o re-anchor fraco 75998 colapsou em dias, mas a
origem 59800 nunca foi rompida — o trend ficou bullish através de um crash de
−30%, com cada fundo novo virando sweep e nenhum BOS de baixa no fundão.)

Detalhe importante: uma falha no nível fraco **re-seeda a escada retomada no
nível da falha** (como um CHoCH seeda seu ciclo) em vez de restaurar o stash
antigo — a referência fraca existia justamente porque o ciclo velho estava
gasto, e o restore puro foi medido apagando escadas inteiras (AAVE 4h, NEAR 1h).
Falhas por origem restauram exatamente como antes.

### CHoCH *pendente* falha no próprio nível rompido — estruturais também
(`choch_pending_fail_at_broken_level = True`, persistência própria **6**, 2026-07-16)

A metade **pendente** da histerese PENDING/CONFIRMED (seção 3), usando a mesma
fronteira `trend_confirmed`. Um CHoCH **sem BOS confirmador** também arma **o
próprio nível que rompeu** como referência de invalidação, *mesmo sendo
estrutural*: sem isso, uma contra-perna impulsiva que nunca imprimiu BOS (queda
sem pivô de pullback) deixava as **duas** saídas — o `CHOCH_FAILED` na origem e
a referência do CHoCH reverso — presas lá no extremo da perna revertida, e uma
recuperação completa imprimia como corrente de sweeps sob um trend velho. Caso
AAVE H1 de 08/07: CHoCH bearish no 87.90 **estrutural** (a flag de ref fraca
não se aplicava), nenhum BOS bearish, e um rally de +14% foi lido como três
sweeps bullish por três dias, até a origem 97.4 quebrar.

A falha no nível estrutural exige persistência **própria** (**6**, maior que a
base 2): um retest ordinário de uma origem genuína segura 2–4 fechamentos e
**não** mata a reversão; o platô entre "retest ordinário" (< 5) e "reclaim
real" (>> 8) é largo — 5 já matava o retest de 07/04 contra o CHoCH bullish
*correto* de 03/07 no próprio AAVE. Diferente da falha de ref fraca, uma falha
no nível estrutural **restaura o stash** da escada pré-CHoCH (o ciclo
interrompido estava vivo — no AAVE, o primeiro BOS do rally retomado referencia
o topo genuíno 97.4). Refs fracas mantêm o comportamento da subseção anterior.
A aposentadoria por displacement-success aposenta esse nível junto com a origem
(uma reversão impulsiva que deu certo não é morta no pullback).

### Sucesso por deslocamento (`choch_success_displacement_atr = 4.5`, cap 20%)

Uma perna de reversão impulsiva pode não formar **nenhum pivô de pullback** —
sem pullback não há BOS emitido, e o CHoCH ficaria provisório para sempre, com
a origem armada esperando matar no primeiro reclaim (caso NEAR H1: rallies de
~5 e ~7.6 ATR sem BOS, ambos marcados `✕` no pullback). Quando o extremo da
perna desloca **≥ 4.5 × TR% médio** além do nível de falha, a origem (e o nível
de falha junto) aposenta como um BOS confirmador faria: a reversão está
estabelecida, e uma reversão *posterior* é um CHoCH oposto novo, não uma falha
desta. O limiar é **capado em 20% do preço**
(`choch_success_displacement_max_pct`): a unidade ATR se adapta sozinha a cada
ativo, mas num diário muito volátil (TR ~10%) 4.5 ATR viraria uma exigência de
30–50% inalcançável — o AERO 1D caiu −31% com BOS de fechamento e ainda levou
um `✕` retroativo na recuperação em V. Com o cap, só os diários voláteis são
governados pelos 20%; todo intraday fica byte-idêntico (limiar ATR 3–15%).

### Staging dos BOS "comidos" pela janela provisória
(`stage_choch_failed_window_bos = True`)

Enquanto um CHoCH está provisório, o trend está virado — então rompimentos de
escada na direção *original* viram sweeps e são "comidos". Se o CHoCH falha,
esses rompimentos eram BOS legítimos da tendência retomada. A flag grava cada
rompimento de escada contra-CHoCH durante a janela e, no `CHOCH_FAILED`,
estagia cada um como BOS aditivo (mesclado/dedupado como o impulse staging, e
re-cronometrado por fechamento na composição — os só-pavio caem). Os extremos
comidos também entram nos pisos restaurados. (Caso BTC H1 do crash de 18–25/06:
a escada 62232 → 61870 → 59060 → 58030 apareceu inteira.) Se o CHoCH confirma,
os registros são descartados.

No gráfico, uma linha de BOS/CHoCH que *parecia* ter sido cortada por um CHoCH
que depois falhou **continua atravessando** até um CHoCH genuíno
(`MainChart.structureLineEndTime`).

---

## 5. O marcador de fizzle (`choch_fizzle_reclaim_candles = 30`)

Existe um caso intermediário: um CHoCH cuja reversão **fizzlou** — o preço
reclama (fechamento sustentado) **o próprio nível que o CHoCH rompeu** logo
depois, e passa a ranger acima dele — mas a origem distante nunca é rompida,
então o `CHOCH_FAILED` clássico não vem e a linha fica pendurada até a borda
(caso SOL M15: CHoCH bearish em 80.72, reclamado em 14 velas, pendurado um dia).

Quando o reclaim começa **dentro de K=30 velas** do CHoCH, o detector emite um
`CHOCH_FAILED` **aditivo** (um *marcador*): ele **não vira o trend** — é
flagado `provisional=True` só para os consumidores de replay
(`LiquidityHuntEngine`/`NarrativeEngine`) o ignorarem — e serve para o frontend
terminar a linha estagnada no reclaim. Um reclaim *depois* da janela é
follow-through genuíno (a reversão segurou) e não é marcado; o K que separa os
dois casos é um platô largo (fizzle real: 14 velas; reversão genuína medida:
133).

Na composição existe o **cancelamento de fizzle retomado**
(`_drop_resumed_fizzle_markers`): um marcador seguido de **prova de retomada**
é descartado — o reclaim era um pullback fundo do qual a reversão se recuperou,
não um fizzle. Duas provas contam:

- **BOS da mesma direção que sobrevive no gráfico** (caso ETH H1: o ✕ falso
  deixava a linha do CHoCH bearish de 19/06 correr até a borda);
- **uma vela que FECHA além do `price_level` do próprio CHoCH marcado** (o
  fundo/topo do pivô disparador; 2026-07-16) — cobre a perna retomada cujo BOS
  ainda não confirmou pullback. Caso SOL M15 de 16/07: um bounce raso sustentou
  6 fechamentos de volta pelo 77.21 e o ✕ colou do lado do CHoCH — mas o preço
  desabou pelo fundo 76.64 do CHoCH duas horas depois; a reversão *funcionou*.
  Fechamento, não pavio: um pavio pelo extremo é sweep, não retomada.

Profundidade do reclaim **não** separa os casos (medido: o fizzle genuíno de
junho reclamou 0.98 ATR, o falso 1.18) — o que separa é o que vem *depois*: o
fizzle genuíno nunca fez extremo novo. O cancelamento vive na composição porque
só ela sabe quais BOS sobrevivem ao re-anchor de fechamento — no caso SOL
genuíno também existe um BOS depois do reclaim, mas é só-pavio e cai do
gráfico, e o preço nunca fechou além do extremo, então o marcador lá fica de pé
(correto). No live edge o marcador ainda aparece honestamente enquanto só o
reclaim é conhecido, e repinta fora uma-duas velas depois do fechamento de
retomada.

No frontend, um fizzle **não** dá a transparência de linha do `CHOCH_FAILED`
real: o trend nunca voltou, então o CHoCH fizzlado ainda corta as linhas
opostas anteriores — só a **própria** linha dele para no reclaim.

---

## 6. LIQUIDITY_SWEEP e os rótulos HL/LH

- **`LIQUIDITY_SWEEP`**: pivô contra a tendência que **rompe** a referência mas
  **não sustenta**. É a "pegada de liquidez". Marca em `price_level` (o extremo
  do pavio).
- **`HIGHER_LOW` / `LOWER_HIGH`**: um pivô que **não** rompe a referência
  trailing. São só rótulos descritivos do pivô (mantêm o próprio timestamp/preço).

---

## 7. Marcas provisórias de live edge (`BOS?`, `CHoCH?`, `CHoCH?*`)

Um pivô só existe `swing_lookback` velas depois do extremo — então, na borda
direita do gráfico, estrutura pode já ter quebrado *por fechamento* sem que a
máquina de estados possa emitir nada. As marcas provisórias preenchem essa
janela, **computadas do estado final autoritativo do detector** (nunca
re-derivadas fora dele), sempre **aditivas** e renderizadas **apagadas e
pontilhadas** com sufixo `?`:

- **`BOS?`** (`emit_provisional_bos = True`): uma continuação cujo piso da
  escada já foi **fechado**-rompido, mas os pivôs de confirmação ainda não se
  formaram. Só emite quando o piso atual é um extremo de BOS genuíno (não a
  seed de um CHoCH fresco — ali a marca só duplicaria a linha do CHoCH).
  Walk-forward: **85% confirmam**, lead mediano ~11 velas.
- **`CHoCH?`** (`emit_provisional_choch = True`): uma reversão cuja referência
  **estrutural** já foi rompida com a **persistência completa** de fechamentos,
  faltando só o pivô. Walk-forward: ~50% confirmam (reversões no live edge são
  inerentemente mais sujeitas a sweep — o estilo apagado comunica isso), lead
  ~8 velas. Um `CHoCH?` supersede um `BOS?` do mesmo rabo (nunca desenham o par
  contraditório).
- **`CHoCH?*`** (`emit_provisional_choch_weak = True`): o mesmo, contra
  referência **fraca**, sustentando a persistência da barreira (4). Existe
  porque depois de um re-anchor a referência de pé é fraca — exatamente nos
  ciclos liberados pelo displacement release, a reversão em formação ficava
  invisível. Walk-forward intraday: 10/10 seguidas do CHoCH confirmado.

Uma marca provisória é **superseded** pela confirmada quando os pivôs se formam,
ou **desaparece** se o movimento falhar antes — um repaint de live edge
intencional, sinalizado honestamente pelo estilo apagado. Provisórias nunca
terminam linha de evento confirmado, ficam fora do replay do hunt/narrative, e
são puladas pelos passes de composição.

---

## 8. Timestamp das marcas (por que a marca cai "na vela certa")

O pivô que *decide* o evento se forma no extremo da **nova** perna. Mas marcar o
evento ali atrasaria visualmente o rompimento. Então, depois de decidir o
evento, o detector faz uma **busca para trás** pela vela que **de fato** rompeu:

- BOS / SWEEP → `find_wick_break_index` (primeira vela cujo pavio cruza o nível)
- CHoCH / CHOCH_FAILED → `find_sustained_break_index` (primeira vela onde a
  persistência se sustenta)

O `timestamp` do evento é o dessa vela; o `price_level` continua sendo o extremo
do pivô.

---

## 9. Os "re-anchors" (por que existem e o que fazem)

Em timeframes maiores (ou impulsos limpos), a tendência pode **travar**: uma
perna de baixa deixa a referência de reversão de alta lá no **topo da perna**, e
o CHoCH de alta só dispara quando o preço sobe tudo de volta. Os re-anchors
**puxam a referência de reversão para um nível local**, **sem virar a `trend`**
(o CHoCH ainda tem que confirmar sozinho). Eles só **apertam** (nunca afrouxam,
nunca caem do lado errado do preço), e o nível que escrevem é sempre uma
referência **fraca** (seção 1.4).

Regras que valem para todos:

- **Referência estrutural alcançável é intocável**: a menos de 3 × ATR do
  preço, o re-anchor recusa (seção 3). Só além do gap ele age.
- **Re-anchor só escreve em `validated_choch_<lado>`**: o nível sintético não
  toca `active_<lado>`/`candidate_choch_<lado>`, que continuam sendo pivôs
  reais. Sem isso, o nível do re-anchor entrava no snapshot da pernada do
  próximo BOS e virava um "fundinho estrutural" falso (medido no M30: 63650 —
  artefato de janela — no lugar do fundo genuíno 65469).
- **Guarda de distância mínima** (`reanchor_min_price_gap_pct = 0.003`): recusa
  ancorar num extremo local **colado no preço** (< 0,3%). Uma referência colada
  é gatilho-fácil: um repique trivial confirma um CHoCH no meio do range que
  falha logo.

### 9.1 Chain re-anchor (`reanchor_mode="chain"`)

Conta os avanços de BOS na perna; ao atingir o limiar, re-ancora para o extremo
local da perna. Com **`reanchor_chain_establish_only = True`** (produção), o
chain **só estabelece** uma referência que ficou cega (`validated_choch_<lado>`
é `None`, típico de impulso limpo que nulou tudo). Ele **não aperta** uma
referência fresca recém-promovida de um pullback bom.

### 9.2 Staleness re-anchor (`stale_reanchor_candles`)

Se a tendência roda X velas além do último BOS/flip sem um novo, puxa a
referência de reversão para o extremo local de uma janela recente. Por
timeframe: M5=120, M15=90, M30=80, H1=80, H4=60, D1=40, W1=26.

### 9.3 Displacement release — ciclos "gastos"
(`stale_reanchor_displacement_atr = 16.0`, `_candles = 15`)

O timer do staleness é cego para o quanto a perna **esticou**: depois de um
movimento violento, a referência fica presa na origem pré-movimento pela janela
inteira (H4 = 60 velas = 10 dias), e o repique mais forte do ciclo é consumido
como sweep contra um nível a muitos ATRs de distância (caso ETH H4: o crash de
05/06 deixou a referência em 2046; o repique de +23% até 1848 imprimiu como
sweep e o gráfico ficou 35+ dias parado no BOS do meio do crash). Quando o gap
entre a referência efetiva e o extremo corrente da perna atinge **16 × ATR
médio**, o ciclo está *gasto*: o limiar de staleness encolhe para 15 velas e a
janela de re-anchor passa a começar **no último avanço** (o range pós-movimento),
então a referência cai no primeiro pullback do range novo. N=16 foi medido num
platô estreito: 8 dispara em pernas rotineiras (o gap ref→extremo *é* a altura
da perna), 20 perde o próprio caso ETH H4.

### 9.4 Displacement por FVG (`reanchor_mode="displacement"`)

Alternativa baseada em FVG (gap de 3 velas). **Não está em produção** — produção
usa `"chain"`.

---

## 10. Staging aditivo de BOS (marcas extras, máquina intocada)

Quatro mecanismos adicionam BOS que a máquina de estados não pôde emitir, sempre
numa **lista separada**, dedupados contra os BOS reais no final, sem tocar
estado/referências/CHoCH (flag desligada = saída byte a byte idêntica):

- **Impulse staging** (`impulse_bos_displacement_pct = 0.015`): num impulso
  limpo (fundos/topos consecutivos **sem** pivô oposto no meio) a máquina
  avança a cada passo mas, sem pullback, emite no máximo um BOS deferido — uma
  queda forte imprimia um trecho vazio. Cada avanço com deslocamento > 1,5%
  além do BOS anterior estagia uma marca no buraco.
- **Wick-rejected staging** (`stage_wick_rejected_bos = True`): quando o único
  pullback de um avanço é um pavio (rejeitado pelo filtro da seção 2) e nenhum
  pullback real chega antes do trend virar, o rompimento genuíno ficava sem
  marca. Estagia o BOS na vela do fechamento, **sem** seedar candidato de CHoCH
  (relaxar o filtro em si foi rejeitado por medição: cascateava e destruía um
  CHoCH correto — a lição aditivo-sobre-máquina-de-estados).
- **Failed-window staging** (`stage_choch_failed_window_bos = True`): os BOS
  comidos pela janela de um CHoCH que depois falhou (seção 4).
- **Reversal-eaten staging** (`stage_reversal_eaten_bos = True`, 2026-07-15): o
  último avanço de uma perna fica *pendente* esperando o pivô de pullback
  confirmador — e quando o pivô seguinte é justamente o reclaim que dispara o
  CHoCH, o pendente é descartado sem emitir: o último fundo/topo antes da
  reversão ficava sem BOS (caso ENA M30 de 20/06). No flip, se o piso do
  pendente teve **close-break** genuíno, a marca é estagiada na vela desse
  fechamento (a chave é o fechamento, não o limiar de deslocamento do impulse
  staging). Puramente aditivo: 0 flips de trend na matriz, 23/36 combos com
  +1..3 marcas.

---

## 11. Os passes de composição (`load_dashboard_data`)

Depois do detector, três passes conservadores rodam sobre a lista de eventos:

1. **`_reanchor_bos_close_break`** — cada BOS é **re-cronometrado** para a
   **primeira vela que FECHA** além do nível formado que rompeu (na janela em
   que o BOS fica ativo); qualquer BOS cuja perna só **pavou** o nível é
   **descartado**; define `reference_timestamp` (origem da linha). Confirmação
   conservadora por fechamento — pode deixar trechos longos sem evento no macro
   (intencional). Roda no internal **e** no major.
2. **`_drop_pre_break_reference_bos`** — descarta um BOS de continuação cuja
   referência se formou **antes** do fechamento confirmador do BOS anterior da
   mesma perna: um pavio que furou o nível ainda-não-rompido ratcheta o extremo
   da escada, e o próximo BOS reportaria esse pavio pré-rompimento como o nível
   que quebrou (caso M15: BOS de 12:45 contra o pavio 61447 de uma tentativa
   falhada). Um CHoCH reseta a restrição para a direção dele.
3. **`_drop_resumed_fizzle_markers`** — o cancelamento de fizzle retomado
   (seção 5). Só no internal.

Marcas provisórias (seção 7) são puladas pelos três.

---

## 12. Parâmetros de produção por timeframe

`_INTERNAL_STRUCTURE_PARAMS` = `(swing_lookback, persistence_candles)` — hoje
**uniforme**: **(5, 2) em todos os timeframes** (recalibração de 2026-07-16:
base 12 → 2 para identificação de tendência mais rápida, compensada pela
barreira de trend confirmado — a coluna "barreira confirmada" abaixo, seção 3).

| TF  | swing_lookback | persistence | barreira confirmada | stale_reanchor | barreira ref fraca | min pullback ATR |
|-----|----------------|-------------|---------------------|----------------|--------------------|------------------|
| M5  | 5              | 2           | 4                   | 120            | 4                  | —                |
| M15 | 5              | 2           | 4                   | 90             | 4                  | 1.5              |
| M30 | 5              | 2           | 4                   | 80             | 4                  | 1.5              |
| H1  | 5              | 2           | 4                   | 80             | 4                  | 1.5              |
| H4  | 5              | 2           | 4                   | 60             | —                  | —                |
| D1  | 5              | 2           | 4                   | 40             | —                  | —                |
| W1  | 5              | 2           | 4                   | 26             | —                  | —                |

Flags ligadas em produção no internal (todas `off` por padrão no construtor):

```
reanchor_mode="chain"                       reanchor_chain_establish_only=True
reanchor_min_price_gap_pct=0.003            impulse_bos_displacement_pct=0.015
bos_pullback_max_wick_pct=0.4               stage_wick_rejected_bos=True
rollback_staircase_on_discard=True
bos_leg_origin_choch_ref=True               bos_leg_origin_release_gap_atr=3.0
bos_leg_origin_min_pullback_atr=1.5 (M15–H1)
bos_leg_origin_require_close_break=True     bos_floor_require_close_break=True
choch_weak_ref_persistence_candles=4 (M5–H1)
choch_confirmed_trend_persistence_candles=4 (todos os TFs)
choch_pending_fail_at_broken_level=True     choch_pending_fail_persistence_candles=6
choch_origin_leg_extreme=True               choch_fizzle_reclaim_candles=30
choch_failed_fallback_suppress_candles=20   stage_choch_failed_window_bos=True
choch_weak_ref_fail_at_broken_level=True    stage_reversal_eaten_bos=True
choch_failed_rearm=True                     choch_failed_rearm_persistent=True
choch_fail_live_edge=True
choch_success_displacement_atr=4.5          choch_success_displacement_max_pct=0.20
stale_reanchor_displacement_atr=16.0        stale_reanchor_displacement_candles=15
emit_provisional_bos=True                   emit_provisional_choch=True
emit_provisional_choch_weak=True            confluence_filter=True
```

---

## 13. Resumo de uma frase por evento

- **BOS** — a tendência continuou: novo extremo estrutural confirmado por
  *fechamento* + *escada* + *pullback real*. Linha no degrau anterior
  (confirmado por fechamento).
- **CHoCH** — a tendência mudou: rompimento **sustentado** da referência de
  reversão (o fundinho/topo da pernada do último BOS). Vira a `trend`.
  Sustentação com histerese: base 2 contra trend *pendente*, barreira 4 contra
  trend já *confirmado* por BOS. `CHoCH*` = referência fraca
  (re-anchor/fallback), persistência própria.
- **CHOCH_FAILED** — a reversão não vingou: preço voltou pela origem (extremo
  mais fundo da perna) — ou, para **qualquer** CHoCH ainda sem BOS confirmador,
  pelo próprio nível rompido (persistência 6 quando estrutural; base quando ref
  fraca) — antes de um BOS confirmar; a `trend` volta.
- **Fizzle (✕ aditivo)** — o CHoCH ficou de pé mas o preço reclamou o nível
  rompido em ≤30 velas: marcador que termina a linha, **sem** virar o trend.
- **LIQUIDITY_SWEEP** — furou mas não sustentou: pegada de liquidez, tendência
  inalterada.
- **`BOS?` / `CHoCH?` / `CHoCH?*`** — live edge: a quebra já aconteceu por
  fechamento, os pivôs ainda não se formaram. Apagado, pontilhado, pode
  repintar.
- **HL/LH** — só um rótulo de pivô que não rompeu nada.

---

## 14. Onde olhar no código

| O quê | Arquivo / símbolo |
|-------|-------------------|
| Máquina de estados completa | `liquidity/detectors/internal_structure.py` → `InternalStructureDetector.detect` |
| Helpers (sustentação, close-break, confluence) | `liquidity/detectors/_common.py` |
| Passes de composição + parâmetros de produção | `app/dashboard_data.py` (`_run_internal_structure`, `_build_internal_detector`) |
| Desenho das linhas BOS/CHoCH (terminação, fizzle, provisórias) | `frontend/src/components/MainChart.tsx` → `structureLineEndTime` |
| Fixtures de regressão (um caso real por flag) | `tests/liquidity/detectors/data/` |
| Histórico de cada flag (caso motivador + medição) | `CLAUDE.md` na raiz do repo |
