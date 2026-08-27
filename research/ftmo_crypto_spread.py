"""O spread real dos Crypto CFD da corretora, e o que ele faz com o resultado.

Fecha o buraco que este projeto carrega desde o primeiro estudo da corretora:
a medicao do setup e sobre **perpetuos USDT da Binance**, mas a corretora vende
**CFD**, e ate aqui so a comissao anunciada entrava na conta. O spread do CFD
nao aparecia em lugar nenhum -- e a simulacao de carteira
(`research/ftmo_portfolio.py`) mostrou que e ele quem decide: com +20bp o mes
cai de +4,10% para +0,88%, com +40bp a conta fica negativa.

Aqui o spread vem do proprio feed, barra a barra, do jeito que ja foi feito com
os indices. **O preco continua vindo da Binance** -- as operacoes ja estao
medidas la, e re-detectar tudo no CFD seria outra medicao, nao um reparo desta.
O que muda e so o custo de cada entrada.

Casar por timestamp e o certo, e nem sempre da: o CFD tem parada diaria e o
perpetuo nao, entao uma entrada pode cair numa barra que nao existe do lado da
corretora. Nesses casos entra a mediana do proprio instrumento, e o relatorio
diz em quantas entradas isso aconteceu -- um numero alto ali seria motivo para
desconfiar do casamento, nao para arredondar por cima.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from pathlib import Path

from research.ftmo_portfolio import CRYPTO_COMMISSION, MAIN, SWAP_DAYS
from research.ftmo_universe import (
    DATASETS,
    FTMO_CRYPTO,
    FTMO_SWAP_YEAR,
    RISK_PER_TRADE,
    load,
)

EXPORT = Path("/mnt/c/mt5-export")
SCANS = (("M15", "qf_m15.json", True), ("H4", "qf_h4.json", False))


def spread_table(ticker: str, timeframe: str, point: float) -> dict[str, float]:
    """Spread por barra, em fracao do preco.

    Um spread de **0 points** nao e spread zero -- e spread abaixo da
    resolucao que o instrumento consegue cotar (XLMUSD tem tick de 0,0001
    sobre um preco de 0,21, ou seja 0,048%, e a mediana dele sai zero). Meio
    tick e o piso honesto: erra para baixo, mas nao finge que a operacao e
    gratuita.
    """
    path = EXPORT / f"{ticker}_{timeframe}.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {
            row["time"]: max(int(row["spread"]), 0.5) * point / float(row["close"])
            for row in csv.DictReader(handle)
            if float(row["close"]) > 0
        }


def every_with_cost(by_binance: dict, summary: list, binance_of: dict) -> list[dict]:
    """So as linhas que receberam custo real -- um ticker sem CSV fica de fora
    em vez de herdar a mediana de outro instrumento."""
    return [r for entry in summary for r in by_binance[binance_of[entry[0]]]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-r", type=float, default=0.20,
                        help="teto de custo mediano por instrumento para a lista enxuta")
    args = parser.parse_args()

    meta = json.loads((EXPORT / "meta.json").read_text(encoding="utf-8"))
    points = {s["symbol"]: s["point"] for s in meta["symbols"]}
    binance_of = FTMO_CRYPTO

    for timeframe, dataset, gated in SCANS:
        rows = load(str(DATASETS / dataset), gated)
        by_binance: dict[str, list[dict]] = {}
        for row in rows:
            by_binance.setdefault(row["symbol"], []).append(row)

        print(f"\n{'=' * 92}\n{timeframe}   spread do feed da corretora, "
              f"barra a barra   (comissao {CRYPTO_COMMISSION:.3%} + swap)\n{'=' * 92}")
        print(f"  {'ticker':9}{'n':>5}{'spread%':>10}{'custo R':>10}{'bruto':>9}"
              f"{'liquido':>10}{'sem casar':>11}   antes (so comissao)")

        kept: list[dict] = []
        summary: list[tuple[str, float, float, int, float]] = []
        for ticker, binance in sorted(binance_of.items()):
            trades = by_binance.get(binance, [])
            if not trades:
                continue
            table = spread_table(ticker, timeframe, points.get(ticker, 0.0))
            if not table:
                print(f"  {ticker:9}{len(trades):>5}   sem CSV exportado")
                continue
            median_spread = st.median(table.values())
            swap = FTMO_SWAP_YEAR / 365 * SWAP_DAYS[timeframe]
            unmatched = 0
            costs, nets, olds = [], [], []
            for row in trades:
                spread = table.get(row["timestamp"])
                if spread is None:
                    spread = median_spread
                    unmatched += 1
                cost = (CRYPTO_COMMISSION + spread + swap) / row["r_pct"]
                costs.append(cost)
                nets.append(row[MAIN] - cost)
                olds.append(row[MAIN] - (CRYPTO_COMMISSION + swap) / row["r_pct"])
                row["cost_r_real"] = cost
            median_cost = st.median(costs)
            summary.append((ticker, median_spread, median_cost, len(trades), st.fmean(nets)))
            mark = "" if median_cost < args.max_cost_r else "   <- caro"
            print(f"  {ticker:9}{len(trades):>5}{median_spread:>10.4%}{median_cost:>10.3f}"
                  f"{st.fmean(r[MAIN] for r in trades):>+9.3f}{st.fmean(nets):>+10.3f}"
                  f"{unmatched / len(trades):>10.0%}   {st.fmean(olds):>+7.3f}{mark}")
            if median_cost < args.max_cost_r:
                kept.extend(trades)

        # Grava o custo real na base, para a carteira
        # (`research/ftmo_portfolio.py`) parar de estimar o lado cripto.
        out = DATASETS / f"ftmo_crypto_{timeframe.lower()}.json"
        out.write_text(json.dumps([
            {"timestamp": r["timestamp"], "symbol": r["symbol"],
             "r2_h40": r[MAIN], "cost_r": r["cost_r_real"], "r_pct": r["r_pct"]}
            for r in every_with_cost(by_binance, summary, binance_of)
        ]))
        print(f"\n  gravado {out.name}")

        every = every_with_cost(by_binance, summary, binance_of)
        for label, sel in (("todos os medidos", every),
                           (f"so custo < {args.max_cost_r:.2f}R", kept)):
            if not sel:
                continue
            net = st.fmean(r["cost_r_real"] for r in sel)
            result = st.fmean(r[MAIN] - r["cost_r_real"] for r in sel)
            months = len({r["timestamp"][:7] for r in sel})
            print(f"\n  {label:<24}{len(sel):>5} ops   custo {net:.3f}R   "
                  f"liquido {result:+.3f}R   {len(sel) / months:.1f} ops/mes   "
                  f"{result * len(sel) / months:+.2f}R/mes   "
                  f"{result * len(sel) / months * RISK_PER_TRADE:+.2%}/mes")


if __name__ == "__main__":
    main()
