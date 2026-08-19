"""Tests for the unified liquidity-grab stream."""

from datetime import UTC, datetime

from liquidity_hunter.app.liquidity_grabs import build_liquidity_grabs
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

SYMBOL = "BTCUSDT"
TF = TimeFrame.H1


def _ts(hour: int) -> datetime:
    return datetime(2026, 8, 19, hour, tzinfo=UTC)


def _pool(
    *,
    zone_type: LiquidityZoneType = LiquidityZoneType.EQUAL_HIGHS,
    side: LiquiditySide = LiquiditySide.BUY_SIDE,
    price_low: float = 100.0,
    price_high: float = 101.0,
    invalidated_at: datetime | None = None,
    sweep_rejected: bool = False,
) -> LiquidityZone:
    return LiquidityZone(
        symbol=SYMBOL,
        timeframe=TF,
        zone_type=zone_type,
        side=side,
        price_low=price_low,
        price_high=price_high,
        formed_at=_ts(1),
        invalidated_at=invalidated_at,
        is_mitigated=invalidated_at is not None,
        sweep_rejected=sweep_rejected,
    )


def _block(
    *,
    direction: MarketDirection = MarketDirection.BULLISH,
    price_low: float = 90.0,
    price_high: float = 92.0,
    invalidated_at: datetime | None = None,
) -> POIZone:
    return POIZone(
        symbol=SYMBOL,
        timeframe=TF,
        direction=direction,
        price_low=price_low,
        price_high=price_high,
        created_at=_ts(1),
        ob_candle_timestamp=_ts(1),
        invalidated_at=invalidated_at,
    )


def _candle(hour: int, close: float) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=_ts(hour),
        open=close,
        high=close + 5,
        low=close - 5,
        close=close,
        volume=10.0,
        taker_buy_volume=5.0,
    )


#: Closes far outside every zone the fixtures use, on both sides, so an order
#: block's "did price actually close beyond this box" check passes unless a
#: test sets up otherwise.
_CANDLES = [_candle(h, 80.0) for h in range(3, 8)] + [_candle(h, 130.0) for h in (8, 9)]


def _build(
    zones: list[LiquidityZone],
    blocks: list[POIZone],
    candles: list[Candle] | None = None,
) -> list[LiquidityGrab]:
    return build_liquidity_grabs(
        symbol=SYMBOL,
        timeframe=TF,
        liquidity_zones=zones,
        poi_zones=blocks,
        candles=_CANDLES if candles is None else candles,
    )


def test_standing_pools_produce_no_grab() -> None:
    assert _build([_pool()], [_block()]) == []


def test_grab_reports_the_pool_edge_that_was_resting() -> None:
    (grab,) = _build([_pool(invalidated_at=_ts(5), sweep_rejected=True)], [])

    assert grab.timestamp == _ts(5)
    # Buy-side liquidity rests at the top of the pool.
    assert grab.price_level == 101.0
    assert grab.side is LiquiditySide.BUY_SIDE
    assert grab.kinds == [LiquidityPoolKind.EQUAL_LEVEL]
    assert grab.pool_count == 1
    assert grab.outcome is LiquidityGrabOutcome.REJECTED
    assert grab.was_rejected


def test_sell_side_pool_reports_its_low() -> None:
    (grab,) = _build(
        [
            _pool(
                zone_type=LiquidityZoneType.EQUAL_LOWS,
                side=LiquiditySide.SELL_SIDE,
                invalidated_at=_ts(5),
            )
        ],
        [],
    )

    assert grab.price_level == 100.0
    assert grab.side is LiquiditySide.SELL_SIDE
    assert grab.outcome is LiquidityGrabOutcome.SPENT


def test_one_candle_taking_stacked_pools_is_one_grab() -> None:
    zones = [
        _pool(price_low=100.0, price_high=101.0, invalidated_at=_ts(5)),
        _pool(price_low=101.2, price_high=101.5, invalidated_at=_ts(5)),
        _pool(price_low=101.6, price_high=102.0, invalidated_at=_ts(5)),
    ]

    (grab,) = _build(zones, [])

    assert grab.pool_count == 3
    # The candle reached the furthest of them; the others are on the way.
    assert grab.price_level == 102.0


def test_grab_carries_every_kind_it_consumed() -> None:
    zones = [
        _pool(
            zone_type=LiquidityZoneType.EQUAL_LOWS,
            side=LiquiditySide.SELL_SIDE,
            price_low=90.5,
            price_high=91.0,
            invalidated_at=_ts(5),
        )
    ]
    blocks = [_block(price_low=90.0, price_high=92.0, invalidated_at=_ts(5))]

    (grab,) = _build(zones, blocks)

    assert grab.kinds == [LiquidityPoolKind.EQUAL_LEVEL, LiquidityPoolKind.ORDER_BLOCK]
    assert grab.pool_count == 2
    assert grab.price_level == 90.0


def test_opposite_sides_at_one_candle_stay_separate() -> None:
    zones = [
        _pool(invalidated_at=_ts(5)),
        _pool(
            zone_type=LiquidityZoneType.EQUAL_LOWS,
            side=LiquiditySide.SELL_SIDE,
            invalidated_at=_ts(5),
        ),
    ]

    grabs = _build(zones, [])

    assert [g.side for g in grabs] == [LiquiditySide.BUY_SIDE, LiquiditySide.SELL_SIDE]


def test_a_spent_pool_makes_the_whole_moment_spent() -> None:
    zones = [
        _pool(invalidated_at=_ts(5), sweep_rejected=True),
        _pool(price_low=101.2, price_high=101.5, invalidated_at=_ts(5)),
    ]

    (grab,) = _build(zones, [])

    assert grab.outcome is LiquidityGrabOutcome.SPENT


def test_order_block_side_follows_the_boundary_that_was_broken() -> None:
    bullish = _block(direction=MarketDirection.BULLISH, invalidated_at=_ts(5))
    bearish = _block(
        direction=MarketDirection.BEARISH,
        price_low=110.0,
        price_high=112.0,
        invalidated_at=_ts(8),
    )

    demand, supply = _build([], [bullish, bearish])

    # A demand block is broken by a close below it: sell-side liquidity.
    assert demand.side is LiquiditySide.SELL_SIDE
    assert demand.price_level == 90.0
    assert supply.side is LiquiditySide.BUY_SIDE
    assert supply.price_level == 112.0
    # An order block only ever retires on a close, so it is never handed back.
    assert demand.outcome is LiquidityGrabOutcome.SPENT
    assert supply.outcome is LiquidityGrabOutcome.SPENT


def test_grabs_come_back_in_time_order() -> None:
    zones = [
        _pool(invalidated_at=_ts(9)),
        _pool(invalidated_at=_ts(3)),
        _pool(invalidated_at=_ts(6)),
    ]

    grabs = _build(zones, [])

    assert [g.timestamp for g in grabs] == [_ts(3), _ts(6), _ts(9)]


def test_lone_swing_points_are_not_pools() -> None:
    """A single pivot is not resting liquidity, and must not pad the count."""
    zones = [
        _pool(invalidated_at=_ts(5)),
        _pool(
            zone_type=LiquidityZoneType.SWING_HIGH,
            price_low=101.5,
            price_high=101.5,
            invalidated_at=_ts(5),
        ),
        _pool(
            zone_type=LiquidityZoneType.SWING_HIGH,
            price_low=102.0,
            price_high=102.0,
            invalidated_at=_ts(5),
        ),
    ]

    (grab,) = _build(zones, [])

    assert grab.pool_count == 1
    assert grab.price_level == 101.0


def test_order_block_retired_by_the_queue_is_not_a_grab() -> None:
    """FIFO retirement culls the oldest box, which price may never have reached.

    `POIZone.invalidated_at` is set when *some* zone of that queue broke, so
    it is bookkeeping, not evidence. Only a close beyond this box's own far
    boundary says price went there.
    """
    untouched = _block(price_low=40.0, price_high=45.0, invalidated_at=_ts(5))

    assert _build([], [untouched]) == []


def test_excursion_measures_the_wick_beyond_the_level_in_atr() -> None:
    """Depth of the sweep, normalized by what this market moves."""
    # Every fixture candle spans 10 (high = close + 5, low = close - 5), and
    # the gap between the two closes is bigger, so the mean true range is
    # driven by that jump -- measure against what the helper actually builds.
    from statistics import fmean

    from liquidity_hunter.indicators.supertrend import true_range_series

    atr = fmean(true_range_series(_CANDLES))
    # The candle at hour 8 closes at 130, so its high is 135.
    zone = _pool(price_low=100.0, price_high=101.0, invalidated_at=_ts(8))

    (grab,) = _build([zone], [])

    assert grab.excursion_atr == (135.0 - 101.0) / atr


def test_a_grab_that_did_not_reach_beyond_the_level_has_no_depth() -> None:
    zone = _pool(price_low=200.0, price_high=201.0, invalidated_at=_ts(8))

    (grab,) = _build([zone], [])

    assert grab.excursion_atr == 0.0


def test_excursion_is_none_without_candles() -> None:
    zone = _pool(invalidated_at=_ts(5))

    (grab,) = _build([zone], [], candles=[])

    assert grab.excursion_atr is None
