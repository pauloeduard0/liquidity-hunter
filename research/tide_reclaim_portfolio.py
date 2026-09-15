"""K15: carteira retomada VWAP H4 + D1 -- os anos ruins se compensam?

De onde vem
-----------
K10: H4 V0 + X3 (uma posição por vez) +686R em ~6 anos, mas 2023 −278R e
2024 −144R. K13/K14: D1 V0 + X3 +212R, robusto a custo e concentração. Aqui
os dois viram uma carteira com 1R de risco por trade, posições independentes
por TF (a mesma moeda pode ter uma posição H4 e uma D1). Custo 0,13%.

Regra, escrita ANTES de rodar
-----------------------------
A carteira PASSA se o SR diário combinado for maior que o do melhor
componente sozinho no conjunto todo, em late e em holdout, e se tiver menos
anos negativos que o H4 sozinho. Reportado também: R por ano, correlação
mensal H4×D1, pior drawdown em R (pico a vale da curva diária). Variante D1
V_hunt reportada ao lado (informativa).

Predição escrita antes: correlação mensal baixa (~0,2); a carteira melhora o
SR, mas 2023 continua negativo (o H4 pesa ~3× mais em R que o D1).

Run:
    poetry run python -m research.tide_reclaim_portfolio
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from statistics import fmean, pstdev

from liquidity_hunter.core.domain import TimeFrame
from research.tide_reclaim_btc_replication import CUTS, rebuild_tf
from research.tide_reclaim_d1_robust import D1_BASELINE
from research.tide_reclaim_h4_trades import BASELINE, one_at_a_time

EXIT = "X3"


def trades(path, tf_name: str, tf: TimeFrame, arm: str) -> list[dict]:
    payload = json.loads(path.read_text())
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in payload["rows"]:
        if r["tf"] == tf_name and "V0" in r["arms"]:
            by_symbol[r["symbol"]].append(r)
    rows = [x for s, rs in by_symbol.items() for x in rebuild_tf(s, tf, rs)]
    return one_at_a_time([r for r in rows if arm in r["arms"]], EXIT)


def daily(ts: list[dict], days: list[str]) -> list[float]:
    d: dict[str, float] = defaultdict(float)
    for r in ts:
        d[r["day"]] += r["net_x"]
    return [d.get(x, 0.0) for x in days]


def sr(series: list[float]) -> float:
    sd = pstdev(series) if len(series) > 1 else 0.0
    return fmean(series) / sd * math.sqrt(365) if sd > 0 else 0.0


def max_dd(series: list[float]) -> float:
    peak = eq = dd = 0.0
    for v in series:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = fmean(a), fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def main() -> None:
    h4 = trades(BASELINE, "4h", TimeFrame.H4, "V0")
    d1 = trades(D1_BASELINE, "1d", TimeFrame.D1, "V0")
    d1h = trades(D1_BASELINE, "1d", TimeFrame.D1, "V_hunt")
    start = max(min(r["day"] for r in h4), min(r["day"] for r in d1))
    end = min(max(r["day"] for r in h4), max(r["day"] for r in d1))
    books = {"H4": h4, "D1": d1, "H4+D1": h4 + d1, "D1 hunt": d1h, "H4+D1 hunt": h4 + d1h}
    books = {k: [r for r in v if start <= r["day"] <= end] for k, v in books.items()}
    all_days = sorted({r["day"] for v in books.values() for r in v})
    print(f"\nK15 - CARTEIRA RETOMADA VWAP H4 + D1 | janela comum {start} .. {end}")

    years = sorted({d[:4] for d in all_days})
    print(f"\n  {'':12s} " + " ".join(f"{y:>7s}" for y in years) + "   total  SR/dia  pior DD  neg")
    res = {}
    for name, ts in books.items():
        per_year = [sum(r["net_x"] for r in ts if r["day"][:4] == y) for y in years]
        s = daily(ts, all_days)
        neg = sum(v < 0 for v in per_year)
        res[name] = {"sr": sr(s), "neg": neg}
        print(f"  {name:12s} " + " ".join(f"{v:+7.0f}" for v in per_year)
              + f"  {sum(per_year):+6.0f}  {sr(s):+6.2f}  {max_dd(s):+7.0f}   {neg}")
        for c in ("late", "holdout"):
            sub = [r for r in ts if CUTS[c](r)]
            days_c = sorted({r["day"] for r in sub})
            span = [d for d in all_days if days_c and days_c[0] <= d <= days_c[-1]]
            res[name][c] = sr(daily(sub, span))
        print(f"  {'':12s} SR late {res[name]['late']:+.2f} | holdout {res[name]['holdout']:+.2f} "
              f"| ~{len(ts) / (len(all_days) / 30.4):.0f} trades/mes")

    months = sorted({d[:7] for d in all_days})
    mh = [sum(r["net_x"] for r in books["H4"] if r["day"][:7] == m) for m in months]
    md = [sum(r["net_x"] for r in books["D1"] if r["day"][:7] == m) for m in months]
    print(f"\n  correlacao mensal H4 x D1: {corr(mh, md):+.2f} ({len(months)} meses); "
          f"meses negativos H4 {sum(v < 0 for v in mh)}, D1 {sum(v < 0 for v in md)}, "
          f"carteira {sum(a + b < 0 for a, b in zip(mh, md, strict=True))}")

    for combo, part in (("H4+D1", "D1"), ("H4+D1 hunt", "D1 hunt")):
        best = max(("H4", part), key=lambda k: res[k]["sr"])
        ok = (all(res[combo][c] > max(res["H4"][c], res[part][c]) for c in ("late", "holdout"))
              and res[combo]["sr"] > res[best]["sr"] and res[combo]["neg"] < res["H4"]["neg"])
        print(f"  {combo}: {'PASSA' if ok else 'reprovado'}")


if __name__ == "__main__":
    main()
