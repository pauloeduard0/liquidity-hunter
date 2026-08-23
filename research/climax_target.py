"""Does a prior VSA climax mark a level the reclaim can reach?

The idea came from a reader doing visual backtests: a climax bar is where the
tape already changed hands violently once, and a move might travel *to* it and
stop there. If that holds, the climax names a target the fixed grid cannot --
one that is a property of the chart rather than a multiple of the stop.

Two readings of it, measured separately because they are different claims:

* **static** -- at entry, the nearest climax *price level* already printed
  beyond the entry in the trade's direction. Known before the trade starts, so
  it is a target in the ordinary sense. The exit is its first touch.
* **dynamic** -- exit on the close of the first candle after entry that prints
  an opposing climax. Not a level but an event: "leave when the tape says the
  move is spent."

Both are free of lookahead. The static level exists before entry by
construction; the dynamic one is read on the candle it closes.

What the comparison has to be
-----------------------------
A climax target is only interesting if it beats the fixed 2R **on the same
trades**. Two ways to get that wrong, both avoided here:

* a trade with no climax available has to be reported, not dropped. Dropping it
  silently selects for charts that happened to have one, which is a property of
  the chart's recent violence rather than of the rule. Every table below is
  computed on the subset that *has* a target, with the 2R baseline recomputed
  on that same subset.
* the distance to the climax varies per trade, so the comparison is in R after
  cost -- never in hit rate, which a nearer target inflates for free.

Usage
-----
    poetry run python research/climax_target.py --trades trades15m_either2.json
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import VSAPattern
from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.psychology.analyzers.volume_spread import VolumeSpreadAnalyzer
from research._paginated import PaginatedFuturesProvider
from research._symbols import HOLDOUT, SEARCH
from research.spread_trades import DEFAULT_TAKER_FEE

CLIMAX = (VSAPattern.SELLING_CLIMAX, VSAPattern.BUYING_CLIMAX)
#: How far back a climax may be and still be the level this move is running at.
MAX_AGE_CANDLES = 200
#: A target nearer than this is not a target, it is the entry candle's noise.
MIN_TARGET_R = 0.5
#: Beyond this the "target" is a horizon, and the trade is really open-ended.
MAX_TARGET_R = 12.0


def static_target(
    signals: Sequence[tuple[int, float]], i: int, entry: float, *, bull: bool,
) -> float | None:
    """The nearest climax level already printed beyond the entry, in R terms.

    `signals` is `(candle index, price level)`. Only levels the trade is moving
    *towards* count; one behind the entry is not a target.
    """
    ahead = [
        lvl for j, lvl in signals
        if i - MAX_AGE_CANDLES <= j < i and ((lvl > entry) if bull else (lvl < entry))
    ]
    if not ahead:
        return None
    return min(ahead) if bull else max(ahead)


def walk(
    candles: Sequence[Candle], i: int, entry: float, r: float, target: float | None,
    climax_idx: Sequence[int], *, bull: bool, horizon: int,
) -> dict[str, float | None]:
    """Price the fixed 2R, the static climax target and the dynamic exit."""
    out: dict[str, float | None] = {"fixed2": None, "static": None, "dyn": None}
    tgt_r = None
    if target is not None:
        tgt_r = ((target - entry) if bull else (entry - target)) / r
        if not (MIN_TARGET_R <= tgt_r <= MAX_TARGET_R):
            tgt_r = None
    live = {"fixed2": True, "static": tgt_r is not None, "dyn": True}
    climax_after = set(climax_idx)
    for j in range(i + 1, min(i + 1 + horizon, len(candles))):
        c = candles[j]
        adv_r = ((entry - c.low) if bull else (c.high - entry)) / r
        fav_r = ((c.high - entry) if bull else (entry - c.low)) / r
        if live["fixed2"]:
            if adv_r >= 1.0:
                out["fixed2"], live["fixed2"] = -1.0, False
            elif fav_r >= 2.0:
                out["fixed2"], live["fixed2"] = 2.0, False
        if live["static"] and tgt_r is not None:
            if adv_r >= 1.0:
                out["static"], live["static"] = -1.0, False
            elif fav_r >= tgt_r:
                out["static"], live["static"] = tgt_r, False
        if live["dyn"]:
            if adv_r >= 1.0:
                out["dyn"], live["dyn"] = -1.0, False
            elif j in climax_after:
                closed = ((c.close - entry) if bull else (entry - c.close)) / r
                out["dyn"], live["dyn"] = closed, False
    j = min(i + horizon, len(candles) - 1)
    mark = ((candles[j].close - entry) if bull else (entry - candles[j].close)) / r
    for k, alive in live.items():
        if alive:
            out[k] = mark
    if tgt_r is None:
        out["static"] = None
    out["target_r"] = tgt_r
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--arm", default="ob-either")
    p.add_argument("--max-r-atr", type=float, default=1.0)
    p.add_argument("--horizon", type=int, default=40)
    p.add_argument("--spreads", default="research/measured_spreads.json")
    p.add_argument("--taker-fee", type=float, default=DEFAULT_TAKER_FEE)
    args = p.parse_args()

    floor = json.loads(open(args.spreads).read())
    rows = [
        r for r in json.loads(open(args.trades).read())
        if r["arm"] == args.arm and r["symbol"] in floor and r.get("r_pct")
        and r.get("r_atr") is not None and r["r_atr"] <= args.max_r_atr
    ]
    tf = TimeFrame(rows[0]["timeframe"])
    print(f"{len(rows)} entradas · {tf.value} · horizonte {args.horizon}\n")

    provider = PaginatedFuturesProvider()
    analyzer = VolumeSpreadAnalyzer()
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    priced: list[dict] = []
    for n, (symbol, trades) in enumerate(sorted(by_symbol.items()), 1):
        try:
            candles = provider.get_ohlcv(symbol, tf, 75_000)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {symbol}: {exc}")
            continue
        idx = {c.timestamp: k for k, c in enumerate(candles)}
        vd = volume_delta_series(candles)
        sigs = analyzer.analyze(list(candles), vd)
        bull_lv = [
            (idx[s.timestamp], s.price_level) for s in sigs
            if s.pattern in CLIMAX and s.timestamp in idx
        ]
        cl_idx = [j for j, _ in bull_lv]
        for r in trades:
            i = idx.get(datetime.fromisoformat(r["timestamp"]))
            if i is None:
                continue
            entry = candles[i].close
            rr = entry * r["r_pct"]
            bull = r["direction"] == "bullish"
            tgt = static_target(bull_lv, i, entry, bull=bull)
            out = walk(
                candles, i, entry, rr, tgt, [j for j in cl_idx if j > i],
                bull=bull, horizon=args.horizon,
            )
            out.update(
                symbol=symbol,
                cost=(2 * args.taker_fee + floor[symbol]["spread"]) / r["r_pct"],
            )
            priced.append(out)
        if n % 20 == 0:
            print(f"  ...{n} simbolos")

    have = [x for x in priced if x["static"] is not None]
    print(f"\n{len(priced)} precificadas · {len(have)} com alvo de climax "
          f"({len(have)/max(len(priced),1):.0%})")
    if have:
        tr = [x["target_r"] for x in have]
        print(f"alvo de climax em R: mediana {st.median(tr):.2f}  "
              f"p10 {sorted(tr)[len(tr)//10]:.2f}  p90 {sorted(tr)[9*len(tr)//10]:.2f}")

    def show(label: str, sel: list[dict], keys: Sequence[str]) -> None:
        if len(sel) < 30:
            print(f"{label:<30} n={len(sel)} pequena demais")
            return
        print(f"{label:<30} n={len(sel)}")
        for k in keys:
            vals = [x[k] for x in sel]
            net = [v - x["cost"] for v, x in zip(vals, sel, strict=True)]
            m = st.fmean(net)
            t = m / (st.stdev(net) / len(net) ** 0.5)
            print(f"    {k:<10} bruto {st.fmean(vals):>+7.3f}  liquido {m:>+7.3f}  t {t:>+5.1f}")

    print("\n--- no subconjunto QUE TEM alvo (a comparacao justa) ---")
    show("todas", have, ("fixed2", "static", "dyn"))
    show("  busca", [x for x in have if x["symbol"] in SEARCH], ("fixed2", "static", "dyn"))
    show("  holdout", [x for x in have if x["symbol"] in HOLDOUT], ("fixed2", "static", "dyn"))
    print("\n--- saida dinamica em TODAS (ela nao precisa de alvo) ---")
    show("todas", priced, ("fixed2", "dyn"))


if __name__ == "__main__":
    main()
