"""Tests for `LeverageLiquidationEstimator`."""

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LeverageLiquidationMap,
    LiquidationBand,
    LiquiditySide,
    LiquidityZone,
    LongShortRatio,
    MarketDirection,
    OpenInterestPoint,
    POIZone,
    POIZoneStatus,
    RetailPositioning,
    TimeFrame,
)
from liquidity_hunter.psychology import LeverageLiquidationEstimator
from liquidity_hunter.tests.psychology._factories import FORMED_AT, make_zone


def make_poi(
    price_low: float,
    price_high: float,
    *,
    status: POIZoneStatus = POIZoneStatus.ACTIVE,
) -> POIZone:
    return POIZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        price_low=price_low,
        price_high=price_high,
        created_at=FORMED_AT,
        origin_choch_timestamp=FORMED_AT,
        origin_bos_timestamp=FORMED_AT,
        extreme_candle_timestamp=FORMED_AT,
        status=status,
    )

TS = datetime(2026, 6, 22, tzinfo=UTC)


def _candle(offset_hours: int, *, high: float, low: float) -> Candle:
    ts = FORMED_AT + timedelta(hours=offset_hours)
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=10.0,
        taker_buy_volume=5.0,
    )


def _oi(first: float, last: float) -> list[OpenInterestPoint]:
    return [
        OpenInterestPoint(symbol="BTCUSDT", timestamp=TS, open_interest=first),
        OpenInterestPoint(symbol="BTCUSDT", timestamp=TS, open_interest=last),
    ]


def _funding(rate: float) -> list[FundingRate]:
    return [FundingRate(symbol="BTCUSDT", timestamp=TS, funding_rate=rate)]


def _long_short(ratio: float) -> list[LongShortRatio]:
    long_pct = ratio / (1 + ratio)
    return [
        LongShortRatio(
            symbol="BTCUSDT",
            timestamp=TS,
            long_account_pct=long_pct,
            short_account_pct=1 - long_pct,
            ratio=ratio,
        )
    ]


def _estimate(
    zones: list[LiquidityZone],
    *,
    funding: float,
    ratio: float,
    oi: tuple[float, float] = (1000.0, 1000.0),
    candles: list[Candle] | None = None,
    poi_zones: list[POIZone] | None = None,
) -> LeverageLiquidationMap:
    return LeverageLiquidationEstimator().estimate(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        current_price=105.0,
        candles=candles if candles is not None else [],
        liquidity_zones=zones,
        open_interest=_oi(*oi),
        funding=_funding(funding),
        long_short=_long_short(ratio),
        poi_zones=poi_zones,
    )


def _side(m: LeverageLiquidationMap, side: LiquiditySide) -> list[LiquidationBand]:
    return [b for b in m.bands if b.side is side]


def test_crowded_longs_make_sell_side_dominant_below_entry() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    assert m.dominant_leveraged_side is RetailPositioning.LONG
    sell = _side(m, LiquiditySide.SELL_SIDE)
    assert sell
    # Long-liquidation pool sits below the 100.0 entry.
    assert all((b.price_low + b.price_high) / 2 < 100.0 for b in sell)
    # The hottest band overall is on the dominant (sell) side.
    assert max(m.bands, key=lambda b: b.intensity).side is LiquiditySide.SELL_SIDE


def test_crowded_shorts_make_buy_side_dominant_above_entry() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.BUY_SIDE, strength=0.8)]
    m = _estimate(zones, funding=-0.0006, ratio=0.5)

    assert m.dominant_leveraged_side is RetailPositioning.SHORT
    buy = _side(m, LiquiditySide.BUY_SIDE)
    assert buy
    assert all((b.price_low + b.price_high) / 2 > 100.0 for b in buy)
    assert max(m.bands, key=lambda b: b.intensity).side is LiquiditySide.BUY_SIDE


def test_both_sides_emitted_with_dominant_brighter() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    sell = _side(m, LiquiditySide.SELL_SIDE)  # dominant (longs liquidate below)
    buy = _side(m, LiquiditySide.BUY_SIDE)  # non-dominant (shorts liquidate above)
    assert sell and buy
    # Same tier is brighter on the dominant side.
    sell_by_lev = {b.leverage: b.intensity for b in sell}
    buy_by_lev = {b.leverage: b.intensity for b in buy}
    assert sell_by_lev[10] > buy_by_lev[10]


def test_band_distances_match_leverage_tiers() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=1.0)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    by_leverage = {
        b.leverage: (b.price_low + b.price_high) / 2
        for b in _side(m, LiquiditySide.SELL_SIDE)
    }
    assert by_leverage[10] == pytest.approx(100.0 * (1 - 0.095))
    assert by_leverage[25] == pytest.approx(100.0 * (1 - 0.036))
    assert by_leverage[50] == pytest.approx(100.0 * (1 - 0.016))
    assert by_leverage[100] == pytest.approx(100.0 * (1 - 0.006))


def test_intensity_normalized_to_peak() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    assert max(b.intensity for b in m.bands) == pytest.approx(100.0)
    # 10x is the most populated tier, so it is the hottest band (dominant side).
    hottest = max(m.bands, key=lambda b: b.intensity)
    assert hottest.leverage == 10
    assert hottest.side is LiquiditySide.SELL_SIDE


def test_lower_leverage_is_hotter_than_higher_for_same_entry() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    intensity = {b.leverage: b.intensity for b in _side(m, LiquiditySide.SELL_SIDE)}
    assert intensity[10] > intensity[25] > intensity[50] > intensity[100]


def test_band_start_time_is_entry_zone_formation() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0006, ratio=1.85)

    assert all(b.start_time == FORMED_AT for b in m.bands)


def test_band_end_time_set_when_price_reaches_liquidation_level() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=1.0)]
    # Sell-side (long-liq) levels: 100x ~99.4 (hit early), 10x ~90.5 (never).
    candles = [
        _candle(1, high=100.2, low=99.0),  # low pierces the 99.4 (100x) level
        _candle(2, high=100.1, low=98.0),
    ]
    m = _estimate(zones, funding=0.0006, ratio=1.85, candles=candles)

    by_leverage = {b.leverage: b.end_time for b in _side(m, LiquiditySide.SELL_SIDE)}
    assert by_leverage[100] == FORMED_AT + timedelta(hours=1)
    # The deep 10x level (~90.5) was never touched -> still live.
    assert by_leverage[10] is None


def test_band_end_time_none_when_level_never_reached() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    # Price stays in a tight band that reaches no liquidation level either side.
    candles = [_candle(1, high=100.3, low=100.1)]
    m = _estimate(zones, funding=0.0006, ratio=1.85, candles=candles)

    assert all(b.end_time is None for b in m.bands)


def test_neutral_positioning_produces_no_bands() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)]
    m = _estimate(zones, funding=0.0, ratio=1.0)

    assert m.dominant_leveraged_side is RetailPositioning.NEUTRAL
    assert m.bands == []


def test_zero_strength_zones_skipped_mitigated_included() -> None:
    active = make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)
    mitigated = make_zone(120.0, side=LiquiditySide.SELL_SIDE, strength=0.8).model_copy(
        update={"is_mitigated": True}
    )
    zero = make_zone(80.0, side=LiquiditySide.SELL_SIDE, strength=0.0)
    m = _estimate([active, mitigated, zero], funding=0.0006, ratio=1.85)

    # Mitigated zones are kept as historical entry anchors; zero-strength skipped.
    assert {b.source_entry_price for b in m.bands} == {100.0, 120.0}


def test_mitigated_entry_is_weaker_than_equivalent_active() -> None:
    active = make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)
    mitigated = make_zone(200.0, side=LiquiditySide.SELL_SIDE, strength=0.8).model_copy(
        update={"is_mitigated": True}
    )
    m = _estimate([active, mitigated], funding=0.0006, ratio=1.85)

    def intensity_at(entry: float, side: LiquiditySide) -> float:
        return next(
            b.intensity
            for b in m.bands
            if b.source_entry_price == entry and b.leverage == 10 and b.side is side
        )

    # Same strength + tier, but the mitigated entry is downweighted.
    assert intensity_at(100.0, LiquiditySide.SELL_SIDE) > intensity_at(
        200.0, LiquiditySide.SELL_SIDE
    )


def test_nearby_entries_merged_into_one_cluster() -> None:
    # Two zones within _ENTRY_CLUSTER_PCT (0.4%) of each other -> one anchor.
    z1 = make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.8)
    z2 = make_zone(100.2, side=LiquiditySide.SELL_SIDE, strength=0.5)
    m = _estimate([z1, z2], funding=0.0006, ratio=1.85)

    entries = {b.source_entry_price for b in m.bands}
    assert entries == {100.0}  # the stronger of the two represents the cluster


def test_poi_order_blocks_anchor_liquidation_bands() -> None:
    # No liquidity zones — only an order block at midpoint 100 seeds bands.
    poi = make_poi(99.0, 101.0)
    m = _estimate([], funding=0.0006, ratio=1.85, poi_zones=[poi])

    assert m.bands
    assert {round(b.source_entry_price) for b in m.bands} == {100}


def test_invalidated_poi_zones_skipped() -> None:
    poi = make_poi(99.0, 101.0, status=POIZoneStatus.INVALIDATED)
    m = _estimate([], funding=0.0006, ratio=1.85, poi_zones=[poi])

    assert m.bands == []


def test_empty_inputs_return_map_without_bands() -> None:
    m = LeverageLiquidationEstimator().estimate(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        current_price=105.0,
        candles=[],
        liquidity_zones=[],
        open_interest=[],
        funding=[],
        long_short=[],
    )
    assert m.dominant_leveraged_side is RetailPositioning.NEUTRAL
    assert m.long_short_ratio == 1.0
    assert m.funding_rate == 0.0
    assert m.bands == []


def test_open_interest_growth_amplifies_intensity_scale() -> None:
    zones = [make_zone(100.0, side=LiquiditySide.SELL_SIDE, strength=0.5)]
    flat = _estimate(zones, funding=0.0002, ratio=1.2, oi=(1000.0, 1000.0))
    growing = _estimate(zones, funding=0.0002, ratio=1.2, oi=(1000.0, 1500.0))

    assert growing.positioning_intensity > flat.positioning_intensity


def test_rejects_non_positive_current_price() -> None:
    with pytest.raises(ValueError, match="current_price must be > 0"):
        LeverageLiquidationEstimator().estimate(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            current_price=0.0,
            candles=[],
            liquidity_zones=[],
            open_interest=[],
            funding=[],
            long_short=[],
        )
