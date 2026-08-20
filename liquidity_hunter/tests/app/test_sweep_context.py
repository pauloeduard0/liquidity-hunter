"""Tests for the sweep pool-context annotation (`app.sweep_context`)."""

from datetime import datetime, timedelta, timezone

from liquidity_hunter.app.sweep_context import build_sweep_contexts
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    POIZone,
    POIZoneKind,
    POIZoneStatus,
    StructureEvent,
    StructureScope,
    TimeFrame,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BTCUSDT"
TF = TimeFrame.H1


def ts(i: int) -> datetime:
    return NOW + timedelta(hours=i)


def make_candles(n: int = 20) -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=TF,
            timestamp=ts(i),
            open=100.0,
            high=102.0,
            low=98.0,
            close=101.0,
            volume=10.0,
            taker_buy_volume=5.0,
        )
        for i in range(n)
    ]


def sweep(i: int, direction: MarketDirection, *, reference: float = 100.0) -> MarketStructure:
    return MarketStructure(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=ts(i),
        event=StructureEvent.LIQUIDITY_SWEEP,
        direction=direction,
        price_level=102.0 if direction is MarketDirection.BULLISH else 98.0,
        reference_price_level=reference,
        scope=StructureScope.INTERNAL,
    )


def block(
    *,
    direction: MarketDirection,
    low: float,
    high: float,
    created_at: datetime,
    invalidated_at: datetime | None = None,
    kind: POIZoneKind = POIZoneKind.ORDER_BLOCK,
) -> POIZone:
    return POIZone(
        symbol=SYMBOL,
        timeframe=TF,
        direction=direction,
        kind=kind,
        price_low=low,
        price_high=high,
        created_at=created_at,
        ob_candle_timestamp=created_at,
        status=(
            POIZoneStatus.ACTIVE if invalidated_at is None else POIZoneStatus.INVALIDATED
        ),
        invalidated_at=invalidated_at,
    )


def test_bearish_sweep_into_demand_block_is_annotated() -> None:
    """A sweep taking lows lands in demand: the block faces it."""
    candles = make_candles()
    demand = block(
        direction=MarketDirection.BULLISH, low=97.0, high=99.0, created_at=ts(2)
    )
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH)],
        poi_zones=[demand],
        candles=candles,
    )
    assert context.in_order_block is True
    assert context.swept_extreme == 98.0
    assert (context.block_low, context.block_high) == (97.0, 99.0)


def test_block_facing_the_wrong_way_does_not_count() -> None:
    """A sweep taking lows runs into demand, not supply.

    Without this direction match the channel is vacuous -- a sweep sits
    inside *some* block about as often as a random pivot does.
    """
    candles = make_candles()
    supply = block(
        direction=MarketDirection.BEARISH, low=97.0, high=99.0, created_at=ts(2)
    )
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH)],
        poi_zones=[supply],
        candles=candles,
    )
    assert context.in_order_block is False
    assert context.block_low is None


def test_block_created_after_the_sweep_does_not_count() -> None:
    """There was no box there yet when price passed through."""
    candles = make_candles()
    later = block(
        direction=MarketDirection.BULLISH, low=97.0, high=99.0, created_at=ts(12)
    )
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH)],
        poi_zones=[later],
        candles=candles,
    )
    assert context.in_order_block is False


def test_block_retired_before_the_sweep_does_not_count() -> None:
    """FIFO retirement took the box off the board; nothing rests there."""
    candles = make_candles()
    retired = block(
        direction=MarketDirection.BULLISH,
        low=97.0,
        high=99.0,
        created_at=ts(2),
        invalidated_at=ts(5),
    )
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH)],
        poi_zones=[retired],
        candles=candles,
    )
    assert context.in_order_block is False


def test_breaker_block_is_ignored() -> None:
    """Only order blocks: the breaker of the same MSB is the same observation."""
    candles = make_candles()
    breaker = block(
        direction=MarketDirection.BULLISH,
        low=97.0,
        high=99.0,
        created_at=ts(2),
        kind=POIZoneKind.BREAKER_BLOCK,
    )
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH)],
        poi_zones=[breaker],
        candles=candles,
    )
    assert context.in_order_block is False


def test_excursion_is_measured_against_the_broken_reference() -> None:
    """Depth in ATR units of the series -- here every true range is 4.0."""
    candles = make_candles()
    (context,) = build_sweep_contexts(
        symbol=SYMBOL,
        timeframe=TF,
        structure_events=[sweep(10, MarketDirection.BEARISH, reference=100.0)],
        poi_zones=[],
        candles=candles,
    )
    # swept extreme 98.0, reference 100.0 -> 2.0 beyond, over a 4.0 mean TR.
    assert context.excursion_atr == 0.5


def test_provisional_sweeps_and_other_events_are_skipped() -> None:
    candles = make_candles()
    bos = MarketStructure(
        symbol=SYMBOL,
        timeframe=TF,
        timestamp=ts(10),
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=102.0,
        scope=StructureScope.INTERNAL,
    )
    provisional = sweep(11, MarketDirection.BEARISH).model_copy(
        update={"provisional": True}
    )
    assert (
        build_sweep_contexts(
            symbol=SYMBOL,
            timeframe=TF,
            structure_events=[bos, provisional],
            poi_zones=[],
            candles=candles,
        )
        == []
    )
