"""Tests for liquidity zone sweep marking."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import LiquiditySide, LiquidityZone, LiquidityZoneType, TimeFrame
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle

_ZONE_DEFAULTS = {
    "symbol": "BTCUSDT",
    "timeframe": TimeFrame.H1,
    "strength": 0.67,
}

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _eqh(price: float, formed_at: datetime) -> LiquidityZone:
    return LiquidityZone(
        **_ZONE_DEFAULTS,
        zone_type=LiquidityZoneType.EQUAL_HIGHS,
        side=LiquiditySide.BUY_SIDE,
        price_high=price,
        price_low=price - 10,
        formed_at=formed_at,
    )


def _eql(price: float, formed_at: datetime) -> LiquidityZone:
    return LiquidityZone(
        **_ZONE_DEFAULTS,
        zone_type=LiquidityZoneType.EQUAL_LOWS,
        side=LiquiditySide.SELL_SIDE,
        price_high=price + 10,
        price_low=price,
        formed_at=formed_at,
    )


class TestMarkSweptZones:
    def test_eqh_swept_by_wick(self) -> None:
        zone = _eqh(100.0, BASE)
        candles = [
            make_candle(0, high=99.0, low=95.0),
            make_candle(1, high=101.0, low=97.0),
        ]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is True
        assert result[0].invalidated_at == candles[1].timestamp

    def test_eql_swept_by_wick(self) -> None:
        zone = _eql(100.0, BASE)
        candles = [
            make_candle(0, high=105.0, low=101.0),
            make_candle(1, high=105.0, low=99.0),
        ]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is True
        assert result[0].invalidated_at == candles[1].timestamp

    def test_not_swept_stays_active(self) -> None:
        zone = _eqh(100.0, BASE)
        candles = [
            make_candle(0, high=98.0, low=95.0),
            make_candle(1, high=99.0, low=96.0),
        ]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is False
        assert result[0].invalidated_at is None

    def test_candle_at_formed_at_is_ignored(self) -> None:
        zone = _eqh(100.0, BASE)
        candles = [make_candle(0, high=101.0, low=95.0)]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is False

    def test_first_sweep_candle_wins(self) -> None:
        zone = _eqh(100.0, BASE)
        candles = [
            make_candle(1, high=101.0, low=97.0),
            make_candle(2, high=105.0, low=99.0),
        ]
        result = mark_swept_zones([zone], candles)

        assert result[0].invalidated_at == candles[0].timestamp

    def test_already_mitigated_zone_unchanged(self) -> None:
        ts = BASE + timedelta(hours=1)
        zone = _eqh(100.0, BASE).model_copy(
            update={"is_mitigated": True, "invalidated_at": ts}
        )
        candles = [make_candle(2, high=110.0, low=95.0)]
        result = mark_swept_zones([zone], candles)

        assert result[0].invalidated_at == ts

    def test_multiple_zones_independent(self) -> None:
        eqh = _eqh(100.0, BASE)
        eql = _eql(90.0, BASE)
        candles = [
            make_candle(1, high=101.0, low=91.0),
        ]
        result = mark_swept_zones([eqh, eql], candles)

        assert result[0].is_mitigated is True
        assert result[1].is_mitigated is False

    def test_eql_exact_touch_not_swept(self) -> None:
        zone = _eql(100.0, BASE)
        candles = [make_candle(1, high=105.0, low=100.0)]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is False

    def test_eqh_exact_touch_not_swept(self) -> None:
        zone = _eqh(100.0, BASE)
        candles = [make_candle(1, high=100.0, low=95.0)]
        result = mark_swept_zones([zone], candles)

        assert result[0].is_mitigated is False
