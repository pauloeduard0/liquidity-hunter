"""The setup as the trader actually takes it: exit grid, freshness, and cost.

Three things the earlier passes got wrong or left out, all of which turn on the
same discipline -- report the whole payoff, never one tail of it:

* **the exit is the trader's**, and it is a fixed multiple ("2x, 2.5x or 3x").
  An edge that exists at one multiple and vanishes at its neighbours is a
  calibration. The grid is reported whole so that shows.
* **the block must be on its first visit** ("pelo menos pela primeira vez").
  Freshness was tested once before against `hit 2R` alone and appeared to hurt;
  that metric counts wins and ignores stops, which is exactly how the order
  block itself was wrongly dismissed.
* **cost is charged in R**. A round trip is a fixed fraction of *price*, so what
  it costs the trade depends on how wide that trade's stop was. Charging it per
  trade rather than as an average is the only honest way: a tight-stop trade
  pays far more of its R away than a wide-stop one.

Usage
-----
    poetry run python research/vwap_exit_grid.py --trades final15.json
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from research._symbols import HOLDOUT, SEARCH

#: Binance USDT-M round trips, from the friendliest plausible to the realistic.
#: The stop exit is a stop-market and therefore always taker, and it fires on
#: roughly half of these trades, so the "taker+maker" column is a floor nobody
#: actually trades at -- it is there to show what the edge would need.
COSTS: dict[str, float] = {
    "taker+maker 0.06%": 0.0006,
    "taker+taker 0.10%": 0.0010,
    "+slippage   0.15%": 0.0015,
}
GRID = ("1.0", "1.5", "2.0", "2.5", "3.0")


def _net(rows: Sequence[dict], target: str, cost: float) -> float:
    return st.mean(r["r_grid"][target] - cost / r["r_pct"] for r in rows)


def _t_stat(rows: Sequence[dict], target: str, cost: float) -> float:
    vals = [r["r_grid"][target] - cost / r["r_pct"] for r in rows]
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return st.mean(vals) / (sd / len(vals) ** 0.5) if sd > 0 else 0.0


def table(rows: Sequence[dict], label: str) -> None:
    if len(rows) < 100:
        print(f"\n{label}: only {len(rows)} trades, skipped")
        return
    med = st.median(r["r_pct"] for r in rows)
    print(f"\n{label}  (n={len(rows)}, R median {med:.2%} of price)")
    print(f"{'exit':>10} {'gross':>9} " + " ".join(f"{name:>19}" for name in COSTS))
    for target in GRID:
        gross = st.mean(r["r_grid"][target] for r in rows)
        cells = [
            f"{_net(rows, target, c):>+9.4f} t{_t_stat(rows, target, c):>+5.1f}"
            for c in COSTS.values()
        ]
        print(f"{target + 'R':>10} {gross:>+9.4f} " + " ".join(f"{c:>19}" for c in cells))


def thirds(rows: Sequence[dict], target: str, cost: float, label: str) -> None:
    ts = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    lo, hi = min(ts), max(ts)
    span = (hi - lo).days or 1
    cells = []
    for k in range(3):
        part = [
            r for r in rows
            if k * span / 3 <= (datetime.fromisoformat(r["timestamp"]) - lo).days
            < (k + 1) * span / 3
        ]
        cells.append(f"{_net(part, target, cost):>+8.4f} (n={len(part)})"
                     if len(part) >= 50 else "     --")
    print(f"  {label:>28} " + "  ".join(cells))


def placebo_table(rows: Sequence[dict], cost: float) -> None:
    """The order block against the same reclaim with no block behind it.

    The headline reading of the whole study, and the only comparison that
    settles whether the block contributes: a random control shows the setup
    beats noise, this shows the *condition* earns its place. Reported as the
    full payoff -- reaching the target, being stopped, and the mean R that
    balances them -- because a win-only rate is what made the block look null
    for most of this investigation.
    """
    print(f"\nblock vs placebo (2R target, cost {cost:.2%} charged per trade)")
    print(f"{'arm':>10} {'n':>6} {'hit 2R':>8} {'stopped':>8} {'open':>7} "
          f"{'gross':>9} {'net':>9} {'t':>6}")
    for arm, label in (("vwap", "placebo"), ("eql", "eql"), ("ob", "block")):
        s = [r for r in rows if r["arm"] == arm]
        if len(s) < 50:
            continue
        hit = sum(1 for r in s if r["r_grid"]["2.0"] == 2.0) / len(s)
        stop = sum(1 for r in s if r["r_grid"]["2.0"] == -1.0) / len(s)
        gross = st.mean(r["r_grid"]["2.0"] for r in s)
        print(f"{label:>10} {len(s):>6} {hit:>7.1%} {stop:>7.1%} "
              f"{1 - hit - stop:>6.1%} {gross:>+9.4f} "
              f"{_net(s, '2.0', cost):>+9.4f} {_t_stat(s, '2.0', cost):>+6.1f}")
    print("   a 2:1 payoff breaks even at a 33.3% hit rate")


def by_direction(rows: Sequence[dict], cost: float) -> None:
    """Both arms split by side, because only the pair settles the asymmetry.

    The block arm is stronger short than long in every sample so far. That is
    either the mechanism (a crowded, funded long side liquidates downward) or
    the period (the window drifted). The placebo answers it: if the placebo is
    asymmetric the same way, the side is the period showing through, and the
    block contributes the same amount either way.
    """
    print(f"\ndirection, 2R target, cost {cost:.2%}")
    print(f"{'arm':>10} {'side':>8} {'n':>6} {'hit 2R':>8} {'net':>9} {'t':>6}")
    for arm, label in (("vwap", "placebo"), ("ob", "block")):
        for side in ("bullish", "bearish"):
            s = [r for r in rows if r["arm"] == arm and r.get("direction") == side]
            if len(s) < 50:
                continue
            hit = sum(1 for r in s if r["r_grid"]["2.0"] == 2.0) / len(s)
            print(f"{label:>10} {side:>8} {len(s):>6} {hit:>7.1%} "
                  f"{_net(s, '2.0', cost):>+9.4f} {_t_stat(s, '2.0', cost):>+6.1f}")


def by_accumulation(rows: Sequence[dict], cost: float, edges: Sequence[int]) -> None:
    """The gradient asked *within* one timeframe, where nothing else moves.

    The lift was seen to rise with the VWAP's accumulation across four earlier
    measurements -- but those varied timeframe, anchor and accumulation at the
    same time, so they cannot say which of the three carried it. Here only the
    accumulation moves. The mechanism predicts the rise: a six-candle average
    is nobody's break-even, a ninety-candle one is a session's worth of
    positions. The placebo is split the same way, since a session's late hours
    could simply behave differently for every candle in them.
    """
    print(f"\nVWAP accumulation at the entry, 2R target, cost {cost:.2%}")
    print(f"{'arm':>10} {'candles':>12} {'n':>6} {'hit 2R':>8} {'net':>9} {'t':>6}")
    bounds = [0, *edges, 10**9]
    for arm, label in (("vwap", "placebo"), ("ob", "block")):
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            s = [
                r for r in rows
                if r["arm"] == arm
                and r.get("vwap_candles") is not None
                and lo <= r["vwap_candles"] < hi
            ]
            if len(s) < 50:
                continue
            hit = sum(1 for r in s if r["r_grid"]["2.0"] == 2.0) / len(s)
            span = f"{lo}-{hi - 1}" if hi < 10**9 else f"{lo}+"
            print(f"{label:>10} {span:>12} {len(s):>6} {hit:>7.1%} "
                  f"{_net(s, '2.0', cost):>+9.4f} {_t_stat(s, '2.0', cost):>+6.1f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--stability-target", default="2.0")
    p.add_argument("--stability-cost", type=float, default=0.0010)
    p.add_argument("--max-r-atr", type=float, default=None,
                   help="keep only entries whose stop is within N x ATR(14) -- "
                        "the condition the edge actually lives in")
    p.add_argument("--min-r-atr", type=float, default=None,
                   help="drop entries whose stop is tighter than N x ATR(14). "
                        "Cost is a fixed fraction of price and R is not, so "
                        "the tightest stops pay the most of themselves away: "
                        "at 0.1 ATR a round trip can cost several R. This is "
                        "the reader's floor, applied here rather than in the "
                        "scan so that moving it does not need a re-run")
    p.add_argument("--sample", choices=("all", "search", "holdout"), default="all",
                   help="which half of research/_symbols.py to report on. The "
                        "split is a hash of the symbol name, recorded so a "
                        "corrected rule can be compared on the same halves")
    p.add_argument("--accumulation-edges", type=int, nargs="*", default=(32, 64),
                   help="bucket boundaries for the VWAP-accumulation split, in "
                        "candles; the M15 session runs to 96")
    args = p.parse_args()

    rows = [r for r in json.loads(Path(args.trades).read_text()) if r.get("r_grid")]
    if args.max_r_atr is not None:
        rows = [r for r in rows
                if r.get("r_atr") is not None and r["r_atr"] <= args.max_r_atr]
    if args.min_r_atr is not None:
        rows = [r for r in rows
                if r.get("r_atr") is not None and r["r_atr"] >= args.min_r_atr]
    if args.sample != "all":
        keep = set(SEARCH if args.sample == "search" else HOLDOUT)
        rows = [r for r in rows if r["symbol"] in keep]
    ts = sorted(datetime.fromisoformat(r["timestamp"]) for r in rows)
    print(f"{len(rows)} trades, {ts[0].date()} .. {ts[-1].date()} "
          f"({(ts[-1] - ts[0]).days} days), sample: {args.sample}")

    placebo_table(rows, args.stability_cost)
    by_direction(rows, args.stability_cost)
    by_accumulation(rows, args.stability_cost, args.accumulation_edges)

    for arm in ("vwap", "ob", "eql"):
        table([r for r in rows if r["arm"] == arm], f"arm: {arm}")

    ob = [r for r in rows if r["arm"] == "ob"]
    table([r for r in ob if r.get("first_test")], "arm: ob -- FIRST visit only")
    table([r for r in ob if r.get("first_test") is False], "arm: ob -- a later return")

    print(f"\nstability in thirds, net at {args.stability_target}R, "
          f"cost {args.stability_cost:.2%}")
    for arm in ("vwap", "ob", "eql"):
        sel = [r for r in rows if r["arm"] == arm]
        if len(sel) >= 150:
            thirds(sel, args.stability_target, args.stability_cost, arm)
    fresh = [r for r in ob if r.get("first_test")]
    if len(fresh) >= 150:
        thirds(fresh, args.stability_target, args.stability_cost, "ob first visit")


if __name__ == "__main__":
    main()
