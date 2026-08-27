"""O Block Reclaim no forex e nos metais da corretora.

Terceira classe de ativo a passar pela mesma regua (cripto, indice, agora
cambio), sem uma linha de deteccao mudada: `quality_features.scan` so troca
de provider, os gates sao os de producao, o alvo e 2R e cada entrada paga o
spread da sua propria barra mais o swap das noites que dormiu -- a
contabilidade que `research/ftmo_index_reclaim.py` ja carrega, reaproveitada
inteira.

**A previsao, escrita antes de rodar, para nao ser reescrita depois:** este e
o caso mais fraco dos tres para a VWAP. Cambio nao tem fechamento de sessao
nem volume real, e o mercado e descentralizado -- nao existe *o* preco medio
que todos enxergam, que era o mecanismo de ponto de Schelling proposto em
`docs/block_reclaim.md`. Em acao americana, com tape de verdade e ancora na
abertura do pregao, o premio institucional da VWAP ja nao apareceu. Aqui
espero empate com o aleatorio nos cruzamentos, e se algo sobreviver aposto
nos metais antes: XAUUSD e XAGUSD tem futuro de referencia na COMEX e sessao,
entao sao os unicos da lista onde a linha significa alguma coisa.

O que ficaria de pe mesmo com a VWAP fraca e o resto do setup -- bloco de
ordem, reclaim, stop no extremo testado, gate de `r_atr`. Se o forex passar,
isso e evidencia CONTRA a tese da VWAP e a favor da geometria; se falhar, nao
distingue as duas.

Uma armadilha propria do cambio: **simbolo nao e aposta**, aqui mais do que
em indice. Os 28 pares sao combinacoes de oito moedas, entao EURUSD, GBPUSD e
AUDUSD compartilham a perna do dolar e sobem juntos quando o dolar cai. O
relatorio quebra por bloco de exposicao exatamente por isso, e o `n` total
segue superestimando a informacao que existe.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import research.ftmo_index_reclaim as idx
from liquidity_hunter.core.domain import TimeFrame
from research._mt5 import MT5CsvProvider
from research.ftmo_index_reclaim import (
    FOREX_SPREAD_UNDERESTIMATE,
    UNMEASURED_GATES,
    attach_costs,
    report,
)
from research.quality_features import scan

#: Os 28 pares da corretora mais os dois metais.
FTMO_FOREX: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF", "CHFJPY",
    "XAUUSD", "XAGUSD",
)

#: Agrupado pela perna que os pares compartilham, nao por ordem alfabetica.
#: Dentro de um bloco os ativos respondem ao mesmo choque de moeda.
FOREX_BLOCS: dict[str, tuple[str, ...]] = {
    "Majors (perna USD)": ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
                           "AUDUSD", "NZDUSD"),
    "Cruzamentos EUR": ("EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD"),
    "Cruzamentos GBP": ("GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD"),
    "Cruzamentos JPY": ("AUDJPY", "NZDJPY", "CADJPY", "CHFJPY"),
    "Antipodeanos/CAD": ("AUDCHF", "AUDCAD", "AUDNZD", "NZDCHF", "NZDCAD", "CADCHF"),
    "Metais": ("XAUUSD", "XAGUSD"),
}

# `report` quebra por bloco lendo o mapa do modulo de indice; aqui os blocos
# sao os de moeda.
idx.BLOCS = FOREX_BLOCS

#: Comissao da corretora, **em USD por lote e por ponta**, lida da ficha do
#: instrumento. Foi a linha que eu tinha deixado de fora: o cambio na FTMO nao
#: e spread puro, ao contrario do indice. Em EURUSD sao 2,5 USD sobre ~116 mil
#: USD de nocional, ou 0,22 ponto-base por ponta -- pequeno em valor absoluto,
#: mas o spread mediano do par e 0,8 bp, entao a ida e volta acrescenta mais de
#: metade do custo que eu vinha contando.
COMMISSION_USD_PER_LOT = 2.5

#: Metais cobram a comissao em **porcentagem do nocional**, nao em USD fixos
#: por lote: 0,0007% por ponta na ficha do XAUUSD, ou 0,14 ponto-base na ida e
#: volta. Como e proporcional, ela dispensa a conversao de nocional que o
#: cambio exige. O XAGUSD **herda a taxa do XAUUSD** -- a ficha lida foi a do
#: ouro, e os dois sao do mesmo setor na corretora; se divergirem, o erro esta
#: num numero que vale menos que o arredondamento do spread.
PERCENT_COMMISSION_PER_SIDE: dict[str, float] = {
    "XAUUSD": 0.000007, "XAGUSD": 0.000007,
}

#: O coeficiente 3 do swap cai na QUARTA no cambio (convencao do spot, que
#: liquida em D+2 e portanto atravessa o fim de semana na quarta), nao na
#: sexta como no indice.
FOREX_TRIPLE_SWAP_WEEKDAY = 2

#: Para converter a comissao de USD para fracao do preco e preciso do nocional
#: em USD, e o nocional e `tamanho do contrato x (moeda base -> USD)`. Para as
#: bases que nao sao o dolar, a taxa sai da propria serie exportada do par
#: contra o dolar -- invertida quando o par exportado e USDxxx.
_BASE_TO_USD: dict[str, tuple[str, bool]] = {
    "EUR": ("EURUSD", False), "GBP": ("GBPUSD", False),
    "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False),
    "CAD": ("USDCAD", True), "CHF": ("USDCHF", True),
    "JPY": ("USDJPY", True),
}

LIMITS = {
    TimeFrame.M5: 120_000, TimeFrame.M15: 120_000, TimeFrame.M30: 120_000,
    TimeFrame.H1: 120_000, TimeFrame.H4: 45_000,
}


def _base_usd_rate(symbol: str, provider: MT5CsvProvider, timeframe: TimeFrame) -> float:
    """Quanto vale um dolar da moeda base, pela mediana da serie exportada.

    Uma mediana e nao a taxa da propria barra: a comissao entra na conta como
    fracao de um nocional que varia poucos por cento ao longo da amostra,
    enquanto o numero que ela produz vive na terceira casa do ponto-base.
    Alinhar no tempo custaria uma serie a mais por par e mudaria o custo em
    menos do que o arredondamento do spread.
    """
    base = symbol[:3]
    if base == "USD":
        return 1.0
    if base not in _BASE_TO_USD:  # XAU, XAG: o proprio par ja cota em USD
        return -1.0
    pair, invert = _BASE_TO_USD[base]
    closes = sorted(float(r["close"]) for r in provider.rows(pair, timeframe))
    mid = closes[len(closes) // 2]
    return 1.0 / mid if invert else mid


def attach_forex_costs(rows: list[dict], timeframe: TimeFrame, export: Path) -> list[dict]:
    """Reprecifica com **comissao**, alem do spread da barra e do swap.

    `attach_costs` (indice) ja pos `spread_pct` e `swap_r` em cada linha; aqui
    so falta a comissao, que no indice e zero e no cambio nao e. Ela entra como
    fracao do preco -- `2 x USD por lote / nocional em USD` -- e vira R pela
    mesma divisao pelo tamanho do stop que o resto do custo usa.

    Metais cobram em porcentagem do nocional em vez de USD por lote, entao
    entram por `PERCENT_COMMISSION_PER_SIDE` e nao passam pela conversao. Um
    simbolo sem nenhuma das duas fichas fica com `commission_r` NaN e o custo
    inalterado -- marcado como desconhecido em vez de herdar outro numero.
    """
    # O cambio e a classe onde o spread FLUTUA dentro da barra, entao a
    # coluna (que e o minimo) subestima o que a entrada paga. Indice e a
    # maioria do cripto sao cotados fixos e nao levam fator.
    rows = attach_costs(
        rows, timeframe, export, FOREX_TRIPLE_SWAP_WEEKDAY,
        spread_factor=FOREX_SPREAD_UNDERESTIMATE.get(timeframe, 1.0),
    )
    provider = MT5CsvProvider(export)
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    info = {s["symbol"]: s for s in meta["symbols"]}
    rate: dict[str, float] = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in rate:
            rate[symbol] = _base_usd_rate(symbol, provider, timeframe)
        if symbol in PERCENT_COMMISSION_PER_SIDE:
            commission_pct = 2 * PERCENT_COMMISSION_PER_SIDE[symbol]
            row["commission_r"] = commission_pct / row["r_pct"]
            row["cost_r"] += row["commission_r"]
            continue
        base_usd = rate[symbol]
        if base_usd < 0:
            row["commission_r"] = float("nan")
            continue
        notional_usd = info[symbol]["trade_contract_size"] * base_usd
        commission_pct = 2 * COMMISSION_USD_PER_LOT / notional_usd
        row["commission_r"] = commission_pct / row["r_pct"]
        row["cost_r"] += row["commission_r"]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--symbols", nargs="*", default=list(FTMO_FOREX))
    parser.add_argument("--export", default="/mnt/c/mt5-export")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--recost", action="store_true",
                        help="reprecifica a base salva sem repetir a varredura")
    args = parser.parse_args()

    timeframe = TimeFrame(args.timeframe)
    export = Path(args.export)
    out = f"research/.datasets/ftmo_fx_{timeframe.value}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    if args.report_only or args.recost:
        rows = json.loads(Path(out).read_text())
        if args.recost:
            rows = attach_forex_costs(rows, timeframe, export)
            Path(out).write_text(json.dumps(rows))
    else:
        provider = MT5CsvProvider(export)
        available = [s for s in args.symbols if provider.path(s, timeframe).exists()]
        missing = [s for s in args.symbols if s not in available]
        if missing:
            print(f"nao exportados (pulando): {', '.join(missing)}\n")
        rows = scan(available, timeframe, LIMITS[timeframe], out,
                    provider=provider, gates=UNMEASURED_GATES.get(timeframe))
        rows = attach_forex_costs(rows, timeframe, export)
        Path(out).write_text(json.dumps(rows))

    report(rows, timeframe, 0)


if __name__ == "__main__":
    main()
