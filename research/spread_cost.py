"""Estimating the round trip from OHLC bars -- and why it does not work here.

The question this was built for
------------------------------
`vwap_exit_grid.py` charges every trade the same round trip and reports where
the edge dies. That is the right shape for a sensitivity table and the wrong
shape for a decision, because cost is not the same for every symbol: the
block-reclaim study's strength sits in the alts, and its own summary calls the
cost assumption the weakest thing in it. Fees are flat and knowable; the
spread is a property of the instrument. If the spread could be read off the
candles already in `research/.klines_cache`, each trade could be charged its
own symbol's cost with no account and no order book -- and "is it operable"
would stop being an opinion.

The answer: it cannot, and the failure is worth keeping.
---------------------------------------------------------
Two standard high-low estimators are implemented, `Abdi & Ranaldo (2017)` and
`Corwin & Schultz (2012)`. Both are calibrated on equity **daily** bars, where
a bar's range is dominated by the bid-ask bounce around a slow-moving
efficient price. Crypto perps on a 15-minute bar are the opposite regime: the
range is dominated by real price movement, and the bounce is a rounding error
inside it.

`--validate` is the test that settles it. A spread is a property of the order
book, so the *same* instrument measured over the *same* calendar window must
give the same answer whatever bar length it is measured on. It does not:

    ETHUSDT    15m  ~0.0%(unmeasurable)   1h  --   1d  1.420%
    BTCUSDT    15m  unmeasurable          1h  --   1d  0.154%
    median     15m  0.107%                1h  0.098%    1d  0.911%

An estimate that grows with bar length is measuring the bar's volatility, not
the book. ETH's real quoted spread is on the order of half a basis point; 1.4%
is off by two orders of magnitude. The second tell is coverage: the estimator
returns a negative covariance -- no answer at all -- for 49 of 72 symbols at
15m and 65 of 72 at 1h, and the ones it fails on are the *liquid* ones, where
the spread is smallest relative to the range. So the symbols it does price are
a biased sample of the wide tail, which is the opposite of a usable measure.

What this means for the study
-----------------------------
Run against the trades it prices, this reported the block arm falling from
+0.45 gross to -0.18 net. **That number is not a result and must not be
quoted.** It rests on an estimator that fails its own sanity check, applied to
a biased subset of symbols. The honest statement is unchanged from before this
script existed: the real round trip is not knowable from candles, and the
sensitivity table in `vwap_exit_grid.py` -- which says what the edge needs
rather than claiming to know what it costs -- remains the right instrument.

Getting the real number needs quotes, not bars: a recorded book snapshot at
each signal, or fills from an account. That is execution, which this project
does not do, so it belongs in the consumer of the API rather than here.

Usage
-----
    poetry run python research/spread_cost.py --validate
    poetry run python research/spread_cost.py --timeframe 15m
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from collections.abc import Sequence
from pathlib import Path

from research._symbols import UNIVERSE

CACHE = Path(__file__).parent / ".klines_cache"

#: Binance USDT-M taker fee at the base VIP tier, one side. A round trip pays
#: it twice: the entry is a taker (entering at the close of a signal candle)
#: and the stop exit is a stop-market, always a taker. Overridable, since a
#: reader's tier and any BNB discount are theirs.
DEFAULT_TAKER_FEE = 0.0005


def _rows(symbol: str, timeframe: str) -> list[list[str]] | None:
    path = CACHE / f"{symbol}_{timeframe}.json"
    if not path.exists():
        return None
    loaded: list[list[str]] = json.loads(path.read_text())
    return loaded


def abdi_ranaldo(highs: Sequence[float], lows: Sequence[float],
                 closes: Sequence[float]) -> float | None:
    """Estimated proportional spread. `None` when the covariance is negative.

    A negative covariance is not a small spread -- it is the estimator saying
    this window does not look like bid-ask bounce at all, usually a stretch of
    strong trend. Returning `None` rather than zero keeps that distinction:
    zero would be a measurement, and this is an absence of one.
    """
    eta = [
        (math.log(h) + math.log(low)) / 2
        for h, low in zip(highs, lows, strict=True)
        if h > 0 and low > 0
    ]
    c = [math.log(x) for x in closes if x > 0]
    if len(eta) != len(c) or len(c) < 3:
        return None
    terms = [(c[t] - eta[t]) * (c[t] - eta[t + 1]) for t in range(len(c) - 1)]
    mean = st.fmean(terms)
    return 2 * math.sqrt(mean) if mean > 0 else None


def corwin_schultz(highs: Sequence[float], lows: Sequence[float]) -> float | None:
    """The two-bar high-low estimator, as a cross-check on Abdi-Ranaldo."""
    out: list[float] = []
    k = 3 - 2 * math.sqrt(2)
    for t in range(len(highs) - 1):
        h1, l1, h2, l2 = highs[t], lows[t], highs[t + 1], lows[t + 1]
        if min(h1, l1, h2, l2) <= 0:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
        spread = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        if spread > 0:  # negatives are the estimator's known failure, not a zero
            out.append(spread)
    return st.fmean(out) if out else None


def spreads(timeframe: str) -> dict[str, dict[str, float | None]]:
    """Both estimates per symbol, over whatever the cache holds."""
    out: dict[str, dict[str, float | None]] = {}
    for symbol in UNIVERSE:
        rows = _rows(symbol, timeframe)
        if rows is None:
            continue
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        closes = [float(r[4]) for r in rows]
        out[symbol] = {
            "ar": abdi_ranaldo(highs, lows, closes),
            "cs": corwin_schultz(highs, lows),
            "bars": float(len(rows)),
        }
    return out


def _round_trip(est: dict[str, float | None], fee: float) -> float | None:
    """Fees both sides, plus crossing the spread on entry and on exit."""
    ar = est.get("ar")
    return None if ar is None else 2 * fee + ar


def report_spreads(est: dict[str, dict[str, float | None]], fee: float) -> None:
    rows = [(s, e) for s, e in est.items() if e["ar"] is not None]
    rows.sort(key=lambda x: x[1]["ar"])  # type: ignore[arg-type,return-value]
    print(f"\nestimated round trip per symbol ({len(rows)} of {len(est)} "
          f"measurable), taker fee {fee:.3%} each side")
    print(f"{'symbol':>12} {'spread A-R':>11} {'C-S':>9} {'round trip':>11}")
    for symbol, e in rows:
        rt = _round_trip(e, fee)
        cs = e["cs"]
        print(f"{symbol:>12} {e['ar']:>10.3%} "
              f"{(f'{cs:.3%}' if cs is not None else '--'):>9} "
              f"{rt:>10.3%}")
    values = [e["ar"] for _, e in rows]
    print(f"\n{'':>12} median spread {st.median(values):.3%}, "  # type: ignore[arg-type]
          f"p10 {sorted(values)[len(values) // 10]:.3%}, "  # type: ignore[index]
          f"p90 {sorted(values)[9 * len(values) // 10]:.3%}")  # type: ignore[index]


def report_trades(trades_path: str, est: dict[str, dict[str, float | None]],
                  fee: float, target: str, max_r_atr: float | None) -> None:
    """The exit grid's headline, with each trade charged its own symbol's cost.

    This is the number the flat table could not give: not "where does the edge
    die" but "does it survive what these instruments actually cost".
    """
    rows = [r for r in json.loads(Path(trades_path).read_text()) if r.get("r_grid")]
    if max_r_atr is not None:
        rows = [r for r in rows
                if r.get("r_atr") is not None and r["r_atr"] <= max_r_atr]
    priced = [
        (r, _round_trip(est[r["symbol"]], fee))
        for r in rows if r["symbol"] in est
    ]
    priced = [(r, c) for r, c in priced if c is not None]
    skipped = len(rows) - len(priced)

    print(f"\nnet R with each trade charged its own symbol's estimated cost "
          f"(target {target}R)")
    if skipped:
        print(f"  {skipped} trades dropped: no usable estimate for their symbol")
    print(f"{'arm':>10} {'n':>6} {'gross':>9} {'cost R':>9} {'net':>9} {'t':>6}")
    for arm, label in (("vwap", "placebo"), ("eql", "eql"), ("ob", "block")):
        sel = [(r, c) for r, c in priced if r["arm"] == arm]
        if len(sel) < 50:
            continue
        gross = st.fmean(r["r_grid"][target] for r, _ in sel)
        charge = st.fmean(c / r["r_pct"] for r, c in sel)
        nets = [r["r_grid"][target] - c / r["r_pct"] for r, c in sel]
        sd = st.stdev(nets) if len(nets) > 1 else 0.0
        t = st.fmean(nets) / (sd / len(nets) ** 0.5) if sd > 0 else 0.0
        print(f"{label:>10} {len(sel):>6} {gross:>+9.4f} {charge:>9.4f} "
              f"{st.fmean(nets):>+9.4f} {t:>+6.1f}")

    block = [(r, c) for r, c in priced if r["arm"] == "ob"]
    if len(block) >= 100:
        print("\nblock arm split at the median estimated cost")
        mid = st.median(c for _, c in block)
        for label, sel in (
            ("cheap half", [(r, c) for r, c in block if c <= mid]),
            ("dear half", [(r, c) for r, c in block if c > mid]),
        ):
            if len(sel) < 50:
                continue
            nets = [r["r_grid"][target] - c / r["r_pct"] for r, c in sel]
            sd = st.stdev(nets) if len(nets) > 1 else 0.0
            t = st.fmean(nets) / (sd / len(nets) ** 0.5) if sd > 0 else 0.0
            print(f"{label:>14} n={len(sel):<5} cost {st.fmean(c for _, c in sel):.3%}"
                  f"  net {st.fmean(nets):>+8.4f}  t {t:>+5.1f}")


def validate(fee: float) -> None:
    """The falsification test: the same book, measured on different bars.

    Printed rather than asserted, because the point is to show the size of the
    disagreement. A usable estimator would give one column three times.
    """
    frames = ("15m", "1h", "4h", "1d")
    est = {tf: spreads(tf) for tf in frames}
    watch = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "CRVUSDT", "EOSUSDT")
    print(f"\nthe same spread measured on different bar lengths "
          f"(taker fee {fee:.3%} per side, shown as spread only)")
    print(f"{'symbol':>12} " + " ".join(f"{tf:>11}" for tf in frames))
    for symbol in watch:
        cells = []
        for tf in frames:
            value = est[tf].get(symbol, {}).get("ar")
            cells.append(f"{value:.3%}" if value is not None else "--")
        print(f"{symbol:>12} " + " ".join(f"{c:>11}" for c in cells))
    print(f"\n{'measurable':>12} " + " ".join(
        f"{sum(1 for e in est[tf].values() if e['ar'] is not None):>4}/"
        f"{len(est[tf]):<6}" for tf in frames))
    print(f"{'median':>12} " + " ".join(
        f"{st.median([e['ar'] for e in est[tf].values() if e['ar'] is not None]):>11.3%}"
        if any(e["ar"] is not None for e in est[tf].values()) else f"{'--':>11}"
        for tf in frames))
    print("\nA spread belongs to the order book, not to the bar it is read on.")
    print("Three different answers means the estimator is reading volatility;")
    print("failing on the liquid symbols means what it does read is the wide tail.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--fee", type=float, default=DEFAULT_TAKER_FEE,
                   help="taker fee per side; the default is Binance USDT-M's "
                        "base tier, and a reader's own tier is theirs to pass")
    p.add_argument("--trades", default=None,
                   help="an export from vwap_ob_pinbar.py; without it only the "
                        "per-symbol spread table is printed")
    p.add_argument("--target", default="2.0")
    p.add_argument("--max-r-atr", type=float, default=1.0)
    p.add_argument("--validate", action="store_true",
                   help="measure the same symbols at several bar lengths. A "
                        "spread is a property of the book, so an estimate that "
                        "moves with bar length is measuring volatility instead")
    args = p.parse_args()

    if args.validate:
        validate(args.fee)
        return

    est = spreads(args.timeframe)
    if not est:
        raise SystemExit(f"no cached {args.timeframe} klines in {CACHE}")
    report_spreads(est, args.fee)
    if args.trades:
        report_trades(args.trades, est, args.fee, args.target, args.max_r_atr)


if __name__ == "__main__":
    main()
