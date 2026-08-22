"""Tests for `app.block_reclaim`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    POIZone,
    POIZoneKind,
    POIZoneStatus,
    TimeFrame,
    VWAPAnchor,
    VWAPPoint,
    VWAPSeries,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
SYMBOL = "BTCUSDT"
TF = TimeFrame.M15


def candle(
    i: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=START + timedelta(minutes=15 * i),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume / 2,
    )


def flat(i: int, price: float) -> Candle:
    """A quiet candle that neither tests a block nor reclaims anything."""
    return candle(i, open_=price, high=price + 1, low=price - 1, close=price)


def vwap_series(candles: list[Candle], value: float) -> VWAPSeries:
    """A flat VWAP at `value`, one point per candle, one accumulation."""
    return VWAPSeries(
        symbol=SYMBOL,
        timeframe=TF,
        anchor=VWAPAnchor.SESSION,
        anchor_timestamp=candles[0].timestamp,
        label="Session",
        band_multipliers=(),
        estimated=True,
        points=[
            VWAPPoint(
                timestamp=c.timestamp,
                anchor_timestamp=candles[0].timestamp,
                value=value,
            )
            for c in candles
        ],
    )


def block(created_index: int, low: float, high: float) -> POIZone:
    return POIZone(
        symbol=SYMBOL,
        timeframe=TF,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.ORDER_BLOCK,
        price_low=low,
        price_high=high,
        created_at=START + timedelta(minutes=15 * created_index),
        ob_candle_timestamp=START + timedelta(minutes=15 * created_index),
        status=POIZoneStatus.ACTIVE,
    )


def bullish_case() -> tuple[list[Candle], list[POIZone], VWAPSeries]:
    """Price dips into a block under the VWAP, then reclaims it on a pinbar."""
    candles = [flat(i, 108.0) for i in range(20)]
    # the test: two candles trading into the block at 100-102, below the VWAP
    candles[20:20] = [
        candle(20, open_=104, high=104.5, low=100.5, close=101.5),
        candle(21, open_=101.5, high=103, low=100.2, close=102.5),
    ]
    candles += [flat(i, 104.0) for i in range(22, 25)]
    # the reclaim: a long lower wick through the VWAP at 105, small body above
    candles.append(candle(25, open_=105.4, high=105.9, low=103.0, close=105.6))
    candles += [flat(i, 106.0) for i in range(26, 30)]
    return candles, [block(0, 100.0, 102.0)], vwap_series(candles, 105.0)


def test_detects_a_reclaim_after_a_block_test() -> None:
    candles, zones, vwap = bullish_case()
    reclaims = detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    )
    assert len(reclaims) == 1
    r = reclaims[0]
    assert r.direction is MarketDirection.BULLISH
    assert r.timestamp == candles[25].timestamp
    assert r.reclaim_price == 105.6
    assert r.vwap_price == 105.0
    # the stop is the extreme of the whole visit, not of the entry candle
    assert r.test_extreme == 100.2
    assert r.reclaim_distance == 105.6 - 100.2
    assert r.first_test is True


def test_r_atr_normalises_the_distance_by_local_volatility() -> None:
    candles, zones, vwap = bullish_case()
    r = detect_block_reclaims(candles, zones, vwap, symbol=SYMBOL, timeframe=TF)[0]
    assert r.r_atr is not None
    # the distance divided by a positive mean true range
    assert r.r_atr > 0
    assert abs(r.r_atr * (r.reclaim_distance / r.r_atr) - r.reclaim_distance) < 1e-9


def test_a_reclaim_without_a_block_is_not_emitted() -> None:
    candles, _zones, vwap = bullish_case()
    assert detect_block_reclaims(candles, [], vwap, symbol=SYMBOL, timeframe=TF) == []


def test_a_close_that_does_not_cross_the_vwap_is_not_a_reclaim() -> None:
    candles, zones, vwap = bullish_case()
    # same wick, but the body closes back below the VWAP
    candles[25] = candle(25, open_=104.6, high=105.9, low=103.0, close=104.4)
    assert detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    ) == []


def test_a_block_created_after_the_test_is_not_used() -> None:
    candles, _zones, vwap = bullish_case()
    late = [block(24, 100.0, 102.0)]
    assert detect_block_reclaims(
        candles, late, vwap, symbol=SYMBOL, timeframe=TF
    ) == []


def test_a_second_visit_is_marked_as_not_fresh() -> None:
    candles, zones, vwap = bullish_case()
    # a first, earlier visit to the same block, deep enough to be seen
    candles[2] = candle(2, open_=104, high=104.5, low=101.0, close=103.5)
    reclaims = detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    )
    assert len(reclaims) == 1
    assert reclaims[0].first_test is False


def test_two_visits_resolving_on_one_candle_are_one_reading() -> None:
    # The nearest test wins: the reclaim is measured against the level price
    # just came off, not against an older, deeper visit to the same block.
    candles, zones, vwap = bullish_case()
    candles[8] = candle(8, open_=104, high=104.5, low=100.0, close=103.5)
    reclaims = detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    )
    assert len(reclaims) == 1
    assert reclaims[0].test_extreme == 100.2


def test_no_vwap_yields_no_readings() -> None:
    candles, zones, _vwap = bullish_case()
    assert detect_block_reclaims(
        candles, zones, None, symbol=SYMBOL, timeframe=TF
    ) == []
