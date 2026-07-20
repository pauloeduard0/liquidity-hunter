"""Tests for `liquidity_hunter.app.structure_confluence.StructureConfluenceEngine`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.dashboard_data import DashboardData
from liquidity_hunter.app.structure_confluence import StructureConfluenceEngine
from liquidity_hunter.core.domain import (
    Candle,
    ConfluenceFactor,
    MarketDirection,
    MarketStructure,
    OIAnalysis,
    OIParticipation,
    OIQualifiedEvent,
    POIZone,
    POIZoneKind,
    POIZoneStatus,
    StructureEvent,
    StructureScope,
    TimeFrame,
    VolumeSpreadSignal,
    VSAPattern,
)
from liquidity_hunter.psychology import RetailBiasEstimate

T0 = datetime(2024, 1, 1, tzinfo=UTC)
H1 = timedelta(hours=1)
EVENT_IDX = 20
EVENT_TS = T0 + H1 * EVENT_IDX


def _candle(i: int, *, taker: float = 5.0, volume: float = 10.0, price: float = 100.0) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * i,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=volume,
        taker_buy_volume=taker,
    )


def _bias() -> RetailBiasEstimate:
    return RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=T0,
        dominant_side="neutral",
        confidence=50.0,
        explanation="Neutral.",
    )


def _bos(direction: MarketDirection = MarketDirection.BULLISH) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=EVENT_TS,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=direction,
        price_level=100.0,
        reference_price_level=100.0,
        scope=StructureScope.INTERNAL,
    )


def _data(**overrides: object) -> DashboardData:
    # The break candle carries strongly bullish taker aggression.
    candles = [_candle(i) for i in range(EVENT_IDX + 1)]
    candles[EVENT_IDX] = _candle(EVENT_IDX, taker=9.0, volume=10.0)  # vd = +8
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": TimeFrame.H1,
        "candles": candles,
        "current_price": 100.0,
        "higher_timeframe_direction": MarketDirection.NEUTRAL,
        "liquidity_zones": [],
        "ranked_zones": [],
        "market_structure_events": [],
        "internal_structure_events": [_bos()],
        "retail_bias": _bias(),
        "poi_zones": [],
        "manipulation_cycles": [],
        "behavior_divergences": [],
        "volume_spread_signals": [],
    }
    defaults.update(overrides)
    return DashboardData(**defaults)  # type: ignore[arg-type]


def test_no_evidence_scores_zero():
    result = StructureConfluenceEngine().build(_data())
    assert len(result) == 1
    # Only the break candle's own aligned volume delta fires by default.
    conf = result[0]
    assert conf.factors == [ConfluenceFactor.VOLUME_DELTA]
    assert conf.score == 9.0


def test_all_factors_present_scores_100():
    vsa = VolumeSpreadSignal(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * (EVENT_IDX - 1),
        pattern=VSAPattern.SELLING_CLIMAX,
        direction=MarketDirection.BULLISH,
        price_level=99.0,
        spread_ratio=2.0,
        close_position=0.5,
        volume_ratio=3.0,
        volume_delta=-5.0,
        confidence=70.0,
        description="climax",
    )
    ob = POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.ORDER_BLOCK,
        price_low=99.0,
        price_high=101.0,
        created_at=T0 + H1 * (EVENT_IDX - 3),
        ob_candle_timestamp=T0 + H1 * (EVENT_IDX - 3),
        status=POIZoneStatus.ACTIVE,
    )
    htf_ob = POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H4,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.ORDER_BLOCK,
        price_low=98.5,
        price_high=101.5,
        created_at=T0 + H1 * (EVENT_IDX - 8),
        ob_candle_timestamp=T0 + H1 * (EVENT_IDX - 8),
        status=POIZoneStatus.ACTIVE,
    )
    oi = OIAnalysis(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        qualified_events=[
            OIQualifiedEvent(
                symbol="BTCUSDT",
                timeframe=TimeFrame.H1,
                event_timestamp=EVENT_TS,
                event_type=StructureEvent.BREAK_OF_STRUCTURE,
                direction=MarketDirection.BULLISH,
                price_level=100.0,
                oi_delta_pct=2.0,
                participation=OIParticipation.NEW_MONEY,
                description="new money",
            )
        ],
    )
    sweep = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * (EVENT_IDX - 4),
        event=StructureEvent.LIQUIDITY_SWEEP,
        direction=MarketDirection.BEARISH,
        price_level=98.0,
        scope=StructureScope.INTERNAL,
    )
    data = _data(
        higher_timeframe_direction=MarketDirection.BULLISH,
        internal_structure_events=[sweep, _bos()],
        volume_spread_signals=[vsa],
        poi_zones=[ob],
        htf_poi_zones=[htf_ob],
        oi_analysis=oi,
    )
    result = StructureConfluenceEngine().build(data)
    conf = next(c for c in result if c.event_type == StructureEvent.BREAK_OF_STRUCTURE)
    assert set(conf.factors) == {
        ConfluenceFactor.HTF_ALIGNMENT,
        ConfluenceFactor.HTF_ORDER_BLOCK,
        ConfluenceFactor.VSA_VOLUME,
        ConfluenceFactor.ORDER_BLOCK,
        ConfluenceFactor.OI_PARTICIPATION,
        ConfluenceFactor.VOLUME_DELTA,
        ConfluenceFactor.LIQUIDITY_SWEEP,
    }
    assert conf.score == 100.0


def test_htf_alignment_factor():
    aligned = StructureConfluenceEngine().build(
        _data(higher_timeframe_direction=MarketDirection.BULLISH)
    )
    assert ConfluenceFactor.HTF_ALIGNMENT in aligned[0].factors

    opposed = StructureConfluenceEngine().build(
        _data(higher_timeframe_direction=MarketDirection.BEARISH)  # BOS is bullish
    )
    assert ConfluenceFactor.HTF_ALIGNMENT not in opposed[0].factors

    neutral = StructureConfluenceEngine().build(
        _data(higher_timeframe_direction=MarketDirection.NEUTRAL)
    )
    assert ConfluenceFactor.HTF_ALIGNMENT not in neutral[0].factors


def test_htf_order_block_factor():
    htf_ob = POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H4,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.ORDER_BLOCK,
        price_low=99.0,
        price_high=101.0,
        created_at=T0 + H1 * (EVENT_IDX - 8),
        ob_candle_timestamp=T0 + H1 * (EVENT_IDX - 8),
        status=POIZoneStatus.ACTIVE,
    )
    result = StructureConfluenceEngine().build(_data(htf_poi_zones=[htf_ob]))
    assert ConfluenceFactor.HTF_ORDER_BLOCK in result[0].factors

    # A bearish HTF OB must not confirm the bullish break.
    bearish_htf_ob = htf_ob.model_copy(update={"direction": MarketDirection.BEARISH})
    result2 = StructureConfluenceEngine().build(_data(htf_poi_zones=[bearish_htf_ob]))
    assert ConfluenceFactor.HTF_ORDER_BLOCK not in result2[0].factors


def test_recently_invalidated_ob_counts_as_breaker_retest():
    # A bearish OB broken then retested: invalidated shortly before the break,
    # its range still holding the reference level. Counts at reduced weight.
    retest_ob = POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.BREAKER_BLOCK,
        price_low=99.0,
        price_high=101.0,
        created_at=T0 + H1 * (EVENT_IDX - 6),
        ob_candle_timestamp=T0 + H1 * (EVENT_IDX - 6),
        status=POIZoneStatus.INVALIDATED,
        invalidated_at=T0 + H1 * (EVENT_IDX - 3),
    )
    result = StructureConfluenceEngine().build(_data(poi_zones=[retest_ob]))
    conf = result[0]
    assert ConfluenceFactor.ORDER_BLOCK in conf.factors
    # VOLUME_DELTA (9) + ORDER_BLOCK at half weight (15 * 0.5 = 7.5).
    assert conf.score == 16.5


def test_stale_invalidated_ob_does_not_count():
    # Invalidated before the tracked candle window (not a recent breaker retest).
    stale_ob = POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        kind=POIZoneKind.BREAKER_BLOCK,
        price_low=99.0,
        price_high=101.0,
        created_at=T0 - H1 * 10,
        ob_candle_timestamp=T0 - H1 * 10,
        status=POIZoneStatus.INVALIDATED,
        invalidated_at=T0 - H1 * 5,  # before candles[0], not in idx_by_ts
    )
    result = StructureConfluenceEngine().build(_data(poi_zones=[stale_ob]))
    assert ConfluenceFactor.ORDER_BLOCK not in result[0].factors


def test_provisional_and_non_break_events_skipped():
    provisional = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=EVENT_TS,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=100.0,
        scope=StructureScope.INTERNAL,
        provisional=True,
    )
    pivot = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.HIGHER_HIGH,
        direction=MarketDirection.BULLISH,
        price_level=100.0,
        scope=StructureScope.INTERNAL,
    )
    result = StructureConfluenceEngine().build(
        _data(internal_structure_events=[provisional, pivot])
    )
    assert result == []


def test_misaligned_evidence_does_not_count():
    # A bearish VSA / bearish OB should not confirm a bullish break.
    vsa = VolumeSpreadSignal(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=EVENT_TS,
        pattern=VSAPattern.UP_THRUST,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        spread_ratio=1.5,
        close_position=0.2,
        volume_ratio=1.5,
        volume_delta=-3.0,
        confidence=50.0,
        description="thrust",
    )
    result = StructureConfluenceEngine().build(
        _data(volume_spread_signals=[vsa])
    )
    assert ConfluenceFactor.VSA_VOLUME not in result[0].factors
