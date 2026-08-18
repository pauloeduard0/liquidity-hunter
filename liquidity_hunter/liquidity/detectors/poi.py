"""POI (Point of Interest / Order Block) detector.

Detects order block zones from market structure breaks (MSB), a faithful
batch port of the "Market Structure Break & Order Block" TradingView
indicator (EmreKb, MPL 2.0). Self-contained: it derives its own swing
pivots from the candle series rather than consuming structure events.

Pivots (Pine ``barssince`` semantics)
-------------------------------------
A rolling window of ``pivot_len`` candles tracks the swing state: a candle
whose high is the window maximum turns the swing up (``to_up``), one whose
low is the window minimum turns it down (``to_down``). Each swing flip
records the completed leg's extreme as a pivot, measured over a *local*
window -- the bars since the previous opposite signal (Pine's
``ta.barssince(to_up[1])`` / ``ta.barssince(to_down[1])``, minimum 1 bar) --
NOT since the last opposite pivot. In choppy stretches these local windows
are shorter than the full leg, which renews pivots faster and makes the
market state machine flip more often; this matches the indicator's on-chart
behavior exactly (verified against TradingView on BTCUSDT 15m). The pivot's
index is the most recent bar whose own low/high equaled its running window
extreme (Pine's ``barssince(low_val == low)``). The recorded pivots yield
the alternating ``h0/h1`` / ``l0/l1`` sequence (0 = most recent).

Market structure break (MSB)
----------------------------
With the market bullish, a new low pivot ``l0 < l1 - fib_factor*|h0 - l1|``
confirms a *bearish* MSB; the bullish mirror breaks ``h1`` by
``fib_factor * |h1 - l0|``. The market starts bullish. After a flip, both
the high and the low pivot *values* must change before another flip can
fire (Pine's ``ta.valuewhen`` same-pivot guard, compared by value).

Order block / breaker / mitigation block
----------------------------------------
Anchor candles are tracked by *running scans* re-evaluated every bar,
exactly like the indicator (including its ``[pivot_len]``-lagged window
bound, so the scan uses the pivot index as known ``pivot_len`` bars ago):

- Bu-OB: last bearish candle (open > close) in ``h1i .. l0i[pivot_len]``.
- Be-OB: last bullish candle in ``l1i .. h0i[pivot_len]``.
- Bu-BB/MB: last bullish candle in ``l1i - pivot_len .. h1i``.
- Be-BB/MB: last bearish candle in ``h1i - pivot_len .. l1i``.

Because the scans are running state, an anchor persists from earlier
windows when the current window holds no matching candle -- faithful to the
indicator. A BB/MB is a BREAKER_BLOCK when the impulse-origin extreme swept
the prior one (bullish ``l0 < l1``, bearish ``h0 > h1``), else a
MITIGATION_BLOCK. All zones span the anchor candle's full high-low range.

Zone lifecycle
--------------
ACTIVE -> INVALIDATED

A zone is broken by a single candle *close* beyond its far boundary (below
``price_low`` for a bullish zone, above ``price_high`` for a bearish one);
price trading back inside it does not break it. But a broken zone does not
retire *itself*. The indicator holds its boxes in four FIFO arrays (Bu-OB,
Be-OB, Bu-BB, Be-BB) and its delete helper shifts the array's **front**, so
each break retires the **oldest** box of that queue, and a box that stays
broken keeps shifting the queue on every later bar until it reaches the front
itself. The effect is a running cull that keeps only the recent, unbroken
shelves: a chart carries a handful of zones rather than every level the market
never came back to.

This asymmetry is what a per-zone reading of the rule gets wrong. Measured on
ZECUSDT H1 (1500 candles, 2026-08-18): retiring each broken zone individually
leaves 10 active zones, while the queue rule leaves 4 -- three order blocks,
matching the three boxes the indicator draws on the same series. ``retire_fifo``
switches back to the per-zone rule for callers that want it.

The indicator seeds each array with five ``na`` slots, so its first five
retirements per queue delete nothing. That is not reproduced: over a production
window the placeholders are consumed long before the visible range and change
nothing (verified on ZECUSDT H1: identical zones either way), while on a short
series they would suppress retirement entirely.
"""

from dataclasses import dataclass

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    POIZoneKind,
    POIZoneStatus,
)
from liquidity_hunter.core.domain.poi_zone import POIZone

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Pivot:
    price: float
    index: int


def _queue_key(kind: POIZoneKind) -> str:
    """Which of the indicator's four box arrays a zone belongs to."""
    return "ob" if kind is POIZoneKind.ORDER_BLOCK else "bb"


@dataclass
class _ZoneState:
    direction: MarketDirection
    kind: POIZoneKind
    price_low: float
    price_high: float
    created_index: int
    ob_candle_index: int
    invalidated_index: int | None = None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class POIDetector:
    """Detects MSB-anchored order block zones from a candle series.

    Parameters
    ----------
    pivot_len:
        Rolling window length for swing detection (the indicator's
        "ZigZag Length"). Default 9.
    fib_factor:
        Fraction of the preceding leg's height a new pivot must exceed the
        broken pivot by to confirm an MSB. Default 0.33.
    retire_fifo:
        Whether a break retires the oldest zone of its queue (the indicator's
        rule, default) rather than the broken zone itself. See the module
        docstring's "Zone lifecycle".
    """

    def __init__(
        self,
        pivot_len: int = 9,
        fib_factor: float = 0.33,
        retire_fifo: bool = True,
    ) -> None:
        if pivot_len < 2:
            raise ValueError("pivot_len must be >= 2")
        if not 0.0 <= fib_factor <= 1.0:
            raise ValueError("fib_factor must be within [0, 1]")
        self._pivot_len = pivot_len
        self._fib_factor = fib_factor
        self._retire_fifo = retire_fifo

    def detect(self, candles: list[Candle]) -> list[POIZone]:
        plen = self._pivot_len
        n = len(candles)
        if n < plen:
            return []

        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        opens = [c.open for c in candles]
        closes = [c.close for c in candles]

        # Pine's ta.highest/lowest are na for the first pivot_len-1 bars, so
        # no swing signal can fire there.
        to_up = [False] * n
        to_down = [False] * n
        for i in range(plen - 1, n):
            to_up[i] = highs[i] >= max(highs[i - plen + 1 : i + 1])
            to_down[i] = lows[i] <= min(lows[i - plen + 1 : i + 1])

        swing = 1  # 1 = up leg forming, -1 = down leg forming
        market = MarketDirection.BULLISH
        high_pivots: list[_Pivot] = []
        low_pivots: list[_Pivot] = []
        # Pivot *values* at the last MSB: both must change before the next
        # flip (the indicator's ta.valuewhen same-pivot guard).
        flip_l0: float | None = None
        flip_h0: float | None = None

        last_to_up: int | None = None  # last bar (strictly before i) with to_up
        last_to_down: int | None = None
        last_low_eq: int | None = None  # last bar whose low == its window low
        last_high_eq: int | None = None

        # Running anchor-candle scans + the lagged pivot-index history the
        # OB windows use (l0i / h0i as known `pivot_len` bars ago).
        l0i_hist = [0] * n
        h0i_hist = [0] * n
        bu_ob_idx = be_ob_idx = bu_bb_idx = be_bb_idx = 0

        zones: list[_ZoneState] = []
        live: dict[tuple[MarketDirection, str], list[_ZoneState | None]] = {
            (direction, key): []
            for direction in (MarketDirection.BULLISH, MarketDirection.BEARISH)
            for key in ("ob", "bb")
        }

        for i in range(n):
            # --- pivot value windows (barssince the previous signal, min 1) ---
            cnt = i - last_to_up - 1 if last_to_up is not None else 1
            cnt = cnt if cnt > 0 else 1
            low_val = min(lows[max(0, i - cnt + 1) : i + 1])
            cnt = i - last_to_down - 1 if last_to_down is not None else 1
            cnt = cnt if cnt > 0 else 1
            high_val = max(highs[max(0, i - cnt + 1) : i + 1])
            if low_val == lows[i]:
                last_low_eq = i
            if high_val == highs[i]:
                last_high_eq = i

            # --- swing flip records the leg's pivot ---
            if swing == 1 and to_down[i]:
                swing = -1
                high_pivots.append(
                    _Pivot(high_val, last_high_eq if last_high_eq is not None else i)
                )
            elif swing == -1 and to_up[i]:
                swing = 1
                low_pivots.append(
                    _Pivot(low_val, last_low_eq if last_low_eq is not None else i)
                )

            h0i = high_pivots[-1].index if high_pivots else 0
            h1i = high_pivots[-2].index if len(high_pivots) >= 2 else 0
            l0i = low_pivots[-1].index if low_pivots else 0
            l1i = low_pivots[-2].index if len(low_pivots) >= 2 else 0
            l0i_hist[i] = l0i
            h0i_hist[i] = h0i

            # --- market state machine ---
            flipped: MarketDirection | None = None
            h0 = h1 = l0 = l1 = 0.0
            if len(high_pivots) >= 2 and len(low_pivots) >= 2:
                h0, h1 = high_pivots[-1].price, high_pivots[-2].price
                l0, l1 = low_pivots[-1].price, low_pivots[-2].price
                guarded = (flip_l0 is not None and l0 == flip_l0) or (
                    flip_h0 is not None and h0 == flip_h0
                )
                if not guarded:
                    if (
                        market == MarketDirection.BULLISH
                        and l0 < l1
                        and l0 < l1 - abs(h0 - l1) * self._fib_factor
                    ):
                        market = flipped = MarketDirection.BEARISH
                    elif (
                        market == MarketDirection.BEARISH
                        and h0 > h1
                        and h0 > h1 + abs(h1 - l0) * self._fib_factor
                    ):
                        market = flipped = MarketDirection.BULLISH
                    if flipped is not None:
                        flip_l0, flip_h0 = l0, h0

            # --- running anchor scans (after the market update, before zone
            # creation -- the indicator's in-bar order) ---
            if i >= plen:
                l0i_lag = l0i_hist[i - plen]
                h0i_lag = h0i_hist[i - plen]
                for j in range(h1i, l0i_lag + 1):
                    if opens[j] > closes[j]:
                        bu_ob_idx = j
                for j in range(l1i, h0i_lag + 1):
                    if opens[j] < closes[j]:
                        be_ob_idx = j
                for j in range(max(0, h1i - plen), l1i + 1):
                    if opens[j] > closes[j]:
                        be_bb_idx = j
                for j in range(max(0, l1i - plen), h1i + 1):
                    if opens[j] < closes[j]:
                        bu_bb_idx = j

            # --- zone creation on the MSB flip ---
            if flipped == MarketDirection.BULLISH:
                self._append_zone(
                    zones, candles, flipped, POIZoneKind.ORDER_BLOCK, bu_ob_idx, i, live
                )
                bb_kind = (
                    POIZoneKind.BREAKER_BLOCK
                    if l0 < l1
                    else POIZoneKind.MITIGATION_BLOCK
                )
                self._append_zone(zones, candles, flipped, bb_kind, bu_bb_idx, i, live)
            elif flipped == MarketDirection.BEARISH:
                self._append_zone(
                    zones, candles, flipped, POIZoneKind.ORDER_BLOCK, be_ob_idx, i, live
                )
                bb_kind = (
                    POIZoneKind.BREAKER_BLOCK
                    if h0 > h1
                    else POIZoneKind.MITIGATION_BLOCK
                )
                self._append_zone(zones, candles, flipped, bb_kind, be_bb_idx, i, live)

            # --- lifecycle (checked from the creation candle onward) ---
            if self._retire_fifo:
                self._retire_oldest_on_break(live, closes[i], i)
            else:
                for zone in zones:
                    if zone.invalidated_index is not None:
                        continue
                    if zone.direction == MarketDirection.BULLISH:
                        if closes[i] < zone.price_low:
                            zone.invalidated_index = i
                    elif closes[i] > zone.price_high:
                        zone.invalidated_index = i

            # --- signal trackers feed the *next* bar's windows (to_up[1]) ---
            if to_up[i]:
                last_to_up = i
            if to_down[i]:
                last_to_down = i

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        return [
            POIZone(
                symbol=symbol,
                timeframe=timeframe,
                direction=z.direction,
                kind=z.kind,
                price_low=z.price_low,
                price_high=z.price_high,
                created_at=candles[z.created_index].timestamp,
                ob_candle_timestamp=candles[z.ob_candle_index].timestamp,
                status=(
                    POIZoneStatus.ACTIVE
                    if z.invalidated_index is None
                    else POIZoneStatus.INVALIDATED
                ),
                invalidated_at=(
                    None
                    if z.invalidated_index is None
                    else candles[z.invalidated_index].timestamp
                ),
            )
            for z in zones
        ]

    # ------------------------------------------------------------------
    # Zone creation
    # ------------------------------------------------------------------

    @staticmethod
    def _append_zone(
        zones: list[_ZoneState],
        candles: list[Candle],
        direction: MarketDirection,
        kind: POIZoneKind,
        anchor_index: int,
        msb_index: int,
        live: dict[tuple[MarketDirection, str], list[_ZoneState | None]] | None = None,
    ) -> None:
        anchor = candles[anchor_index]
        if anchor.high <= anchor.low:
            return  # degenerate candle with no range
        zone = _ZoneState(
            direction=direction,
            kind=kind,
            price_low=anchor.low,
            price_high=anchor.high,
            created_index=msb_index,
            ob_candle_index=anchor_index,
        )
        zones.append(zone)
        if live is not None:
            live[(direction, _queue_key(kind))].append(zone)

    @staticmethod
    def _retire_oldest_on_break(
        live: dict[tuple[MarketDirection, str], list[_ZoneState | None]],
        close: float,
        index: int,
    ) -> None:
        """Retire the oldest zone of a queue for each broken zone in it.

        The indicator's own retirement rule (see the module docstring's
        "Zone lifecycle"): a broken box does not delete *itself*, it shifts the
        **front** of its array. So a break retires the oldest box of that queue,
        and the broken one keeps triggering a shift on every later bar it stays
        broken -- draining the queue until it reaches the front itself.
        """
        for queue in live.values():
            broken = 0
            for zone in queue:
                if zone is None:
                    continue
                if zone.direction == MarketDirection.BULLISH:
                    if close < zone.price_low:
                        broken += 1
                elif close > zone.price_high:
                    broken += 1
            for _ in range(min(broken, len(queue))):
                retired = queue.pop(0)
                if retired is not None and retired.invalidated_index is None:
                    retired.invalidated_index = index
