# Dominant Liquidity D5 — representação e decisão de produto

Pergunta: qual representação do card é **semanticamente honesta** e visualmente
útil? Nada de produção nem do frontend foi alterado, e nada foi implementado.

Esta rodada não mede nada novo: lê o replay do D4 (2.030 observações) e pergunta
o que é possível **dizer com verdade** com os níveis que ele já produz. Nenhum
score novo, nenhum peso, nenhuma strength.

## Conclusão

Resultado **B da D5.15**. O `MODEL B` (melhor EQ e melhor swing, lado a lado) é
o único que preserva integralmente a **única distinção que a série inteira
conseguiu medir** — a de família (D4: o EQ é atravessado 9–11pp menos que o
swing, com distância casada e replicação nos quatro blocos). O `MODEL C` (mapa
de lados) é mais enxuto e responde a pergunta operacional, mas mostra **dois
swings em 84,16%** das leituras: perde de vista justamente a família que se
comporta de modo diferente. O `MODEL A` é o mais simples e o mais fácil de
nomear com honestidade, e descarta o melhor da outra família — que está a menos
de 1 ATR do mostrado em 44,78% dos casos de mesmo lado.

**16 = NÃO**: não há evidência para mudar produção. A escolha entre os três é
decisão de produto, e a medição só estabelece o que não se pode dizer.

## D5.2 — Matriz de informação

Quantos níveis cada modelo põe no card:

| Modelo | 0 níveis | 1 nível | 2 níveis |
|---|---:|---:|---:|
| A | 0,00% | **100,00%** | 0,00% |
| B | 0,00% | 9,75% | **90,25%** |
| C | 0,00% | 6,06% | **93,94%** |

Nenhum modelo fica vazio nesta amostra. O que cada um **garante** não descartar:

| Modelo | o mais próximo no geral | um de cada lado | um de cada família |
|---|---:|---:|---:|
| A | 100,00% | 6,06% | 9,75% |
| B | 100,00% | 12,32% | **100,00%** |
| C | 100,00% | **100,00%** | 16,01% |

Os três sempre incluem o nível mais próximo — a diferença é só o que mais
garantem. Duplicação:

- **MODEL B**: as duas linhas apontam para o **mesmo lado** em 55,95%.
- **MODEL C**: as duas linhas são da **mesma família** em **84,16%** — quase
  sempre dois swings.
- B e C mostram exatamente o mesmo par de níveis em apenas **7,24%**: são
  desenhos genuinamente diferentes, não variações de apresentação.

Um dado que fecha a leitura: **o caso "swing longe (>3 ATR) e EQ perto (≤1 ATR)"
não ocorre nenhuma vez em 2.030 observações.** Os swings são ~6x mais numerosos,
então o nível mais próximo é quase sempre um swing e a linha de EQ do `MODEL B`
é, na prática, sempre a linha mais distante — a que o D4 mostrou segurar melhor.

## D5.3 — Mesmo lado e lados opostos

Das 1.832 observações com as duas famílias: **mesmo lado 1.025 (55,95%)**,
**lados opostos 807 (44,05%)**.

| Situação | O que o MODEL A descarta | Distância do descartado | Dentro de 1 ATR do mostrado |
|---|---|---:|---:|
| mesmo lado | o melhor da outra família | 2,83 ATR (mediana, +1,34 além) | **44,78%** |
| lados opostos | idem | 4,50 ATR (mediana, +3,32 além) | 16,98% |

Leitura: quando os dois estão do mesmo lado, o modelo de um nível só está
escondendo um nível **comparável** quase metade das vezes; quando estão em lados
opostos, o descartado costuma estar longe — mas aí a pergunta muda de natureza.

- **Mesmo lado**, `MODEL B` comunica uma escada ("o swing a 1,3 e o pool a 4,1,
  ambos acima") e `MODEL C` responde o que há de cada lado, sem dizer que há dois
  alvos na mesma direção.
- **Lados opostos**, `MODEL C` é o único cujas duas linhas respondem a
  **perguntas diferentes** (o que há acima / o que há abaixo) em vez de
  competirem; `MODEL B` continua correto, mas o leitor precisa notar as palavras
  "above"/"below" para não ler como ranking.

Nenhum dos dois precisa da palavra "dominância" para isso.

## D5.4 — Coerência com o `MainChart`

O gráfico, hoje, em produção (verificado nesta rodada, com o arquivo no estado
atual): pega `data.ranked_zones`, **descarta o score**, filtra apenas
`equal_highs`/`equal_lows`, mantém só os que estão à frente do preço no lado de
onde são caçados, ordena por **proximidade** e desenha
`NEAREST_POOLS_PER_SIDE = 2` de cada lado via `balancedTake` — com o comentário
de que swings isolados "just clutter the chart".

Traduzindo: o gráfico já é **"mais próximos, por lado, só EQ"**. Logo:

- `MODEL C` (por lado) é o que mais se parece com a *mecânica* do gráfico;
- `MODEL B` (por família) é o que mais se parece com a *ontologia* do gráfico —
  que trata EQ e swing como coisas diferentes a ponto de desenhar um e não o
  outro;
- `MODEL A` é o mais distante dos dois: o gráfico nunca mostra "um nível".

## D5.5 — Densidade visual (mock textual, frontend intocado)

| Modelo | linhas | largura p50 | p90 | linhas com "—" |
|---|---:|---:|---:|---:|
| A | 1 | 23 chars | 24 | 0,00% |
| B | 2 | 27 chars | 28 | 4,88% |
| C | 2 | 21 chars | 21 | 3,03% |

Mock real, do mesmo snapshot (BTCUSDT 15m 2026-08-31 11:00):

```
LEGACY   Dominant Liquidity     EQL 2.9 ATR below
MODEL A  Nearest Liquidity      Swing High 0.8 ATR above
MODEL B  Liquidity References   EQ  EQL 2.9 ATR below
                                SW  Swing High 0.8 ATR above
MODEL C  Liquidity Map          ↑  Swing High 0.8 ATR
                                ↓  Swing Low 1.4 ATR
```

Nenhum passa de 28 caracteres na linha mais larga, contra o card atual que já
mostra um preço formatado mais o tipo da zona. **Densidade não é o critério de
desempate**: os três cabem.

## D5.6 — Leitura de uma frase

```
BTCUSDT 4h 2026-03-28 04:00
  A: Nearest liquidity is a swing low 1.0 ATR below.
  B: Nearest EQ is EQH 5.5 ATR above; nearest swing is Swing Low 1.0 ATR below.
  C: Liquidity above: EQH 5.5 ATR; below: Swing Low 1.0 ATR.
```

As três frases são verdadeiras e nenhuma sugere força. A de `A` é a mais curta;
a de `B` é a única que diz ao leitor que existe um pool de EQ; a de `C` é a mais
natural para quem pensa em "para onde o preço pode ir".

## D5.7 — Honestidade do nome

Pergunta operacionalizada: **o nível exibido é o mais forte da própria família?**
Se não for, chamá-lo de dominante/mais forte é falso.

| Card | Verdadeiro em |
|---|---:|
| LEGACY, hoje rotulado **"Dominant Liquidity"** | **45,02%** |
| MODEL A | 7,64% |
| MODEL B | 26,95% |
| MODEL C | 7,98% |

O card atual já erra o próprio nome em 55% das leituras — e isso **antes** de
considerar que o D1/D3 mostraram que a métrica de força não prevê nada. Os
modelos novos escolhem por proximidade, então para eles o nome seria falso em
mais de 90% das vezes.

Nomes avaliados:

| Nome | Veredito |
|---|---|
| **Dominant Liquidity** | Reprovado: afirma dominância sem métrica validada (D1/D3) e é falso em 55% das leituras mesmo pela definição fraca acima |
| Strongest / Key Liquidity | Reprovados pelo mesmo motivo — "key" e "strongest" implicam hierarquia |
| Best Liquidity | Reprovado: "best" implica um critério de qualidade que não existe |
| **Nearest Liquidity** | Aprovado para o `MODEL A` — descreve exatamente o critério usado |
| **Liquidity References** | Aprovado para o `MODEL B` — não hierarquiza, e "references" cabe em duas famílias |
| **Liquidity Map** | Aprovado para o `MODEL C` — "map" implica cobertura dos dois lados, que é o que ele garante |
| Liquidity Levels | Aceitável e vago; não erra, mas não informa o critério |

## D5.8/D5.9/D5.10 — O que cada linha pode dizer

- **Família sempre visível** (D4 mediu comportamento diferente): `EQH`, `EQL`,
  `Swing High`, `Swing Low`. Testado.
- **Distância em ATR causal**, nunca percentual, nunca score abstrato. Testado.
- **Strength e score nunca exibidos.** A strength permanece apenas como
  desempate exato interno (consultada em 3,0% das observações no D4) e não
  aparece em nenhuma linha nem em nenhuma frase. Testado nos três modelos.

## D5.11 — Casos reais, os três modelos lado a lado

| Caso | Situação | LEGACY | A | B | C |
|---|---|---|---|---|---|
| mesmo lado | BTCUSDT 15m 02/09 13:00 | Swing High 1,3 ATR acima | Swing High 1,3 acima | EQH 4,1 acima + Swing High 1,3 acima | ↑ Swing High 1,3 / ↓ Swing Low 2,2 |
| lados opostos | BTCUSDT 15m 31/08 11:00 | EQL 2,9 ATR abaixo | Swing High 0,8 acima | EQL 2,9 abaixo + Swing High 0,8 acima | ↑ Swing High 0,8 / ↓ Swing Low 1,4 |
| EQ distante | BTCUSDT 15m 01/09 12:00 | Swing Low 0,8 abaixo | Swing Low 0,8 abaixo | EQH 6,3 acima + Swing Low 0,8 abaixo | ↑ Swing High 0,9 / ↓ Swing Low 0,8 |
| swing distante, EQ perto | — | **nenhuma ocorrência em 2.030 observações** | | | |
| ambos perto | BTCUSDT 15m 03/09 14:00 | EQL 5,7 abaixo | EQH 0,9 acima | EQH 0,9 acima + Swing High 0,9 acima | ↑ EQH 0,9 / ↓ EQL 5,7 |
| H4 saturado | ETHUSDT 4h 13/04 20:00 | EQL 7,3 abaixo | Swing Low 4,0 abaixo | EQL 7,3 abaixo + Swing Low 4,0 abaixo | ↑ — / ↓ Swing Low 4,0 |

O caso "ambos perto" é o mais eloquente: a LEGACY aponta para 5,7 ATR abaixo
enquanto existem dois níveis a 0,9 ATR acima.

## D5.12/D5.13 — Impacto de migração e compatibilidade de API

Quantas leituras deixam de apontar para o nível que o card mostra hoje:

| Modelo | O nível de hoje some do card |
|---|---:|
| A | 48,77% |
| B | **19,80%** |
| C | 43,69% |

`MODEL B` é o de menor ruptura: em 4 de cada 5 leituras o nível atual continua
no card (como uma das duas linhas).

**API (`DashboardDataResponse`)**: `candles`, `current_price` e `ranked_zones`
(com `zone.zone_type`, `price_high/low`, `side`, `strength`) já bastam para
construir **os três modelos sem alterar o contrato**. Uma ressalva verificada:
**não existe ATR exposto** — nem série, nem campo — e o frontend não tem helper
de true range (`grep` por `trueRange`/`atr(` em `frontend/src` não retorna
nada). Então qualquer modelo que exiba distância em ATR precisa de ~10 linhas de
cálculo no frontend a partir de `candles` (já presentes) **ou** de um campo novo
no backend. É a única dependência nova, e é pequena.

| Modelo | Backend | Frontend | Labels | Risco de regressão |
|---|---|---|---|---|
| A | nenhum (ATR no cliente) | trocar o corpo do card + renomear | 1 | baixo |
| B | nenhum (ATR no cliente) | card de 2 linhas + renomear | 2 | baixo |
| C | nenhum (ATR no cliente) | card de 2 linhas + renomear | 2 | baixo |

Nenhum exige tocar em detector, scoring ou `load_dashboard_data`. `ranked_zones`
continua servindo — o card apenas deixaria de usar a ordem dele.

## D5.14 — Testes pré-definidos (já escritos)

Os 15 testes desta rodada cobrem a lista pedida e servem de especificação para
uma implementação futura: só EQ, só swing, ambos, mesmo lado, lados opostos,
empate exato (resolvido sem exibir a strength), os três timeframes, distância em
ATR (nunca `%` nem score), família sempre nomeada, e o fallback de lado vazio
(`↓ —`). Estão em `test_dominant_liquidity_representation_audit.py`.

## Respostas

1. **Informação preservada:** B garante as duas famílias (100%); C garante os
   dois lados (100%); A garante só o mais próximo. Em volume bruto de informação
   C mostra 2 linhas mais vezes (93,94% contra 90,25%), mas **duplica família em
   84,16%**; B nunca duplica família.
2. **Mais honesto:** os três são honestos se renomeados; o problema semântico é
   do nome atual, não do desenho. A é o mais fácil de nomear sem risco.
3. **`MainChart`:** C casa com a mecânica (por lado, por proximidade); B casa com
   a ontologia (EQ e swing são coisas diferentes). A não casa com nenhuma.
4. **Menos poluído:** C (21 chars por linha, 3,03% de linhas vazias) entre os de
   duas linhas; A no absoluto.
5. **Mais fácil de explicar:** A ("o nível mais próximo, e de que tipo é").
6/7. **Menos mudança:** nenhum exige mudar API; todos exigem ATR no cliente
   (~10 linhas). Menor ruptura semântica: **B (19,80%)**.
8. **"Dominant Liquidity" não deve continuar** — é falso em 55% das leituras
   mesmo pela definição mais generosa, e a série inteira não achou métrica de
   dominância.
9. **Strength não deve ser exibida.** 10. **Score não deve ser exibido.**
11. **Sim, distância em ATR** (causal), não percentual nem score.
12. **Sim, família sempre visível** — é a única diferença medida.
13. **Por família (B)**, se o critério for preservar o que foi medido; **por lado
    (C)**, se o critério for a pergunta operacional. Ver abaixo.
14. BTC/ETH/SOL na tabela da §D5.11.
15. **Recomendaria o `MODEL B`** — ver abaixo.
16. **NÃO.** 17. Ver abaixo.

## 13/15 — A recomendação, e por que

Em quatro rodadas, **a única coisa que separou alguma coisa foi a família**: o
pool EQ é atravessado 9 a 11 pontos percentuais menos que o swing, com distância
casada, medida neutra de largura e replicação nos quatro blocos (D4). Tudo o
mais — strength bruta, strength normalizada, curvas de distância — não separou
nada.

O `MODEL B` é o único desenho que **garante** que essa distinção esteja na tela.
E o formato dele casa com o que a amostra mostra: como o caso "EQ perto e swing
longe" não ocorre nunca, a linha de swing é sempre a próxima e a de EQ sempre a
mais distante — ou seja, o card leria naturalmente como *"o nível que o preço
alcança primeiro (e que costuma ser rompido) e o pool mais distante (que costuma
segurar)"*. Isso é uma leitura de duas funções diferentes, que é exatamente o
que a medição sustenta, e nenhuma delas precisa da palavra "dominante".

O `MODEL C` é a segunda opção, e é a melhor se o critério for operacional em vez
de medido: cobre os dois lados sempre, é o mais enxuto, e é o mais parecido com
o que o gráfico já faz. O custo é que, em 84,16% das leituras, ele mostra dois
swings — e o EQ, a família com comportamento distinto, some do card.

O `MODEL A` fica em terceiro **apenas** por descartar informação comparável:
quando as duas famílias estão do mesmo lado, o nível descartado está a menos de
1 ATR do mostrado em 44,78% das vezes.

Reforço do limite: nada aqui mede que o usuário **decide melhor** com um desenho
ou outro. A recomendação é de coerência entre o que o card diz e o que foi
medido, não de desempenho.

## 16/17 — Decisão

**16. Existe evidência suficiente para mudar produção? NÃO.**

A representação é escolha de produto. A medição estabelece o que **não** se pode
afirmar (dominância, força, comparação entre famílias) e qual distinção merece
ficar visível (a família) — não estabelece qual das três telas serve melhor ao
usuário. Nada foi alterado: nem ranking, nem pesos, nem `distance_score`, nem
desempate, nem EQ, nem swing, nem API, nem frontend.

**17. O patch mínimo**, em ordem de custo e risco — nenhum implementado, e
apenas o primeiro é independente de decisão de produto:

1. **Corrigir `docs/scoring.md`.** Não é mudança de produto, é um fato errado: o
   `touch_score` deixou de ser proxy de contagem de toques em 2026-08-19
   (substituído por volume de área, com a contagem explicitamente rejeitada), e
   o `timeframe_score` é constante dentro de um chart, logo não ordena nada.
2. **Renomear o card.** "Dominant Liquidity" é falso em 55% das leituras mesmo
   pela definição mais generosa de dominância. Se nada mais mudar,
   "Liquidity Levels" já é verdadeiro; se o corpo mudar, o nome segue o modelo
   escolhido (Nearest Liquidity / Liquidity References / Liquidity Map).
3. **Trocar a curva de distância pela `LINEAR_5_ATR`** (D2), o único ganho limpo
   e sem contrapartida medida da série inteira.
4. **Rebaixar a strength a desempate exato** (D1/D3), o que também elimina a
   dependência de janela da escolha.
5. Só então, como decisão de produto, adotar A, B ou C.

Os passos 1 e 2 corrigem afirmações falsas e não dependem de nenhuma escolha de
arquitetura. Os passos 3 a 5 dependem.

## Limites

Esta rodada não mede desfecho: reaproveita o replay do D4 e avalia representação.
Densidade foi medida em mock textual, não na UI real — larguras em caracteres
não são larguras em pixels, e nenhuma verificação visual foi feita. O achado de
famílias distintas que fundamenta a recomendação é **uma métrica**, com n de 71
a 152 por bloco, sem amostra independente. As observações compartilham histórico
e níveis. Nada aqui mede resultado financeiro.

## Validação e artefatos

15 testes novos do D5 passam (e servem de especificação para uma futura
implementação); `ruff check` e `git diff --check` limpos; nenhum arquivo de
produção ou do frontend modificado por esta rodada.

[auditor](dominant_liquidity_representation_audit.py),
[testes](test_dominant_liquidity_representation_audit.py),
[D4](DOMINANT_LIQUIDITY_D4_PRODUCT.md),
[D3](DOMINANT_LIQUIDITY_D3_CALIBRATION.md),
[D2](DOMINANT_LIQUIDITY_D2_DISTANCE.md),
[D1](DOMINANT_LIQUIDITY_D1_STRENGTH.md).
Execução: `python -c "from research.dominant_liquidity_representation_audit
import report; report()"` (usa o baseline do D4; nenhum baseline novo).
