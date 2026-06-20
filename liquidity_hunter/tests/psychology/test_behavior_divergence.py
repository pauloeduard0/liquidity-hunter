"""Tests for ``BehaviorDivergenceAnalyzer``."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    DivergenceType,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.psychology import BehaviorDivergenceAnalyzer

SYMBOL = "BTCUSDT"
TF = TimeFrame.H1
T0 = datetime(2024, 6, 1, tzinfo=UTC)


def _ts(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def _candle(
    hour: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    taker_buy_volume: float = 50.0,
) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=_ts(hour),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy_volume,
    )


def _zone(
    price: float,
    *,
    side: LiquiditySide,
    zone_type: LiquidityZoneType = LiquidityZoneType.EQUAL_LOWS,
    strength: float = 0.5,
) -> LiquidityZone:
    return LiquidityZone(
        symbol=SYMBOL,
        timeframe=TF,
        zone_type=zone_type,
        side=side,
        price_high=price,
        price_low=price,
        formed_at=T0,
        strength=strength,
    )


def _structure(
    hour: int,
    event: StructureEvent,
    direction: MarketDirection,
    price: float,
) -> MarketStructure:
    return MarketStructure(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=_ts(hour),
        event=event,
        direction=direction,
        price_level=price,
        scope=StructureScope.INTERNAL,
    )


def _vd(candle: Candle) -> float:
    return 2 * candle.taker_buy_volume - candle.volume


class TestDistribution:
    """Price rising + negative VD near buy-side zone."""

    def test_distribution_detected_near_buy_side_zone(self) -> None:
        zone = _zone(
            105.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
        )
        candles = [
            _candle(0, 100, 101, 99, 101, volume=200, taker_buy_volume=60),
            _candle(1, 101, 102, 100, 102, volume=200, taker_buy_volume=60),
            _candle(2, 102, 103, 101, 103, volume=200, taker_buy_volume=60),
            _candle(3, 103, 104, 102, 104, volume=200, taker_buy_volume=60),
            _candle(4, 104, 105, 103, 105, volume=200, taker_buy_volume=60),
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.05,
            min_price_change_pct=0.01,
            min_vd_ratio=0.1,
        )
        results = analyzer.analyze(candles, vd, [zone], [])

        assert len(results) >= 1
        dist = [r for r in results if r.divergence_type == DivergenceType.DISTRIBUTION]
        assert len(dist) >= 1
        assert dist[0].direction == MarketDirection.BULLISH
        assert dist[0].nearest_zone_side == LiquiditySide.BUY_SIDE
        assert dist[0].volume_delta_avg < 0

    def test_no_distribution_without_buy_side_zone(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)
        candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 101 + i, volume=200, taker_buy_volume=60)
            for i in range(5)
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.05,
            min_price_change_pct=0.01,
            min_vd_ratio=0.1,
        )
        results = analyzer.analyze(candles, vd, [zone], [])
        dist = [r for r in results if r.divergence_type == DivergenceType.DISTRIBUTION]
        assert len(dist) == 0


class TestAccumulation:
    """Price falling + positive VD near sell-side zone."""

    def test_accumulation_detected_near_sell_side_zone(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)
        candles = [
            _candle(0, 100, 101, 99, 99, volume=200, taker_buy_volume=140),
            _candle(1, 99, 100, 98, 98, volume=200, taker_buy_volume=140),
            _candle(2, 98, 99, 97, 97, volume=200, taker_buy_volume=140),
            _candle(3, 97, 98, 96, 96, volume=200, taker_buy_volume=140),
            _candle(4, 96, 97, 95, 95, volume=200, taker_buy_volume=140),
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.06,
            min_price_change_pct=0.01,
            min_vd_ratio=0.1,
        )
        results = analyzer.analyze(candles, vd, [zone], [])

        accum = [r for r in results if r.divergence_type == DivergenceType.ACCUMULATION]
        assert len(accum) >= 1
        assert accum[0].direction == MarketDirection.BEARISH
        assert accum[0].nearest_zone_side == LiquiditySide.SELL_SIDE
        assert accum[0].volume_delta_avg > 0


class TestExhaustion:
    """VD magnitude declining after BOS while price continues."""

    def test_exhaustion_after_bullish_bos(self) -> None:
        candles = [
            _candle(0, 100, 102, 99, 101, volume=200, taker_buy_volume=170),
            _candle(1, 101, 103, 100, 102, volume=200, taker_buy_volume=170),
            _candle(2, 102, 104, 101, 103, volume=200, taker_buy_volume=160),
            _candle(3, 103, 104, 102, 103.5, volume=200, taker_buy_volume=120),
            _candle(4, 103.5, 104.5, 103, 104, volume=200, taker_buy_volume=110),
            _candle(5, 104, 105, 103.5, 104.5, volume=200, taker_buy_volume=105),
            _candle(6, 104.5, 105.5, 104, 105, volume=200, taker_buy_volume=103),
        ]
        vd = [_vd(c) for c in candles]

        bos = _structure(0, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 101.0)

        analyzer = BehaviorDivergenceAnalyzer(window_size=7)
        results = analyzer.analyze(candles, vd, [], [bos])

        exhaust = [r for r in results if r.divergence_type == DivergenceType.EXHAUSTION]
        assert len(exhaust) >= 1
        assert exhaust[0].direction == MarketDirection.BULLISH

    def test_no_exhaustion_if_vd_stays_strong(self) -> None:
        candles = [
            _candle(i, 100 + i, 102 + i, 99 + i, 101 + i, volume=200, taker_buy_volume=170)
            for i in range(7)
        ]
        vd = [_vd(c) for c in candles]

        bos = _structure(0, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 101.0)

        analyzer = BehaviorDivergenceAnalyzer(window_size=7)
        results = analyzer.analyze(candles, vd, [], [bos])

        exhaust = [r for r in results if r.divergence_type == DivergenceType.EXHAUSTION]
        assert len(exhaust) == 0


class TestAbsorption:
    """High volume + small price movement near a zone."""

    def test_absorption_near_zone(self) -> None:
        zone = _zone(100.0, side=LiquiditySide.SELL_SIDE)
        candles = [
            _candle(0, 100, 101, 99, 100, volume=10, taker_buy_volume=5),
            _candle(1, 100, 101, 99, 100, volume=10, taker_buy_volume=5),
            _candle(2, 100, 101, 99, 100, volume=10, taker_buy_volume=5),
            _candle(3, 100, 101, 99, 100, volume=10, taker_buy_volume=5),
            _candle(4, 100, 101, 99, 100, volume=10, taker_buy_volume=5),
            _candle(5, 100, 100.2, 99.8, 100.1, volume=300, taker_buy_volume=180),
            _candle(6, 100.1, 100.3, 99.9, 100, volume=300, taker_buy_volume=180),
            _candle(7, 100, 100.2, 99.8, 100.1, volume=300, taker_buy_volume=180),
            _candle(8, 100.1, 100.2, 99.9, 100, volume=300, taker_buy_volume=180),
            _candle(9, 100, 100.3, 99.7, 100.1, volume=300, taker_buy_volume=180),
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.02,
            min_price_change_pct=0.005,
        )
        results = analyzer.analyze(candles, vd, [zone], [])

        absorb = [r for r in results if r.divergence_type == DivergenceType.ABSORPTION]
        assert len(absorb) >= 1
        assert absorb[0].nearest_zone_side == LiquiditySide.SELL_SIDE

    def test_no_absorption_if_far_from_zone(self) -> None:
        zone = _zone(80.0, side=LiquiditySide.SELL_SIDE)
        candles = [
            _candle(i, 100, 100.2, 99.8, 100.1, volume=300, taker_buy_volume=150)
            for i in range(8)
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(window_size=5, proximity_pct=0.02)
        results = analyzer.analyze(candles, vd, [zone], [])

        absorb = [r for r in results if r.divergence_type == DivergenceType.ABSORPTION]
        assert len(absorb) == 0


class TestEdgeCases:
    def test_empty_candles(self) -> None:
        assert BehaviorDivergenceAnalyzer().analyze([], [], [], []) == []

    def test_too_few_candles(self) -> None:
        candles = [_candle(0, 100, 101, 99, 100)]
        assert BehaviorDivergenceAnalyzer().analyze(candles, [0.0], [], []) == []

    def test_candles_fewer_than_window(self) -> None:
        candles = [_candle(i, 100, 101, 99, 100) for i in range(3)]
        vd = [0.0] * 3
        analyzer = BehaviorDivergenceAnalyzer(window_size=10)
        assert analyzer.analyze(candles, vd, [], []) == []

    def test_no_divergence_when_vd_confirms_price(self) -> None:
        zone = _zone(
            105.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
        )
        candles = [
            _candle(i, 100 + i, 102 + i, 99 + i, 101 + i, volume=200, taker_buy_volume=170)
            for i in range(5)
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.05,
            min_price_change_pct=0.01,
            min_vd_ratio=0.1,
        )
        results = analyzer.analyze(candles, vd, [zone], [])

        dist = [r for r in results if r.divergence_type == DivergenceType.DISTRIBUTION]
        assert len(dist) == 0

    def test_confidence_bounded(self) -> None:
        zone = _zone(
            105.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
        )
        candles = [
            _candle(i, 100 + i, 102 + i, 99 + i, 101 + i, volume=200, taker_buy_volume=10)
            for i in range(5)
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.1,
            min_price_change_pct=0.001,
            min_vd_ratio=0.01,
        )
        results = analyzer.analyze(candles, vd, [zone], [])
        for r in results:
            assert 0 <= r.confidence <= 100

    def test_deduplication_keeps_highest_confidence(self) -> None:
        zone = _zone(
            112.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
        )
        candles = [
            _candle(i, 100 + i, 102 + i, 99 + i, 101 + i, volume=200, taker_buy_volume=60)
            for i in range(10)
        ]
        vd = [_vd(c) for c in candles]

        analyzer = BehaviorDivergenceAnalyzer(
            window_size=5,
            proximity_pct=0.15,
            min_price_change_pct=0.01,
            min_vd_ratio=0.1,
        )
        results = analyzer.analyze(candles, vd, [zone], [])

        dist = [r for r in results if r.divergence_type == DivergenceType.DISTRIBUTION]
        assert len(dist) <= 2

    def test_timeframe_adaptive_window(self) -> None:
        candles_m5 = [
            Candle(
                symbol=SYMBOL,
                timeframe=TimeFrame.M5,
                timestamp=T0 + timedelta(minutes=5 * i),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=100.0,
                taker_buy_volume=50.0,
            )
            for i in range(20)
        ]
        vd = [0.0] * 20

        analyzer = BehaviorDivergenceAnalyzer()
        results = analyzer.analyze(candles_m5, vd, [], [])
        assert results == []
