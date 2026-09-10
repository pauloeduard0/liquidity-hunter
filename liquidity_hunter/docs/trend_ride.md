# Trend Ride — surfar a tendência da EMA9 + VWAP (sem OB)

Setup **de pesquisa**, irmão do `deep_reclaim` e igualmente separado do caminho
ao vivo: não lê nem escreve nada de `app/block_reclaim.py`, `app/paper_journal.py`
ou `research/ftmo_*.py`. Medido em 2026-09-06/07.

- Detecção e grade: `research/trend_ride.py`
- Walk-forward: `research/trend_ride_walkforward.py`
- Datasets: `research/.datasets/tr_*.json` (não versionados)

## A leitura

Sem OB. A zona **são** as linhas.

1. **Tendência** = preço fechando do mesmo lado da VWAP **e** da EMA9, com a
   EMA9 inclinada a favor (medida até a vela *anterior* ao gatilho) há ≥2 velas.
2. **Pullback** = o preço voltando para as linhas.
3. **Gatilho** = a **sombra**: a mínima fura a VWAP ou a EMA9 **e** fura a linha
   do **POC** (`indicators/footprint_poc.py`), com o fechamento voltando do lado
   bom das três. Vela que só fecha acima não testou nada.
4. **Piso** `r_atr >= 0.5` — aritmética, não leitura: com stop de 0,06% do preço
   o custo de giro sozinho passa de 1R.
5. Stop no extremo do pinbar, alvo 3R, horizonte 120 velas, **H4**.

Pinbar: união dos três graus, calda 65% no `legacy`, cor exigida no `l2`.

## O resultado: confirmado em holdout

Regra congelada **antes** de olhar, alvo 3R/h120, 25 símbolos nunca usados:

| | busca (4 majors) | **holdout (25 símbolos)** |
|---|---|---|
| n | 375 | **1861** |
| acerto | 32,8% | **28,9%** |
| controle casado | 19,7% | **19,5%** |
| líquido/trade | +0,253R | **+0,116R** |
| total | +95,0R | **+215,5R** |

18 dos 24 símbolos positivos. Todos os alvos confirmam (2R 37,8% vs 26,7%;
5R 19,7% vs 12,6%). O líquido cair pela metade é o esperado de um achado real.

## O tempo gráfico É o achado

A separação do controle cresce monotônica com o TF, e o custo cai junto:

| TF | separação (2R) | custo/trade | líquido |
|---|---|---|---|
| M15 | +1,6pp | 0,381R | −0,507 |
| M30 | +4,9pp | 0,270R | −0,307 |
| H1 | +6,8pp | 0,149R | −0,207 |
| **H4** | **+9,6pp** | **0,059R** | **−0,023** → positivo com POC |

Replicou em quatro amostras independentes. **M15/M30/H1 são negativos em todos
os anos, sem exceção; o H4 é positivo em cinco dos seis.** O M30 acerta 37,0%
no 2R — acima do break-even de 33,3% — e ainda perde 579R: a leitura funciona,
quem mata é o custo. **O setup é de H4 e só de H4.**

## O corte por regime (2025-26)

| ano | gatilho | controle | razão |
|---|---|---|---|
| 2020-24 | 29-36% | 18-24% | 1,45-1,6× |
| 2025 | 25% | 21% | **1,19×** |
| 2026 | 18% | **8%** | **2,25×** |

Em 2026 o **controle desabou junto**: não é o setup que parou, é o mercado que
parou de entregar 3R em 120 velas de H4. Em separação, 2026 é o melhor ano. O
ano fraco de verdade é 2025 (quase colado no aleatório). Nenhum alvo escapa —
2R/2,5R/3R/5R são todos negativos em 2025 e 2026, porque o problema é amplitude,
não escolha de alvo.

**Consequência operacional:** o acerto do controle aleatório é um termômetro de
regime mensurável *antes* de operar. Este projeto nunca usou isso.

## Os seis filtros medidos, e o que cada um custou

| filtro | veredito |
|---|---|
| **ordem das linhas** (EMA9 do lado bom da VWAP) | mantido; reduz prejuízo, não gera edge |
| **sombra na POC** | **mantido** — único que replicou em >1 TF (M30/H1/H4) |
| **piso `r_atr>=0.5`** | **mantido**; melhor regra da grade (SR 1,21) — é conserto de custo, não achado |
| EMA50 por baixo | REJEITADO: −20% de fluxo, ±0,5pp de acerto, piora o H4 |
| lado do POC (azul/vermelho) | REJEITADO: ganha **por operação** (+0,278 vs +0,253) e **perde por risco no tempo** (SR 0,53 → 0,38) |
| média do RSI (SMA14 sobre RSI14) vs 50 | REJEITADO: SR 0,02 — zera a base |

### Walk-forward (H4, 23 candidatas declaradas, 9 folds)

PBO **0,333** (3R) e **0,267** (5R); degradação −0,88 e −2,10. Nenhum filtro de
leitura bate `base & r_atr>=0.5`. Com 4 símbolos e 9 folds, é o teto do que a
amostra sustenta.

## Três suposições minhas que o dado derrubou

1. **"Linhas coladas são confluência"** — são lateralização. `gap<0,3 ATR`:
   21,5% e −66,2R; `gap>=0,8`: 30,2% e +67,0R.
2. **"Pavio furando as duas linhas é o caso forte"** — é o pior caso (21,4%,
   −71,8R) contra só a EMA9 (30,0%, +86,8R). Pelo mesmo motivo do item 1.
3. **"Três recortes concordando são três evidências"** — eram três vistas do
   **mesmo** balde de 174 trades. A melhora era aritmética: o ganho é exatamente
   o balde removido, no dado em que ele foi escolhido. Só replicar em outro
   lugar conta.

## O que ficou pendente

- **O custo real.** `COST_PCT = 0.0010` é constante chutada, e o líquido de
  +0,116R depende inteiro dela.
- **Um trade por perna**: o gatilho dispara em velas consecutivas da mesma
  perna (BNB 31/08 21:00 e 23:00, ETH 23/08 18:00 e 20:00 — todos morrem em par).
  Dobra o custo sem dobrar a chance. Não medido.
- **Teto de `r_atr`**: existe piso, não existe teto. Entradas com `r_atr` 2,40
  aparecem entre as perdedoras.
- O holdout dos 25 símbolos **já foi gasto uma vez**; a próxima confirmação vale
  menos.
