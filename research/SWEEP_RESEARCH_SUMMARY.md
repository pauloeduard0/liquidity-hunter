# SWEEP — fechamento da linha de pesquisa

**Status: KEEP SWEEP DETECTOR AS IS.**

Cinco etapas (S0, S1, S1.1, S7, S2) auditaram o evento `LIQUIDITY_SWEEP` de
ponta a ponta: o que ele é, quando ele passa a existir, quem o consome, se o
lado importa e se a geometria mistura dois fenômenos. **Uma única alteração de
produção saiu de tudo isso, e é uma frase.**

O detector, os enums, a API, o `SweepContext`, o HUNT, a
`structure_confluence`, o `manipulation_cycle`, o `oi_regime` e o frontend
ficaram **intactos**.

---

## A. O que o Sweep é

A definição real, lida do detector (`internal_structure.py:66`) e não da
intuição que o nome sugere:

> `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing reference
> but is not a confirmed reversal

É a **categoria residual da máquina de estados**. Um pivô contra-tendência rompe
a referência trailing, e a confirmação de reversão — `_common.is_sustained_break`
— exige que o candle da quebra **e** os `persistence_candles` seguintes fechem
todos além do nível (em produção, **3 fechamentos consecutivos**). Quando esse
teste falha, o que sobra é um sweep.

Três consequências que organizam tudo o que vem abaixo:

1. **O rótulo é uma afirmação sobre a máquina de estados, não sobre o candle.**
   O detector nunca olha o close do candle que ele data — olha o close dos três
   candles a partir do pivô.
2. **Ele representa *liquidity taken*, não necessariamente *wick rejection*.**
   É o que todo consumidor decisional do projeto de fato quer.
3. O próprio `core/domain/sweep_context.py` já dizia isso em voz alta:
   *"a sweep is emitted as a **residual** category ... a statement about the
   state machine, not about liquidity"*.

---

## B. S0 — Auditoria (`SWEEP_S0_AUDIT.md`)

- **13.550 sweeps** auditados.
- O **edge histórico aparente era artefato causal**: medido do timestamp que o
  evento carrega, que é retroativo.
- **43–44% fecham além do nível varrido.**
- **Forte overlap com `mitigation`** — a duplicidade medida chegou a 97,4% no
  item S3.
- **Convenção de direção consistente**: `direction` é o lado que o pavio
  alcançou, em 100% das emissões.
- **Sem evidência de precursor estrutural robusto.**

## C. S1 — Causalidade (`SWEEP_S1_CAUSALITY.md`)

- O **timestamp é retroativo em 100% dos casos** (mediana 6 candles, até 68 no
  H4 — 1h30 no M15, 1 dia no H4).
- O `known_at` (primeiro prefixo em que a pipeline afirma que o evento existe)
  vem sempre depois.
- O atraso é **inteiramente back-dating + `swing_lookback`**: o replay exato
  (12 símbolos × 3 TFs × 800 candles, passo 1) mediu o residual da máquina de
  estados em **0 candles no p50 e no p90**.
- O stream é **~98% estável depois de nascer** (97,6% nunca mudam; 1,59% de
  repaint).
- **O edge desaparece medido causalmente**: do `known_at`, 50–54%, contra um
  controle de 50,9% com n = 13.520.
- **Não é repaint relevante — é latência com data retroativa.**
- 95,8% dos níveis varridos já eram conhecíveis no candle do sweep: o atraso
  não vem do nível, vem do pivô que dispara a emissão.

## D. S1.1 — Impacto nos consumidores (`SWEEP_S1_1_AVAILABILITY.md`)

Gate `known_at <= T` aplicado a todos os consumidores causais, com os
consumidores **reais** reexecutados nos dois braços (112 símbolos × 3 TFs, 336
painéis, 0 falhas).

- **Bug causal real; impacto funcional pequeno.**
- **< 4% dos eventos** afetados em todo consumidor: `confluence/BOS` 3,4%,
  `confluence/CHoCH` 3,8%, `hunt/history` 1,7%, `hunt/continuation` 1,0%.
- **Nenhuma classificação relevante alterada.**
- **HUNT ao vivo praticamente imune**: 0 de 336.
- **`manipulation_cycle` e `oi_regime` herdam a datação, não o conteúdo**: 0%
  de mudança de valor, 100% de datação retroativa.
- **A âncora da VWAP não muda**: 0 de 334 (o `_VWAP_MIN_ANCHOR_CANDLES = 3` já
  dá a folga).
- Separação que decidiu o CHoCH: 27,2% perdem crédito sob gate estrito, mas só
  **3,8%** sob gate na janela — a diferença é a janela forward funcionando como
  desenhada.
- **Decisão: NÃO corrigir agora.**

## E. S7 — Direção / confluência (`SWEEP_S7_DIRECTIONAL_CONFLUENCE.md`)

A hipótese do "wrong-side" (42,0% dos CHoCH creditados pelo lado errado) **caiu,
e caiu ao contrário**.

- **`direction` do Sweep não é variável independente.** O detector só rotula
  como sweep um pivô **contra-tendência** — medido em **93,9%** —, então a
  direção é a tendência vigente invertida.
- **É fortemente condicionada pelo estado estrutural**: num CHoCH, "alinhado"
  concorda **96,6%** com "veio depois do flip". Os dois testes são quase o mesmo
  teste.
- **No CHoCH, o grupo chamado wrong-side é justamente o causalmente
  utilizável.** Restringindo à parte da janela que uma decisão em tempo real
  teria (só para trás), a ordem inverte:

  | grupo | h=20 MFE>MAE | n |
  |---|---|---|
  | wrong_side | **71,0%** | 994 |
  | sem sweep | 64,9% | 1.545 |
  | aligned | **57,6%** | 59 |

- O ganho aparente do "aligned" na janela cheia (72,2%) é **artefato de seleção
  para frente**: 95,4% desses créditos são posteriores ao evento.
- **Exigir aligned removeria informação útil** — tiraria o fator de 24,3% dos
  CHoCH, exatamente da população informativa.
- Robusto: 4/4 blocos temporais, 108/109 símbolos, 6/6 estratos.
- **Não alterar a lógica de direção.**

**Observação registrada, sem ação:** o peso do Sweep no **BOS** parece pouco
útil ou negativo (h=20: 46,1% MFE>MAE com sweep vs 49,8% sem, n=436 vs 5.139), e
o engine aplica o mesmo peso 9,0 ao BOS e ao CHoCH. **Não agir**, porque
`StructureConfluence` **não tem downstream decisional**: o `score` não é lido por
nada — nem em Python (só constrói e serializa) nem no frontend —, e o único
consumo é `factors.length` → o selo `✦N` do `MainChart`. A confluência ainda roda
por último no composition root, sem realimentar nada.

## F. S2 — Reclaim vs close-through (`SWEEP_S2_RECLAIM.md`)

Sobre 6.712 sweeps confirmados com referência:

| | n | % |
|---|---|---|
| **RECLAIM** (pavio além, close de volta) | 3.761 | **56,0%** |
| **CLOSE_THROUGH** (close além) | 2.951 | **44,0%** |

O close-through **não é ruído numérico**: mediana 0,27 ATR além do nível, 79,4%
acima de 0,1 ATR, 10,8% acima de 1 ATR, máximo 11,23 ATR. E é universal: 109 de
111 símbolos acima de 30%, sem gradiente por TF, direção ou bloco.

Mas:

- **Ambos se comportam como o mesmo evento estrutural.** Medidos do `known_at`
  contra controle casado em símbolo/TF/direção/período, os dois ficam colados no
  controle em 4/4 horizontes (−2,9% a +0,7%).
- **94,8% dos close-through voltam a atravessar o nível em até 20 candles** — o
  *"failed to hold"* está certo; era o *"wick"* que não estava.
- **Os consumidores decisionais querem *liquidity taken*.** `_swept_since`
  (*"whether a capture-side liquidity sweep fired during this leg"*), o ciclo
  `Accumulation → Manipulation (sweep) → Expansion`, e o FLUSH
  (*"leveraged positions force-closed"*) — nenhum pede rejeição.
- **Não há motivo para dividir enum ou domínio.** Os dois creditam CHoCH quase
  igual (47,4% vs 51,0%), então separar não isolaria nada.
- **`mitigation.py` já possui a taxonomia geométrica própria** —
  `sweep`/`sweep_rejected` vs `breach`, com lag zero, ao nível de
  `LiquidityZone`. Concorda 77,6% com esta classificação; a diferença é de
  *nível* (zona vs referência estrutural), não de definição.
- **A diferença é relevante apenas para a descrição.**

---

## G. A única correção de produção

**Commit `0001c04` — "Corrigir narrativa geometrica do Sweep".**

`app/narrative.py` afirmava *"wick pierced ... but failed to hold"* para todo
sweep, o que é falso para 44% deles. A frase agora escolhe a geometria pelo
close do próprio candle:

| geometria | frase |
|---|---|
| close volta aquém | `wick pierced {ref} {side} but failed to hold` *(inalterada)* |
| close além | `closed beyond {ref} {side} but failed to hold` |
| close exatamente no nível | `pierced {ref} {side} and closed on it` |
| sem nível **ou** candle fora da janela | frase antiga (fallback) |

`"failed to hold"` fica nas duas primeiras porque é a afirmação real do
detector. Um close além **não** é chamado de breakout, break confirmado, BOS nem
breach — o detector já descartou a reversão ao emitir um sweep, e há teste
explícito para isso.

Registrado:

- **~45,5% das frases mudam** (4.720 de 10.370 no painel de 112 símbolos × 3
  TFs — os 44% de close-through mais a classe "close no nível");
- **eventos, scores e consumidores permanecem idênticos**: 24.480 entradas de
  timeline, 906 anomalias e 8.853 eventos `SWEEP` inalterados;
- **969 testes passaram** (11 novos em `test_narrative.py`, cobrindo as duas
  direções de cada classe, o caso no nível e os dois fallbacks).

---

## H. Hipóteses rejeitadas

**Não reabrir sem nova evidência.**

| hipótese | por que caiu |
|---|---|
| Sweep como edge medido no timestamp histórico | S0 + S1.10: o edge é artefato da data retroativa; do `known_at` o baseline **é** o controle |
| `provisional` como solução causal | S1.8: semanticamente impossível — no candle do sweep a *geometria* existe, a **classificação** não |
| Corrigir `direction` para exigir aligned | S7: o "aligned" só existe na janela forward e mede pior que não ter sweep; removeria a população informativa |
| Separar reclaim/close-through em eventos novos | S2: outcome indistinguível, e nenhum consumidor decisional quer só um dos dois |
| Alterar o HUNT por causa do `known_at` | S1.1: 0 de 336 no estado ao vivo; 1,7% no histórico, sem mudar classificação |
| Alterar a âncora da VWAP por causa do Sweep | S1.1: 0 de 334 âncoras mudam; VWAP e sigma idênticos |

---

## I. Pendências reais

Registradas como observação futura. **Nenhuma pesquisa aberta agora.**

1. **`event_time` vs `known_at` continua semanticamente imperfeito.** O modelo A
   (manter `timestamp`, somar `known_at`) é a representação correta e é aditivo,
   mas o impacto atual é baixo (<4% em todo consumidor), então não paga o custo
   de manter a derivação em duas camadas.
2. **`StructureConfluence` usa o Sweep em BOS e CHoCH com o mesmo peso 9,0**,
   apesar de comportamento diferente (parece negativo no BOS, positivo no
   CHoCH). **Baixa prioridade porque o `score` não tem downstream decisional.**
3. **`oi_regime` e `manipulation_cycle` herdam o timestamp retroativo** (78,8%
   dos ciclos são disparados por um sweep e carregam a data dele, p50 de 6
   candles de antecedência), **mas isso não muda o conteúdo atual.**

---

## J. Status final

**KEEP SWEEP DETECTOR AS IS.**

Única alteração de produção em toda a linha: **corrigir a narrativa
geométrica** (`0001c04`).

### Arquivos da linha

```
research/SWEEP_S0_AUDIT.md                    research/sweep_audit.py
research/SWEEP_S1_CAUSALITY.md                research/sweep_causality.py
research/SWEEP_S1_1_AVAILABILITY.md           research/sweep_availability_impact.py
research/SWEEP_S7_DIRECTIONAL_CONFLUENCE.md   research/sweep_directional_confluence.py
research/SWEEP_S2_RECLAIM.md                  research/sweep_reclaim_audit.py
research/SWEEP_RESEARCH_SUMMARY.md            (este arquivo)
```

Cada estudo tem seu `test_*.py` ao lado e seu `*_baseline.json` gitignored
(regenerável pelo `--json` de cada script).
