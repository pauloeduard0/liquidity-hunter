# Os limites da estrutura confirmada

> **Confirmed SMC is intentionally lagging in low-pullback expansions.**

Este documento fecha a linha de investigacao das Etapas 4.1-4.6 e registra a
fronteira que ela estabeleceu: **o que a estrutura confirmada pode responder,
o que ela deliberadamente nao responde, e por que a proxima camada tem de ser
independente dela.**

## As tres perguntas, e quem responde cada uma

O detector hoje responde duas. A terceira nao tem dono, e e o objeto da
Etapa 5.

| leitura | valores | significado | onde vive |
|---|---|---|---|
| **CONFIRMED STRUCTURE** | `bullish` / `bearish` | o ultimo bias estrutural **confirmado** (`final_trend`, movido por BOS/CHoCH) | `InternalStructureDetector` |
| **LEG ACTIVITY** | `active` / `stale` | a perna confirmada continua **operacionalmente ativa**, ou parou e devolveu movimento | `StructuralStall` |
| **CURRENT MARKET PRESSURE** | — | o preco/fluxo **agora** empurra a favor ou contra a estrutura confirmada | **ainda nao implementado** |

As tres sao ortogonais, e a combinacao que hoje nao tem como ser expressa e
exatamente a interessante:

```
confirmed structure = bearish
leg activity        = stale
current pressure    = bullish
```

Isso nao e uma contradicao a ser resolvida forcando o CHoCH a virar mais
cedo. E **informacao**, e ela precisa de uma camada propria.

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

## Etapa 5 — CURRENT MARKET PRESSURE / STRUCTURAL CONFLICT

*Proposta registrada. Nada disto foi implementado nem medido.*

**Objetivo futuro:** detectar de forma causal quando o comportamento atual do
mercado entra em conflito com a estrutura confirmada.

**A proxima etapa NAO tentara antecipar CHoCH.** Ela investigara uma camada
**independente**, sem alterar `final_trend`, `CHoCH`, `BOS` nem protected
levels.

Possiveis inputs a pesquisar (lista de partida, nao decisao):

- displacement recente;
- retorno / give-back da perna;
- posicao relativa a VWAP / EMA;
- flow, volume delta e CVD — ja disponiveis em `indicators`;
- sweeps;
- microestrutura / local structure;
- MTF.

As regras de medicao do `CLAUDE.md` valem: controle casado em simbolo,
timeframe **e direcao**; metricas scale-free; negativo e resultado.
