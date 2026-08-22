"""The effective spread, measured from trades instead of estimated from bars.

Why this exists
---------------
`spread_cost.py` tried to read the round trip off the OHLC bars and failed its
own sanity check: the high-low estimators are calibrated on equity dailies and
on a crypto perp they return the bar's volatility wearing a spread's units.
The lesson there was that a spread belongs to the order book, and a bar does
not carry the book.

A trade does. Binance's `aggTrades` is public, historical, and carries
`isBuyerMaker` — the side that crossed. That turns the spread from something
to be inferred into something to be counted: inside a short window, trades
that lifted the ask print higher than trades that hit the bid, and the gap
between those two averages **is** the effective spread the taker paid. No
model, no calibration, no assumption about what drives a bar's range.

    effective spread ≈ mean(price | buyer crossed) − mean(price | seller crossed)

Read as a fraction of the mid, this is what a market order actually gave up,
which is exactly the quantity `vwap_exit_grid.py` charges as `cost`.

The check that `spread_cost.py` failed applies here too: a real spread is a
property of the book, so it must not move when the measurement window changes.
`--validate` measures each symbol at 1, 5 and 15 minutes **anchored on the
same moments**, and it passes at short windows:

    BTCUSDT    0.0051%   0.0069%   0.0105%
    CRVUSDT    0.0418%   0.0427%   0.0608%
    CELRUSDT        --   0.0310%   0.0586%

One and five minutes agree closely; fifteen inflates by 1.5-2x. That is drift
leaking in, and it is the same failure that sank the bar estimators, only
orders of magnitude slower -- which is why a short window is safe here and no
window was safe there. Hence the one-minute default.

Two things that check taught, both worth keeping:

* **The columns must share their anchors.** A first version drew fresh windows
  per column and produced a non-monotonic CRV (0.023%, 0.013%, 0.032%). A
  window-length effect cannot be non-monotonic, so the shape itself said the
  instrument was wrong, not the object. Comparing a length against a length
  means holding the sample fixed too.
* **The inflation is worse where the symbol is thinner.** So the tempting fix
  for poor coverage -- lengthen the window -- overcharges precisely the
  illiquid names, in the direction that would condemn the setup. Coverage is
  given up instead.

What it still does not cover
----------------------------
This is the spread on the *tape*, so it prices a fill at the touch. It does
not price depth (a size larger than the top of book walks further), latency,
or the extra given up by a stop-market firing into a fast move — and a stop
fires on roughly half of these trades. So this is a **floor** on the true
round trip, and therefore a **ceiling** on the net edge. It is a much tighter
floor than a flat guess, which is the point.

And it is measured on **recent tape**, not on the study's own window. Binance
refuses a time-ranged `aggTrades` search older than about two days (error
-4166), while the trades being priced span two years. So this says what these
instruments cost *now*, and reads across to the study only under the
assumption that a symbol's spread is stable in relative terms — that CRV has
always been dearer than BTC, not that either has held a fixed number. That
assumption is not tested here, and it is the weakest joint in the chain.

Usage
-----
    poetry run python research/spread_trades.py --symbols BTCUSDT CRVUSDT
    poetry run python research/spread_trades.py --validate
    poetry run python research/spread_trades.py --all --out spreads.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics as st
import time
from pathlib import Path
from typing import Any

import ccxt
from research._symbols import UNIVERSE

CACHE = Path(__file__).parent / ".spread_cache"
#: One aggTrades request covers at most this many trades.
PAGE = 1000
#: Windows sampled per symbol, and how long each one is. Spread varies over
#: the day, so several scattered windows beat one long one.
DEFAULT_WINDOWS = 12
#: One minute. Longer windows let the price's own drift leak into the gap
#: between the two side means, and `--validate` shows it does: measured on the
#: same moments, BTC reads 0.0051% / 0.0069% / 0.0105% at 1 / 5 / 15 minutes
#: and CELR 0.0310% / 0.0586% at 5 / 15. The inflation is **worse on the thin
#: symbols** -- CELR 1.9x against BTC's 1.5x -- so a longer window overcharges
#: exactly the illiquid names whose cost the study is least sure of. A minute
#: buys immunity at the price of coverage: a symbol too thin to fill both
#: sides of a one-minute window reports nothing, which is the right answer
#: rather than a number biased high.
DEFAULT_WINDOW_MINUTES = 1
#: A window with fewer trades than this on either side cannot separate the two
#: means from noise, and is dropped rather than averaged in.
MIN_TRADES_PER_SIDE = 20
#: Binance USDT-M taker fee, base tier, one side.
DEFAULT_TAKER_FEE = 0.0005
#: Binance rejects a time-ranged aggTrades search older than this with error
#: -4166. So the spread is measured on *recent* tape and applied to trades that
#: span two years -- see the "what this does not cover" note in the docstring.
MAX_LOOKBACK_DAYS = 1.8


def _exchange() -> ccxt.Exchange:
    return ccxt.binanceusdm({"enableRateLimit": True})


def _agg_trades(
    exchange: ccxt.Exchange, symbol: str, start_ms: int, end_ms: int
) -> list[dict[str, Any]]:
    """Every aggregated trade in a window, paged forward from `start_ms`."""
    out: list[dict[str, Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        rows = exchange.fapiPublicGetAggTrades(
            {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": min(cursor + 60 * 60 * 1000, end_ms),
                "limit": PAGE,
            }
        )
        if not rows:
            break
        out.extend(rows)
        last = int(rows[-1]["T"])
        if len(rows) < PAGE:
            break
        if last <= cursor:
            break
        cursor = last + 1
    return out


def window_spread(trades: list[dict[str, Any]]) -> float | None:
    """Proportional effective spread over one window, or None if too thin.

    `m` is true when the *buyer* was the maker, i.e. the seller crossed the
    spread and traded at the bid. So `m == False` is the ask side.
    """
    asks = [float(t["p"]) for t in trades if not t["m"]]
    bids = [float(t["p"]) for t in trades if t["m"]]
    if len(asks) < MIN_TRADES_PER_SIDE or len(bids) < MIN_TRADES_PER_SIDE:
        return None
    ask, bid = st.fmean(asks), st.fmean(bids)
    mid = (ask + bid) / 2
    if mid <= 0 or ask <= bid:
        # A negative gap is drift inside the window overwhelming the bounce,
        # not a negative spread. Dropped, not floored at zero.
        return None
    return (ask - bid) / mid


def measure(
    exchange: ccxt.Exchange,
    symbol: str,
    *,
    windows: int,
    minutes: int,
    days_back: float,
    rng: random.Random,
) -> dict[str, float] | None:
    """The median effective spread over `windows` scattered samples."""
    now = exchange.milliseconds()
    span = int(days_back * 24 * 60 * 60 * 1000)
    values: list[float] = []
    for _ in range(windows):
        start = now - span + rng.randrange(span)
        spread = window_spread(
            _agg_trades(exchange, symbol, start, start + minutes * 60 * 1000)
        )
        if spread is not None:
            values.append(spread)
    if len(values) < max(3, windows // 3):
        return None
    return {
        "spread": st.median(values),
        "p25": sorted(values)[len(values) // 4],
        "p75": sorted(values)[3 * len(values) // 4],
        "windows": float(len(values)),
    }


def _cache_path(minutes: int) -> Path:
    return CACHE / f"spreads_{minutes}m.json"


def load_cache(minutes: int) -> dict[str, dict[str, float]]:
    path = _cache_path(minutes)
    if not path.exists():
        return {}
    loaded: dict[str, dict[str, float]] = json.loads(path.read_text())
    return loaded


def save_cache(minutes: int, data: dict[str, dict[str, float]]) -> None:
    CACHE.mkdir(exist_ok=True)
    _cache_path(minutes).write_text(json.dumps(data, indent=1, sort_keys=True))


def report(data: dict[str, dict[str, float]], fee: float) -> None:
    rows = sorted(data.items(), key=lambda kv: kv[1]["spread"])
    print(f"\nmeasured effective spread ({len(rows)} symbols), "
          f"taker fee {fee:.3%} per side")
    print(f"{'symbol':>12} {'spread':>9} {'p25':>9} {'p75':>9} "
          f"{'round trip':>11} {'n':>4}")
    for symbol, e in rows:
        print(f"{symbol:>12} {e['spread']:>8.4%} {e['p25']:>8.4%} "
              f"{e['p75']:>8.4%} {2 * fee + e['spread']:>10.4%} "
              f"{int(e['windows']):>4}")
    values = [e["spread"] for _, e in rows]
    print(f"\n{'':>12} median {st.median(values):.4%}  "
          f"round trip {2 * fee + st.median(values):.4%}")


def validate(symbols: list[str], fee: float, rng: random.Random,
             windows: int = 8) -> None:
    """The check the bar estimators failed: does the window length matter?

    The columns share their **anchor times**. A first version drew fresh random
    windows per column and produced a non-monotonic CRV -- 0.023%, 0.013%,
    0.032% -- which cannot be a window-length effect and was the sampling
    noise of eight draws showing through. Comparing a length against a length
    means holding everything else, the sample included, fixed.
    """
    exchange = _exchange()
    lengths = (1, 5, 15)
    now = exchange.milliseconds()
    span = int(MAX_LOOKBACK_DAYS * 24 * 60 * 60 * 1000)
    # one set of start times, reused by every column
    anchors = [now - span + rng.randrange(span - 15 * 60 * 1000)
               for _ in range(windows)]
    print("\nthe same spread measured over different window lengths, "
          f"anchored on the same {windows} moments")
    print(f"{'symbol':>12} " + " ".join(f"{m:>3}min" for m in lengths))
    for symbol in symbols:
        cells = []
        for minutes in lengths:
            values = [
                v for start in anchors
                if (v := window_spread(
                    _agg_trades(exchange, symbol, start,
                                start + minutes * 60 * 1000))) is not None
            ]
            cells.append(f"{st.median(values):.4%}" if len(values) >= 3 else "--")
        print(f"{symbol:>12} " + " ".join(f"{c:>9}" for c in cells), flush=True)
    print("\nA spread belongs to the book, so these columns should agree.")
    print("Growing with the window is drift leaking in: over fifteen minutes")
    print("the price itself moves, and that movement lands in the gap between")
    print("the two side means. The short columns are the ones to trust.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "CRVUSDT"])
    p.add_argument("--all", action="store_true", help="every symbol in the universe")
    p.add_argument("--windows", type=int, default=DEFAULT_WINDOWS)
    p.add_argument("--minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    p.add_argument("--days-back", type=float, default=MAX_LOOKBACK_DAYS,
                   help="how far back to scatter the sampled windows; Binance "
                        "restricts a time-ranged aggTrades search to the "
                        "recent 2 days, so this cannot reach the study window")
    p.add_argument("--fee", type=float, default=DEFAULT_TAKER_FEE)
    p.add_argument("--out", default=None, help="also write the table to this JSON")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    symbols = list(UNIVERSE) if args.all else args.symbols

    if args.validate:
        validate(symbols, args.fee, rng)
        return

    exchange = _exchange()
    data = load_cache(args.minutes)
    for i, symbol in enumerate(symbols, 1):
        if symbol in data:
            continue
        started = time.time()
        got = measure(exchange, symbol, windows=args.windows,
                      minutes=args.minutes, days_back=args.days_back, rng=rng)
        if got is not None:
            data[symbol] = got
        save_cache(args.minutes, data)
        print(f"[{i}/{len(symbols)}] {symbol} "
              f"{'--' if got is None else format(got['spread'], '.4%')} "
              f"({time.time() - started:.0f}s)", flush=True)

    shown = {s: data[s] for s in symbols if s in data}
    report(shown, args.fee)
    if args.out:
        Path(args.out).write_text(json.dumps(shown, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
