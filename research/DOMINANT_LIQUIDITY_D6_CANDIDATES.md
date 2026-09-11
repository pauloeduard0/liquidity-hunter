# Dominant Liquidity D6 — existe algum nível "forte"?

D1–D5 encerraram a tentativa de justificar o composite atual. Esta rodada não
procura um score: pergunta se **algum tipo de nível** merece a palavra "forte",
onde forte significa *o preço reagiu depois de tocar*, e não *o preço tocou*.

Pesquisa apenas. Produção, API e frontend intactos.

Painel: as 211 fixtures do manifesto EQ E0 (conferidas por SHA-256), prefixos
causais a partir de 200 barras, passo 100, mínimo de 40 barras futuras.
2.030 observações, 72.700 candidatos.

## Pré-registro (fixado antes de ler qualquer resultado)

- Força é medida **só depois do primeiro contato** (D6.0/D6.10). Alcance é
  reportado ao lado e nunca somado: o nível mais próximo ganha contato por
  geometria, o que não é edge.
- Geometria neutra entre famílias (D6.7). Todo nível é um **ponto** (o meio da
  banda); contato é uma barra futura cujo range contém o ponto; *through* é um
  fechamento que limpa o ponto em `CLEARANCE_ATR`. Pool EQ tem 0,3–0,5 ATR de
  largura e swing é um ponto — ler na borda compararia largura, não família.
- `CLEARANCE_ATR = 0,25` é o número do D4, reusado literalmente, e é também a
  tolerância de confluência (D6.14). Um número pré-registrado, nunca varrido.
- Placebo (D6.25 E): um preço sorteado dentro do **mesmo lado e mesmo balde de
  ATR** da própria observação, semente determinística, medido pelo mesmo código.
- Estratos de controle: símbolo × TF × lado × balde de ATR × bloco temporal.

## 1–4. O que é o Volume Profile deste projeto

`indicators/volume_profile.py`, chamado uma vez em `load_dashboard_data`
sobre `candles[-200:]` com 200 bandas (`_VOLUME_PROFILE_LOOKBACK` /
`_VOLUME_PROFILE_BUCKETS`). A faixa `[min(low), max(high)]` da janela é dividida
em bandas de largura igual (piso no tick do instrumento); o volume de cada vela
é distribuído pelas bandas que seu range cobre, proporcionalmente. Tipos que o
modelo de domínio realmente publica — nenhum nome foi inventado:

**POC**, **VAH**, **VAL** (bordas da value area, 70%), **HVN** e **LVN**
(`high_volume_nodes` / `low_volume_nodes`, bandas a ≥1,5× e ≤0,35× a média das
bandas negociadas). Shelves HVN/LVN contíguos foram fundidos: um patamar é um
nível, não cinco candidatos. O shelf do próprio POC não vira HVN duplicado.

**É causal?** Sim, por construção — lê só as últimas 200 velas do prefixo.
Invariância de truncamento: **2.030/2.030 (100%)** dos perfis reconstruídos
batem bit a bit. Nada do futuro entra.

**Repinta historicamente?** Sim, no sentido que importa: não existe perfil
histórico. A janela é **deslizante**, então o perfil publicado hoje é o de
*agora*, nunca o que estava disponível no passado — em **0,00%** das
observações a janela publicada coincide com a janela causal. O POC publicado
fica a **9,91 ATR** (mediana) do POC causal, e a mais de 1 ATR em **91,97%** dos
casos (VAH 10,56 / 93,94%; VAL 8,44 / 93,35%). Medir edge no perfil publicado
seria hindsight puro. Tudo abaixo usa exclusivamente o perfil causal.

## 5. Distância típica (D6.12) — aqui a intuição do usuário se confirma

| tipo | n | por obs. | p50 (ATR) | ≤1 ATR | ≤2 ATR | largura |
|---|---|---|---|---|---|---|
| POC | 2.030 | 1,00 | **2,79** | 23,05% | 40,34% | 0 |
| VAL | 2.027 | 1,00 | 3,22 | 16,67% | 33,00% | 0 |
| HVN | 3.839 | 1,89 | 3,81 | 12,76% | 27,35% | 0,314 |
| VAH | 2.029 | 1,00 | 4,11 | 12,76% | 25,97% | 0 |
| LVN | 6.510 | 3,21 | 5,74 | 5,12% | 13,86% | 0,742 |
| equal_highs | 2.530 | 1,25 | 7,25 | 2,65% | 9,53% | 0,462 |
| swing_low | 18.962 | 9,34 | 7,51 | 3,52% | 11,49% | 0 |
| swing_high | 16.453 | 8,10 | 7,62 | 3,41% | 11,23% | 0 |
| equal_lows | 3.635 | 1,79 | 7,76 | 2,42% | 9,49% | 0,296 |

O POC é o nível mais próximo do preço que o projeto produz: mediana de 2,79 ATR
contra ~7,5 ATR de EQ e swing, e está dentro de 1 ATR em 23% das leituras contra
2–3%. **A metade "próximo" da hipótese é verdadeira.** É a metade "forte" que
precisa sobreviver.

## 6–8. Alcance (D6.6) — e por que ele não decide nada

| família | n | h5 | h10 | h20 | h40 | h80 | d_atr p50 |
|---|---|---|---|---|---|---|---|
| VP | 16.435 | 12,63% | 17,16% | 22,74% | 31,51% | 42,21% | 4,35 |
| EQ | 6.165 | 4,64% | 8,40% | 12,04% | 17,68% | 27,38% | 7,53 |
| SW | 35.415 | 5,42% | 9,15% | 13,26% | 19,28% | 28,59% | 7,55 |
| **RAND** | 14.685 | **19,64%** | **27,37%** | **35,40%** | **46,35%** | **57,44%** | 2,67 |

O placebo é o mais alcançado de todos, porque é o mais próximo. Restringindo
todo mundo a ≤1 ATR, o alcance colapsa numa faixa só: VP 83,16% / EQ 85,81% /
SW 79,15% / **RAND 83,23%** em h20. Alcance é distância, não família — como o
protocolo antecipou (D6.26), e por isso ele não entra em nenhum veredito.

## 9–12. Hold depois do primeiro contato

Bruto, sem controlar distância (20 barras após o contato):

| família | n | through | rejeitado | MFE10 | MAE10 | MFE/MAE | barras até romper |
|---|---|---|---|---|---|---|---|
| VP | 5.935 | 71,2% | 28,8% | 1,597 | 1,151 | 1,000 | 2 |
| EQ | 1.285 | 72,0% | 28,0% | 1,618 | 1,464 | 0,743 | 2 |
| SW | 8.067 | 72,0% | 28,0% | 1,658 | 1,441 | 0,796 | 2 |
| **RAND** | 7.544 | **73,1%** | 26,9% | 1,553 | 1,270 | 0,917 | 2 |

Com distância casada (estratos completos), a diferença de *through* contra o
placebo (negativo = segura melhor):

| par | gap | estratos | pares | MFE−MAE |
|---|---|---|---|---|
| VP vs RAND | **+0,67pp** | 795 | 1.677 | +0,041 ATR |
| EQ vs RAND | +3,23pp | 66 | 134 | −0,436 ATR |
| SW vs RAND | +1,37pp | 974 | 2.054 | −0,200 ATR |
| VP vs EQ | −5,55pp | 63 | 139 | +0,250 ATR |
| VP vs SW | +0,42pp | 576 | 1.338 | −0,033 ATR |
| EQ vs SW | +2,06pp | 141 | 306 | −0,379 ATR |

Nenhuma família segura melhor que um preço sorteado à mesma distância. Os três
gaps contra o placebo são **positivos** — as famílias são atravessadas um pouco
*mais*, não menos. E o sinal não se mantém: VP vs RAND é +3,17pp no 15m,
−1,27pp no 1h, −0,38pp no 4h; EQ vs SW é −2,52pp no 15m e +3,43pp no 1h e no 4h.

No balde operacional (D6.13) o quadro não muda. Dentro de 1 ATR: VP 76,2% /
EQ 75,0% / SW 76,6% / RAND 76,2%. Dentro de 2 ATR: 73,8 / 74,0 / 74,9 / 74,7.
Dentro de 3 ATR: 72,4 / 73,7 / 74,2 / 73,8. Quatro populações, um número.

## 13–14. POC/HVN/LVN diferem entre si? (D6.11)

Bruto, o espalhamento entre tipos é de 69,0% (LVN) a 75,0% (VAH) de *through* —
uma faixa de 6pp que some quando se controla distância e se pede replicação:

| tipo vs RAND | 15m | 1h | 4h | b0 | b1 | b2 | b3 |
|---|---|---|---|---|---|---|---|
| POC | +0,88 | +3,33 | −1,67 | n/a | −1,19 | +7,14 | n/a |
| VAH | +4,27 | n/a | n/a | n/a | +7,14 | +5,33 | n/a |
| VAL | n/a | n/a | +0,93 | n/a | n/a | −2,08 | n/a |
| HVN | +2,20 | +1,32 | +0,65 | +10,00 | −0,97 | +1,08 | +0,00 |
| LVN | **+13,02** | **−10,49** | **−17,78** | −9,09 | −7,75 | −7,87 | +2,63 |

O único gap grande do painel agregado (LVN vs RAND −6,14pp, 220 pares) troca de
sinal entre timeframes com amplitude de 30pp. É ruído de célula pequena, não um
achado. HVN vs LVN (+20,00pp) sai de **7 estratos e 15 pares** e não vale
leitura. Nenhum tipo de perfil se separa dos outros nem do placebo.

## 15–18. Confluência (D6.14–D6.18, D6.30)

Categorias geométricas, tolerância 0,25 ATR:

| combo | n | through | MFE10 | MAE10 | d_atr p50 |
|---|---|---|---|---|---|
| SW | 5.088 | 71,9% | 1,702 | 1,482 | 8,40 |
| VP | 4.309 | 71,0% | 1,610 | 1,096 | 4,16 |
| EQ+SW | 1.966 | 72,2% | 1,618 | 1,511 | 8,48 |
| SW+VP | 2.623 | 72,2% | 1,564 | 1,304 | 4,60 |
| EQ+SW+VP | 1.008 | 70,9% | 1,584 | 1,278 | 5,02 |
| EQ | 173 | 70,5% | 1,847 | 1,793 | 10,50 |
| EQ+VP | 120 | 71,7% | 1,565 | 1,361 | 4,87 |

Contra cada componente isolado, com distância casada: SW+VP vs VP −1,34pp
(200 pares), SW+VP vs SW −0,50pp (401), EQ+SW vs SW −0,37pp (322). EQ+VP não
alcança amostra (3 e 1 estratos: **não é mensurável**, não é "empate").
EQ+SW+VP vs VP dá −10,62pp em 18 estratos / 40 pares — a mesma ordem de célula
minúscula que produziu o artefato do LVN.

O critério D6.30 exige superar **ambos** os componentes. Nada chega perto:
as diferenças ficam abaixo de 1,5pp onde há amostra, e onde são grandes não há
amostra. E a leitura contínua (D6.18) não tem gradiente nenhum — quanto mais
perto o nível da outra família, *igual*: 72,0% (0–0,25 ATR), 69,7% (0,25–0,5),
72,0% (0,5–1), 71,8% (1–2), 73,8% (>2). Confluência não é sinal aqui.

## 19–21. Por timeframe (D6.22)

| TF | VP | EQ | SW | RAND |
|---|---|---|---|---|
| 15m | 72,7% | 71,4% | 70,2% | 71,0% |
| 1h | 68,2% | 67,1% | 71,8% | 72,5% |
| 4h | 72,6% | 76,1% | 73,7% | 75,6% |

O nível do *through* muda por timeframe (é regime), mas a ordem entre famílias
não é estável: VP fica acima do placebo no 15m e abaixo no 1h; EQ é o melhor do
4h e o pior do 1h. Nenhum candidato replica em ≥2 TFs (D6.29.3).

## 22. Lado e tendência (D6.19/D6.20)

Aqui existe o único efeito grande e sistemático do painel — e ele **não é de
família nenhuma**. Acima do preço: 75–78% de through em todas as famílias,
placebo incluído (75,9%). Abaixo: 66–70%, placebo 70,0%. Em tendência bullish
causal, nível **contra** a tendência é atravessado 78,7% das vezes e nível **a
favor** 67,2% — um gap de 11,5pp que aparece igual em VP, EQ, SW e RAND.

É direção do mercado no período, exatamente o confundidor que o CLAUDE.md manda
casar. O controle já o casa por lado; sobrando ele, sobra zero.

## 23–24. Estabilidade e símbolos (D6.23/D6.24)

Por bloco temporal, as quatro famílias se movem juntas e o placebo acompanha
(bloco 0: VP 68,5 / EQ 70,9 / SW 68,3 / RAND 70,5; bloco 1: 73,4 / 76,3 / 73,7 /
74,4; bloco 2: 71,3 / 70,7 / 73,5 / 74,1; bloco 3: 70,8 / 67,9 / 70,7 / 72,2).
Não há bloco em que uma família se descole.

Por símbolo, gap casado de *through* contra o placebo: VP melhora em 50,00% dos
56 símbolos com amostra e piora em 41,07%, mediana **−0,00pp**. SW melhora em
49,18% de 61 e piora em 50,82%, mediana +0,17pp. EQ não alcança o piso em
símbolo nenhum. Isso é uma moeda.

## 25–26. Primeiro toque, reteste e estrutura

Primeiro toque vs segundo (D6.10): todo mundo piora igual no reteste — VP 71,2
→ 77,9%, EQ 72,0 → 79,4%, SW 72,0 → 78,8%, **RAND 73,1 → 78,9%**. O reteste é
mais atravessado porque o preço já está ali, não porque o nível gastou força.

Estrutura depois do toque (D6.21, secundário): um evento estrutural segue o
contato em 99,02% (VP), 98,83% (EQ) e 98,76% (SW) dos casos. Com 80 barras de
janela, "houve um BOS/CHoCH depois" é praticamente certo e não discrimina nada.

## 27. Casos reais (D6.31)

| caso | leitura |
|---|---|
| A perfil perto e segurou | BTCUSDT 15m 2026-09-05 16:00 · POC 1,69 ATR abaixo · contato +60 · segurou 20 barras · MFE10 4,44 MAE10 0,00 |
| B perfil tocado e atravessado | BTCUSDT 15m 2026-08-31 11:00 · POC 2,10 ATR abaixo · contato +5 · THROUGH em 1 barra · 4 visitas |
| C EQ segurou | BTCUSDT 1h 2026-08-08 13:00 · equal_highs 1,46 ATR acima · contato +1 · segurou · 8 visitas |
| D swing segurou | BTCUSDT 15m 2026-08-31 11:00 · swing_high 2,44 ATR acima · contato +30 · segurou |
| E perfil + EQ | ETHUSDT 1h 2026-09-06 17:00 · POC 2,49 ATR abaixo · contato +44 · segurou · MAE10 0,00 |
| F perfil + swing | BTCUSDT 15m 2026-08-31 11:00 · swing_high 0,80 ATR acima · THROUGH em 3 barras · 11 visitas |
| G confluência tripla | BTCUSDT 1h 2026-08-12 17:00 · swing_low 0,63 ATR abaixo · contato +2 · segurou |
| H candidato perto que falha | BTCUSDT 15m 2026-08-31 11:00 · VAH **0,03 ATR** acima · contato +1 · THROUGH · 7 visitas |

Os casos A, E e G existem e são bonitos. O ponto do painel é que B, F e H também
existem, na mesma proporção em que apareceriam por acaso.

## 28–30. Respostas finais

**Existe efeito causal? (25)** Não. O único efeito causal grande é direcional
(lado/tendência) e é idêntico no placebo.

**Existe candidato real a "Strong Liquidity"? (26)** Não. Contra os oito
critérios do D6.29: (1) nenhuma família supera o controle — as três são
atravessadas *mais*; (2) as diferenças somem ou invertem quando a distância é
casada; (3) nenhuma replica em ≥2 TFs; (4) nenhuma replica nos blocos; (5) o
gap mediano por símbolo é −0,00pp com metade dos símbolos de cada lado; (6) só
o perfil causal foi usado e ele é causal; (7) a amostra é grande e ainda assim
nada aparece; (8) tudo o que sobra é efeito de proximidade.

**Nível isolado ou confluência? (27)** Nenhum dos dois. Confluência não vence
componente isolado em lugar nenhum onde há amostra (D6.30 reprovado).

**Como Dominant Liquidity deveria ser definido? (28)** Pelos dados desta linha,
não deveria ser definido como *força*. A única propriedade medida e verdadeira
do Volume Profile é **proximidade** (POC a 2,79 ATR contra 7,5 ATR das outras
famílias), e proximidade é geometria observável, não qualidade. Qualquer rótulo
que continue prometendo dominância segue sem lastro — como o D5 já havia
quantificado para o card atual.

**Existe evidência suficiente para alterar produção? (29) — NÃO.**

**Qual D7 mínima faria sentido? (30)** Nenhuma que estas medições justifiquem.
Resultado do D6.32: **letra E** — nenhuma família supera os controles, e não
existe "strong liquidity" mensurável neste painel. Um D7 só teria sentido com
um mecanismo *novo* e uma hipótese própria (o protocolo proíbe, com razão,
varrer tolerância, clearance ou janela atrás de um número que passe).

## Limites

O veredito "through" com 0,25 ATR em 20 barras é permissivo: ~72% de tudo é
atravessado, o que deixa pouca margem de discriminação. Isso não invalida a
comparação — o placebo é medido na mesma escala, e é o *contraste* que é lido —
mas significa que um efeito muito pequeno poderia estar abaixo da resolução.
Nada foi varrido para contornar isso, por pré-registro.

O painel é o mesmo das rodadas anteriores: 211 fixtures, janelas curtas por TF,
datas diferentes entre timeframes, observações que compartilham histórico e
níveis (não são amostras independentes). O perfil causal replica a fiação de
produção (200 velas, 200 bandas); outra janela é outra pergunta, não testada.
Contato não é sweep, retorno, probabilidade calibrada nem trade.

Execução: `poetry run python -m research.dominant_liquidity_candidate_audit`
(replay), depois `report()` (análise).
Código: [auditor](dominant_liquidity_candidate_audit.py),
[testes](test_dominant_liquidity_candidate_audit.py).
Dados locais: `research/.replay_cache/dominant_liquidity_d6_baseline.json`
(gitignored).
