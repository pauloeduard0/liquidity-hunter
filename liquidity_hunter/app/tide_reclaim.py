"""Tide reclaim: price returns to the periodic VWAP's trend side after a pullback.

The D1 setup measured in `research/tide_reclaim_setup.py` (K8/K13) and
hardened in K9-K15 (`docs/tide_reclaim.md`). An observation, not an order:
on the close of candle ``i`` it reports that

- the higher-timeframe direction (W1 for D1) is bullish or bearish;
- at least :data:`MIN_PULLBACK` consecutive candles before ``i`` closed on the
  *other* side of the Tide VWAP, all inside the same anchor period (the first
  candle of a period is excluded by construction: there the VWAP re-anchors
  and "closing above it" is automatic);
- candle ``i`` closed back on the higher timeframe's side.

The reading carries the level it was measured against -- the pullback's
extreme -- and whether the chart timeframe's confirmed structure still runs
against the higher timeframe (`hunt_active`, the HUNT context that added
+0.06R per trade on D1 in the measurement).

Only closed candles mean anything here. :class:`ClosedCandleProvider` drops the
forming candle of every timeframe it serves, which is what the study did by
construction when it replayed history.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
    VWAPSeries,
)
from liquidity_hunter.data import OHLCVProvider

#: Closes on the wrong side of the VWAP that make a pullback (K8, fixed before
#: the run; not tuned).
MIN_PULLBACK = 3
#: Longest pullback walked back when locating its extreme.
MAX_PULLBACK = 60
#: The exit confirmed in K10 (beat 2R on daily Sharpe in 6 of 7 years).
TARGET_R = 3.0
#: Candles the study let a trade run before closing it at market.
HORIZON_CANDLES = 60
#: The one timeframe the setup is measured on. H4 is positive but thin and
#: carries two losing years (K10/K15); M15-H1 lose (K8).
TIDE_RECLAIM_TIMEFRAME = TimeFrame.D1


@dataclass(frozen=True)
class TideReclaimSignal:
    """A tide reclaim on the last closed candle of a series."""

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    direction: MarketDirection
    entry_price: float
    stop_price: float
    vwap_price: float
    pullback_candles: int
    hunt_active: bool

    @property
    def risk(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def target_price(self) -> float:
        sign = 1.0 if self.direction is MarketDirection.BULLISH else -1.0
        return self.entry_price + sign * TARGET_R * self.risk


def confirmed_leg_trend(events: Sequence[MarketStructure]) -> MarketDirection:
    """Direction of the chart timeframe's leg from its confirmed events.

    BOS/CHoCH set it, a failed CHoCH reverts it; provisional marks are
    ignored. The same replay the hunt engine and the study use.
    """
    trend = MarketDirection.NEUTRAL
    for e in sorted((e for e in events if not e.provisional), key=lambda e: e.timestamp):
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER):
            trend = e.direction
        elif e.event is StructureEvent.CHOCH_FAILED:
            trend = (
                MarketDirection.BEARISH
                if e.direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
    return trend


def detect_tide_reclaim(
    candles: Sequence[Candle],
    tide: VWAPSeries | None,
    *,
    htf_direction: MarketDirection,
    leg_trend: MarketDirection,
) -> TideReclaimSignal | None:
    """The tide reclaim on the **last** candle of `candles`, if there is one."""
    if tide is None or len(candles) < MIN_PULLBACK + 1:
        return None
    if htf_direction not in (MarketDirection.BULLISH, MarketDirection.BEARISH):
        return None
    points = {p.timestamp: p for p in tide.points}
    bullish = htf_direction is MarketDirection.BULLISH

    def side(candle: Candle) -> int:
        p = points.get(candle.timestamp)
        if p is None or candle.close == p.value:
            return 0
        return 1 if candle.close > p.value else -1

    want = 1 if bullish else -1
    last = candles[-1]
    here = points.get(last.timestamp)
    if here is None or side(last) != want:
        return None
    run = 0
    k = len(candles) - 2
    while k >= 0 and run < MAX_PULLBACK and side(candles[k]) == -want:
        p = points.get(candles[k].timestamp)
        if p is None or p.anchor_timestamp != here.anchor_timestamp:
            break
        run += 1
        k -= 1
    if run < MIN_PULLBACK:
        return None
    segment = candles[len(candles) - 1 - run :]
    stop = min(c.low for c in segment) if bullish else max(c.high for c in segment)
    if (last.close - stop if bullish else stop - last.close) <= 0:
        return None
    return TideReclaimSignal(
        symbol=last.symbol,
        timeframe=last.timeframe,
        timestamp=last.timestamp,
        direction=htf_direction,
        entry_price=last.close,
        stop_price=stop,
        vwap_price=here.value,
        pullback_candles=run,
        hunt_active=leg_trend in (MarketDirection.BULLISH, MarketDirection.BEARISH)
        and leg_trend is not htf_direction,
    )


_DURATION_SECONDS: dict[TimeFrame, int] = {
    TimeFrame.M1: 60, TimeFrame.M5: 300, TimeFrame.M15: 900, TimeFrame.M30: 1800,
    TimeFrame.H1: 3600, TimeFrame.H4: 14400, TimeFrame.D1: 86400,
    TimeFrame.W1: 604800, TimeFrame.MN1: 2678400,
}


class ClosedCandleProvider(OHLCVProvider):
    """Serve only candles that have closed, for every timeframe requested.

    A live D1 series ends in today's forming candle, and the W1 series in this
    week's; both would let the reading see a close that has not happened. The
    study never had that candle, so neither does the live reading.
    """

    def __init__(
        self, inner: OHLCVProvider, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._inner = inner
        self._clock = clock
        self.max_fetch_limit = inner.max_fetch_limit

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        now = self._clock()
        candles = self._inner.get_ohlcv(symbol, timeframe, min(limit + 1, self.max_fetch_limit))
        seconds = _DURATION_SECONDS[timeframe]
        closed = [c for c in candles if (now - c.timestamp).total_seconds() >= seconds]
        return closed[-limit:]

    def series_key(self, symbol: str) -> str:
        return self._inner.series_key(symbol)
