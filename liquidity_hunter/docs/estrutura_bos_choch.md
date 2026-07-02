# Como o BOS e o CHoCH funcionam (guia em português)

Este documento explica, de forma direta, como o **`InternalStructureDetector`**
(`liquidity_hunter/liquidity/detectors/internal_structure.py`) — o detector que
**o gráfico React usa em todos os timeframes** — identifica e marca:

- **BOS** (Break of Structure / Rompimento de Estrutura)
- **CHoCH** (Change of Character / Mudança de Caráter)
- **CHOCH_FAILED** (CHoCH que falhou)
- **LIQUIDITY_SWEEP** (varredura de liquidez)
- **HH/HL/LH/LL** (rótulos descritivos de pivôs)

A ideia é você ler isto e conseguir bater o olho no gráfico e saber **por que**
cada marca apareceu — e decidir se algo precisa de ajuste.

> Observação: existe também o `SwingStructureDetector` (estrutura "maior"). Ele
> compartilha a mesma arquitetura, mas **não é desenhado no gráfico** hoje
> (alimenta o `market_structure_events` da API). Tudo que você vê no chart vem
> do **internal**. Este guia foca no internal.

---

## 1. Os blocos básicos

### 1.1 Pivôs (swing points)

Tudo começa com **pivôs**: topos e fundos locais.

- Um **swing high** (pivô de topo) é uma vela cujo topo é maior que o das
  `lookback` velas de cada lado.
- Um **swing low** (pivô de fundo) é o espelho disso.

O parâmetro que controla isso é o **`swing_lookback`**. Quanto menor, mais
sensível (mais pivôs, estrutura mais "fina"); quanto maior, mais grosso.

No M5 o `swing_lookback = 2` (bem sensível). Os pivôs são coletados e percorridos
em **ordem cronológica** — o detector é uma máquina de estados que processa um
pivô de cada vez.

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

### Filtro 3 — Confirmação por *pullback*

Mesmo com o estado avançado, o BOS só é **emitido** (vira marca no gráfico)
quando um **pullback** aparece: um pivô na direção oposta.

- BOS de baixa → confirma quando aparece um **topo (LH)** abaixo do topo anterior
- BOS de alta → confirma quando aparece um **fundo (HL)** acima do fundo anterior

Ou seja, o preço cai, faz o fundo (avança o estado), **repica** formando um topo
menor — e *aí* o BOS é confirmado e desenhado. Esse pullback é importante porque
ele também vira a **semente da referência do CHoCH** (ver seção 3).

#### 3b. Filtro de pavio no pullback (`bos_pullback_max_wick_pct = 0.4`)

Como o `swing_lookback` do M5 é pequeno, o pullback pode ser um **pavio de uma
vela só** — a vela espeta o extremo intrabar mas o corpo fecha longe. Isso é um
"pullback" que nunca repicou de verdade.

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

> **Este foi um dos ajustes recentes** (commit `6b94925`). Antes, um BOS no M5
> confirmava em cima de um pavio e ficava "pequeno demais".

### O que o BOS reporta (níveis e linha no gráfico)

- **`price_level`** = o extremo do pivô que disparou (o novo fundo/topo da perna)
- **`reference_price_level`** = o **nível formado que ele rompeu** (o degrau
  anterior da escada). É por isso que a linha do BOS é desenhada no **extremo do
  swing anterior**, formando a escada limpa — e não no extremo novo.
- **`reference_timestamp`** = a vela que *formou* aquele nível rompido (origem da
  linha), preenchido depois pelo passo de re-anchor de close-break.

---

## 3. CHoCH — Change of Character (reversão)

Um **CHoCH** diz: "a tendência **mudou de caráter** — pode estar revertendo."

É um rompimento **contra** a tendência atual. Num movimento de baixa, um CHoCH de
alta acontece quando o preço rompe (e *sustenta*) acima de uma referência de
reversão. É o evento que **vira a `trend`**.

### A pergunta-chave: qual nível o CHoCH precisa romper?

Esse é o coração do detector. A referência de reversão é, em ordem de prioridade:

```
validated_choch_<lado>  OU  choch_origin_<lado>  OU  active_<lado>
```

#### `validated_choch_high` / `validated_choch_low` — a referência "boa"

Desde 2026-07-02 (`bos_leg_origin_choch_ref`, ligado em produção), a regra
principal é direta: **o fundinho/topo da pernada do último BOS emitido é a
referência de CHoCH**. Quando um BOS de alta confirma (fechamento além do nível
+ pullback), o **fundo de onde a perna do rompimento saiu** (o
`pullback_ref` da pending BOS) vira `validated_choch_low` **na hora da emissão**
— marcado como *estrutural*. Cada novo BOS emitido **renova** a referência para
o fundinho da sua própria pernada (substitui sempre, mesmo que o novo fundo seja
mais distante: estrutura manda). Espelhado no lado de baixa (o topo da pernada
vira `validated_choch_high`).

Por cima disso ainda roda o pipeline clássico de candidato/continuação (que pode
*apertar* a referência para o pullback pós-BOS quando a perna faz novo extremo):

1. **Candidato**: quando um BOS de baixa confirma, o topo (LH) que o confirmou
   vira `candidate_choch_high` — **provisório**, ainda não é a referência.

2. **Promoção com trava de continuação**: o candidato vira
   `validated_choch_high` **se um próximo fundo fizer um novo extremo da perna**
   (abaixo de `bear_leg_low`). Isso prova que a perna realmente continuou. (Com
   a regra do fundinho, essa promoção só prevalece até o próximo BOS emitido
   renovar a referência para a sua pernada.)

   - **2b. Re-anchor por sweep**: se, enquanto a perna se desenrola, uma
     varredura (sweep) fura acima do candidato atual, o candidato é re-ancorado
     para o **extremo varrido** (só para mais extremo). Razão: depois que o preço
     pega a liquidez acima e volta a cair, é desse topo varrido que a reversão
     vai partir. Isso só mexe no *candidato*, nunca na referência validada.

3. **Estrutural é protegida**: uma referência estrutural (vinda de fundinho de
   BOS ou de promoção por continuação) não pode ser deslizada por re-anchor
   enquanto estiver **alcançável** — a menos de 4% do preço
   (`bos_leg_origin_release_gap_pct`). Foi o que consertou o CHoCH do H4 de
   maio/2026: disparou contra a mínima 78128 do BOS de 04/05 em vez do mínimo
   local deslizante 78713 que o staleness escrevia. Além de 4%, a perna "fugiu"
   da origem e o staleness volta a poder agir (caso H4 fev–mar, onde a queda
   impulsiva não emite BOS e a referência ficaria presa a 10%+ do preço).

#### `choch_origin_<lado>` — o fallback "one-shot"

No instante em que um CHoCH dispara, **todo** o estado validado/candidato é
zerado. Reconstruir a referência do lado oposto leva tempo (precisa de um BOS +
continuação). Nessa janela, se a reversão falhasse, a tendência ficaria
"presa". O `choch_origin` é o extremo da perna que o CHoCH acabou de reverter, e
serve de **fallback** até uma referência validada nova ser construída. É
**one-shot** (não arma o lado oposto), para não criar ping-pong.

#### `active_<lado>` — o fallback de bootstrap

No comecinho (`trend = NEUTRAL`, nada construído ainda), a referência trailing
serve de fallback para o detector conseguir "virar" a primeira tendência se o
chute inicial estiver errado.

### A confirmação do CHoCH: *persistência* (não pavio)

Um furo de uma vela que volta na hora **não** é CHoCH — é sweep. Para ser CHoCH,
o rompimento precisa **sustentar**: a vela do rompimento **mais** as
`persistence_candles` velas seguintes têm que **fechar** todas além da referência
(`is_sustained_break`). No M5, `persistence_candles = 5`.

```
   referência de reversão ──────────────────────
                    ╷ volta na hora → SWEEP
   ┌──┐  ┌──┐      ╷╵
   │  │  │  │   ┌──┐                  ┌──┐┌──┐┌──┐┌──┐┌──┐┌──┐  → 6 fechamentos
   └──┘  └──┘   └──┘  (sweep)         └──┘└──┘└──┘└──┘└──┘└──┘    acima → CHoCH
```

Se rompe mas **não** sustenta → vira `LIQUIDITY_SWEEP` (tendência inalterada).

### O que o CHoCH reporta

- **`price_level`** = o extremo do pivô que disparou
- **`reference_price_level`** = a referência que foi rompida (o nível validado)
- **`reference_timestamp`** = o timestamp do pivô validado (origem da linha) —
  por isso a linha do CHoCH começa na origem real, não na vela do rompimento.

---

## 4. CHOCH_FAILED — quando a reversão não vinga

Um CHoCH é **provisório** até um **BOS na nova direção** confirmá-lo. Enquanto
não confirma, ele carrega uma **origem** (`bull_choch_origin` / `bear_choch_origin`)
— o swing de onde o movimento do CHoCH partiu.

Se o preço **rompe de volta** essa origem (sustentado) **antes** de um BOS
confirmar, a reversão falhou:

- dispara um **`CHOCH_FAILED`** (direção = a do CHoCH que falhou,
  `reference_price_level` = a origem rompida)
- a `trend` **volta** para a anterior

Como a tendência original nunca terminou de verdade, a escada de BOS dela
**retoma do último BOS genuíno** (guardado em `pre_choch_*_bos_high/low` quando o
CHoCH disparou), não da origem do CHoCH. Também é **one-shot** — uma falha não
arma a origem oposta, então não há ping-pong.

No gráfico, uma linha de BOS/CHoCH que *parecia* ter sido cortada por esse CHoCH
provisório **continua atravessando** até um CHoCH genuíno de verdade
(`MainChart.structureLineEndTime`).

---

## 5. LIQUIDITY_SWEEP e os rótulos HL/LH

- **`LIQUIDITY_SWEEP`**: pivô contra a tendência que **rompe** a referência mas
  **não sustenta**. É a "pegada de liquidez". Marca em `price_level` (o extremo
  do pavio).
- **`HIGHER_LOW` / `LOWER_HIGH`**: um pivô que **não** rompe a referência
  trailing. São só rótulos descritivos do pivô (mantêm o próprio timestamp/preço).

---

## 6. Timestamp das marcas (por que a marca cai "na vela certa")

O pivô que *decide* o evento se forma no extremo da **nova** perna. Mas marcar o
evento ali atrasaria visualmente o rompimento. Então, depois de decidir o
evento, o detector faz uma **busca para trás** pela vela que **de fato** rompeu:

- BOS / SWEEP → `find_wick_break_index` (primeira vela cujo pavio cruza o nível)
- CHoCH / CHOCH_FAILED → `find_sustained_break_index` (primeira vela onde a
  persistência se sustenta)

O `timestamp` do evento é o dessa vela; o `price_level` continua sendo o extremo
do pivô.

---

## 7. Os "re-anchors" (por que existem e o que fazem)

Em timeframes maiores (ou impulsos limpos), a tendência pode **travar**: uma
perna de baixa deixa a referência de reversão de alta lá no **topo da perna**, e
o CHoCH de alta só dispara quando o preço sobe tudo de volta. Os re-anchors
**puxam a referência de reversão para um nível local**, **sem virar a `trend`**
(o CHoCH ainda tem que confirmar sozinho). Eles só **apertam** (nunca afrouxam,
nunca caem do lado errado do preço).

Com `bos_leg_origin_choch_ref` (produção), duas regras extras valem para todos:

- **Referência estrutural alcançável é intocável**: se `validated_choch_<lado>`
  veio de um fundinho de BOS (ou promoção por continuação) e está a menos de 4%
  do preço, o re-anchor recusa (ver seção 3). Só além desse gap ele age.
- **Re-anchor só escreve em `validated_choch_<lado>`**: o nível sintético não
  toca mais `active_<lado>`/`candidate_choch_<lado>`, que continuam sendo pivôs
  reais. Sem isso, o nível do re-anchor entrava no snapshot da pernada do
  próximo BOS e virava um "fundinho" estrutural falso (medido no M30: 63650 —
  artefato de janela — no lugar do fundo genuíno 65469).

### 7.1 Chain re-anchor (`reanchor_mode="chain"`)

Conta os avanços de BOS na perna; ao atingir `reanchor_chain_threshold`,
re-ancora para o extremo local da perna.

- **`reanchor_chain_establish_only = True`** (produção): o chain **só
  estabelece** uma referência que ficou cega (`validated_choch_<lado>` é `None`,
  típico de impulso limpo). Ele **não aperta** uma referência fresca que acabou
  de ser promovida de um pullback bom. *(Ajuste recente — commit `e078ecd` —
  resolveu o CHoCH ~58861 no M5, onde o chain rebaixava uma referência boa de
  59316 para uma fraca de 58861.)*

### 7.2 Staleness re-anchor (`stale_reanchor_candles`)

Independente do modo. Se a tendência roda X velas além do último BOS/flip sem um
novo, puxa a referência de reversão para o extremo local de uma janela recente.
Por timeframe: M5=120, M15=90, M30=80, H1=80, H4=60, D1=40, W1=26.

### 7.3 Guarda de distância mínima (`reanchor_min_price_gap_pct = 0.003`)

Vale para **todos** os re-anchors. Recusa ancorar a referência num extremo local
**colado no preço** (< 0,3%). Uma referência colada é "gatilho-fácil": um repique
trivial confirma um CHoCH no meio do range que falha logo. Exigir a distância faz
o rompimento ser uma reversão de verdade. *(Ajuste recente — commit `f799987` —
resolveu o CHoCH ~59307 no M5.)*

### 7.4 Displacement (`reanchor_mode="displacement"`)

Alternativa baseada em FVG (gap de 3 velas). **Não está em produção** — produção
usa `"chain"`.

---

## 8. Impulse BOS staging (`impulse_bos_displacement_pct = 0.015`)

Num **impulso limpo** (fundos/topos consecutivos **sem** pivô oposto no meio), a
máquina de estados avança a cada passo mas, sem pullback para confirmar, emite
**no máximo um** BOS — então uma queda forte de várias pernas imprime um único
trecho vazio em vez de uma escada.

Esse recurso **estagia** um BOS em cada avanço cujo deslocamento além do BOS
anterior passe de **1,5%**, numa **lista separada**. No fim, esses BOS estagiados
são **deduplicados** contra os BOS reais e mesclados — então ele **só adiciona**
marcas nos buracos do impulso. A máquina de estados, as referências e o CHoCH
ficam **intocados** (com a flag desligada, a saída é byte a byte idêntica).

---

## 9. O passo de composição (`_reanchor_bos_close_break`)

Depois do detector, em `load_dashboard_data`, cada BOS passa por um ajuste final:

- é **re-cronometrado** para a **primeira vela que FECHA** além do nível formado
  que ele rompeu (dentro da janela em que o BOS fica ativo)
- qualquer BOS cuja perna só **pavou** (nunca fechou) além do nível é **descartado**
- define o `reference_timestamp` para a vela que **formou** o nível rompido (a
  origem da linha)

É uma confirmação conservadora por fechamento — pode deixar trechos longos sem
evento no macro (intencional).

---

## 10. Parâmetros de produção por timeframe

`_INTERNAL_STRUCTURE_PARAMS` = `(swing_lookback, persistence_candles)`:

| TF  | swing_lookback | persistence_candles | stale_reanchor |
|-----|----------------|---------------------|----------------|
| M5  | 2              | 5                   | 120            |
| M15 | 3              | 8                   | 90             |
| M30 | 5              | 12                  | 80             |
| H1  | 5              | 12                  | 80             |
| H4  | 5              | 8                   | 60             |
| D1  | 5              | 8                   | 40             |
| W1  | 5              | 12                  | 26             |

Flags ligadas em produção (no internal):
`reanchor_mode="chain"`, `reanchor_chain_establish_only=True`,
`reanchor_min_price_gap_pct=0.003`, `impulse_bos_displacement_pct=0.015`,
`bos_pullback_max_wick_pct=0.4`, `confluence_filter=True`.

---

## 11. Resumo de uma frase por evento

- **BOS** — a tendência continuou: novo extremo estrutural confirmado por
  *fechamento* + *escada* + *pullback real*. Linha no degrau anterior.
- **CHoCH** — a tendência mudou: rompimento **sustentado** da referência de
  reversão (pullback do último BOS com continuação). Vira a `trend`.
- **CHOCH_FAILED** — a reversão não vingou: preço voltou pela origem antes de um
  BOS confirmar; a `trend` volta.
- **LIQUIDITY_SWEEP** — furou mas não sustentou: pegada de liquidez, tendência
  inalterada.
- **HL/LH** — só um rótulo de pivô que não rompeu nada.

---

## 12. Onde olhar no código

| O quê | Arquivo / símbolo |
|-------|-------------------|
| Máquina de estados completa | `liquidity/detectors/internal_structure.py` → `InternalStructureDetector.detect` |
| Helpers (sustentação, close-break, FVG, confluence) | `liquidity/detectors/_common.py` |
| Re-anchor de close-break + parâmetros de produção | `app/dashboard_data.py` |
| Desenho das linhas BOS/CHoCH | `frontend/src/components/MainChart.tsx` → `structureLineEndTime` |
