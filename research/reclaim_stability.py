"""Does a live block reclaim survive a later replay of the same candles?

The whole study runs the detector **once** over a finished series. A live
reader runs it on every closed candle, and the two need not agree: a visit to
a block merges across gaps of up to `MERGE_GAP_CANDLES`, and the detector
emits one reclaim per visit *after* the visit ends -- so a visit that keeps
absorbing later candles can swallow a trigger that had already fired. Found on
GALAUSDT M30 (2026-08-23): two reclaims the paper journal recorded live were
gone from the same series read a few hours later.

If that is common, the measured population is not the population a live reader
would trade, and every net figure describes a set of trades nobody could have
taken in that form. This measures how common it is:

    poetry run python research/reclaim_stability.py --symbols BTCUSDT ETHUSDT \
        --timeframes 15m 30m --limit 1500

For each series it replays the detector over growing prefixes (one pass per
closed candle), collects every reclaim that was ever emitted *non-provisionally
at the live edge*, and asks how many of those still exist in the final,
whole-series read -- the read the study measures.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from _paginated import PaginatedFuturesProvider  # type: ignore[import-not-found]
from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.app.dashboard_data import (
    _BLOCK_RECLAIM_EMA_PERIOD,
    _VWAP_ANCHOR_PERIOD,
    _VWAP_DEFAULT_ANCHOR_PERIOD,
)
from liquidity_hunter.core.domain import BlockReclaim, Candle, TimeFrame
from liquidity_hunter.indicators import ema_series, vwap
from liquidity_hunter.liquidity import POIDetector

#: A reclaim's identity across reads: the trigger candle and its direction.
Key = tuple[str, str]


def key_of(r: BlockReclaim) -> Key:
    return (r.timestamp.isoformat(), r.direction.value)


def detect_at(candles: list[Candle], symbol: str, timeframe: TimeFrame) -> list[BlockReclaim]:
    """The production pipeline over one prefix."""
    zones = POIDetector().detect(candles)
    series = vwap(
        candles,
        symbol=symbol,
        timeframe=timeframe,
        anchor=_VWAP_ANCHOR_PERIOD.get(timeframe, _VWAP_DEFAULT_ANCHOR_PERIOD),
    )
    return detect_block_reclaims(
        candles,
        zones,
        series,
        symbol=symbol,
        timeframe=timeframe,
        ema=ema_series(candles, _BLOCK_RECLAIM_EMA_PERIOD),
    )


@dataclass
class Result:
    symbol: str
    timeframe: str
    live: set[Key]
    final: set[Key]

    @property
    def survived(self) -> set[Key]:
        return self.live & self.final

    @property
    def vanished(self) -> set[Key]:
        return self.live - self.final

    @property
    def invented(self) -> set[Key]:
        """In the final read but never emitted live: the study's own extras."""
        return self.final - self.live


def scan(
    candles: list[Candle], symbol: str, timeframe: TimeFrame, *, warmup: int = 200
) -> Result:
    live: set[Key] = set()
    for end in range(warmup, len(candles) + 1):
        prefix = candles[:end]
        for r in detect_at(prefix, symbol, timeframe):
            # Only what a live reader could act on: the trigger candle has
            # closed (the last candle of a prefix is the forming one).
            if not r.provisional:
                live.add(key_of(r))
    final = {key_of(r) for r in detect_at(candles, symbol, timeframe)}
    return Result(symbol, timeframe.value, live, final)


def report(results: Sequence[Result]) -> None:
    print(
        f"\n{'symbol':>10} {'tf':>4} {'live':>6} {'final':>6} "
        f"{'survived':>9} {'vanished':>9} {'extra':>6}"
    )
    tl = tf_ = ts = tv = te = 0
    for r in results:
        print(
            f"{r.symbol:>10} {r.timeframe:>4} {len(r.live):>6} {len(r.final):>6} "
            f"{len(r.survived):>9} {len(r.vanished):>9} {len(r.invented):>6}"
        )
        tl += len(r.live)
        tf_ += len(r.final)
        ts += len(r.survived)
        tv += len(r.vanished)
        te += len(r.invented)
    if tl:
        print(
            f"\ntotal: {tl} emitted live, {ts} survive the final read "
            f"({ts / tl:.1%}), {tv} vanish ({tv / tl:.1%})"
        )
    if tf_:
        print(
            f"       {tf_} in the final read, {te} of them were never emitted "
            f"live ({te / tf_:.1%}) -- trades the study counts that a live "
            f"reader never saw"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--timeframes", nargs="+", default=["15m", "30m"])
    p.add_argument("--limit", type=int, default=1500)
    p.add_argument("--warmup", type=int, default=200)
    args = p.parse_args()

    provider = PaginatedFuturesProvider()
    results: list[Result] = []
    for symbol in args.symbols:
        for tf in (TimeFrame(t) for t in args.timeframes):
            candles = provider.get_ohlcv(symbol, tf, args.limit)
            results.append(scan(candles, symbol, tf, warmup=args.warmup))
            r = results[-1]
            print(
                f"{symbol} {tf.value}: live {len(r.live)} final {len(r.final)} "
                f"vanished {len(r.vanished)}"
            )
    report(results)


if __name__ == "__main__":
    main()
