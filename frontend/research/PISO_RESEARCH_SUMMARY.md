# PISO — estado final da pesquisa (P0 a P7.1)

Este documento fecha a rodada de auditoria do PISO. Ele existe para uma coisa:
**impedir que hipoteses ja rejeitadas sejam reabertas sem evidencia nova.** Se
voce esta prestes a mexer no PISO, leia a secao 5 antes, e depois a 8.

Conclusao geral: **KEEP PRODUCTION RULE AS IS.** Fora o ATR causal (secao 4), a
regra sobreviveu a auditoria inteira. Nao ha evidencia suficiente para outra
mudanca.

Trilha de commits:

| Etapa | Commit | O que mediu |
|---|---|---|
| P0/P1 | `9842f1f` | harness: medir o PISO com o codigo que ele publica |
| P1.x  | `4e8c601` | ATR causal por candle (**a unica mudanca funcional**) |
| P2    | `7d2deda` | `MIN_FAMILIES`: 2 contra 3 |
| P3    | `cf91df2` | a familia `structural` como qualificador |
| P4    | `ba64c00` | near-miss structural ate 0,5 ATR |
| P5    | `42cc99d` | ancora da VWAP no H4 |
| P6    | `e95194b` | funil bullish do H4 |
| P7    | `c6b478f` | lifecycle dos niveis, fonte por fonte |
| P7.1  | `b1f1819` | lifecycle proprio para o CHoCH (rejeitado) |

## 1. O que o PISO E

Uma **leitura de defesa geometrica** de um nivel. O PISO marca que o preco
varreu e recuperou a banda da VWAP +/-1 sigma, e que a vela que fez isso tem
forma de rejeicao, num nivel que mais de uma fonte independente ainda considera
vivo. Concretamente:

- sweep/reclaim da banda **VWAP +/-1 sigma**;
- **excursao >= 1 ATR**, causal;
- **wick/body >= 2**;
- **>= 2 familias vivas** (`MIN_FAMILIES`);
- familias: `structural`, `order`, `resting`, `fair`;
- **sinal descritivo de reacao imediata**, principalmente em **h=5**.

E o que o PISO **nao e**, por mais que a marca no grafico convide a leitura:

- nao e reversao garantida;
- nao e absorcao (nada aqui mede fluxo agressor contra passivo);
- nao e liquidity hunt (o HUNT e outro modulo, com outra auditoria);
- nao e setup de swing — o efeito e imediato e nao sobrevive ao horizonte.

## 2. A unica mudanca que entrou em producao

**ATR causal por candle** (`4e8c601`).

O ATR antigo era de janela: uma vela nova reescrevia o ATR usado por marcas
historicas, ou seja, o **futuro alterava o passado**. Isso quebra qualquer
medicao e, pior, quebra a propria marca no grafico do usuario, que mudava de
lugar sozinha.

Resultado no painel medido: **213 -> 264 marcas**, com a invariante de
truncamento preservada (a decisao em T e identica com a serie completa e com a
serie cortada em T). **Nenhum threshold foi relaxado** — as marcas novas sao as
que o ATR errado escondia, nao marcas mais fracas admitidas por um gate menor.

## 3. Hipoteses rejeitadas

Cada uma foi medida contra controle casado por simbolo, direcao e periodo. Nao
reabrir nenhuma delas so com outro threshold ou outro bucket.

**P2 — `MIN_FAMILIES` 3 nao substitui 2.** Exigir tres familias nao compra
qualidade no horizonte principal; duas familias **nao sao ruido** em h=5. O
custo em amostra e alto e o retorno em qualidade nao aparece.

**P3 — a presenca de `structural` nao e qualificador robusto.** O efeito
aparente some depois de matching e estratificacao: ele era proximidade, nao
estrutura.

**P4 — near-miss structural ate 0,5 ATR nao adiciona cobertura segura.**
Aceitar o quase-encaixe traz marcas que medem no controle.

**P5 — a ancora da VWAP nao explica o H4 ruim.** O H4 continua ruim com ancora
de sessao, de semana e de mes. O problema nao e onde a VWAP comeca.

**P6 — a assimetria bullish/bearish no H4 nao vem de regra direcional.** Ela
vem principalmente da geometria e da composicao do mercado no painel. Nao ha
regra assimetrica a escrever.

**P7 — o lifecycle das fontes esta majoritariamente correto.** Cinco das seis
fontes medem exatamente no controle depois da morte (z de -1,40 a +1,01). A
janela zumbi dos pools rejeita **abaixo** do acaso, o que confirma a morte no
toque. A variante `invalidated_at -> breached_at` para equal levels, a unica
que a semantica justificava, **piora** o PISO.

**P7.1 — prolongar o CHoCH ate a invalidacao por close nao explica a P7.** A P7
tinha achado reacao acima do controle depois do fim visual do CHoCH (56,8%
contra 50,1%, z 3,76). A P7.1 mostrou que essa reacao **nao vem dos CHoCH ainda
validos por preco**: separando as 987 revisitas, as ainda validas dao 50,8%
(z 0,50) e as **ja invalidadas** dao 58,4% (z 4,41). O efeito mora justamente
nos niveis que a variante nao manteria vivos. Prolongar acrescenta 13 marcas
(+5% de cobertura) piores que a producao (MFE/MAE 0,58 contra 1,04), e nem o
teto diagnostico — o CHoCH que nunca morre — produz qualidade (81 marcas,
MFE/MAE 0,63, z 0,52). O teto inteiro e ruido, entao nenhuma variante de morte
por preco tem para onde crescer.

## 4. Achados verdadeiros, porem nao acionaveis

Estes sao **observacoes**, nao **regras validadas**. A diferenca e que uma
observacao descreve o painel medido e uma regra sobrevive a controle casado,
estratificacao, blocos temporais e robustez por simbolo. Nada abaixo sobreviveu
a esse conjunto:

- o efeito do PISO e principalmente **imediato**, em h=5, e nao se estende;
- o **H4 mede abaixo do controle**;
- o **H4 bullish** e particularmente raro e fraco;
- a **idade da ancora** apresenta gradiente descritivo;
- um **CHoCH visualmente aposentado** pode receber revisitas com reacao acima
  do acaso — mas o lifecycle por preco **nao captura** esse efeito (P7.1);
- o lifecycle **elimina a maior parte dos candidatos** (80-86% dos que ja
  passaram todos os outros gates), e mesmo assim **relaxa-lo nao mostrou
  qualidade** em nenhuma variante testada.

## 5. Limitacoes que continuam de pe

- **`fair` / volume profile tem hindsight historico.** Toda leitura que depende
  dessa familia deve ser reportada tambem sem ela.
- **Vela aberta pode gerar leitura provisoria sem `?` explicito.**
- **A janela rolante altera o inicio disponivel da serie causal**, entao o
  comeco de cada fixture nao e comparavel ao meio.
- **Amostra baixa** em varios recortes, em especial **H4 bullish** e **>= 3
  familias**.
- O **PISO e leitura descritiva**, nao um edge de trading validado. Nada aqui
  foi medido com custo, e o horizonte util e curto demais para absorver custo.

## 6. Regras para pesquisa futura

So reabrir o PISO com pelo menos um destes:

- **A)** dado novo substancial;
- **B)** periodo temporal novo;
- **C)** universo novo;
- **D)** hipotese mecanicamente diferente;
- **E)** correcao causal/semantica concreta.

**Nao** reabrir por:

- tentar 0,6 / 0,7 / 0,75;
- grace period arbitrario;
- `MIN_FAMILIES` diferente;
- tolerancia ligeiramente maior;
- trocar a ancora de novo;
- excluir o H4 para salvar o resultado;
- adicionar um indicador aleatorio.

## 7. Status final

**KEEP PRODUCTION RULE AS IS.**

Com a excecao do ATR causal ja promovido, a regra atual sobreviveu a auditoria.
`defendedLevels.ts`, `MainChart`, `structureLineEndTime`, os thresholds,
`MIN_FAMILIES` e `LEVEL_TOLERANCE_ATR` ficam como estao.
