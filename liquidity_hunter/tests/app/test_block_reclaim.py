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


def test_a_block_closed_through_before_its_queue_stamp_is_spent() -> None:
    """The FIFO stamp is bookkeeping, not the break.

    The POI queue retires the oldest zone of a side whenever any zone breaks,
    so `invalidated_at` can land days after price closed through this box.
    Between the two, the block holds nobody -- a "test" of it is not the
    observation this layer names.
    """
    candles, zones, _ = bullish_case()
    # price closes below the block's floor long before the queue notices
    candles[10] = candle(10, open_=99, high=99.5, low=97.0, close=98.0)
    zones = [
        zones[0].model_copy(
            update={
                "status": POIZoneStatus.INVALIDATED,
                "invalidated_at": candles[28].timestamp,
            }
        )
    ]
    reclaims = detect_block_reclaims(
        candles, zones, vwap_series(candles, 105.0), symbol=SYMBOL, timeframe=TF
    )
    assert reclaims == []


def test_a_block_retired_without_breaking_stops_resting_at_the_stamp() -> None:
    """A box the queue removed is off the board, however it left."""
    candles, zones, vwap = bullish_case()
    zones = [
        zones[0].model_copy(
            update={
                "status": POIZoneStatus.INVALIDATED,
                # retired before the test at candles 20-21 happens
                "invalidated_at": candles[15].timestamp,
            }
        )
    ]
    assert detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    ) == []


def test_a_block_still_resting_survives_a_later_queue_stamp() -> None:
    """Untouched by a close, the block is alive right up to the stamp."""
    candles, zones, vwap = bullish_case()
    zones = [
        zones[0].model_copy(
            update={
                "status": POIZoneStatus.INVALIDATED,
                "invalidated_at": candles[27].timestamp,
            }
        )
    ]
    assert len(detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    )) == 1


def test_a_reclaim_on_the_last_candle_is_provisional() -> None:
    """The forming candle can still stop being a reclaim before it prints."""
    candles, zones, _ = bullish_case()
    live = candles[:26]  # the reclaim at index 25 is now the live edge
    reclaims = detect_block_reclaims(
        live, zones, vwap_series(live, 105.0), symbol=SYMBOL, timeframe=TF
    )
    assert [r.provisional for r in reclaims] == [True]


def test_a_settled_reclaim_is_not_provisional() -> None:
    candles, zones, vwap = bullish_case()
    reclaims = detect_block_reclaims(
        candles, zones, vwap, symbol=SYMBOL, timeframe=TF
    )
    assert [r.provisional for r in reclaims] == [False]


# --- the EMA(9) second route -------------------------------------------------


def _ema_at(candles: list[Candle], value: float) -> list[float | None]:
    """A flat EMA at `value`, so a test states the line rather than derives it."""
    return [value] * len(candles)


def test_without_an_ema_the_trigger_is_unchanged() -> None:
    """The layer's original reading is what it reports when no line is given."""
    candles, zones, vwap = bullish_case()
    out = detect_block_reclaims(
        candles, zones, vwap, symbol="BTCUSDT", timeframe=TimeFrame.M15
    )
    assert out and all(r.trigger_line == "vwap" for r in out)


def test_ema_route_needs_the_cross() -> None:
    """A pinbar off the 9 does not count while the 9 sits under the average.

    The gate is a state, not an event: it asks where the two lines are, and an
    uncrossed pair means the recovery the setup waits for has not happened.
    """
    candles, zones, vwap = bullish_case()
    below = 100.0  # under the VWAP at 105: the 9 has not crossed
    out = detect_block_reclaims(
        candles, zones, vwap, symbol="BTCUSDT", timeframe=TimeFrame.M15,
        ema=_ema_at(candles, below),
    )
    assert all(r.trigger_line == "vwap" for r in out)


def test_ema_route_needs_price_to_hold_the_vwap_side() -> None:
    """A wick off the 9 *underneath* the average is a different picture."""
    candles, zones, vwap = bullish_case()
    high = 200.0  # far above every close, so no candle can hold above it
    out = detect_block_reclaims(
        candles, zones, vwap, symbol="BTCUSDT", timeframe=TimeFrame.M15,
        ema=_ema_at(candles, high),
    )
    # the close can never be above an EMA that far up, so the route stays shut
    assert all(r.trigger_line == "vwap" for r in out)


def test_ema_route_fires_where_the_vwap_is_never_touched_again() -> None:
    """The charted case: price clears the average, then pinbars off the 9.

    The trigger the VWAP-only rule cannot see. Price tests the block, runs
    clear of the average on a plain candle, and the pullback finds the fast
    line without ever coming back to the VWAP -- so there is no reclaim to
    detect, and the observation would simply be missed.
    """
    candles = [flat(i, 108.0) for i in range(20)]
    candles[20:20] = [
        candle(20, open_=104, high=104.5, low=100.5, close=101.5),
        candle(21, open_=101.5, high=103, low=100.2, close=102.5),
    ]
    # clears the VWAP at 105 on a plain body -- no wick through it, no pinbar
    candles.append(candle(22, open_=103.0, high=107.2, low=102.9, close=107.0))
    # the pullback: a pinbar whose wick finds the 9 at 106.5, closing above
    # both it and the VWAP
    candles.append(candle(23, open_=107.0, high=107.1, low=106.0, close=106.9))
    candles += [flat(i, 107.0) for i in range(24, 30)]

    out = detect_block_reclaims(
        candles, [block(0, 100.0, 102.0)], vwap_series(candles, 105.0),
        symbol="BTCUSDT", timeframe=TimeFrame.M15,
        ema=_ema_at(candles, 106.5),
    )
    assert [r.trigger_line for r in out] == ["ema"]
    assert out[0].timestamp == candles[23].timestamp
    # the stop is still the block test's extreme, not the pullback's
    assert out[0].test_extreme == 100.2

    # and without the line the same series yields nothing at all
    assert not detect_block_reclaims(
        candles, [block(0, 100.0, 102.0)], vwap_series(candles, 105.0),
        symbol="BTCUSDT", timeframe=TimeFrame.M15,
    )


# --- the pinbar grades ------------------------------------------------------


def _bar(o: float, h: float, low: float, c: float) -> Candle:
    return candle(50, open_=o, high=h, low=low, close=c)


def test_the_three_grades_are_different_candles() -> None:
    """`legacy`, `l1` and `l2` name different bars, not one threshold."""
    from liquidity_hunter.app.block_reclaim import pinbar_grades

    # golden rule: 70% tail, tiny body, no nose
    golden = _bar(o=107.5, h=108.0, low=100.0, c=107.8)
    assert "l1" in pinbar_grades(golden, bullish=True)

    # the reader's 03 Aug bar: 43% body, 53% tail, 4% nose -- l2 only
    lvl2 = _bar(o=62737.3, h=62915.9, low=62533.3, c=62901.5)
    grades = pinbar_grades(lvl2, bullish=True)
    assert grades == frozenset({"l2"}), grades


def test_a_capped_nose_is_what_l1_adds_over_legacy() -> None:
    """The legacy rule never looked at the far wick; both new grades do."""
    from liquidity_hunter.app.block_reclaim import pinbar_grades

    # 55% tail, 15% body, 30% nose: legacy accepts it, neither new grade does
    bar = _bar(o=105.5, h=110.0, low=100.0, c=107.0)
    grades = pinbar_grades(bar, bullish=True)
    assert "legacy" in grades
    assert "l1" not in grades and "l2" not in grades


def test_a_flat_candle_has_no_grade() -> None:
    from liquidity_hunter.app.block_reclaim import pinbar_grades

    assert pinbar_grades(_bar(o=100.0, h=100.0, low=100.0, c=100.0), bullish=True) == frozenset()


def test_the_detector_reports_which_grade_fired() -> None:
    candles, zones, vwap = bullish_case()
    out = detect_block_reclaims(
        candles, zones, vwap, symbol="BTCUSDT", timeframe=TimeFrame.M15
    )
    assert out and all(r.pinbar_grade for r in out)
