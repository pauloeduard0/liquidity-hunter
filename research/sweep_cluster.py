"""Sweep Cluster — does a *run* of sweeps on the same side mean anything?

The observation under test
--------------------------
Several ``LIQUIDITY_SWEEP`` events on the same side, close together in time
(the BTCUSDT M15 2026-08-18/19 picture: three ``Sweep ▼`` in a row, then a
vertical rally). The claim is that the level "is respected" afterwards — i.e.
the cluster's extreme holds and price expands away from it.

What is measured
----------------
For every cluster of exactly ``n`` same-direction sweeps inside a
``--window`` candle span (a cluster is *closed* — no further same-side sweep
within the window after the last one), taken at the close of the last sweep:

* ``held``  — the cluster's swept extreme was never *closed* through in the
  next H candles ("price respects it").
* ``MFE/MAE`` in R, R = entry → cluster extreme, in the direction the sweeps
  argue for (a ▼ sweep takes lows → bullish).
* ``hit 2R`` before −1R.

Baselines, direction-matched (the lesson of ``raid_reversal.py``: an unmatched
control measures drift, not the event). For each cluster event a random
candle in the same series is drawn and framed **in the same direction** with
an ATR-scaled R of the same size, and n=1 / n=2 clusters are reported as
their own arms — the honest question is not "is a sweep followed by a move?"
but "does the *third* sweep add anything over the first?".

Usage
-----
    poetry run python research/sweep_cluster.py
    poetry run python research/sweep_cluster.py --symbols BTCUSDT ETHUSDT \
        --timeframes 15m 1h --window 30 --horizons 5 10 20 40
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent

BEAR = MarketDirection.BEARISH
BULL = MarketDirection.BULLISH


@dataclass
class Ev:
    arm: str            # "n1" | "n2" | "n3+" | "random"
    symbol: str
    timeframe: str
    direction: MarketDirection   # direction the sweeps argue FOR
    entry_index: int
    entry: float
    stop: float
    r: float
    held: dict[int, bool] = field(default_factory=dict)
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    hit_2r: bool | None = None


def _atr(candles: Sequence[Candle]) -> float:
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    return statistics.mean(trs) if trs else 0.0


def _measure(ev: Ev, candles: Sequence[Candle], horizons: Sequence[int]) -> Ev:
    i0 = ev.entry_index
    for h in horizons:
        w = candles[i0 + 1 : i0 + 1 + h]
        if not w:
            continue
        hi = max(c.high for c in w)
        lo = min(c.low for c in w)
        if ev.direction is BULL:
            ev.mfe[h] = (hi - ev.entry) / ev.r
            ev.mae[h] = (ev.entry - lo) / ev.r
            ev.held[h] = all(c.close > ev.stop for c in w)
        else:
            ev.mfe[h] = (ev.entry - lo) / ev.r
            ev.mae[h] = (hi - ev.entry) / ev.r
            ev.held[h] = all(c.close < ev.stop for c in w)
    tgt = ev.entry + 2 * ev.r if ev.direction is BULL else ev.entry - 2 * ev.r
    for c in candles[i0 + 1 :]:
        stopped = c.low <= ev.stop if ev.direction is BULL else c.high >= ev.stop
        reached = c.high >= tgt if ev.direction is BULL else c.low <= tgt
        if stopped:
            ev.hit_2r = False
            break
        if reached:
            ev.hit_2r = True
            break
    return ev


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    window: int,
    horizons: Sequence[int],
    min_r_atr: float,
    random_reps: int,
    rng: random.Random,
) -> list[Ev]:
    data = load_dashboard_data(symbol=symbol, timeframe=timeframe, limit=limit)
    candles = data.candles
    if len(candles) < 100:
        return []
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = _atr(candles)
    min_r = min_r_atr * atr
    max_h = max(horizons)

    sweeps: list[tuple[int, MarketDirection]] = []
    for e in data.internal_structure_events:
        if e.provisional or e.event is not StructureEvent.LIQUIDITY_SWEEP:
            continue
        i = idx_of.get(e.timestamp)
        if i is not None:
            sweeps.append((i, e.direction))
    sweeps.sort()

    # group same-direction sweeps into closed clusters
    clusters: list[list[int]] = []
    dirs: list[MarketDirection] = []
    for i, d in sweeps:
        if clusters and dirs[-1] is d and i - clusters[-1][-1] <= window:
            clusters[-1].append(i)
        else:
            clusters.append([i])
            dirs.append(d)

    out: list[Ev] = []
    for members, sweep_dir in zip(clusters, dirs, strict=True):
        n = len(members)
        arm = "n1" if n == 1 else "n2" if n == 2 else "n3+"
        # a ▼ sweep takes lows -> argues bullish
        d = BULL if sweep_dir is BEAR else BEAR
        ei = members[-1]
        if ei + max_h >= len(candles):
            continue
        seg = candles[members[0] : ei + 1]
        extreme = min(c.low for c in seg) if d is BULL else max(c.high for c in seg)
        entry = candles[ei].close
        r = abs(entry - extreme)
        if r < min_r:
            continue
        out.append(_measure(
            Ev(arm, symbol, timeframe.value, d, ei, entry, extreme, r), candles, horizons
        ))
        # direction-matched random control, same R
        for _ in range(random_reps):
            ri = rng.randrange(50, len(candles) - max_h - 1)
            e2 = candles[ri].close
            s2 = e2 - r if d is BULL else e2 + r
            out.append(_measure(
                Ev("random", symbol, timeframe.value, d, ri, e2, s2, r), candles, horizons
            ))
    return out


def report(events: Sequence[Ev], horizons: Sequence[int]) -> None:
    arms = ["n1", "n2", "n3+", "random"]
    print(f"\n{'arm':>7} {'N':>5} " + " ".join(
        f"{'held@'+str(h):>9} {'MFE@'+str(h):>8} {'MAE@'+str(h):>8}" for h in horizons
    ) + f" {'hit2R':>7}")
    for arm in arms:
        rows = [e for e in events if e.arm == arm]
        if not rows:
            continue
        cells = []
        for h in horizons:
            held = [e.held[h] for e in rows if h in e.held]
            mfe = [e.mfe[h] for e in rows if h in e.mfe]
            mae = [e.mae[h] for e in rows if h in e.mae]
            cells.append(
                f"{(sum(held)/len(held)*100 if held else 0):>8.0f}% "
                f"{(statistics.mean(mfe) if mfe else 0):>8.2f} "
                f"{(statistics.mean(mae) if mae else 0):>8.2f}"
            )
        res = [e.hit_2r for e in rows if e.hit_2r is not None]
        hit = f"{sum(res)/len(res)*100:>6.0f}%" if res else "     --"
        print(f"{arm:>7} {len(rows):>5} " + " ".join(cells) + f" {hit}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    p.add_argument("--limit", type=int, default=1200)
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20, 40])
    p.add_argument("--min-r-atr", type=float, default=0.5)
    p.add_argument("--random-reps", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    rng = random.Random(a.seed)

    allev: list[Ev] = []
    for s in a.symbols:
        for tf in a.timeframes:
            try:
                evs = run_combo(
                    s, TimeFrame(tf), limit=a.limit, window=a.window,
                    horizons=a.horizons, min_r_atr=a.min_r_atr,
                    random_reps=a.random_reps, rng=rng,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {s} {tf}: {exc}")
                continue
            real = [e for e in evs if e.arm != "random"]
            print(f"  {s} {tf}: {len(real)} clusters "
                  f"(n3+: {sum(1 for e in real if e.arm == 'n3+')})")
            allev.extend(evs)
    report(allev, a.horizons)


if __name__ == "__main__":
    main()
