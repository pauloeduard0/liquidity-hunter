# Os limites da estrutura confirmada

> **Confirmed SMC is intentionally lagging in low-pullback expansions.**

Este documento fecha a linha de investigacao das Etapas 4.1-4.6 e registra a
fronteira que ela estabeleceu: **o que a estrutura confirmada pode responder,
o que ela deliberadamente nao responde, e por que a proxima camada tem de ser
independente dela.**

## As tres perguntas, e quem responde cada uma

O detector hoje responde duas. A terceira foi investigada na Etapa 5.0 e
**rejeitada com os dados e as features atuais**: continua sem dono, e por
enquanto e para continuar assim.

| leitura | valores | significado | onde vive |
|---|---|---|---|
| **CONFIRMED STRUCTURE** | `bullish` / `bearish` | o ultimo bias estrutural **confirmado** (`final_trend`, movido por BOS/CHoCH) | `InternalStructureDetector` |
| **LEG ACTIVITY** | `active` / `stale` | a perna confirmada continua **operacionalmente ativa**, ou parou e devolveu movimento | `StructuralStall` |
| **CURRENT MARKET PRESSURE** | — | o preco/fluxo **agora** empurra a favor ou contra a estrutura confirmada | **medido e rejeitado** (Etapas 5.0 e 5.1) |

As tres sao ortogonais, e a combinacao que hoje nao tem como ser expressa e
exatamente a interessante:

```
confirmed structure = bearish
leg activity        = stale
current pressure    = bullish
```

Isso nao e uma contradicao a ser resolvida forcando o CHoCH a virar mais
cedo. Continua sendo **informacao** — o que a Etapa 5.0 mostrou e que nao
sabemos medi-la: a terceira linha da tabela permanece vazia porque nenhuma
feature testada preencheu-a com qualidade acima do acaso, nao porque a
pergunta tenha deixado de fazer sentido.

## O achado central

Quando o preco percorre uma grande distancia **sem formar novos pullbacks
confirmados**, nao existe necessariamente uma referencia estrutural mais
proxima que possa ser usada legitimamente para antecipar um CHoCH.

Nao ha estrutura intermediaria porque o mercado nao a produziu. O atraso do
CHoCH nesses casos nao e um defeito da regra de confirmacao — e o preco de
exigir confirmacao. Antecipar exigiria inventar um pivo que nunca existiu.

Seis tentativas de reduzir esse atraso foram medidas. **Nenhuma justificou
mudanca de producao.** O registro completo, com os numeros de cada rejeicao,
esta em [`structure_decisions.md`](structure_decisions.md) (secao
"2026-09-09 — O atraso da estrutura confirmada: seis rejeicoes").

## O que fica proibido

Consequencia direta das seis medicoes, e nao preferencia de estilo:

- **NAO forcar CHoCH precoce.**
- **NAO criar synthetic structural anchors** — nao inventar pivo, nivel ou
  referencia que o mercado nao formou.
- **NAO enfraquecer protected levels.**
- **NAO transformar displacement em CHoCH.** Deslocamento re-ancora
  referencia; ele nao *e* uma mudanca de carater.
- **NAO usar referencias locais ruidosas** como substitutas da referencia
  estrutural confirmada. A Etapa 4.4 mediu o preco disso: no painel
  incondicional, a maioria esmagadora dos flips extras nunca confirma
  estrutura nova.
- **NAO continuar tentando resolver esse problema dentro do
  `StructuralStall`.** Ele mede outra coisa (abaixo).

## O caso de referencia: ZECUSDT D1

Fica registrado como **caso demonstrativo de structural confirmation lag
causado por ausencia de estrutura intermediaria** — nao como bug aberto do
CHoCH.

A reversao bearish de 2026-06-04 parece atrasada no grafico porque a expansao
bullish anterior aconteceu com pouquissima estrutura intermediaria: o preco
subiu ~220% entre 29/04 e 16/05 sem formar pullback confirmado. A referencia
que o CHoCH usou **era de fato a melhor referencia confirmada disponivel** —
a Etapa 4.4 verificou que o ultimo pullback confirmado ERA a referencia
oficial, com lead zero, nos dois CHoCH obrigatorios do caso.

No movimento de recuperacao posterior a mesma limitacao reaparece na direcao
oposta, o que e a evidencia de que se trata da propriedade e nao do simbolo.

## `StructuralStall` — o que ele mede, e o que nao e trabalho dele

Continua valido para o problema que realmente mede:

> uma perna **BOS-opened** deixou de avancar e devolveu uma quantidade
> relevante do movimento.

Producao permanece exatamente como esta:

| parametro | valor |
|---|---|
| `N` (barras sem advance) | **50** |
| `K` (retracao) | **6.0 x ATR** |
| base | **fechamentos** |
| ATR | **congelado no opener** |
| opener | **BOS** (nao CHoCH) |
| gate de expansao | **nenhum** |

A representacao `ACTIVE` / `STALE` no frontend permanece valida.

**`StructuralStall` NAO e responsavel por antecipar mudancas de regime que
ainda nao possuem confirmacao estrutural.** As Etapas 4.1, 4.3, 4.5 e 4.6
foram, cada uma a seu modo, tentativas de faze-lo carregar essa
responsabilidade; as quatro falharam na medicao.

## `CHOCH_FAILED` — valido e independente

As tres semanticas nao se substituem:

| conceito | significado |
|---|---|
| `CHoCH` | mudanca estrutural **confirmada** |
| `CHOCH_FAILED` (`CHoCH ✕`) | **tentativa** de mudanca estrutural que foi **invalidada** |
| `StructuralStall` | **atividade/vigencia** da perna |

Um `CHOCH_FAILED` nao e uma perna parada, e uma perna parada nao e uma
tentativa invalidada. Nenhum dos tres deve ser lido a partir do outro.

## Etapa 5.0 — CURRENT MARKET PRESSURE: medida e rejeitada

*Investigada. **Rejeitada.** Nada disto foi implementado.*

A pergunta era independente do CHoCH: *enquanto a estrutura confirmada aponta
para um lado, o comportamento atual do mercado ja empurra para o outro?* A
medicao esta em `research/current_market_pressure.py`, e os numeros em
[`structure_decisions.md`](structure_decisions.md) (secao "2026-09-09 —
Current market pressure: uma rejeicao").

**O resultado.** Num painel de 72 simbolos x M15/H1/H4/D1, as chamadas de
pressao ficaram **abaixo da taxa-base da propria direcao em 8 de 8 estratos**.
Chamar aquela mesma direcao num candle aleatorio do mesmo timeframe bate a
camada. Preco, EMA/VWAP e fluxo foram testados em camadas separadas; nenhuma
familia acrescentou separacao util, o holdout nao salvou nenhuma regra, e nos
episodios de conflito a estrutura antiga retomou ~2,5x mais vezes do que virou
na direcao apontada.

**A ausencia de uma leitura "mais atual" no grafico e, portanto, uma limitacao
aceita** — nao um item de backlog. Ela so deixa de ser aceita quando houver
evidencia robusta, e a Etapa 5.0 e o registro de que a evidencia disponivel
hoje nao chega la.

### O que fica proibido por esta rejeicao

- **NAO criar `pressure_score`** nem enum de pressao em producao.
- **NAO criar uma terceira camada** de leitura no dominio, na API, no
  `dashboard_data` ou no frontend.
- **NAO usar o achado de reversao a media como proxy de pressao.** Ele existe,
  esta registrado, e mede outra coisa (ver abaixo).
- **NAO usar o ZEC D1 como excecao** que autorize a camada. Ele continua sendo
  caso demonstrativo de *structural confirmation lag*, e nada alem disso.
- **NAO alterar SMC, Tide, schemas ou `StructuralStall`** por causa desta
  investigacao.

### O achado de reversao a media, e por que ele nao vale como pressao

Todas as features principais sairam **levemente anti-preditivas** no curto
prazo, de forma consistente nos quatro timeframes e com o placebo em 0,500. A
leitura invertida — desvanecer o movimento recente em vez de segui-lo — bate a
taxa-base por aproximadamente **+1,3 a +4,1 pontos**.

Isso e um resultado, e fica registrado como tal. **Nao autoriza a camada**, e
os motivos importam mais que o numero: e reversao a media de curto prazo, nao
"pressao corrente"; o efeito e pequeno e medido em fechamentos, **sem nenhum
modelo de custo**, na exata faixa em que a taxa de corretagem ja apagou
achados anteriores deste repositorio; o efeito se dissolve no horizonte maior;
e ele nao antecipa estrutura de forma util.

## Etapa 5.1 — o Tide nao e a terceira camada

*Investigada. **Rejeitada.** Nada foi implementado.* A pergunta seguinte era se
o **Tide** ja carregava, sem que se soubesse, a leitura que a Etapa 5.0 nao
achou. Nao carrega — e o motivo principal e estrutural, nao estatistico:

> **A cor do Tide E a estrutura confirmada.** O matiz da fita vem de
> `structureTrendByCandle` (`frontend/src/utils/tideRibbon.ts`), que replaya o
> mesmo stream nao-provisional que move o `final_trend`. `confirmed = bullish`
> com `Tide = bearish` nao pode ocorrer: e uma identidade de codigo, e e
> deliberada — a fita existe para nunca discordar dos rotulos desenhados sobre
> ela.

O Tide e uma **visualizacao composta** (envelope VWAP ±1σ + estrutura +
conviction/controller), nao um detector independente. Os canais que de fato nao
sao a estrutura — posicao no envelope, inclinacao da linha central, largura,
agressao — foram medidos e **nao acrescentam sinal causal robusto alem do preco
recente**: ao casar o estrato pelo retorno recente o lift cai 60-100%, a
agressao vai a zero, e o residuo nao replica no holdout. Os numeros estao em
[`structure_decisions.md`](structure_decisions.md) (secao "2026-09-09 — Tide
como sinal de transicao estrutural: uma rejeicao").

Dois limites do Tide que ficam registrados como propriedades conhecidas, e nao
como bugs a corrigir: a saturacao e normalizada pelo p90 da janela **inteira**
(lookahead — legitimo numa leitura retrospectiva, proibido como feature causal
sem reimplementacao), e o `controller` depende de open interest, que a Binance
retem ~30 dias (cobertura zero em painel historico).

**O Tide permanece visualizacao contextual.** Nao antecipa CHoCH, nao altera
`final_trend`, nao cria *structural conflict* nem *pressure state*, e nao mexe
em protected levels.

### O que ficou nao estabelecido

A interacao **`STALE` x pressao** nao foi validada de forma conclusiva, e nao
deve ser citada em nenhuma direcao. A comparacao por candle nao foi
normalizada pela taxa-base **dentro de cada estrato**, que e exatamente o
controle que derrubou o resultado principal — entao nem "STALE melhora" nem
"STALE piora" esta demonstrado. Fica em aberto, e nao justifica etapa propria
neste momento.

## Etapa 6.0 — VWAP acceptance tambem nao qualifica o evento

*Investigada. **Rejeitada.** Nada foi implementado.* Depois de perguntar se o
Tide **antecipa** a transicao (nao antecipa), a pergunta seguinte foi menor e
posterior: dado um BOS/CHoCH **ja confirmado**, a aceitacao relativa a VWAP nos
candles seguintes qualifica o evento como forte ou fragil? Nao qualifica, e o
motivo principal e mecanico:

> **O evento ja nasce do lado certo da VWAP.** Um BOS/CHoCH acontece *por* um
> rompimento na direcao do evento, entao `side_of_vwap` no candle do evento e
> praticamente constante (AUC 0,489-0,504; coerente em 84 de 84 nos casos
> obrigatorios). A pergunta "aconteceu do lado certo da VWAP?" ja vem
> respondida pelo proprio evento.

A aceitacao curta satura (mediana 1,00 nos **dois** desfechos em N=3 e N=5), e
a separacao que aparece em N=20 e batida pelo deslocamento puro pos-evento em
todo timeframe. Casando o estrato pelo deslocamento o residual cai ~71%, e no
holdout ele inverte de sinal. Os numeros estao em
[`structure_decisions.md`](structure_decisions.md) (secao "2026-09-09 — VWAP
acceptance como qualificador de evento: uma rejeicao").

**Fica proibido** usar VWAP/Tide como qualificador estrutural de BOS ou CHoCH,
como preditor de `CHOCH_FAILED`, ou como fonte de *structural conflict* /
*current pressure*. O que a Etapa 6.0 **nao** testou, e por isso continua em
aberto, e a VWAP como *location* — onde e quando entrar depois que a estrutura
ja existe. Essa e outra pergunta, e nao autoriza nenhuma das proibicoes acima.
