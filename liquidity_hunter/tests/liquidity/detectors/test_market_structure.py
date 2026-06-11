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
#   index 12: swing high 190  -> below active_high (200) -> pending_high,
#                                  labeled LOWER_HIGH vs. previous high (200)
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

# A "ladder" sequence (lookback=2) where no pivot ever breaks the bootstrap
# active_high (300) / active_low (100), so every subsequent pivot is a
# pending HH/HL/LH/LL label and no BOS/CHoCH is emitted.
#
#   index  2: swing high 300 -> bootstraps active_high
#   index  7: swing low  100 -> bootstraps active_low
#   index 12: swing high 250 -> LOWER_HIGH vs. previous high (300)
#   index 17: swing low  150 -> HIGHER_LOW vs. previous low (100)
#   index 22: swing high 280 -> HIGHER_HIGH vs. previous high (250)
#   index 27: swing low  120 -> LOWER_LOW vs. previous low (150)
LADDER_HIGHS = [200.0] * 30
for _index, _value in {2: 300.0, 12: 250.0, 22: 280.0}.items():
    LADDER_HIGHS[_index] = _value

LADDER_LOWS = [190.0] * 30
for _index, _value in {7: 100.0, 17: 150.0, 27: 120.0}.items():
    LADDER_LOWS[_index] = _value

# Two equal swing highs (lookback=2): the second is neither higher nor lower
# than the first, so no HH/LH label (and no BOS, since it doesn't exceed
# active_high) is emitted.
EQUAL_HIGHS = [200.0] * 20
for _index in (2, 12):
    EQUAL_HIGHS[_index] = 300.0

EQUAL_LOWS = [190.0] * 20
EQUAL_LOWS[7] = 100.0


def test_swing_structure_detector_full_sequence() -> None:
    candles = make_series(HIGHS, LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 190.0, 200.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 193.0, 190.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 120.0, 130.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 205.0, 193.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[12].timestamp,
        candles[17].timestamp,
        candles[22].timestamp,
        candles[27].timestamp,
        candles[32].timestamp,
    ]
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_pending_pivot_does_not_trigger_bos_choch_until_promoted() -> None:
    """The minor swing high at index 12 (190 < active_high 200) is held as
    `pending_high`. It surfaces only as a descriptive `LOWER_HIGH` label, not
    a BOS/CHoCH; it only becomes the BOS/CHoCH *reference* once `active_low`
    breaks at index 17, where it is promoted to `active_high` (190) and
    index 22's break (193) reports it as `reference_price_level`.
    """
    candles = make_series(HIGHS, LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    pivot_12_events = [e for e in events if e.timestamp == candles[12].timestamp]
    assert [e.event for e in pivot_12_events] == [StructureEvent.LOWER_HIGH]

    choch_events = [
        e
        for e in events
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
    ]
    assert choch_events[1].reference_price_level == 190.0


def test_pivot_labels_for_pending_pivots() -> None:
    candles = make_series(LADDER_HIGHS, LADDER_LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 250.0, 300.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 150.0, 100.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 280.0, 250.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 120.0, 150.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[12].timestamp,
        candles[17].timestamp,
        candles[22].timestamp,
        candles[27].timestamp,
    ]


def test_no_label_for_equal_pivots() -> None:
    candles = make_series(EQUAL_HIGHS, EQUAL_LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert events == []


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
