"""Tests for the MSB order block `POIDetector`."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    POIZoneKind,
    POIZoneStatus,
    TimeFrame,
)
from liquidity_hunter.liquidity import POIDetector

SYMBOL = "BTCUSDT"
TF = TimeFrame.H1
T0 = datetime(2024, 6, 1, tzinfo=UTC)


def _ts(i: int) -> datetime:
    return T0 + timedelta(hours=i)


def _candle(i: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=_ts(i),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0,
    )


# A bearish-MSB scenario (pivot_len=3): an up leg to 105, a down leg to 99.5,
# a lower recovery to 104, then a drop through the prior low far enough to
# clear the fib extension. The MSB confirms at bar 18 (the swing flip that
# records the 97.0 low pivot); the order block is the last bullish candle of
# the 99.5 -> 104 up leg (bar 12, range 102.8-104).
def _bearish_msb_candles() -> list[Candle]:
    return [
        # Up leg A -> high pivot 105 @ 4
        _candle(0, 100, 101, 99, 100.5),
        _candle(1, 100.5, 102, 100, 101.5),
        _candle(2, 101.5, 103, 101, 102.5),
        _candle(3, 102.5, 104, 102, 103.5),
        _candle(4, 103.5, 105, 103, 104.5),
        # Down leg B -> low pivot 99.5 @ 8
        _candle(5, 104.5, 104.8, 102.5, 103),
        _candle(6, 103, 103.2, 101, 101.5),
        _candle(7, 101.5, 101.8, 100, 100.5),
        _candle(8, 100.5, 100.8, 99.5, 100),
        # Up leg C (lower high) -> high pivot 104 @ 12
        _candle(9, 100, 101.5, 99.8, 101.2),
        _candle(10, 101.2, 102.5, 101, 102.2),
        _candle(11, 102.2, 103.5, 102, 103.2),
        _candle(12, 103.2, 104, 102.8, 103.8),
        # Down leg D breaking 99.5 by more than 0.33 * |104 - 99.5|
        _candle(13, 103.8, 103.9, 102, 102.3),
        _candle(14, 102.3, 102.5, 100.5, 100.8),
        _candle(15, 100.8, 101, 98.5, 98.7),
        _candle(16, 98.7, 98.9, 97.0, 97.3),
        # Turn back up -> low pivot 97.0 recorded @ 18: bearish MSB fires
        _candle(17, 97.3, 98.5, 97.1, 98.2),
        _candle(18, 98.2, 99.5, 98.0, 99.2),
    ]


class TestBearishMSB:
    def test_creates_supply_zone_from_last_bullish_candle(self) -> None:
        zones = POIDetector(pivot_len=3).detect(_bearish_msb_candles())

        assert len(zones) == 2
        zone = zones[0]
        assert zone.kind == POIZoneKind.ORDER_BLOCK
        assert zone.direction == MarketDirection.BEARISH
        # Full range of the last bullish candle of the l1 -> h0 leg (bar 12).
        assert zone.price_low == 102.8
        assert zone.price_high == 104.0
        assert zone.ob_candle_timestamp == _ts(12)
        assert zone.created_at == _ts(18)
        assert zone.status == POIZoneStatus.ACTIVE
        assert zone.invalidated_at is None

    def test_creates_breaker_block_from_broken_low_leg(self) -> None:
        zones = POIDetector(pivot_len=3).detect(_bearish_msb_candles())

        # The block anchors at the last bearish candle of the broken-low leg
        # (bar 8). Kind is BREAKER here: with no to_down signal before the
        # first swing flip, the bootstrap h1 pivot collapses to the flip
        # bar's own high (103.2, Pine's nz(...,1) window) rather than the
        # 105 leg top, so h0 (104) > h1.
        block = zones[1]
        assert block.kind == POIZoneKind.BREAKER_BLOCK
        assert block.direction == MarketDirection.BEARISH
        assert block.price_low == 99.5
        assert block.price_high == 100.8
        assert block.ob_candle_timestamp == _ts(8)
        assert block.created_at == _ts(18)

    def test_close_inside_zone_does_not_retire_it(self) -> None:
        candles = [
            *_bearish_msb_candles(),
            # Close back *inside* the OB zone (102.8-104): still active. The
            # mitigation block below (99.5-100.8) is closed through -> retired.
            _candle(19, 99.2, 103.5, 99.0, 103.5),
        ]
        zones = POIDetector(pivot_len=3).detect(candles)

        assert len(zones) == 2
        assert zones[0].status == POIZoneStatus.ACTIVE
        assert zones[1].status == POIZoneStatus.INVALIDATED

    def test_single_close_beyond_top_invalidates(self) -> None:
        candles = [
            *_bearish_msb_candles(),
            _candle(19, 99.2, 99.5, 97.5, 98),
            # One close above the zone top (104) retires the supply zone.
            _candle(20, 98, 105.5, 97.8, 105.2),
        ]
        zones = POIDetector(pivot_len=3).detect(candles)

        assert len(zones) == 2
        assert zones[0].status == POIZoneStatus.INVALIDATED
        assert zones[0].invalidated_at == _ts(20)

    def test_fib_factor_gates_the_break(self) -> None:
        # With fib_factor=1.0 the 97.0 low is not deep enough below 99.5
        # (would need < 95.0), so no MSB confirms.
        zones = POIDetector(pivot_len=3, fib_factor=1.0).detect(_bearish_msb_candles())
        assert zones == []


# Variant of the bearish scenario where the recovery leg tops at 106 —
# *above* the prior 105 high (h0 > h1) — before the breakdown, so the
# broken-low-leg block is a breaker block instead of a mitigation block.
def _bearish_breaker_candles() -> list[Candle]:
    return [
        *_bearish_msb_candles()[:9],
        # Up leg C sweeping the prior 105 high -> high pivot 106 @ 12
        _candle(9, 100, 101.5, 99.8, 101.2),
        _candle(10, 101.2, 103, 101, 102.8),
        _candle(11, 102.8, 104.5, 102.5, 104.2),
        _candle(12, 104.2, 106, 104, 105.7),
        # Down leg breaking 99.5 by more than 0.33 * |106 - 99.5|
        _candle(13, 105.7, 105.8, 103.9, 104.2),
        _candle(14, 104.2, 104.4, 102.3, 102.6),
        _candle(15, 102.6, 102.8, 100.2, 100.5),
        _candle(16, 100.5, 100.7, 96.8, 97.1),
        # Turn back up -> low pivot 96.8 recorded @ 19: bearish MSB fires
        _candle(17, 97.1, 98.3, 96.9, 98.0),
        _candle(18, 98.0, 99.6, 97.8, 99.3),
        _candle(19, 99.3, 100.9, 99.1, 100.6),
    ]


class TestBreakerBlock:
    def test_swept_prior_high_makes_breaker_block(self) -> None:
        zones = POIDetector(pivot_len=3).detect(_bearish_breaker_candles())

        assert len(zones) == 2
        assert zones[0].kind == POIZoneKind.ORDER_BLOCK
        assert zones[0].price_high == 106.0

        block = zones[1]
        assert block.kind == POIZoneKind.BREAKER_BLOCK
        assert block.direction == MarketDirection.BEARISH
        assert block.price_low == 99.5
        assert block.price_high == 100.8
        assert block.ob_candle_timestamp == _ts(8)
        assert block.created_at == _ts(19)


# Continuation of the bearish scenario: a strong rally to 107.5, a shallow
# pullback to 102.5, confirming a bullish MSB at bar 25 (107.5 breaks the 104
# prior high by more than the fib extension). The same-pivot guard blocks the
# earlier flip at bar 22 (the low pivot is still the one that fired the
# bearish MSB). The order block is the last bearish candle of the decline into
# the 102.5 low (bar 24, range 102.5-103.6).
def _bullish_msb_candles() -> list[Candle]:
    return [
        *_bearish_msb_candles(),
        # Impulsive rally through the old supply (invalidates the bearish zone)
        _candle(19, 99.2, 106.0, 99.0, 105.5),
        _candle(20, 105.5, 107.5, 105.0, 107.0),
        _candle(21, 107.0, 107.2, 104.5, 105.0),
        # Swing flip down @ 22 records the 107.5 high pivot (guard blocks MSB)
        _candle(22, 105.0, 105.2, 104.0, 104.3),
        _candle(23, 104.3, 104.5, 103.0, 103.4),
        _candle(24, 103.4, 103.6, 102.5, 102.8),
        # Swing flip up @ 25 records the 102.5 low pivot: bullish MSB fires
        _candle(25, 102.8, 104.8, 102.6, 104.5),
    ]


class TestBullishMSB:
    def test_creates_demand_zone(self) -> None:
        zones = POIDetector(pivot_len=3).detect(_bullish_msb_candles())

        # Bearish OB + breaker from bar 18, bullish OB + MB from bar 25.
        assert len(zones) == 4
        # The rally closed above both bearish zones on bar 19.
        assert zones[0].direction == MarketDirection.BEARISH
        assert zones[0].status == POIZoneStatus.INVALIDATED
        assert zones[0].invalidated_at == _ts(19)
        assert zones[1].status == POIZoneStatus.INVALIDATED

        zone = zones[2]
        assert zone.kind == POIZoneKind.ORDER_BLOCK
        assert zone.direction == MarketDirection.BULLISH
        # The Bu-OB scan window ends at the pivot_len-lagged low-pivot index
        # (Pine's l0i[zigzag_len]), which predates the 102.5 pivot at bar 24,
        # so the anchor is the last bearish candle up to bar 16 (97.0-98.9).
        assert zone.price_low == 97.0
        assert zone.price_high == 98.9
        assert zone.ob_candle_timestamp == _ts(16)
        assert zone.created_at == _ts(25)
        assert zone.status == POIZoneStatus.ACTIVE

        block = zones[3]
        assert block.kind == POIZoneKind.MITIGATION_BLOCK  # l0 (102.5) >= l1
        assert block.direction == MarketDirection.BULLISH
        assert block.ob_candle_timestamp == _ts(12)
        assert block.status == POIZoneStatus.ACTIVE

    def test_single_close_below_bottom_invalidates(self) -> None:
        candles = [
            *_bullish_msb_candles(),
            # One close below the demand zone bottom (97.0) retires it.
            _candle(26, 104.5, 104.7, 96.5, 96.8),
        ]
        zones = POIDetector(pivot_len=3).detect(candles)

        assert len(zones) == 4
        assert zones[2].status == POIZoneStatus.INVALIDATED
        assert zones[2].invalidated_at == _ts(26)


# --- Real-data regression -------------------------------------------------
# BTCUSDT perp 15m, 2026-06-25 13:15 -> 2026-07-11 04:00 UTC (1500 candles).
# Expectations verified visually against the original MSB-OB TradingView
# indicator (EmreKb) on the same series: the 07-09 17:45 bullish MSB's
# Bu-OB/Bu-MB pair (the ~62.6-62.85k green boxes) and the 07-10 16:45
# bearish MSB's Be-OB + Be-BB pair. The previous leg-extreme pivot
# semantics missed the 07-09 17:45 flip entirely (wider pivot windows ->
# fewer flips), which is the divergence that motivated the faithful port.
_BTC15M_DATA = Path(__file__).parent / "data" / "btcusdt_15m_2026_06_25_07_11.json"


def _load_btc15m_candles() -> list[Candle]:
    rows = json.loads(_BTC15M_DATA.read_text())
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def test_real_btcusdt_15m_matches_tradingview_boxes() -> None:
    zones = POIDetector().detect(_load_btc15m_candles())
    by_creation = {
        (z.created_at.strftime("%m-%d %H:%M"), z.kind): z for z in zones
    }

    bull_ob = by_creation[("07-09 17:45", POIZoneKind.ORDER_BLOCK)]
    assert bull_ob.direction == MarketDirection.BULLISH
    assert bull_ob.price_low == pytest.approx(62661.0)
    assert bull_ob.price_high == pytest.approx(62848.9)
    assert bull_ob.ob_candle_timestamp == datetime(2026, 7, 9, 13, 15, tzinfo=UTC)
    assert bull_ob.status == POIZoneStatus.ACTIVE

    bull_mb = by_creation[("07-09 17:45", POIZoneKind.MITIGATION_BLOCK)]
    assert bull_mb.price_low == pytest.approx(62569.4)
    assert bull_mb.price_high == pytest.approx(62824.7)
    assert bull_mb.status == POIZoneStatus.ACTIVE

    bear_ob = by_creation[("07-10 16:45", POIZoneKind.ORDER_BLOCK)]
    assert bear_ob.direction == MarketDirection.BEARISH
    assert bear_ob.price_low == pytest.approx(64348.1)
    assert bear_ob.price_high == pytest.approx(64680.0)

    bear_bb = by_creation[("07-10 16:45", POIZoneKind.BREAKER_BLOCK)]
    assert bear_bb.price_low == pytest.approx(64127.0)
    assert bear_bb.price_high == pytest.approx(64255.7)

    # Full-series regression: the flip sequence is deterministic.
    assert len(zones) == 44


class TestEdgeCases:
    def test_series_shorter_than_pivot_len_returns_empty(self) -> None:
        candles = [_candle(i, 100, 101, 99, 100.5) for i in range(2)]
        assert POIDetector(pivot_len=3).detect(candles) == []

    def test_flat_series_produces_no_zones(self) -> None:
        candles = [_candle(i, 100, 101, 99, 100.5) for i in range(30)]
        assert POIDetector(pivot_len=3).detect(candles) == []

    def test_invalid_params_raise(self) -> None:
        with pytest.raises(ValueError):
            POIDetector(pivot_len=1)
        with pytest.raises(ValueError):
            POIDetector(fib_factor=1.5)
