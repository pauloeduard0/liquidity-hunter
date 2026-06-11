"""Tests for `SwingStructureDetector`."""

import pytest

from liquidity_hunter.core.domain import MarketDirection, StructureEvent
from liquidity_hunter.liquidity.detectors.market_structure import SwingStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Pivot sequence (lookback=2, so each pivot sits at its index with 2 flat
# candles on either side):
#
#   index  2: swing high 200  -> bootstraps active_high
#   index  7: swing low  140  -> bootstraps active_low
#   index 12: swing high 190  -> below active_high (200) -> pending_high
#   index 17: swing low  130  -> below active_low (140) -> BOS bearish,
#                                  promotes pending_high (190) to active_high
#   index 22: swing high 193  -> above active_high (190) -> CHoCH bullish
#   index 27: swing low  120  -> below active_low (130) -> CHoCH bearish
#   index 32: swing high 205  -> above active_high (193) -> CHoCH bullish
HIGHS = [150.0] * 35
for _index, _value in {2: 200.0, 12: 190.0, 22: 193.0, 32: 205.0}.items():
    HIGHS[_index] = _value

LOWS = [145.0] * 35
for _index, _value in {7: 140.0, 17: 130.0, 27: 120.0}.items():
    LOWS[_index] = _value


def test_swing_structure_detector_full_sequence() -> None:
    candles = make_series(HIGHS, LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 193.0, 190.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 120.0, 130.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 205.0, 193.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[17].timestamp,
        candles[22].timestamp,
        candles[27].timestamp,
        candles[32].timestamp,
    ]
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_pending_pivot_does_not_trigger_event_until_promoted() -> None:
    """The minor swing high at index 12 (190 < active_high 200) is held as
    `pending_high` and produces no event by itself; it only surfaces once
    `active_low` breaks at index 17, where it becomes the new `active_high`
    (190) that index 22's break (193) reports as `reference_price_level`.
    """
    candles = make_series(HIGHS, LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    event_timestamps = {e.timestamp for e in events}
    assert candles[12].timestamp not in event_timestamps
    assert events[1].reference_price_level == 190.0


def test_swing_structure_detector_returns_empty_for_short_series() -> None:
    candles = make_series(HIGHS[:4], LOWS[:4])

    assert SwingStructureDetector(swing_lookback=2).detect(candles) == []


def test_swing_structure_detector_rejects_mixed_symbols() -> None:
    candles = make_series(HIGHS, LOWS)
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        SwingStructureDetector(swing_lookback=2).detect(candles)


def test_swing_structure_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        SwingStructureDetector().detect([])
