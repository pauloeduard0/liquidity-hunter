"""Tests for `ManipulationCycleDetector`."""

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.psychology import ManipulationCycleDetector

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
    formed_hour: int = 0,
) -> LiquidityZone:
    return LiquidityZone(
        symbol=SYMBOL,
        timeframe=TF,
        zone_type=zone_type,
        side=side,
        price_high=price,
        price_low=price,
        formed_at=_ts(formed_hour),
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


class TestConfirmedCycle:
    """Full accumulation -> sweep -> expansion cycle."""

    def test_bullish_cycle_sweep_lows_then_bos_up(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [
            _candle(0, 100, 101, 99, 100),
            # Accumulation: price consolidates near 95
            _candle(1, 96, 97, 94.5, 95.5),
            _candle(2, 95.5, 96, 94.8, 95.2),
            _candle(3, 95.2, 96, 94.5, 95.0),
            _candle(4, 95.0, 95.5, 94.2, 95.3),
            _candle(5, 95.3, 95.8, 94.0, 95.1),
            # Sweep: wick below zone
            _candle(6, 95.0, 95.5, 93.0, 94.5),
            # Recovery + expansion BOS up
            _candle(7, 94.5, 98.0, 94.0, 97.5),
            _candle(8, 97.5, 100.0, 97.0, 99.0),
        ]
        vd = [0.0] * len(candles)

        sweep_event = _structure(
            6, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )
        bos_event = _structure(
            8, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 99.0
        )

        detector = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=3,
            max_expansion_candles=10,
        )
        cycles = detector.detect(
            candles=candles,
            structure_events=[sweep_event, bos_event],
            liquidity_zones=[zone],
            volume_deltas=vd,
        )

        assert len(cycles) == 1
        cycle = cycles[0]
        assert cycle.direction == MarketDirection.BULLISH
        assert cycle.phase == ManipulationPhase.EXPANSION
        assert cycle.status == ManipulationCycleStatus.CONFIRMED
        assert cycle.sweep_timestamp == _ts(6)
        assert cycle.sweep_extreme == 93.0
        assert cycle.expansion_timestamp == _ts(8)
        assert cycle.expansion_price == 99.0
        assert cycle.target_zone_side == LiquiditySide.SELL_SIDE

    def test_bearish_cycle_sweep_highs_then_bos_down(self) -> None:
        zone = _zone(
            105.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
        )

        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 104, 105.5, 103.5, 104.5),
            _candle(2, 104.5, 105.2, 104, 104.8),
            _candle(3, 104.8, 105.3, 104.2, 105.0),
            _candle(4, 105.0, 105.5, 104.5, 104.7),
            _candle(5, 104.7, 105.1, 104.0, 104.9),
            # Sweep above zone
            _candle(6, 105.0, 107.0, 104.5, 105.5),
            # Expansion BOS down
            _candle(7, 105.5, 106.0, 102.0, 102.5),
            _candle(8, 102.5, 103.0, 100.0, 100.5),
        ]
        vd = [0.0] * len(candles)

        sweep = _structure(
            6, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH, 107.0
        )
        bos = _structure(
            8, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 100.5
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=3,
        ).detect(candles, [sweep, bos], [zone], vd)

        assert len(cycles) == 1
        assert cycles[0].direction == MarketDirection.BEARISH
        assert cycles[0].phase == ManipulationPhase.EXPANSION
        assert cycles[0].status == ManipulationCycleStatus.CONFIRMED


class TestInProgressManipulation:
    """Sweep happened but no expansion BOS yet."""

    def test_sweep_without_expansion_is_in_progress(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [
            _candle(0, 96, 97, 95.5, 96),
            _candle(1, 96, 96.5, 95, 95.5),
            _candle(2, 95.5, 96, 94.5, 95.2),
            # Sweep
            _candle(3, 95.0, 95.5, 93.0, 94.5),
            # Post-sweep: no BOS yet
            _candle(4, 94.5, 95.5, 94.0, 95.0),
        ]
        vd = [0.0] * len(candles)

        sweep = _structure(
            3, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=1,
            max_expansion_candles=10,
        ).detect(candles, [sweep], [zone], vd)

        assert len(cycles) == 1
        assert cycles[0].phase == ManipulationPhase.MANIPULATION
        assert cycles[0].status == ManipulationCycleStatus.IN_PROGRESS

    def test_sweep_with_expired_expansion_window_is_failed(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [_candle(i, 96, 97, 95, 96) for i in range(20)]
        candles[5] = _candle(5, 95.0, 95.5, 93.0, 94.5)
        vd = [0.0] * len(candles)

        sweep = _structure(
            5, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=1,
            max_expansion_candles=5,
        ).detect(candles, [sweep], [zone], vd)

        assert len(cycles) == 1
        assert cycles[0].phase == ManipulationPhase.MANIPULATION
        assert cycles[0].status == ManipulationCycleStatus.FAILED


class TestProspectiveAccumulation:
    """Active zone where price is consolidating (no sweep yet)."""

    def test_accumulation_near_active_zone(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 101, 99, 100),
            # Last 6 candles consolidate near zone
            _candle(2, 95.5, 96, 95, 95.3),
            _candle(3, 95.3, 96, 94.8, 95.5),
            _candle(4, 95.5, 96, 95.0, 95.2),
            _candle(5, 95.2, 95.8, 94.5, 95.0),
            _candle(6, 95.0, 95.5, 94.5, 95.3),
            _candle(7, 95.3, 96, 95.0, 95.5),
        ]
        vd = [0.0] * len(candles)

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=5,
        ).detect(candles, [], [zone], vd)

        assert len(cycles) == 1
        cycle = cycles[0]
        assert cycle.phase == ManipulationPhase.ACCUMULATION
        assert cycle.status == ManipulationCycleStatus.IN_PROGRESS
        assert cycle.direction == MarketDirection.BULLISH
        assert cycle.consolidation_candles >= 5
        assert cycle.sweep_timestamp is None
        assert cycle.expansion_timestamp is None

    def test_no_accumulation_if_price_far_from_zone(self) -> None:
        zone = _zone(80.0, side=LiquiditySide.SELL_SIDE)

        candles = [_candle(i, 100, 101, 99, 100) for i in range(10)]
        vd = [0.0] * len(candles)

        cycles = ManipulationCycleDetector(
            min_accumulation_candles=3,
        ).detect(candles, [], [zone], vd)

        assert len(cycles) == 0

    def test_mitigated_zone_excluded_from_prospective(self) -> None:
        zone = LiquidityZone(
            symbol=SYMBOL,
            timeframe=TF,
            zone_type=LiquidityZoneType.EQUAL_LOWS,
            side=LiquiditySide.SELL_SIDE,
            price_high=95.0,
            price_low=95.0,
            formed_at=_ts(0),
            strength=0.5,
            is_mitigated=True,
        )

        candles = [_candle(i, 95.5, 96, 95, 95.3) for i in range(10)]
        vd = [0.0] * len(candles)

        cycles = ManipulationCycleDetector(
            min_accumulation_candles=3,
        ).detect(candles, [], [zone], vd)

        assert len(cycles) == 0


class TestEdgeCases:
    def test_empty_candles_returns_empty(self) -> None:
        cycles = ManipulationCycleDetector().detect([], [], [], [])
        assert cycles == []

    def test_no_zones_returns_empty(self) -> None:
        candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
        vd = [0.0] * len(candles)
        cycles = ManipulationCycleDetector().detect(candles, [], [], vd)
        assert cycles == []

    def test_volume_delta_captured_on_sweep_and_expansion(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [
            _candle(0, 96, 97, 95.5, 96),
            _candle(1, 96, 96.5, 95, 95.5),
            _candle(2, 95.0, 95.5, 93.0, 94.5, volume=200, taker_buy_volume=30),
            _candle(3, 94.5, 98.0, 94.0, 97.5, volume=200, taker_buy_volume=170),
        ]
        vd = [2 * c.taker_buy_volume - c.volume for c in candles]

        sweep = _structure(
            2, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )
        bos = _structure(
            3, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 97.5
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=1,
        ).detect(candles, [sweep, bos], [zone], vd)

        assert len(cycles) == 1
        assert cycles[0].sweep_volume_delta == pytest.approx(-140.0)
        assert cycles[0].expansion_volume_delta == pytest.approx(140.0)

    def test_duplicate_zone_not_double_counted(self) -> None:
        zone = _zone(95.0, side=LiquiditySide.SELL_SIDE)

        candles = [
            _candle(0, 96, 97, 95.5, 96),
            _candle(1, 96, 96.5, 95, 95.5),
            _candle(2, 95.0, 95.5, 93.0, 94.5),
            _candle(3, 95.0, 95.5, 92.5, 94.0),
        ]
        vd = [0.0] * len(candles)

        sweep1 = _structure(
            2, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )
        sweep2 = _structure(
            3, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 92.5
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=1,
        ).detect(candles, [sweep1, sweep2], [zone], vd)

        assert len(cycles) == 1

    def test_cycles_sorted_by_accumulation_start(self) -> None:
        zone_a = _zone(95.0, side=LiquiditySide.SELL_SIDE, formed_hour=0)
        zone_b = _zone(
            105.0,
            side=LiquiditySide.BUY_SIDE,
            zone_type=LiquidityZoneType.EQUAL_HIGHS,
            formed_hour=5,
        )

        candles = [
            _candle(0, 96, 97, 95.5, 96),
            _candle(1, 96, 96.5, 95, 95.5),
            _candle(2, 95.0, 95.5, 93.0, 94.5),
            _candle(3, 94.5, 98.0, 94.0, 97.5),
            _candle(4, 97.5, 100.0, 97.0, 99.0),
            _candle(5, 104, 105.5, 103.5, 104.5),
            _candle(6, 104.5, 105.2, 104, 104.8),
            _candle(7, 105.0, 107.0, 104.5, 105.5),
            _candle(8, 105.5, 106.0, 102.0, 102.5),
        ]
        vd = [0.0] * len(candles)

        sweep_a = _structure(
            2, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 93.0
        )
        sweep_b = _structure(
            7, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH, 107.0
        )
        bos_a = _structure(
            4, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 99.0
        )
        bos_b = _structure(
            8, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 102.5
        )

        cycles = ManipulationCycleDetector(
            proximity_pct=0.02,
            min_accumulation_candles=1,
        ).detect(candles, [sweep_a, sweep_b, bos_a, bos_b], [zone_a, zone_b], vd)

        assert len(cycles) == 2
        assert cycles[0].accumulation_start < cycles[1].accumulation_start
