# Deep Reclaim — a visita rasa e curta

Setup **separado** do block reclaim de producao (`app/block_reclaim.py` +
`app/paper_journal.py`), medido do zero e mantido a parte de proposito. Nada
aqui esta ligado no caminho ao vivo. O codigo e `research/deep_reclaim.py`
(deteccao + grade) e `research/deep_visit_walkforward.py` (validacao).

## Por que ele existe

O setup de producao e estatisticamente valido e **nao e o setup que o leitor
opera**. O caso que separou os dois: BTCUSD M15, 2026-09-04, entrada vendida
com o fechamento 0,10 ATR ABAIXO da VWAP e 0,76 ATR ACIMA da EMA9, numa vela
verde -- nominalmente um reclaim (a rota `on_vwap or on_ema` pede o lado bom de
UMA linha), na tela nada. O deep existe para encaixar a leitura do grafico, e a
medicao entra depois para dizer se a leitura se sustenta.

## A regra, como ficou

Sobre o gatilho de producao (`detect_block_reclaims`), quatro condicoes:

| condicao | o que e |
|---|---|
| `r_atr <= 2` | o fundo da visita ficou perto da entrada -- a **visita rasa** |
| `visit_candles < 3` | a visita durou uma ou duas velas -- o **toque limpo** |
| `ema9_slope_lag1 > 0` | EMA9 inclinada a favor, medida ate a vela ANTERIOR ao gatilho |
| `not pierced` | o pavio da visita nao atravessou o bloco de ponta a ponta |

Stop no fundo da visita, alvo **2R**, horizonte 120 velas.

### M15, alvo 2R, h120, custo 0,10% ida e volta

| | busca | holdout |
|---|---|---|
| a regra | **57,0% / +0,510R** (n=256) | **55,7% / +0,460R** (n=140) |
| controle casado em simbolo e direcao | 22,7% | 23,5% |
| universo inteiro do gatilho | 27,7% / -0,040R | 27,9% / -0,028R |

Stop: `r_atr` mediano 1,22, `r_pct` mediano 0,62%, custo mediano 0,16R.

**Frequencia: ~20 operacoes por mes** no universo de 72 simbolos (396 no total,
span mediano de 619 dias por simbolo; nos ultimos doze meses fechados, 13 a 34
por mes com mediana 20). Isso e **0,27 por simbolo-mes** -- o fluxo so existe
por causa do universo, nenhum simbolo sozinho entrega o setup. A ~+0,46R
liquidos por operacao, ~9R/mes.

## O que a medicao estabeleceu

- **Os dois eixos nao sao a mesma coisa e somam.** `r_atr<=2` sozinho da 43,8%
  / 44,1%; `visita<3` sozinha da 44,7% / 45,4% mas **perde dinheiro** quando o
  `r_atr` nao acompanha (22,7%, -0,233R). Juntos, 52,7% / 51,8%. A profundidade
  e necessaria; a duracao e o que faltava.
- **Walk-forward sobre a regra final** (53 folds, 60/20 rolantes, 15
  candidatas declaradas -- as vencedoras, as variantes de limiar, as perdedoras
  da grade e a versao sem os gates):

  | | SR treino -> teste | degradacao | folds positivos |
  |---|---|---|---|
  | a regra final, FIXA | 2,03 -> 1,93 | **-0,10** | 29/53 |
  | a mesma sem os dois gates | 1,51 -> 1,22 | -0,29 | 31/53 |

  **PBO 0,467** (era 0,800 antes de o gate de cor sair; a queda e amostra:
  317 -> 396 operacoes). Por SR anual as candidatas formam um **plato**, nao um
  pico: 2,85 (a final), 2,59, 2,50, 2,42, 2,36, 2,13. Esse formato e o de um
  eixo real com um limiar que **nao se calibra** -- a regra final e a melhor
  das 15 e nao e defensavelmente melhor que a segunda. As perdedoras continuam
  perdendo, o que e o controle de sanidade: `sem sweep` SR 0,04, `tudo` 0,00,
  so os gates 0,46.

  **29/53 folds positivos e 55%**: a regra passa metade das janelas de 20 dias
  no vermelho. Com ~7 operacoes por janela isso e o esperado de um acerto de
  56% em 2R, mas quer dizer que um mes ruim e normal e **nao** e sinal de
  quebra.
- **Dentro do par, a briga do stop acaba.** `visit3`, `visit10` e `look10`
  colapsam no MESMO preco (a visita durou 1-2 velas, entao os tres "fundos" sao
  o mesmo fundo), e ate o stop no pinbar empata (53,1% / 50,0%) depois de ter
  sido um desastre na grade geral (-0,204R).

## Os quatro timeframes

| TF | busca | holdout | por mes | custo em R |
|---|---|---|---|---|
| M5 | 43,8% / +0,004 | 39,8% / **-0,070** | -- | 0,32 |
| **M15** | **57,0% / +0,510** | **55,7% / +0,460** | **19,5** | 0,16 |
| M30 | 47,8% / +0,288 | 45,8% / +0,234 | 9,1 | 0,15 |
| H1 | 49,4% / +0,413 (n=79) | n=39, sem amostra | 1,8 | 0,07 |

**O setup mora no M15**, e a curva nao e a que a intuicao do custo previa. O
custo em R cai monotonicamente subindo de timeframe (0,32 -> 0,07), mas a
frequencia despenca (19,5 -> 1,8) e o bruto nao melhora. O M5 morre pelo custo;
o H1 morre por falta de ocorrencia -- `visita<3` no H1 quer dizer que o preco
tocou o bloco e reagiu dentro de duas horas, e isso quase nao acontece, entao o
holdout nem alcanca o piso de 40 do relatorio; o M30 funciona e entrega metade
(e nao por ser mais caro: o custo la e MENOR, 0,149 contra 0,162 -- o que cai e
o bruto, +0,437 contra +0,672). So o M15 tem as duas coisas ao mesmo tempo.

O H4 nao foi rodado de proposito: com 1,8 por mes no H1, o H4 produziria um
"poucos" e nada mais.

## O que foi medido e REJEITADO

- **As tres regras de linha do leitor** (perna acima das duas linhas,
  fechamento acima das duas, toque de pavio) **pioram nas duas amostras**:
  24,3% / 24,0% contra 30,1% / 33,1% sem elas. E elas sao **incompativeis com o
  par por construcao**: `com as linhas` da n=0, porque a regra da perna exige
  uma vela entre o ultimo toque e o gatilho, e a visita curta nao deixa espaco
  para ela. O que as linhas cortavam era justamente a visita curta.
- **O toque de pavio nao filtra nada** (`toque de corpo: 0`): o gatilho de
  producao ja exige que o pavio fure a linha e o corpo volte.
- **O gate de cor do pinbar `l2` custa dinheiro.** Exigindo a cor: 52,7% /
  51,8% (n=207/110). Sem exigir: 57,0% / 55,7% (n=256/140) -- mais operacoes E
  melhor. Estava ligado por leitura ("uma vela verde nao e rejeicao
  vendedora"), nunca por medicao. Ressalva: n=49 / n=30 de diferenca, entao o
  que replica e o SINAL, nao a magnitude.
- **Stop no pinbar, na grade geral:** melhor acerto de todos (34,1%) e pior
  liquido (-0,204R, -1295R no total), porque `r_atr` mediano 0,94 quadruplica o
  custo em R. Apertar compra acerto E custo, e o custo ganha.
- **`sweeps_in_block`: nada.** Todas as faixas entre 26,6% e 26,9%, plano nas
  duas amostras, SR 0,24 no walk-forward. O achado do selo `▣` (sweep x OB,
  `held@5` 76% contra 60%) **nao se transfere** para o reclaim.
- **`prior_visits`: nada, e provavelmente mal formulado.** 4570 das 6352
  entradas tem 12+ visitas anteriores -- a regra "o OB tem que continuar vivo"
  nao e *poucos toques*, e precisa ser dita de outro jeito para virar numero.
- **`penetration` e `block_age`: efeito real mas dentro do que o `r_atr` ja
  pega** (`pen<0,25` sem `r_atr<=2` da 27,5%, o baseline).
- **M5: o mecanismo replica e o custo mata.** 43,8% / 39,8% de acerto, bruto
  +0,325 / +0,246 e custo **0,32R** -- liquido +0,004 / -0,070. Mesmo desfecho
  ja registrado para o setup de producao: se o custo aperta, **sobe** de
  timeframe.

## Gates que continuam ligados sem medicao propria

`vwap_candles>=4` (piso de acumulacao, medido em `vwap_age_walkforward`) e a
calda de 65% no grau `legacy` (ligada por leitura: o `legacy` limita o corpo
mas nao diz nada sobre o nariz, entao um doji passa). Nenhum dos dois foi
isolado neste setup.

## Pendente

Os quatro timeframes estao medidos e o M15 venceu. O que falta e o **custo
real**: os 0,10% usados aqui sao uma constante, e no feed da FTMO o spread e
propriedade da barra -- ja esta medido neste projeto que isso chegou a inverter
o sinal do resultado em instrumento caro (ver
`project_mt5_spread_is_bar_minimum` na memoria). Nada disso deve virar conversa
sobre operar antes desse passo.
