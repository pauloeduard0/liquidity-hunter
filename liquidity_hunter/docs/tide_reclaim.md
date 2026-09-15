# Tide Reclaim (D1)

Setup medido em 2026-09-14. Leitura, não ordem: o projeto registra o sinal e
o que a fita fez depois (`app/tide_reclaim_journal.py`); não envia nem gerencia
posição. Changelog completo das medições: `structure_decisions.md`, K4 a K15.

## A regra

No **fechamento** do candle diário `i`:

| peça | regra |
|---|---|
| direção | a do HTF (W1), com o candle semanal em formação excluído; neutro = sem sinal |
| recuo | ≥ 3 candles seguidos fechando do lado CONTRA da VWAP mensal do Tide, todos no mesmo mês de âncora |
| gatilho | o candle `i` fecha de volta do lado a favor da VWAP |
| entrada | fechamento do candle `i` (ao vivo: o primeiro preço depois dele) |
| stop | extremo do recuo (menor mínima do recuo + candle `i` na compra), sem folga |
| alvo | 3R |
| expiração | 60 candles, fecha a mercado |
| posições | uma por símbolo; gatilho ignorado enquanto houver trade aberto |
| variante HUNT | só operar quando a perna D1 confirmada ainda está CONTRA o W1 (`hunt_active`) |

Código: `app/tide_reclaim.py` (`detect_tide_reclaim`, `ClosedCandleProvider`).
Paridade verificada contra a população da pesquisa (BTC, ETH, YFI, XLM, SOL):
295/295 sinais idênticos, inclusive a marca de HUNT.

## O que foi medido

72 perpétuos Binance, ~6 anos de D1, custo 0,13% ida e volta, controle
aleatório casado em série, direção e tamanho de stop, recortes early/late
(70/30 no tempo) e search/holdout (split fixo de símbolos).

| versão | R/trade | controle | trades/mês (universo) | R total | SR diário | pior DD | anos negativos |
|---|---|---|---|---|---|---|---|
| retomada (V0), 2R, sobreposto | +0,123 | +0,032 | — | — | — | — | — |
| retomada, 3R, 1 posição | +0,098 | | ~38–75 | +212R | 0,60–0,84 | −196R | 1 de 6 (2023, −5R) |
| **retomada + HUNT**, 3R, 1 posição | +0,111 | | ~20–40 | +131R | 0,54–0,76 | **−88R** | 1 de 6 (2023, −16R) |

Robustez (K14): a 0,30% de custo segue positivo em late e holdout (+180R /
+115R); sem os 5 melhores símbolos +124R / +76R; 64–67% dos símbolos positivos.

Ressalvas que continuam valendo:
- o holdout de símbolos é fino (+0,03 a +0,10R/trade); o período recente carrega;
- a venda carrega o bruto (alts caíram no período), mas contra o controle os
  dois lados têm edge parecido;
- ~35% de trades no lucro: sequências longas de stop são o normal;
- nada disso é execução real — o journal existe para medir a derrapagem.

## O que foi medido e rejeitado no caminho

| ideia | resultado |
|---|---|
| HUNT/CONT como gatilho (K1/K2) | edge era atraso de confirmação: ao vivo empata com o aleatório |
| HUNT como filtro do Block Reclaim (K3) | nenhuma regra passa |
| clímax VSA + HUNT / + Tide / + OB ou pool (K4–K7) | empata ou perde em todo TF, janela longa |
| retomada em M15/M30/H1 (K8) | negativa (−0,13 / −0,09 / −0,03R) |
| retomada H4 (K8–K10) | positiva e fina (+0,04R), 2023–24 perdem −422R |
| saída na VWAP oposta (K9) | escolhida na busca, falha no late |
| cortar faixas de stop (K10) | nenhuma faixa negativa; stop > 3 ATR é a melhor por trade |
| filtros de regime: volatilidade, eficiência, curva de capital (K11) | nenhum passa |
| BTC a favor da EMA200 (K11/K12) | forte no H4, não replica em M15/M30/H1 |
| carteira H4 + D1 com 1R cada (K15) | o H4 domina e dilui; D1 sozinho é melhor |
| envelope do Tide (fase ±1σ) como filtro no D1 (K13) | não soma |

## Rodar

```bash
# uma passada por dia, depois de 00:00 UTC: liquida o que fechou e registra o que disparou
poetry run python -m liquidity_hunter.app.tide_reclaim_journal
poetry run python -m liquidity_hunter.app.tide_reclaim_journal --report-only
```
