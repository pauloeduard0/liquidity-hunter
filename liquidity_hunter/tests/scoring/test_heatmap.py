"""Tests for `LiquidityHeatmapEngine`."""

from datetime import UTC, datetime

import pytest

from liquidity_hunter.core.domain import (
    HeatmapBucket,
    LiquidityHeatmap,
    LiquiditySide,
    ManipulationCycle,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    POIZone,
    POIZoneStatus,
    RetailPositioning,
    TimeFrame,
)
from liquidity_hunter.psychology import RetailBiasEstimate
from liquidity_hunter.scoring import LiquidityHeatmapEngine
from liquidity_hunter.tests.liquidity.detectors._factories import make_series
from liquidity_hunter.tests.scoring._factories import make_zone

CURRENT_PRICE = 100.0
_TS = datetime(2024, 1, 1, tzinfo=UTC)

# A series spanning [90, 110]; with bucket_pct=0.01 -> width 1.0 -> 20 buckets.
CANDLES = make_series(highs=[100.0, 110.0], lows=[90.0, 100.0])


def _engine() -> LiquidityHeatmapEngine:
    # Explicit bucket width and no smoothing for deterministic assertions.
    return LiquidityHeatmapEngine(bucket_pct=0.01, smoothing_sigma=0.0)


def _build(engine: LiquidityHeatmapEngine, **kwargs: object) -> LiquidityHeatmap:
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": TimeFrame.H1,
        "candles": CANDLES,
        "current_price": CURRENT_PRICE,
        "liquidity_zones": [],
        "poi_zones": [],
        "manipulation_cycles": [],
        "retail_bias": None,
    }
    defaults.update(kwargs)
    return engine.build(**defaults)  # type: ignore[arg-type]


def _make_poi(
    price_low: float,
    price_high: float,
    status: POIZoneStatus = POIZoneStatus.ACTIVE,
) -> POIZone:
    return POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        price_low=price_low,
        price_high=price_high,
        created_at=_TS,
        origin_choch_timestamp=_TS,
        origin_bos_timestamp=_TS,
        extreme_candle_timestamp=_TS,
        status=status,
    )


def _make_cycle(
    price_low: float,
    price_high: float,
    status: ManipulationCycleStatus = ManipulationCycleStatus.IN_PROGRESS,
) -> ManipulationCycle:
    return ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=status,
        target_zone_price_low=price_low,
        target_zone_price_high=price_high,
        target_zone_type=make_zone(price_high).zone_type,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=_TS,
        accumulation_end=_TS,
        consolidation_candles=10,
    )


def _make_bias(side: RetailPositioning) -> RetailBiasEstimate:
    return RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=_TS,
        dominant_side=side,
        confidence=70.0,
        explanation="test",
    )


def _hot_bucket(heatmap: LiquidityHeatmap) -> HeatmapBucket:
    return max(heatmap.buckets, key=lambda b: b.heat)


def test_empty_candles_raises() -> None:
    with pytest.raises(ValueError, match="candles must not be empty"):
        _build(_engine(), candles=[])


def test_invalid_current_price_raises() -> None:
    with pytest.raises(ValueError, match="current_price must be > 0"):
        _build(_engine(), current_price=0.0)


def test_poi_zone_creates_hottest_band() -> None:
    heatmap = _build(_engine(), poi_zones=[_make_poi(104.0, 105.0)])

    hot = _hot_bucket(heatmap)
    assert hot.heat == pytest.approx(100.0)
    assert hot.heat_poi > 0
    assert hot.price_low <= 104.0 <= hot.price_high or 104.0 <= hot.price_low <= 105.0
    assert hot.side is LiquiditySide.BUY_SIDE


def test_zone_heat_scales_with_strength() -> None:
    strong = _build(_engine(), liquidity_zones=[make_zone(95.0, strength=0.9)])
    weak = _build(_engine(), liquidity_zones=[make_zone(95.0, strength=0.1)])

    assert _hot_bucket(strong).heat_zones > _hot_bucket(weak).heat_zones


def test_mitigated_zone_excluded() -> None:
    zone = make_zone(95.0, strength=0.9)
    mitigated = zone.model_copy(update={"is_mitigated": True})

    heatmap = _build(_engine(), liquidity_zones=[mitigated])

    assert all(b.heat_zones == 0.0 for b in heatmap.buckets)


def test_inactive_poi_excluded() -> None:
    heatmap = _build(
        _engine(),
        poi_zones=[_make_poi(104.0, 105.0, status=POIZoneStatus.INVALIDATED)],
    )

    assert all(b.heat_poi == 0.0 for b in heatmap.buckets)


def test_only_in_progress_cycles_contribute() -> None:
    in_progress = _build(_engine(), manipulation_cycles=[_make_cycle(94.0, 95.0)])
    confirmed = _build(
        _engine(),
        manipulation_cycles=[_make_cycle(94.0, 95.0, status=ManipulationCycleStatus.CONFIRMED)],
    )

    assert any(b.heat_manipulation > 0 for b in in_progress.buckets)
    assert all(b.heat_manipulation == 0.0 for b in confirmed.buckets)


def test_side_assignment_relative_to_current_price() -> None:
    heatmap = _build(_engine())

    for bucket in heatmap.buckets:
        midpoint = (bucket.price_low + bucket.price_high) / 2
        expected = (
            LiquiditySide.BUY_SIDE if midpoint >= CURRENT_PRICE else LiquiditySide.SELL_SIDE
        )
        assert bucket.side is expected


def test_retail_long_amplifies_sell_side() -> None:
    poi_above = _make_poi(104.0, 105.0)  # buy-side
    poi_below = _make_poi(94.0, 95.0)  # sell-side

    neutral = _build(_engine(), poi_zones=[poi_above, poi_below])
    long_bias = _build(
        _engine(),
        poi_zones=[poi_above, poi_below],
        retail_bias=_make_bias(RetailPositioning.LONG),
    )

    def side_peak(heatmap: LiquidityHeatmap, side: LiquiditySide) -> float:
        return max(b.heat for b in heatmap.buckets if b.side is side)

    # Without bias both sides peak equally; LONG bias makes sell-side hotter.
    assert side_peak(neutral, LiquiditySide.BUY_SIDE) == pytest.approx(
        side_peak(neutral, LiquiditySide.SELL_SIDE)
    )
    assert side_peak(long_bias, LiquiditySide.SELL_SIDE) > side_peak(
        long_bias, LiquiditySide.BUY_SIDE
    )


def test_neutral_bias_does_not_amplify() -> None:
    poi_above = _make_poi(104.0, 105.0)
    poi_below = _make_poi(94.0, 95.0)

    heatmap = _build(
        _engine(),
        poi_zones=[poi_above, poi_below],
        retail_bias=_make_bias(RetailPositioning.NEUTRAL),
    )

    buy = max(b.heat for b in heatmap.buckets if b.side is LiquiditySide.BUY_SIDE)
    sell = max(b.heat for b in heatmap.buckets if b.side is LiquiditySide.SELL_SIDE)
    assert buy == pytest.approx(sell)


def test_bucket_pct_resolved_from_timeframe() -> None:
    engine = LiquidityHeatmapEngine(smoothing_sigma=0.0)

    h4 = _build(engine, timeframe=TimeFrame.H4)
    m5 = _build(engine, timeframe=TimeFrame.M5)

    assert h4.bucket_pct == 0.005
    assert m5.bucket_pct == 0.001


def test_all_heat_values_within_bounds() -> None:
    heatmap = _build(
        _engine(),
        liquidity_zones=[make_zone(95.0, strength=0.8)],
        poi_zones=[_make_poi(104.0, 105.0)],
        manipulation_cycles=[_make_cycle(94.0, 95.0)],
    )

    assert all(0.0 <= b.heat <= 100.0 for b in heatmap.buckets)
    assert any(b.heat > 0 for b in heatmap.buckets)
