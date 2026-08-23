"""Does letting the winners run rescue the bands a fixed 2R target throws away?

The question came from a chart, not from the data. A reader showed a 5m block
reclaim that ran **9.83R** and never came near its stop -- a trade this study
had never counted, because its stop sat 1.87 local ATR from the entry and every
table reported here caps that at 1.0. Measuring its band closed the first half
of the question: at 1.5-2.0 ATR the hit rate is 26.8% against the 33.3% a 2:1
payoff needs, and gross is negative at every target from 1R to 3R.

The second half stayed open, and it is the one this file answers. **The grid
stopped at 3R.** A band that wins rarely can still pay if the wins are enormous,
and a fixed target is exactly the exit that cannot collect that: capping the
reader's trade at 2R discards 7.83R of it. Counting hits at a target measures
one point of the distribution; a runner is a claim about its tail.

What is measured
----------------
For every entry already dated by `vwap_ob_pinbar.py`, the forward path is walked
candle by candle and priced under three exit families:

* **fixed** -- the first touch of NR, the grid extended out to 10R;
* **trail** -- arm at AR, then follow the excursion by BR, no cap at all;
* **MFE** -- what the path actually offered, as a distribution rather than a
  count. This is not an exit anybody can take (it needs the future), and it is
  reported only as the ceiling every real rule is measured against.

Two horizons, both stated: the 40 candles the rest of the study uses, and 200.
Extending the horizon is itself a degree of freedom -- a trade held sixteen
hours on 5m is not the trade held three, it carries funding and it carries
overnight gaps -- so the short horizon stays in the table beside the long one
rather than being quietly replaced by it.

Costs are charged per trade from `measured_spreads.json`, in R, exactly as
`vwap_exit_grid.py` charges them: a round trip is a fixed fraction of price, so
what it takes out of a trade depends on how wide that trade's stop was.

Adverse-first within the candle
-------------------------------
When a candle's range covers both the stop and the next target, the stop is
credited. It is the same conservative tie-break `_managed` uses in the main
study, and it biases every runner column *down* -- the direction an honest
measurement of one's own hypothesis should lean.

Usage
-----
    poetry run python research/runner_exits.py --trades trades5m_either.json
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain import Candle, TimeFrame
from research._paginated import PaginatedFuturesProvider
from research.spread_trades import DEFAULT_TAKER_FEE

#: Fixed targets, the study's grid extended past the 3R it used to stop at.
FIXED = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
#: `(arm, follow)` -- start trailing once the excursion reaches `arm`, then keep
#: the stop `follow` behind the best price seen. No target, so no cap.
TRAILS = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (3.0, 2.0))
#: Chandelier: `(atr period, multiplier)`. The stop hangs `mult x ATR` under
#: the highest high since entry (over it, for a short) and the ATR is recomputed
#: every candle -- so unlike the R-unit trails above, which freeze their
#: distance at entry, this one *widens* when the move gets violent. That is the
#: whole claim: it should keep the stop clear of a fast leg's own noise.
CHANDELIER = ((22, 3.0), (22, 2.0), (22, 1.5), (14, 3.0), (14, 2.0))

#: The bands the r_atr question is actually about.
BANDS = ((0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99.0))


def atr_series(candles: Sequence[Candle], period: int) -> list[float | None]:
    """Wilder's ATR, 1:1 with `candles`, None until it is defined."""
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return out
    trs = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    prev_atr = sum(trs[1 : period + 1]) / period
    out[period] = prev_atr
    for i in range(period + 1, len(candles)):
        prev_atr = (prev_atr * (period - 1) + trs[i]) / period
        out[i] = prev_atr
    return out


def walk(
    candles: Sequence[Candle], i: int, entry: float, r: float, *, bull: bool,
    horizon: int, atrs: dict[int, Sequence[float | None]] | None = None,
) -> tuple[float, dict[str, float]]:
    """Price one entry's forward path under every exit family.

    Returns the path's MFE in R and a payoff per rule. A position still open at
    the horizon is marked to market there -- never dropped, since dropping the
    unresolved ones keeps only the trades that already went somewhere.

    Within a candle the adverse extreme is credited first, and the trailing
    level is the one the *previous* candles had earned: this candle's own new
    high cannot retroactively raise the stop that this candle then hits. Both
    choices bias the runner columns down.
    """
    best = 0.0
    out: dict[str, float] = {}
    live_fixed = dict.fromkeys(FIXED, True)
    live_trail = dict.fromkeys(TRAILS, True)
    live_chand = dict.fromkeys(CHANDELIER, atrs is not None)
    peak = 0.0  # best favourable excursion in R, for the chandelier anchor
    for j in range(i + 1, min(i + 1 + horizon, len(candles))):
        c = candles[j]
        adv_r = ((entry - c.low) if bull else (c.high - entry)) / r
        fav_r = ((c.high - entry) if bull else (entry - c.low)) / r

        for t in FIXED:
            if not live_fixed[t]:
                continue
            if adv_r >= 1.0:
                out[f"fixed{t}"], live_fixed[t] = -1.0, False
            elif fav_r >= t:
                out[f"fixed{t}"], live_fixed[t] = t, False

        for arm, follow in TRAILS:
            if not live_trail[(arm, follow)]:
                continue
            level = -1.0 if best < arm else best - follow
            if -adv_r <= level:
                out[f"trail{arm}/{follow}"] = level
                live_trail[(arm, follow)] = False

        if atrs is not None:
            for period, mult in CHANDELIER:
                if not live_chand[(period, mult)]:
                    continue
                a = atrs[period][j - 1] if j - 1 < len(atrs[period]) else None
                if a is None:
                    continue
                # the anchor is the excursion the PREVIOUS candles earned, and
                # the ATR is the previous close's -- this candle cannot move the
                # stop it is about to hit.
                level = peak - (mult * a) / r
                level = max(level, -1.0)  # never looser than the structural stop
                if -adv_r <= level:
                    out[f"chand{period}/{mult}"] = level
                    live_chand[(period, mult)] = False
        peak = max(peak, fav_r)
        best = max(best, fav_r)

    for t in FIXED:
        if live_fixed[t]:
            out[f"fixed{t}"] = _mark(candles, i, entry, r, bull=bull, horizon=horizon)
    for arm, follow in TRAILS:
        if live_trail[(arm, follow)]:
            out[f"trail{arm}/{follow}"] = _mark(
                candles, i, entry, r, bull=bull, horizon=horizon
            )
    for period, mult in CHANDELIER:
        if live_chand[(period, mult)]:
            out[f"chand{period}/{mult}"] = _mark(
                candles, i, entry, r, bull=bull, horizon=horizon
            )
    return best, out


def _mark(
    candles: Sequence[Candle], i: int, entry: float, r: float, *, bull: bool,
    horizon: int,
) -> float:
    j = min(i + horizon, len(candles) - 1)
    close = candles[j].close
    return ((close - entry) if bull else (entry - close)) / r


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--arm", default="ob-either")
    p.add_argument("--horizons", nargs="+", type=int, default=[40, 200])
    p.add_argument("--spreads", default="research/measured_spreads.json")
    p.add_argument("--taker-fee", type=float, default=DEFAULT_TAKER_FEE)
    args = p.parse_args()

    floor = json.loads(open(args.spreads).read())
    rows = [
        r for r in json.loads(open(args.trades).read())
        if r["arm"] == args.arm and r["symbol"] in floor and r.get("r_pct")
        and r.get("r_atr") is not None
    ]
    print(f"{len(rows)} entradas do braco {args.arm}\n")

    provider = PaginatedFuturesProvider()
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    tf = TimeFrame(rows[0]["timeframe"])
    priced: list[dict] = []
    for n, (symbol, trades) in enumerate(sorted(by_symbol.items()), 1):
        try:
            candles = provider.get_ohlcv(symbol, tf, 75_000)
        except Exception as exc:  # noqa: BLE001 - one symbol must not sink it
            print(f"  ! {symbol}: {exc}")
            continue
        idx = {c.timestamp: k for k, c in enumerate(candles)}
        atrs = {p: atr_series(candles, p) for p in {q for q, _ in CHANDELIER}}
        for r in trades:
            i = idx.get(datetime.fromisoformat(r["timestamp"]))
            if i is None:
                continue
            entry = candles[i].close
            rr = entry * r["r_pct"]
            bull = r["direction"] == "bullish"
            rec = {
                "symbol": symbol, "r_atr": r["r_atr"],
                "cost": (2 * args.taker_fee + floor[symbol]["spread"]) / r["r_pct"],
            }
            for h in args.horizons:
                mfe, out = walk(
                    candles, i, entry, rr, bull=bull, horizon=h, atrs=atrs
                )
                rec[f"mfe{h}"] = mfe
                for k, v in out.items():
                    rec[f"{k}@{h}"] = v
            priced.append(rec)
        if n % 20 == 0:
            print(f"  ...{n} simbolos")

    print(f"\n{len(priced)} entradas precificadas")
    for h in args.horizons:
        print(f"\n{'='*78}\nHORIZONTE {h} candles\n{'='*78}")
        for lo, hi in BANDS:
            sel = [x for x in priced if lo < x["r_atr"] <= hi] if lo else [
                x for x in priced if x["r_atr"] <= hi
            ]
            if len(sel) < 50:
                continue
            mfe = [x[f"mfe{h}"] for x in sel]
            cst = st.fmean(x["cost"] for x in sel)
            p90 = sorted(mfe)[int(0.9 * len(mfe))]
            print(
                f"\n  r_atr {lo}-{hi}   n={len(sel)}   custo {cst:.3f}R"
                f"   MFE mediana {st.median(mfe):.2f}R  p90 {p90:.2f}R"
                f"  max {max(mfe):.1f}R"
            )
            print(f"    {'regra':<16} {'bruto':>8} {'liquido':>9} {'t':>7}")
            for key in (
                [f"fixed{t}" for t in FIXED]
                + [f"trail{a}/{b}" for a, b in TRAILS]
                + [f"chand{p}/{m}" for p, m in CHANDELIER]
            ):
                vals = [x[f"{key}@{h}"] for x in sel]
                net = [v - x["cost"] for v, x in zip(vals, sel, strict=True)]
                m = st.fmean(net)
                t = m / (st.stdev(net) / len(net) ** 0.5) if len(net) > 1 else 0.0
                mark = "  <<<" if m > 0 else ""
                print(f"    {key:<16} {st.fmean(vals):>+8.3f} {m:>+9.3f} {t:>+7.1f}{mark}")


if __name__ == "__main__":
    main()
