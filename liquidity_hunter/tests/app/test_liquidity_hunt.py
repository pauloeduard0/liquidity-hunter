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
    VolumeSpreadSignal,
    VSAPattern,
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
        "manipulation_cycles": [],
        "behavior_divergences": [],
        "volume_spread_signals": [],
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


# ── ATR-normalized proximity ────────────────────────────────────────


def test_atr_proximity_widens_the_pool_map() -> None:
    # Two candles with a 2-point true range at price 100 -> mean TR% = 2%.
    # With proximity_atr=2 the bound is 4%, so an EQH 3% above price maps as
    # a target; the fixed 2% default excludes it.
    data = _minimal_data(
        candles=[_candle(0), _candle(1)],
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(103.0)],
    )

    fixed = LiquidityHuntEngine().build(data)
    atr = LiquidityHuntEngine(proximity_atr=2.0).build(data)

    assert fixed.targets_total == 0
    assert atr.targets_total == 1
    assert atr.phase is LiquidityHuntPhase.COUNTER_TREND


def test_atr_proximity_tightens_on_a_calm_series() -> None:
    # Same 2% mean TR, but proximity_atr=0.5 bounds the map at 1%: a zone
    # 1.5% away that the fixed 2% would map is excluded — the M15 declutter.
    data = _minimal_data(
        candles=[_candle(0), _candle(1)],
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(101.5)],
    )

    fixed = LiquidityHuntEngine().build(data)
    atr = LiquidityHuntEngine(proximity_atr=0.5).build(data)

    assert fixed.targets_total == 1
    assert atr.targets_total == 0


def test_atr_proximity_falls_back_to_fixed_pct_on_short_series() -> None:
    # A single candle cannot measure a true range: the ATR bound falls back
    # to proximity_pct (2%), so the 3%-distant zone stays excluded.
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[_bearish_choch()],
        liquidity_zones=[_eqh_zone(103.0)],
    )
    state = LiquidityHuntEngine(proximity_atr=2.0).build(data)
    assert state.targets_total == 0


# ----------------------------------------------------------------------
# build_history — concluded past hunts
# ----------------------------------------------------------------------


def test_history_empty_when_htf_not_directional() -> None:
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.NEUTRAL,
        internal_structure_events=[_bearish_choch(5)],
    )
    assert LiquidityHuntEngine().build_history(data) == []


def _candles(n: int) -> list[Candle]:
    return [_candle(i) for i in range(n)]


def test_history_episode_ends_at_the_grab_not_the_reflip() -> None:
    # Bullish HTF. A bearish CHoCH opens a counter-trend leg; a capture-side
    # (bullish) up-sweep + a co-located VSA up-thrust grab the shorts
    # (confluence, score 6); a bullish CHoCH resumes the trend much later. The
    # hunt must end at the grab, not drag to the re-flip.
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(8, VSAPattern.UP_THRUST, MarketDirection.BEARISH)],
        candles=_candles(24),
    )
    history = LiquidityHuntEngine().build_history(data)
    assert len(history) == 1
    episode = history[0]
    assert episode.hunted_side == RetailPositioning.SHORT
    assert episode.correction_direction == MarketDirection.BEARISH
    assert episode.start_timestamp == events[0].timestamp
    assert episode.end_timestamp == events[1].timestamp  # the sweep, not the re-flip


def test_history_multiple_grabs_in_one_leg_are_separate_hunts() -> None:
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(14, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[
            _vsa(8, VSAPattern.UP_THRUST, MarketDirection.BEARISH),
            _vsa(14, VSAPattern.UP_THRUST, MarketDirection.BEARISH),
        ],
        candles=_candles(24),
    )
    history = LiquidityHuntEngine().build_history(data)
    assert [(e.start_timestamp, e.end_timestamp) for e in history] == [
        (events[0].timestamp, events[1].timestamp),
        (events[1].timestamp, events[2].timestamp),
    ]


def test_history_skips_counter_trend_leg_without_capture() -> None:
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(12, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        candles=_candles(16),
    )
    assert LiquidityHuntEngine().build_history(data) == []


def _vsa(i: int, pattern: VSAPattern, direction: MarketDirection) -> VolumeSpreadSignal:
    return VolumeSpreadSignal(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * i,
        pattern=pattern,
        direction=direction,
        price_level=100.0,
        spread_ratio=2.0,
        close_position=0.2,
        volume_ratio=2.5,
        volume_delta=50.0,
        confidence=80.0,
        description="VSA.",
    )


def test_history_lone_strong_signal_is_below_threshold() -> None:
    # A single sweep (weight 3) no longer reaches the capture threshold (5):
    # a real turning point needs confluence.
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        candles=_candles(24),
    )
    assert LiquidityHuntEngine().build_history(data) == []


def test_history_confluence_closes_hunt_with_score_and_sources() -> None:
    # Sweep (3) + a strong VSA up-thrust (confidence 80 -> weight 4) co-located
    # = score 7 >= threshold 6.
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(8, VSAPattern.UP_THRUST, MarketDirection.BEARISH)],
        candles=_candles(24),
    )
    history = LiquidityHuntEngine().build_history(data)
    assert len(history) == 1
    assert history[0].capture_score == 7.0
    assert history[0].capture_sources == ["sweep", "vsa"]


def test_history_sweep_plus_zone_is_below_threshold() -> None:
    # A sweep (3) grabbing an equal-highs pool (2) = score 5, below the
    # two-strong-signal threshold (6): still not a captured turning point.
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        liquidity_zones=[_eqh_zone(103.0, mitigated_at=T0 + H1 * 8)],
        candles=_candles(24),
    )
    assert LiquidityHuntEngine().build_history(data) == []


def test_history_wrong_side_vsa_does_not_count() -> None:
    # For hunted shorts the grab rejects the high (UP_THRUST/BUYING_CLIMAX). A
    # DOWN_THRUST (low-side rejection) is not a shorts-capture, so a sweep +
    # down-thrust stays at 3 (below threshold).
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
        _event(20, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(8, VSAPattern.DOWN_THRUST, MarketDirection.BULLISH)],
        candles=_candles(24),
    )
    assert LiquidityHuntEngine().build_history(data) == []


def test_continuation_absorbs_failed_choch_excursion() -> None:
    # Bullish HTF, aligned bull leg. A bearish CHoCH opens a counter-trend
    # excursion that then *fails* (CHOCH_FAILED reverts the trend): a deep
    # continuation pullback, not a reversal. Its floor prints a down-sweep +
    # a DOWN_THRUST exhaustion candle. The continuation stream must absorb the
    # excursion and register the grab there; the hunt stream must not claim it.
    events = [
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
        _event(8, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(10, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH),
        _event(12, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(10, VSAPattern.DOWN_THRUST, MarketDirection.BULLISH)],
        candles=_candles(24),
    )
    engine = LiquidityHuntEngine()
    # The failed excursion is a continuation pullback, not a hunt.
    assert engine.build_history(data) == []
    continuation = engine.build_continuation_history(data)
    assert len(continuation) == 1
    episode = continuation[0]
    assert episode.correction_direction == MarketDirection.BULLISH
    assert episode.hunted_side == RetailPositioning.SHORT
    assert episode.end_timestamp == events[2].timestamp  # the grab floor
    assert "sweep" in episode.capture_sources
    assert "vsa" in episode.capture_sources


def test_continuation_anchors_the_box_on_the_vsa_candle() -> None:
    # Bull continuation floor: a down-sweep (i=8) and its VSA down-thrust
    # exhaustion candle (i=10) are within one cluster. The grab must anchor on
    # the VSA candle (the exhaustion the user sees), not the earlier sweep.
    events = [
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(10, VSAPattern.DOWN_THRUST, MarketDirection.BULLISH)],
        candles=_candles(24),
    )
    continuation = LiquidityHuntEngine().build_continuation_history(data)
    assert len(continuation) == 1
    assert continuation[0].end_timestamp == T0 + H1 * 10  # the VSA candle, not i=8


def test_continuation_strong_lone_vsa_floor_closes_alone() -> None:
    # A strong floor down-thrust (confidence 82 -> weight 4) with no co-located
    # sweep still reaches the continuation threshold on its own: the exhaustion
    # candle is the floor signature (the ZEC 1h 2026-07-17 case).
    events = [
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(10, VSAPattern.DOWN_THRUST, MarketDirection.BULLISH)],
        candles=_candles(24),
    )
    continuation = LiquidityHuntEngine().build_continuation_history(data)
    assert len(continuation) == 1
    assert continuation[0].capture_score == 4.0
    assert continuation[0].capture_sources == ["vsa"]


def test_continuation_weak_lone_vsa_floor_is_below_threshold() -> None:
    # A weak floor down-thrust (confidence 50 -> weight 3) alone stays below the
    # threshold: without a partner it is not a grab, keeping the noise out.
    events = [
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
    ]
    weak = _vsa(10, VSAPattern.DOWN_THRUST, MarketDirection.BULLISH).model_copy(
        update={"confidence": 50.0}
    )
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[weak],
        candles=_candles(24),
    )
    assert LiquidityHuntEngine().build_continuation_history(data) == []


def test_continuation_requires_vsa_on_the_floor() -> None:
    # Same failed-excursion shape, but no VSA exhaustion candle: without the
    # mandatory climax/thrust the continuation grab does not register.
    events = [
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH),
        _event(8, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(10, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH),
        _event(12, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        candles=_candles(24),
    )
    assert LiquidityHuntEngine().build_continuation_history(data) == []


def test_history_includes_past_grab_of_still_open_leg() -> None:
    # A grab already happened inside the current (open) counter-trend leg: it is
    # a completed hunt; only the tail after it is the live state.
    events = [
        _event(5, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(8, StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH),
    ]
    data = _minimal_data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=events,
        volume_spread_signals=[_vsa(8, VSAPattern.UP_THRUST, MarketDirection.BEARISH)],
        candles=_candles(16),
    )
    history = LiquidityHuntEngine().build_history(data)
    assert len(history) == 1
    assert history[0].start_timestamp == events[0].timestamp
    assert history[0].end_timestamp == events[1].timestamp
