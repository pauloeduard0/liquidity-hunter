"""Tests for `liquidity_hunter.app.liquidity_hunt.LiquidityHuntEngine`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.dashboard_data import DashboardData
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import (
    Candle,
    LeverageLiquidationMap,
    LiquidationBand,
    LiquidityHuntPhase,
    LiquidityHuntTargetKind,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    OIAnalysis,
    OIParticipation,
    OIQualifiedEvent,
    OIRegime,
    OIRegimeReading,
    RetailPositioning,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.psychology import RetailBiasEstimate

T0 = datetime(2024, 1, 1, tzinfo=UTC)
H1 = timedelta(hours=1)

CHOCH_TS = T0 + H1 * 10


def _candle(i: int, price: float = 100.0) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * i,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10.0,
        taker_buy_volume=5.0,
    )


def _bias() -> RetailBiasEstimate:
    return RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=T0,
        dominant_side="neutral",
        confidence=50.0,
        explanation="Neutral.",
    )


def _minimal_data(**overrides: object) -> DashboardData:
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": TimeFrame.H1,
        "candles": [_candle(0)],
        "current_price": 100.0,
        "higher_timeframe_direction": MarketDirection.NEUTRAL,
        "liquidity_zones": [],
        "ranked_zones": [],
        "market_structure_events": [],
        "internal_structure_events": [],
        "retail_bias": _bias(),
        "poi_zones": [],
        "poi_sweep_events": [],
        "manipulation_cycles": [],
        "behavior_divergences": [],
    }
    defaults.update(overrides)
    return DashboardData(**defaults)  # type: ignore[arg-type]


def _event(
    i: int,
    event: StructureEvent,
    direction: MarketDirection,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * i,
        event=event,
        direction=direction,
        price_level=100.0,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def _bearish_choch(i: int = 10) -> MarketStructure:
    return _event(i, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH)


def _eqh_zone(
    level: float, mitigated_at: datetime | None = None
) -> LiquidityZone:
    return LiquidityZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        zone_type=LiquidityZoneType.EQUAL_HIGHS,
        side=LiquiditySide.BUY_SIDE,
        price_high=level,
        price_low=level,
        formed_at=T0,
        strength=0.8,
        is_mitigated=mitigated_at is not None,
        invalidated_at=mitigated_at,
    )


def _band(
    level: float,
    side: LiquiditySide = LiquiditySide.BUY_SIDE,
    end_time: datetime | None = None,
    leverage: int = 25,
    intensity: float = 60.0,
) -> LiquidationBand:
    return LiquidationBand(
        price_low=level - 0.1,
        price_high=level + 0.1,
        leverage=leverage,
        side=side,
        source_entry_price=100.0,
        intensity=intensity,
        start_time=T0,
        end_time=end_time,
    )


def _liq_map(bands: list[LiquidationBand]) -> LeverageLiquidationMap:
    return LeverageLiquidationMap(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        current_price=100.0,
        dominant_leveraged_side=RetailPositioning.SHORT,
        positioning_intensity=0.5,
        funding_rate=-0.0001,
        open_interest_change_pct=0.01,
        long_short_ratio=1.2,
        bands=bands,
    )


def _oi(
    regime: OIRegime | None = None,
    flush_at: datetime | None = None,
    flush_direction: MarketDirection = MarketDirection.BULLISH,
) -> OIAnalysis:
    reading = None
    if regime is not None:
        reading = OIRegimeReading(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=T0 + H1 * 20,
            regime=regime,
            price_change_pct=0.01,
            oi_change_pct=-0.02,
            window_candles=7,
            intensity=50.0,
            description="",
        )
    qualified = []
    if flush_at is not None:
        qualified.append(
            OIQualifiedEvent(
                symbol="BTCUSDT",
                timeframe=TimeFrame.H1,
                event_timestamp=flush_at,
                event_type=StructureEvent.LIQUIDITY_SWEEP,
                direction=flush_direction,
                price_level=101.0,
                oi_delta_pct=-0.01,
                participation=OIParticipation.FLUSH,
                description="",
            )
        )
    return OIAnalysis(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        current_regime=reading,
        qualified_events=qualified,
    )


# ── Aligned / no structure ──────────────────────────────────────────


def test_no_events_produces_none_phase() -> None:
    state = LiquidityHuntEngine().build(_minimal_data())
    assert state.phase is LiquidityHuntPhase.NONE
    assert state.hunted_side is RetailPositioning.NEUTRAL
    assert state.targets == []


def test_aligned_trend_produces_none_phase() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[
            _event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH)
        ],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.NONE


def test_provisional_choch_does_not_flip_trend() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[
            _event(5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
            _event(
                10,
                StructureEvent.CHANGE_OF_CHARACTER,
                MarketDirection.BEARISH,
                provisional=True,
            ),
        ],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.NONE


def test_choch_failed_reverts_to_aligned() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[
            _event(5, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
            _bearish_choch(10),
            _event(14, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
        ],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.NONE


# ── Counter-trend: intact pools ─────────────────────────────────────


def test_counter_trend_with_intact_pools() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0)],
    )
    state = LiquidityHuntEngine().build(data)

    assert state.phase is LiquidityHuntPhase.COUNTER_TREND
    assert state.hunted_side is RetailPositioning.SHORT
    assert state.correction_direction is MarketDirection.BEARISH
    assert state.counter_structure_timestamp == CHOCH_TS
    assert state.targets_total == 1
    assert state.targets_captured == 0
    assert state.targets[0].kind is LiquidityHuntTargetKind.EQUAL_LEVEL
    assert not state.targets[0].captured


def test_intact_pool_beyond_proximity_is_not_a_target() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(110.0)],  # 10% away, beyond the 2% window
    )
    state = LiquidityHuntEngine().build(data)
    assert state.targets_total == 0
    assert state.phase is LiquidityHuntPhase.COUNTER_TREND


def test_zone_swept_before_flip_is_excluded() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0, mitigated_at=CHOCH_TS - H1 * 3)],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.targets_total == 0


# ── Hunt in progress ────────────────────────────────────────────────


def test_partial_capture_is_hunt_in_progress() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[
            _eqh_zone(101.0, mitigated_at=CHOCH_TS + H1 * 2),
            _eqh_zone(101.8),
        ],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS
    assert state.targets_captured == 1
    assert state.targets_total == 2


def test_oi_unwinding_alone_is_hunt_in_progress() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0)],
        oi_analysis=_oi(regime=OIRegime.SHORT_COVERING),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS
    assert state.oi_unwinding is True


def test_flush_after_flip_is_recorded() -> None:
    flush_ts = CHOCH_TS + H1 * 3
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0)],
        oi_analysis=_oi(flush_at=flush_ts),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS
    assert state.last_flush_timestamp == flush_ts


def test_flush_before_flip_is_ignored() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0)],
        oi_analysis=_oi(flush_at=CHOCH_TS - H1 * 2),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.last_flush_timestamp is None
    assert state.phase is LiquidityHuntPhase.COUNTER_TREND


def test_capture_side_sweep_after_flip_is_hunt_in_progress() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[
            _bearish_choch(),
            _event(13, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        ],
        liquidity_zones=[_eqh_zone(101.0)],
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS


# ── Captured ────────────────────────────────────────────────────────


def test_all_pools_captured_and_oi_calm_is_captured() -> None:
    swept_at = CHOCH_TS + H1 * 4
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0, mitigated_at=swept_at)],
        oi_analysis=_oi(regime=OIRegime.FLAT),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.CAPTURED
    assert state.captured_at == swept_at
    assert state.targets_captured == state.targets_total == 1


def test_all_pools_captured_but_oi_still_unwinding_is_not_captured() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.0, mitigated_at=CHOCH_TS + H1 * 4)],
        oi_analysis=_oi(regime=OIRegime.SHORT_COVERING),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS


def test_no_mapped_pools_is_never_captured() -> None:
    # Absence of mapped pools is not evidence they were consumed: the state
    # stays conservative rather than declaring the hunt concluded.
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        oi_analysis=_oi(regime=OIRegime.FLAT),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.phase is LiquidityHuntPhase.COUNTER_TREND


# ── Liquidation-band targets ────────────────────────────────────────


def test_live_band_is_intact_target_and_hit_band_is_captured() -> None:
    hit_at = CHOCH_TS + H1 * 2
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidation_map=_liq_map(
            [
                _band(101.5),  # live pool above price
                _band(100.6, end_time=hit_at),  # consumed during this leg
                _band(101.0, side=LiquiditySide.SELL_SIDE),  # wrong side
                _band(100.5, end_time=CHOCH_TS - H1),  # consumed before the leg
            ]
        ),
    )
    state = LiquidityHuntEngine().build(data)

    assert state.targets_total == 2
    banded = {t.price_level: t for t in state.targets}
    assert banded[101.5].captured is False
    assert banded[100.6].captured is True
    assert banded[100.6].captured_at == hit_at
    assert all(
        t.kind is LiquidityHuntTargetKind.LIQUIDATION_BAND for t in state.targets
    )
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS


def test_clustered_bands_collapse_to_one_pool() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidation_map=_liq_map(
            [
                _band(101.50, intensity=40.0),
                _band(101.52, intensity=80.0, leverage=50),
            ]
        ),
    )
    state = LiquidityHuntEngine().build(data)
    assert state.targets_total == 1
    assert state.targets[0].label == "50x"  # strongest member represents the pool


# ── Long-side mirror ────────────────────────────────────────────────


def test_bullish_correction_in_bearish_htf_hunts_longs() -> None:
    eql = LiquidityZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        zone_type=LiquidityZoneType.EQUAL_LOWS,
        side=LiquiditySide.SELL_SIDE,
        price_high=99.0,
        price_low=99.0,
        formed_at=T0,
        strength=0.8,
    )
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BEARISH,
        internal_structure_events=[
            _event(10, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH)
        ],
        liquidity_zones=[eql],
        oi_analysis=_oi(regime=OIRegime.LONG_LIQUIDATION),
    )
    state = LiquidityHuntEngine().build(data)

    assert state.hunted_side is RetailPositioning.LONG
    assert state.oi_unwinding is True
    assert state.targets_total == 1
    assert state.targets[0].label == "EQL"
    assert state.phase is LiquidityHuntPhase.HUNT_IN_PROGRESS
