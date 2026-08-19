"""Build the unified stream of liquidity grabs from the mapped pools.

Composition-level, like `NarrativeEngine` and `LiquidityHuntEngine`: it
reads outputs of two different layers (`liquidity` for equal-level pools,
`liquidity` again for order blocks) and speaks about them in one vocabulary.

It emits *every* grab in the series. Deciding which few belong on a chart --
the side the current trend consumes, the events since the last structural
advance -- is presentation, and lives in the frontend with the rest of the
decluttering.
"""

from collections import defaultdict
from datetime import datetime
from statistics import fmean

from liquidity_hunter.core.domain import (
    Candle,
    LiquidityGrab,
    LiquidityGrabOutcome,
    LiquidityPoolKind,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    POIZone,
    TimeFrame,
)
from liquidity_hunter.indicators.supertrend import true_range_series

_KIND_ORDER = {kind: i for i, kind in enumerate(LiquidityPoolKind)}


class _Contribution:
    """One pool consumed at a grab moment."""

    def __init__(
        self,
        kind: LiquidityPoolKind,
        price_level: float,
        outcome: LiquidityGrabOutcome,
    ) -> None:
        self.kind = kind
        self.price_level = price_level
        self.outcome = outcome


#: Only grouped pools count. A standalone swing high or low is a single
#: pivot, not a pool -- the same reason the chart refuses to draw them, and
#: the same mistake the old swing_lookback of 2 was making when it grouped
#: noise into levels that measured worse than random. Counting them here
#: inflates the one number a grab exists to report: how much was resting.
_POOL_ZONE_TYPES = frozenset(
    {LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS}
)


def build_liquidity_grabs(
    *,
    symbol: str,
    timeframe: TimeFrame,
    liquidity_zones: list[LiquidityZone],
    poi_zones: list[POIZone],
    candles: list[Candle],
) -> list[LiquidityGrab]:
    """Collapse every consumed pool into one grab per candle and side."""
    by_moment: dict[tuple[datetime, LiquiditySide], list[_Contribution]] = defaultdict(list)
    by_timestamp = {candle.timestamp: candle for candle in candles}
    # One volatility unit for the whole window, so depths are comparable
    # between grabs of the same chart (and, being a ratio, roughly between
    # charts). A per-candle ATR would make a grab in a quiet stretch look
    # deeper than the same distance in a violent one, which is backwards:
    # the point is how far price went, measured in what this market moves.
    atr = fmean(true_range_series(candles)) if candles else 0.0

    for zone in liquidity_zones:
        if zone.invalidated_at is None or zone.zone_type not in _POOL_ZONE_TYPES:
            continue
        # The pool's own edge is the level that was resting: the top of a
        # buy-side pool, the bottom of a sell-side one.
        level = zone.price_high if zone.side is LiquiditySide.BUY_SIDE else zone.price_low
        outcome = (
            LiquidityGrabOutcome.REJECTED
            if zone.sweep_rejected
            else LiquidityGrabOutcome.SPENT
        )
        by_moment[(zone.invalidated_at, zone.side)].append(
            _Contribution(LiquidityPoolKind.EQUAL_LEVEL, level, outcome)
        )

    for poi in poi_zones:
        # `invalidated_at` says nothing about when this box was taken. The POI
        # queue retires the **oldest** zone of a side whenever any zone breaks
        # (the indicator's `array.shift`, the rule that unclogged the chart),
        # so the field records when the queue got around to this box -- often
        # days after price left it. Measured on BTCUSDT 1h: a bearish block at
        # 65091-65230 was closed through on 10 Aug and stamped 19 Aug, nine
        # days later, which put its tombstone on the wrong rally.
        #
        # Checking that the stamped candle closes beyond the box does not fix
        # it either: once price has left a box behind, *every* later candle
        # closes beyond it. The grab is the **first** close past the far
        # boundary, so that is what is searched for -- from the candle that
        # *confirmed* the box forward, and only within this window (a break
        # before the series starts is not an event in it).
        #
        # `created_at`, not `ob_candle_timestamp`: the anchor candle is chosen
        # in hindsight, when the MSB confirms, and price moving through that
        # level in between broke nothing -- there was no box there yet. On
        # BTCUSDT 1h that lag is a day and a half, and it dated the 65091-65230
        # block's grab to 10 Aug when the box only existed from the 11th.
        bullish = poi.direction is MarketDirection.BULLISH
        side = LiquiditySide.SELL_SIDE if bullish else LiquiditySide.BUY_SIDE
        level = poi.price_low if bullish else poi.price_high
        broken_at: datetime | None = None
        for candle in candles:
            if candle.timestamp < poi.created_at:
                continue
            if (candle.close < level) if bullish else (candle.close > level):
                broken_at = candle.timestamp
                break
        if broken_at is None:
            continue
        # ...and the box has to still be on the board when it breaks. FIFO
        # retirement removes a box because *another* one broke, and a retired
        # box is gone from the chart and from the indicator's picture -- price
        # closing past that level later is not a grab, there is nothing left
        # resting there. Without this the scan reaches back into buried
        # history: BTCUSDT 1h stamped today's rally as taking a 22 Jul block
        # at 66364 that the queue had retired on 3 Aug, an excursion of 14 ATR
        # -- the tell that the level was ancient.
        if poi.invalidated_at is not None and broken_at > poi.invalidated_at:
            continue
        by_moment[(broken_at, side)].append(
            _Contribution(LiquidityPoolKind.ORDER_BLOCK, level, LiquidityGrabOutcome.SPENT)
        )

    grabs: list[LiquidityGrab] = []
    for (timestamp, side), contributions in by_moment.items():
        kinds = sorted({c.kind for c in contributions}, key=lambda k: _KIND_ORDER[k])
        # The reported level is the furthest one reached: a candle that took
        # four stacked pools went as far as the deepest of them, and the
        # shallower ones are on the way there.
        levels = [c.price_level for c in contributions]
        price_level = max(levels) if side is LiquiditySide.BUY_SIDE else min(levels)
        # One pool handed back while another was spent is a spent moment:
        # price closed beyond a level that was resting there.
        outcome = (
            LiquidityGrabOutcome.REJECTED
            if all(c.outcome is LiquidityGrabOutcome.REJECTED for c in contributions)
            else LiquidityGrabOutcome.SPENT
        )
        grab_candle = by_timestamp.get(timestamp)
        excursion: float | None = None
        if grab_candle is not None and atr > 0:
            beyond = (
                grab_candle.high - price_level
                if side is LiquiditySide.BUY_SIDE
                else price_level - grab_candle.low
            )
            excursion = max(0.0, beyond) / atr
        grabs.append(
            LiquidityGrab(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                price_level=price_level,
                side=side,
                kinds=kinds,
                pool_count=len(contributions),
                outcome=outcome,
                excursion_atr=excursion,
            )
        )

    grabs.sort(key=lambda g: (g.timestamp, g.side.value))
    return grabs
