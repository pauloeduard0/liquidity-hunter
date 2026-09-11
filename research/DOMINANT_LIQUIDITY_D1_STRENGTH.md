# Dominant Liquidity D1 — auditoria de calibração da `strength`

Pergunta desta rodada: a `strength` acrescenta informação além da proximidade,
e ela é comparável entre EQ e swing? Nada de produção foi alterado.

Amostra: as mesmas 211 fixtures do manifesto EQ (SHA-256 conferido), a mesma
grade causal do D0 (início em 200 barras, passo 100, 40 barras futuras
exigidas) — **2.030 observações com candidatos e 41.597 candidatos**, porque
esta rodada registra *todos* os candidatos, não só o vencedor.

## Conclusão

O critério (26) cai em **E, agravado por B**: nenhuma das duas `strength`
acrescenta contato além do que a distância já explica, e as duas famílias não
vivem na mesma escala — o canal de 40 pontos que elas dividem é, na prática,
um canal só de EQ. Não há evidência para escolher pesos novos aqui; há
evidência de que a calibração atual não se sustenta. Nenhuma alteração feita.

## 1. A fórmula, exata

`LiquidityScoringEngine.score` (`scoring/engine.py`), pesos default:

```
score = 0.4 * distance_score + 0.4 * touch_score + 0.2 * timeframe_score
distance_score  = clamp(1 - distance_pct / 0.05, 0, 1) * 100
touch_score     = zone.strength * 100
timeframe_score = DEFAULT_TIMEFRAME_WEIGHTS[tf] * 100   (constante no chart)
```

`distance_pct` usa o ponto médio da banda. Não há componente de lado nem de
contexto: `side` não entra no score, e `timeframe_score` é o mesmo para todos
os candidatos de um chart — logo **o ranking dentro de um chart é decidido só
por `distance_score` e `touch_score`**, 40 pontos cada.

A `strength` bruta, por família:

| Família | Strength bruta | Normalização |
|---|---|---|
| EQH/EQL | volume negociado **dentro da banda**, na janela do 1º ao último toque | `min(1, volume_area / (volume_médio × 118))` — 118 é o p75 medido de pools vivas |
| Swing | proeminência local: `pivot − max(vizinhos)` (ou espelhado) | `prominence / price_range(candles)`, o **span high-low da série inteira** |

São grandezas de naturezas diferentes com denominadores de naturezas
diferentes. A do swing é normalizada pelo tamanho da janela carregada, não por
uma propriedade do swing: mais barras na tela ⇒ span maior ⇒ strength menor
para o mesmo pivô. É a mesma dependência de janela já registrada em
`mean_tr_pct`.

## 2/3. Escalas brutas — incompatíveis

| Família | TF | n | p10 | p50 | p90 | p99 | max |
|---|---|---:|---:|---:|---:|---:|---:|
| EQ | 15m | 2.174 | 0,0629 | 0,2935 | 0,7693 | 1,0000 | 1,0000 |
| EQ | 1h | 1.806 | 0,0568 | 0,2750 | 0,7678 | 1,0000 | 1,0000 |
| EQ | 4h | 2.201 | 0,0521 | 0,3458 | 0,9210 | 1,0000 | 1,0000 |
| SW | 15m | 12.080 | 0,0019 | 0,0092 | 0,0374 | 0,1335 | 0,6080 |
| SW | 1h | 11.734 | 0,0015 | 0,0080 | 0,0349 | 0,1572 | 0,5811 |
| SW | 4h | 11.602 | 0,0015 | 0,0085 | 0,0382 | 0,1330 | 0,4231 |

A mediana EQ é **~35x** a mediana swing. Como `touch_score = strength × 100`,
o pipeline **não comprime nada**: a diferença bruta chega inteira ao ranking.
As duas distribuições de `touch_score` são as de cima multiplicadas por 100.

## 4. Saturação

Share de candidatos por faixa de `touch_score` (canal disponível: 0–100, peso
0,4 ⇒ 0–40 pontos de composite):

| Família | TF | 0–10 | 10–20 | 20–30 | 30–40 | ≥40 | em 1,0 |
|---|---|---:|---:|---:|---:|---:|---:|
| EQ | 15m | 16,8% | 16,0% | 18,6% | 13,9% | 31,4% | 3,2% |
| EQ | 1h | 20,9% | 18,9% | 13,7% | 12,4% | 29,9% | 4,2% |
| EQ | 4h | 17,8% | 14,7% | 12,4% | 11,7% | 35,2% | 8,3% |
| SW | 15m | 98,2% | 1,4% | 0,3% | 0,0% | 0,0% | 0,0% |
| SW | 1h | 98,3% | 0,9% | 0,5% | 0,2% | 0,2% | 0,0% |
| SW | 4h | 98,2% | 1,4% | 0,3% | 0,1% | 0,0% | 0,0% |

Nenhuma família está colada no teto de 1,0 (EQ 3–8%), mas a leitura relevante é
a outra ponta: **98% dos swings vivem no primeiro décimo do canal**. Para o
swing, `touch_score` é um canal morto — não por saturar no topo, por colapsar
no zero. É o mesmo defeito que aposentou a contagem de toques do EQ, com o
sinal invertido.

## 5/6/7/8/9/17. A `strength` acrescenta contato além da distância? Não.

Método: split pela **mediana da própria célula**, onde a célula é
`símbolo × TF × lado × bucket de distância em ATR × bloco temporal`. Assim a
comparação nunca põe um nível perto contra um longe, um símbolo agitado contra
um calmo, nem um período contra outro. Células com menos de 6 candidatos ou
sem dispersão de strength são descartadas, não agrupadas. Buckets fixados
antes de olhar resultado: 0–0,5 / 0,5–1 / 1–2 / 2–3 / 3–5 / >5 ATR.

Gap (alto − baixo) em contato@20, por bucket, todas as famílias:

| Bucket ATR | n | contato@20 | gap | células | ganha/perde |
|---|---:|---:|---:|---:|---:|
| 1–2 | 3.222 | 55,74% | +0,59pp | 57 | 18/13 |
| 2–3 | 3.458 | 33,46% | −2,71pp | 79 | 16/14 |
| 3–5 | 6.258 | 15,85% | +0,81pp | 392 | 62/59 |
| >5 | 27.260 | 1,66% | −0,13pp | 1.101 | 70/72 |

(0–0,5 e 0,5–1 ATR não produzem células utilizáveis: quase tudo é contato.)

Dentro da família, por TF e horizonte (gap em pp, célula completa):

| Família | TF | h5 | h10 | h20 | h40 | h80 |
|---|---|---:|---:|---:|---:|---:|
| EQ | 15m | +0,00 | −0,51 | −0,51 | −0,30 | +0,78 |
| EQ | 1h | +0,36 | +0,68 | **+2,41** | +1,98 | +3,72 |
| EQ | 4h | +0,00 | −0,23 | −0,73 | −1,15 | −3,01 |
| SW | 15m | +0,03 | +0,02 | −0,19 | −0,69 | −1,67 |
| SW | 1h | −0,10 | −0,30 | −0,66 | −1,01 | −3,15 |
| SW | 4h | −0,02 | −0,42 | −0,98 | −2,32 | −4,91 |

**EQ (8):** não agrega. O único sinal positivo é o EQ 1h, e ele não replica nos
outros dois TFs nem entre blocos (b1 +4,52pp, b2 −0,14pp, b3 −0,80pp) — é um
recorte, não um achado. **Swing (9):** não agrega; a proeminência é
consistentemente *negativa* nos horizontes longos, em todos os TFs.

## 10/11. Calibração cross-family, com distância casada

Pares dentro da **mesma observação, mesmo lado, mesmo bucket de ATR** (3.515
células):

| TF | células | strength EQ | strength SW | vantagem em composite | contato@20 EQ/SW | through EQ/SW | largura da banda EQ/SW |
|---|---:|---:|---:|---:|---:|---:|---:|
| 15m | 1.148 | 0,3433 | 0,0169 | **+13,06 pts** | 8,65% / 4,48% | 53,3% / 62,4% | 0,321 / 0,000 ATR |
| 1h | 1.073 | 0,3266 | 0,0183 | **+12,33 pts** | 13,87% / 6,41% | 61,3% / 70,8% | 0,373 / 0,000 ATR |
| 4h | 1.294 | 0,3656 | 0,0169 | **+13,95 pts** | 14,54% / 7,35% | 64,3% / 73,8% | 0,494 / 0,000 ATR |

Resposta a (11): **não, `strength=1,0` de EQ e `strength=1,0` de swing não
significam coisas comparáveis** — e, empiricamente, o swing nunca chega perto
de 1. Ser EQ vale ~13 pontos de composite antes de qualquer mérito. Traduzindo
para a moeda do outro canal (40 pontos cobrem os 5% de distância, ver §21):
**uma vantagem de partida de ~2,6 ATR no 15m, ~1,7 ATR no 1h e ~0,8 ATR no 4h**.

Os dois desfechos desta tabela (contato e through) **não** provam superioridade
de família: a banda EQ tem 0,32–0,49 ATR de largura e a do swing é um ponto de
largura zero. Um alvo mais largo é tocado mais e é atravessado menos por
construção. Fica registrado como confundidor, não como achado.

## 12/14. Por que o vencedor venceu

| Motivo | n | share |
|---|---:|---:|
| B — melhor strength | 774 | 38,13% |
| A — melhor distância | 748 | 36,85% |
| C — melhor nos dois | 460 | 22,66% |
| D — empate no topo | 48 | 2,36% |

Os **173 casos** com vencedor >3 ATR existindo alternativa ≤1 ATR, decompostos:

| vencedor | mais próximo | distance_score do vencedor | n |
|---|---|---|---:|
| EQ | SW | zero | 112 |
| EQ | SW | >0 | 49 |
| EQ | EQ | zero | 6 |
| EQ | EQ | >0 | 3 |
| EQ | SW | zero (nenhum dos dois pontua) | 2 |
| SW | SW | zero | 1 |

Ou seja: **172/173 dos vencedores distantes são EQ e 161/173 vencem de um
swing**. A mediana do gap de strength é 0,9613 — quase o canal inteiro. Em 120
dos 173 o vencedor tinha `distance_score` zerado, isto é, a distância nem
participou da decisão. Split por TF: 15m 42, 1h 34, **4h 97**.

Esses 173 não são "o ranking prefere strength": são **o ranking preferindo a
família cuja strength é numericamente grande**.

(Contato@20 nesses casos: vencedor 2,89%, mais próximo 85,55% — vantagem
geométrica trivial do nível perto, não edge. Não usar como argumento.)

## 13/14/15. Contrafactuais (research only, nenhum é candidato de produção)

| Ranking | vencedor muda | d_atr mediano | share EQ | contato@20 | through |
|---|---:|---:|---:|---:|---:|
| atual | — | 1,53 | 52,6% | 49,21% | — |
| sem strength | 42,86% | 1,03 | 12,5% | 64,04% | 73,4% |
| strength só em EQ | **2,76%** | 1,56 | 54,2% | 48,92% | 67,9% |
| strength só em swing | 48,03% | 1,04 | 5,6% | 63,20% | 73,5% |
| só distância | 48,57% | 1,03 | 8,2% | 64,68% | 73,2% |

Duas leituras que fecham a ablação (15):

- **O ranking atual é, a 97,2%, o ranking "strength só em EQ".** A strength do
  swing é numericamente inerte: ligá-la sobre um ranking de distância pura
  muda o vencedor em 0,5% dos casos (48,57% → 48,03%).
- **Tirar a strength muda o vencedor em 42,9% das observações** e derruba a
  share de EQ de 52,6% para 12,5%. A dependência do ranking em relação à
  strength é grande e é inteiramente de uma família.

As colunas de contato sobem em todo contrafactual que aproxima o vencedor —
de novo geometria, não qualidade. Não são critério de escolha.

## 18/19. Reação depois do contato (separada da alcançabilidade)

Entre os níveis efetivamente tocados, nos 10 candles seguintes ao 1º toque:

| Família | Bucket | n | through | mediana rejeição (ATR) | through alto/baixo strength |
|---|---|---:|---:|---:|---|
| EQ | 1–2 | 327 | 60,9% | 1,327 | 64,4% / 57,3% |
| EQ | 2–3 | 301 | 57,5% | 1,519 | 53,3% / 61,6% |
| EQ | 3–5 | 400 | 61,5% | 1,603 | 59,0% / 64,0% |
| EQ | >5 | 359 | 58,8% | 2,291 | 57,5% / 60,0% |
| SW | 1–2 | 2.065 | 73,2% | 1,450 | 73,4% / 73,0% |
| SW | 3–5 | 2.130 | 70,5% | 1,729 | 68,9% / 72,0% |
| SW | >5 | 2.012 | 67,1% | 2,414 | 67,0% / 67,3% |

A `strength` não ordena a reação dentro de nenhuma família: os splits alternam
de sinal entre buckets e a mediana de rejeição é praticamente idêntica. A
rejeição cresce com a distância nas duas famílias — isso é escala de ATR
acumulada em 10 candles, não mérito do nível.

Reachability (§5) e reação ficam reportadas separadamente, sem número único.

## 16. Strength × distância

Spearman entre strength e distância em ATR: EQ 15m −0,085, EQ 1h −0,138, EQ 4h
+0,166; SW 15m −0,170, SW 1h −0,106, SW 4h +0,068. Fraco e com sinal trocando
por TF. **O ranking não é empurrado para longe por uma correlação entre
strength e distância**; é empurrado pela escala da §11.

## 20/22. Estabilidade

Blocos (contato@20, gap estratificado dentro do bloco): EQ b1 +4,52pp, b2
−0,14pp, b3 −0,80pp; SW b0 +0,68pp, b1 −1,56pp, b2 −0,33pp, b3 −0,38pp.
Nenhum efeito replica em mais de um bloco.

Por símbolo (células ≥3): EQ — 53 símbolos, 9 positivos, 5 negativos, mediana
+0,00pp (p10 +0,00 / p90 +5,64). SW — 68 símbolos, 26 positivos, **41
negativos**, mediana −0,54pp (p10 −2,34 / p90 +1,09). Não há concentração: o
nada é distribuído.

## 23. Empates

48 observações (2,36%) empatam no primeiro score; 44 são vencidas por EQ.
Em 1 delas todos os `distance_score` já eram zero. O desempate atual é a ordem
de entrada dos detectores (`sorted` estável: swings, depois EQH, EQL) — não é
uma regra, é um efeito colateral da ordem de composição. Simulado em research,
desempatar por proximidade mudaria a escolha em **7 empates (14,6%)**, com
d_atr mediano caindo de 6,75 para 5,49. Efeito pequeno; o empate não é o
problema principal.

## 21/24. O que valem os 5% do `distance_score`

`distance_score` zera em 5% porque `max_distance_pct = 0.05` é uma constante
percentual do engine, igual para todo símbolo e todo TF. Em ATR14, esses 5%
são:

| TF | p10 | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| 15m | 4,50 | 5,82 | **7,97** | 10,54 | 13,66 |
| 1h | 2,81 | 3,79 | **5,40** | 7,61 | 10,23 |
| 4h | 1,32 | 1,74 | **2,34** | 2,98 | 3,86 |

O mesmo número percentual compra 8 ATR de alcance no 15m e 2,3 ATR no 4h. É
por isso que o H4 é o TF quebrado: vencedor >3 ATR em 40,60% (vs 24,85% e
23,82%) e **14,48% das observações do H4 com todos os `distance_score` zerados**
(vs 1,47% e 2,65%). No H4 a distância deixa de existir como critério em 1 de
cada 7 leituras, e a decisão sobra inteira para a strength — cuja escala é a
da §11. **Sim (22): o H4 é especialmente mal calibrado**, e por dois defeitos
somados.

Hipótese pré-registrada para depois (§25 do prompt, **não medida aqui**): o
`distance_score` deve depender da distância em ATR, não de um percentual fixo.
Curva e limiar ficam deliberadamente por escolher — escolher agora seria
escolher olhando estes resultados.

## Bug ou calibração?

Não há bug de código: os detectores e o engine fazem o que documentam. Há um
**defeito de projeto** na strength do swing — `prominence / price_range(série
inteira)` não é propriedade do swing, é propriedade da janela carregada, e o
mesmo pivô muda de nota conforme o quanto se carregou. Isso é da mesma família
de problema já registrado nos gates de ATR dependentes da janela. O resto é
calibração: duas métricas incomensuráveis dividindo um canal comum, e um canal
de distância percentual que morre cedo demais no H4.

## Limites

Contato é interseção geométrica com a banda original — não é sweep, retorno,
manutenção da publicação nem trade. Níveis próximos e bandas largas têm
vantagem mecânica em contato; nada aqui compara perto contra longe. As
observações compartilham histórico e níveis; não são independentes. Os TFs
cobrem períodos diferentes. Ausência de lift em contato **não** prova que a
strength seja inútil para qualidade — prova que ela não prevê alcançabilidade
além da distância e não ordena a reação dentro da própria família, nesta
amostra.

## Decisão

Critério **E** (nenhuma strength agrega além da distância) combinado com **B**
(EQ e swing não são comparáveis) e **F** (o H4 é um caso à parte). Nenhuma
alteração de pesos, `distance_score`, `touch_score`, desempate, EQ, swing ou
frontend foi feita.

**Existe evidência suficiente para mudar produção? Não desta rodada.** Há
evidência suficiente para dizer que a calibração atual não se justifica, e
nenhuma para escolher a substituta — escolher agora seria ajustar à mesma
amostra que produziu o diagnóstico. O próximo componente a pesquisar é o
`distance_score` (§24 acima), não a strength: a strength já está respondida.

### D2 proposto (somente proposta)

1. Pré-registrar, antes de qualquer medição: `distance_score` em ATR, e a
   família de curvas admissíveis, escolhida por argumento e não por resultado.
2. Pré-registrar o critério de utilidade do card (o que é um bom alvo) e a
   cobertura mínima — antes de rodar, não depois.
3. Medir em período independente das 211 fixtures do D0/D1, com controle
   casado em símbolo, TF **e direção**.
4. Tratar "remover a strength" e "strength só em EQ" como braços do mesmo
   experimento, já que hoje eles diferem em 42,9% e 0% das escolhas.
5. Só então decidir se a strength do swing volta em alguma escala, ou sai.

## Validação e artefatos

37 testes passaram (12 novos do D1 + os do D0 + os do scoring existente).
`ruff check` e `git diff --check` limpos. `git status` não mostra nenhum
arquivo de produção modificado.

[auditor](dominant_liquidity_strength_audit.py),
[testes](test_dominant_liquidity_strength_audit.py),
[D0](DOMINANT_LIQUIDITY_D0_RESULTS.md).
Execução: `poetry run python -m research.dominant_liquidity_strength_audit`
(replay) e `python -c "from research.dominant_liquidity_strength_audit import
report; report()"` (tabelas).
Baseline local: `research/.replay_cache/dominant_liquidity_d1_baseline.json`
(16,8 MB, gitignored).
