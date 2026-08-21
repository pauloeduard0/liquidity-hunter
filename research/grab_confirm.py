"""Grab Confirmation — is a `⚡` worth more after a second candle?

The observation under test
--------------------------
`LiquidityGrabOutcome` is read on the grabbing candle **alone**: the wick
took the resting orders and the close came back inside, so the level was
handed back (`REJECTED`, drawn `⚡`); a close beyond spends it (`SPENT`, `✕`).
The chart therefore paints a `⚡` on a candle that the *next* one closes
straight through — the level was never handed back at all, and the tombstone
said it was.

The proposal: confirm the rejection over N candles instead of one. That is
not free — waiting N-1 candles to label also means entering N-1 candles
later, and the grab's own snap-back is usually the fastest part of the move.
So the question is not "does waiting remove bad labels?" (it does, by
construction) but **"is the surviving `⚡`, read from where a reader could
actually act on it, a better observation than the immediate one?"**

What is measured
----------------
For each confirmation depth ``N`` an arm ``rej@N``: a grab whose candles
``g … g+N-1`` all closed on the inside of the taken level. Entry is the
**close of candle ``g+N-1``** — the candle the label becomes knowable on, so
no arm is credited with a move that happened before it existed (the lesson
of ``control_continuation.py`` / ``provisional_edge.py``). Direction is the
rejection's: a buy-side pool taken from below argues bearish.

* ``held``    — price never *closed* back through the taken level within H.
* ``MFE/MAE`` in R, R = entry → the grab window's swept extreme (the stop a
  reader would use), scale-free, so a widening of both tails reads as the
  volatility it is rather than as edge.
* ``hit 2R``  — 2R reached before −1R.

Every arm gets its own **direction-matched** random control drawn from the
same series with the same R (``rand@N``): an unmatched control measures the
period's drift, not the event.

Also reported, purely descriptively: the **falsification rate** — what share
of the `⚡` the chart draws today are closed through by candle 2, 3, …

Usage
-----
    poetry run python research/grab_confirm.py
    poetry run python research/grab_confirm.py --symbols BTCUSDT ETHUSDT \
        --timeframes 15m 1h --depths 1 2 3 --horizons 5 10 20 40
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import (
    LiquidityGrabOutcome,
    LiquiditySide,
    MarketDirection,
)

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH


@dataclass
class Ev:
    arm: str
    symbol: str
    timeframe: str
    direction: MarketDirection
    entry_index: int
    entry: float
    stop: float
    r: float
    level: float
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
            # the level was taken from above; it holds while nothing closes back below
            ev.held[h] = all(c.close > ev.level for c in w)
        else:
            ev.mfe[h] = (ev.entry - lo) / ev.r
            ev.mae[h] = (hi - ev.entry) / ev.r
            ev.held[h] = all(c.close < ev.level for c in w)
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


def _inside(candle: Candle, level: float, *, buy_side: bool) -> bool:
    """Whether the candle closed back on the pool's own side of the level."""
    return candle.close <= level if buy_side else candle.close >= level


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    limit: int,
    depths: Sequence[int],
    horizons: Sequence[int],
    min_r_atr: float,
    random_reps: int,
    rng: random.Random,
) -> tuple[list[Ev], dict[int, int], int]:
    data = load_dashboard_data(symbol=symbol, timeframe=timeframe, limit=limit)
    candles = data.candles
    if len(candles) < 100:
        return [], {}, 0
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    atr = _atr(candles)
    min_r = min_r_atr * atr
    max_h = max(horizons)
    max_d = max(depths)

    out: list[Ev] = []
    survivors: dict[int, int] = dict.fromkeys(depths, 0)
    total_rej1 = 0

    for grab in data.liquidity_grabs:
        if grab.outcome is not LiquidityGrabOutcome.REJECTED:
            continue
        g = idx_of.get(grab.timestamp)
        if g is None or g + max_d + max_h >= len(candles):
            continue
        buy_side = grab.side is LiquiditySide.BUY_SIDE
        level = grab.price_level
        # a buy-side pool taken from below and handed back argues bearish
        d = BEAR if buy_side else BULL
        total_rej1 += 1

        for n in sorted(depths):
            window = candles[g : g + n]
            if not all(_inside(c, level, buy_side=buy_side) for c in window):
                break  # falsified at this depth; deeper ones cannot survive
            survivors[n] += 1
            ei = g + n - 1
            entry = candles[ei].close
            extreme = (
                max(c.high for c in candles[g : ei + 1])
                if buy_side
                else min(c.low for c in candles[g : ei + 1])
            )
            r = abs(entry - extreme)
            if r < min_r:
                continue
            out.append(_measure(
                Ev(f"rej@{n}", symbol, timeframe.value, d, ei, entry, extreme, r, level),
                candles, horizons,
            ))
            for _ in range(random_reps):
                ri = rng.randrange(50, len(candles) - max_h - 1)
                e2 = candles[ri].close
                s2 = e2 + r if d is BEAR else e2 - r
                out.append(_measure(
                    Ev(f"rand@{n}", symbol, timeframe.value, d, ri, e2, s2, r, s2),
                    candles, horizons,
                ))
    return out, survivors, total_rej1


def report(
    events: Sequence[Ev],
    depths: Sequence[int],
    horizons: Sequence[int],
    survivors: dict[int, int],
    total: int,
) -> None:
    print(f"\nfalsification of the `⚡` as drawn today (N=1), {total} grabs")
    for n in sorted(depths):
        s = survivors.get(n, 0)
        pct = s / total * 100 if total else 0.0
        print(f"  survives {n} candle(s): {s:>5}  ({pct:>5.1f}%)")

    print(f"\n{'arm':>8} {'N':>5} " + " ".join(
        f"{'held@' + str(h):>9} {'MFE@' + str(h):>8} {'MAE@' + str(h):>8}"
        for h in horizons
    ) + f" {'MFE/MAE':>8} {'hit2R':>7}")
    arms = [f"{p}@{n}" for n in sorted(depths) for p in ("rej", "rand")]
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
                f"{(sum(held) / len(held) * 100 if held else 0):>8.0f}% "
                f"{(statistics.mean(mfe) if mfe else 0):>8.2f} "
                f"{(statistics.mean(mae) if mae else 0):>8.2f}"
            )
        hh = max(horizons)
        mf = [e.mfe[hh] for e in rows if hh in e.mfe]
        ma = [e.mae[hh] for e in rows if hh in e.mae]
        ratio = (
            statistics.mean(mf) / statistics.mean(ma)
            if ma and statistics.mean(ma) > 0
            else 0.0
        )
        res = [e.hit_2r for e in rows if e.hit_2r is not None]
        hit = f"{sum(res) / len(res) * 100:>6.0f}%" if res else "     --"
        print(f"{arm:>8} {len(rows):>5} " + " ".join(cells) + f" {ratio:>8.2f} {hit}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    p.add_argument("--depths", nargs="+", type=int, default=[1, 2, 3])
    p.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20])
    p.add_argument("--limit", type=int, default=1200)
    p.add_argument("--min-r-atr", type=float, default=0.25)
    p.add_argument("--random-reps", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--per-combo", action="store_true", help="also report each combo")
    args = p.parse_args()

    rng = random.Random(args.seed)
    all_events: list[Ev] = []
    totals: dict[int, int] = dict.fromkeys(args.depths, 0)
    grand = 0
    for sym in args.symbols:
        for tf_name in args.timeframes:
            tf = TimeFrame(tf_name)
            try:
                evs, surv, tot = run_combo(
                    sym, tf,
                    limit=args.limit,
                    depths=args.depths,
                    horizons=args.horizons,
                    min_r_atr=args.min_r_atr,
                    random_reps=args.random_reps,
                    rng=rng,
                )
            except Exception as exc:  # noqa: BLE001 - a dead symbol must not stop the run
                print(f"  !! {sym} {tf_name}: {exc}")
                continue
            print(f"  {sym:>10} {tf_name:>4}: {tot:>3} rejected grabs")
            all_events.extend(evs)
            grand += tot
            for n, c in surv.items():
                totals[n] += c
            if args.per_combo and evs:
                report(evs, args.depths, args.horizons, surv, tot)

    report(all_events, args.depths, args.horizons, totals, grand)


if __name__ == "__main__":
    main()
