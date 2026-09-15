"""K16: alvo maior na retomada da VWAP do Tide no D1 -- 4R, 5R ou sem alvo?

De onde vem
-----------
K10 confirmou 3R contra 2R (6/7 anos) no H4 e o D1 herdou o 3R (K13-K15).
Alvos acima de 3R nunca foram medidos. Braços V0 e V_hunt, uma posição por
vez, custo 0,13%, stop no extremo do recuo, horizonte 60 candles.

Saídas, fixadas ANTES de rodar
------------------------------
    X3   alvo 3R (o atual)
    X4   alvo 4R
    X5   alvo 5R
    XN   sem alvo: só stop ou fechamento a mercado no candle 60

Regra (a mesma que aprovou o 3R no K10)
---------------------------------------
Uma saída substitui o X3 se, no braço V0:
  1. bater o X3 em SR diário em pelo menos 2/3 dos anos com n >= 100 nas duas;
  2. tiver R total > 0 em late E em holdout;
  3. tiver SR diário >= X3 no conjunto todo.
Se mais de uma passar, fica a de maior SR diário no conjunto todo. V_hunt é
reportado e segue a mesma regra à parte (informativo).

Predição escrita antes: X4 empata com X3 (ganha em ~metade dos anos); X5 e XN
perdem em SR diário por dependerem de poucos trades.

Run:
    poetry run python -m research.tide_reclaim_d1_targets
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from liquidity_hunter.app.dashboard_data import _VWAP_ANCHOR_PERIOD
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.indicators import vwap
from research.block_reclaim_hunt_context import LIMIT
from research.quality_features import CachedProvider
from research.tide_reclaim_btc_replication import CUTS
from research.tide_reclaim_d1_robust import D1_BASELINE
from research.tide_reclaim_h4_trades import fmt, one_at_a_time, simulate, stats
from research.tide_reclaim_setup import MAX_PULLBACK

TF = TimeFrame.D1
EXITS = ("X3", "X4", "X5", "XN")
ARMS = ("V0", "V_hunt")


def rebuild(symbol: str, rows: list[dict]) -> list[dict]:
    candles = CachedProvider().get_ohlcv(symbol, TF, LIMIT)
    tide = vwap(candles, symbol=symbol, timeframe=TF, anchor=_VWAP_ANCHOR_PERIOD[TF])
    pts = {p.timestamp: p.value for p in tide.points} if tide else {}
    vw = [pts.get(c.timestamp) for c in candles]
    idx = {c.timestamp: k for k, c in enumerate(candles)}
    out = []
    for r in rows:
        i = idx.get(datetime.fromisoformat(r["ts"]))
        if i is None:
            continue
        seg = candles[max(0, i - r["run"]) : i + 1][-(MAX_PULLBACK + 1):]
        stop = min(c.low for c in seg) if r["up"] else max(c.high for c in seg)
        trades = {x: res for x in EXITS if (res := simulate(candles, vw, i, r["up"], stop, x))}
        if trades:
            out.append({**r, "i": i, "trades": trades,
                        "day": candles[i].timestamp.date().isoformat()})
    return out


def main() -> None:
    payload = json.loads(D1_BASELINE.read_text())
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in payload["rows"]:
        if "V0" in r["arms"]:
            by_symbol[r["symbol"]].append(r)
    rows = [x for s, rs in by_symbol.items() for x in rebuild(s, rs)]
    days = sorted({r["day"] for r in rows})
    months = (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days / 30.4
    print(f"\nK16 - ALVO NO D1 | {len(rows)} gatilhos, ~{months:.0f} meses")
    for arm in ARMS:
        pop = [r for r in rows if arm in r["arms"]]
        taken = {x: one_at_a_time(pop, x) for x in EXITS}
        print(f"\n=== {arm} ===")
        res = {}
        for x in EXITS:
            t = taken[x]
            target_hits = sum(1 for r in t if r["net_x"] > 1.5) / len(t)
            print(f"  {x}  ~{len(t) / months:.0f} trades/mes, "
                  f"{target_hits:.0%} dos trades fecham acima de +1,5R")
            for c in ("late", "holdout", "tudo"):
                st = stats([r for r in t if CUTS[c](r)], days)
                res[(x, c)] = st
                print(f"     [{c:7s}] {fmt(st)}")
        years = sorted({r["day"][:4] for r in pop})
        print("  por ano (R total / SR diario):")
        for y in years:
            cells = []
            for x in EXITS:
                st = stats([r for r in taken[x] if r["day"][:4] == y], days)
                res[(x, y)] = st
                cells.append(f"{x} {st['total']:+6.1f}/{st['sr']:+.2f}" if st else f"{x} -")
            print(f"     {y}  " + "  ".join(cells))
        passing = []
        for x in EXITS[1:]:
            valid = [y for y in years
                     if res[(x, y)] and res[("X3", y)]
                     and res[(x, y)]["n"] >= 100 and res[("X3", y)]["n"] >= 100]
            wins = sum(res[(x, y)]["sr"] > res[("X3", y)]["sr"] for y in valid)
            c1 = bool(valid) and wins >= 2 * len(valid) / 3
            c2 = all(res[(x, c)] and res[(x, c)]["total"] > 0 for c in ("late", "holdout"))
            c3 = res[(x, "tudo")]["sr"] >= res[("X3", "tudo")]["sr"]
            ok = c1 and c2 and c3
            if ok:
                passing.append(x)
            print(f"  {x} vs X3: vence {wins}/{len(valid)} anos; total>0 late+holdout {c2}; "
                  f"SR tudo >= X3 {c3} => {'SUBSTITUI' if ok else 'nao substitui'}")
        best = max(passing, key=lambda x: res[(x, "tudo")]["sr"]) if passing else "X3"
        print(f"  => alvo de {arm}: {best}")


if __name__ == "__main__":
    main()
