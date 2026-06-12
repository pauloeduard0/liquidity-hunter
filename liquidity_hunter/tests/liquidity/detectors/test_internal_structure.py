"""Tests for `InternalStructureDetector`."""

from datetime import timedelta

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Pivot sequence (lookback=1, so each pivot sits at its index with 1 flat
# candle on either side):
#
#   index  1: swing high 200 -> bootstraps active_high = 200 (active_low not
#                                 yet set, so pending_high is not seeded); no
#                                 event
#   index  3: swing low   90 -> bootstraps active_low = 90; active_high is
#                                 already set, so pending_low is seeded with
#                                 this pivot (90); no event
#   index  5: swing high 220 -> above active_high (200); trend NEUTRAL ->
#                                 BREAK_OF_STRUCTURE bullish (price-only);
#                                 trend becomes BULLISH; active_low is
#                                 promoted from pending_low (90, unchanged
#                                 since it was seeded at index 3) and
#                                 pending_low cleared; active_high = 220
#   index  7: swing low  100 -> above active_low (90) -> HIGHER_LOW label;
#                                 active_high (220) is folded into
#                                 pending_high; active_low = 100
#   index  9: swing high 210 -> below active_high (220) -> LOWER_HIGH label;
#                                 active_low (100) is folded into
#                                 pending_low; active_high = 210
#   index 11: swing low   80 -> below active_low (100); trend BULLISH ->
#                                 CHoCH-candidate. Default close/volume don't
#                                 confirm -> LIQUIDITY_SWEEP bearish;
#                                 active_high (210) is folded into
#                                 pending_high (210 < 220, so pending_high
#                                 stays 220, from index 7); active_low = 80
#                                 (still updates); trend stays BULLISH
#   index 13: swing high 230 -> above active_high (210); trend still BULLISH
#                                 (the sweep above didn't change it) ->
#                                 BREAK_OF_STRUCTURE bullish (continuation);
#                                 active_low is promoted from pending_low
#                                 (100, accumulated at index 9) and
#                                 pending_low cleared; active_high = 230
#   index 15: swing low   70 -> below active_low (100 -- the true extreme of
#                                 the prior leg, not the swept 80); trend
#                                 BULLISH -> CHoCH-candidate, confirmed (close
#                                 beyond 100 with bearish volume delta) ->
#                                 CHANGE_OF_CHARACTER bearish referencing 100;
#                                 trend becomes BEARISH; active_high is
#                                 promoted from pending_high (220, accumulated
#                                 at index 7) and pending_high cleared;
#                                 active_low = 70
#   index 17: swing high 215 -> below active_high (220 -- the true extreme of
#                                 the prior leg, not the BOS pivot 230) ->
#                                 LOWER_HIGH label referencing 220;
#                                 active_low (70) is folded into pending_low;
#                                 active_high = 215
#   index 19: swing low   60 -> below active_low (70); trend BEARISH ->
#                                 BREAK_OF_STRUCTURE bearish (price-only);
#                                 active_high is promoted from pending_high
#                                 (None, cleared at index 15 and never
#                                 re-accumulated) -> active_high = None;
#                                 active_low = 60
HIGHS = [150.0] * 21
for _index, _value in {1: 200.0, 5: 220.0, 9: 210.0, 13: 230.0, 17: 215.0}.items():
    HIGHS[_index] = _value

LOWS = [140.0] * 21
for _index, _value in {3: 90.0, 7: 100.0, 11: 80.0, 15: 70.0, 19: 60.0}.items():
    LOWS[_index] = _value


def _series_with_confirmed_choch() -> list[Candle]:
    """`make_series(HIGHS, LOWS)` with index 15's CHoCH confirmed.

    `close=75` closes beyond the active low (100) and `taker_buy_volume=0.3`
    gives a `volume_delta` ratio of 0.4 (>= the default `min_volume_delta_ratio`
    of 0.2) in the bearish direction.
    """
    candles = make_series(HIGHS, LOWS)
    candles[15] = make_candle(15, HIGHS[15], LOWS[15], close=75.0, taker_buy_volume=0.3)
    return candles


def test_internal_structure_detector_full_sequence() -> None:
    candles = _series_with_confirmed_choch()

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 200.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 100.0, 90.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 210.0, 220.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 80.0, 100.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 210.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 70.0, 100.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 215.0, 220.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 60.0, 70.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[5].timestamp,
        candles[7].timestamp,
        candles[9].timestamp,
        candles[11].timestamp,
        candles[13].timestamp,
        candles[15].timestamp,
        candles[17].timestamp,
        candles[19].timestamp,
    ]
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_first_pivot_of_each_kind_produces_no_event() -> None:
    """Index 1 (first high) and index 3 (first low) bootstrap the active
    references without emitting any event.
    """
    candles = _series_with_confirmed_choch()

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert candles[1].timestamp not in [e.timestamp for e in events]
    assert candles[3].timestamp not in [e.timestamp for e in events]


def test_pivot_equal_to_active_reference_produces_no_event() -> None:
    """A pivot exactly equal to the current active reference (neither a
    break nor a label) produces no event, and still becomes the new active
    reference for the next comparison.
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 200.0, 150.0, 150.0, 150.0]
    lows = [140.0, 140.0, 140.0, 100.0, 140.0, 140.0, 140.0, 90.0, 140.0]
    candles = make_series(highs, lows)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    # index 5's high (200) equals active_high (200, from index 1) -> no event.
    # index 7's low (90) breaks active_low (100); trend is still NEUTRAL ->
    # BREAK_OF_STRUCTURE bearish (price-only).
    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 90.0, 100.0),
    ]


def test_internal_structure_detector_stamps_internal_scope() -> None:
    candles = _series_with_confirmed_choch()

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert events
    assert all(event.scope is StructureScope.INTERNAL for event in events)


# A sequence (lookback=1) where `active_low` reaches the lowest low of the
# entire remaining series early on, via a confirmed CHoCH whose
# `pending_high` is still empty -- so `active_high` is retired to `None`
# rather than promoted to some stale value:
#
#   index  1: swing high 200 -> bootstraps active_high = 200 (active_low not
#                                 yet set, no pending seed); no event
#   index  3: swing low  100 -> bootstraps active_low = 100; pending_low
#                                 seeded with this pivot (100); no event
#   index  5: swing high 210 -> above active_high (200); trend NEUTRAL ->
#                                 BREAK_OF_STRUCTURE bullish; trend becomes
#                                 BULLISH; active_low is promoted from
#                                 pending_low (100, unchanged) and pending_low
#                                 cleared; active_high = 210
#   index  7: swing low   50 -> below active_low (100); trend BULLISH ->
#                                 CHoCH-candidate, confirmed (close 55 beyond
#                                 100 with bearish volume delta) ->
#                                 CHANGE_OF_CHARACTER bearish; trend becomes
#                                 BEARISH; active_high is promoted from
#                                 pending_high (None, never accumulated) ->
#                                 active_high = None; active_low = 50
#   index  9: swing high 205 -> active_high is None, so this pivot silently
#                                 re-bootstraps it (no event, no LOWER_HIGH
#                                 label); active_low is already set, so
#                                 pending_high is seeded with this pivot (205)
#   index 11: swing low   70 -> above active_low (50) -> HIGHER_LOW label;
#                                 active_high (205) is folded into
#                                 pending_high (no change, already 205);
#                                 active_low = 70
#   index 13: swing high 215 -> above active_high (205); trend BEARISH ->
#                                 CHoCH-candidate, confirmed (close 212 beyond
#                                 205 with bullish volume delta) ->
#                                 CHANGE_OF_CHARACTER bullish; trend becomes
#                                 BULLISH; active_low is promoted from
#                                 pending_low (None, never re-accumulated
#                                 since index 5) -> active_low = None;
#                                 active_high = 215
#   index 15: swing low   80 -> active_low is None, so this pivot silently
#                                 re-bootstraps it (no event, no HIGHER_LOW
#                                 label); active_high is already set, so
#                                 pending_low is seeded with this pivot (80)
#   index 17: swing high 225 -> above active_high (215); trend still BULLISH
#                                 -> BREAK_OF_STRUCTURE bullish (continuation);
#                                 active_low is promoted from pending_low
#                                 (80, accumulated at index 15) and
#                                 pending_low cleared; active_high = 225
NEVER_FROZEN_HIGHS = [150.0] * 19
for _index, _value in {1: 200.0, 5: 210.0, 9: 205.0, 13: 215.0, 17: 225.0}.items():
    NEVER_FROZEN_HIGHS[_index] = _value

NEVER_FROZEN_LOWS = [140.0] * 19
for _index, _value in {3: 100.0, 7: 50.0, 11: 70.0, 15: 80.0}.items():
    NEVER_FROZEN_LOWS[_index] = _value


def test_active_references_recover_after_retirement_to_none() -> None:
    candles = make_series(NEVER_FROZEN_HIGHS, NEVER_FROZEN_LOWS)
    # index 7: close (55) beyond active_low (100) with bearish volume delta
    # (ratio 0.4) -> confirmed CHANGE_OF_CHARACTER, retiring active_high to
    # `None` (pending_high is still empty at this point).
    candles[7] = make_candle(
        7, NEVER_FROZEN_HIGHS[7], NEVER_FROZEN_LOWS[7], close=55.0, taker_buy_volume=0.3
    )
    # index 13: close (212) beyond active_high (205) with bullish volume
    # delta (ratio 0.4) -> confirmed CHANGE_OF_CHARACTER, retiring active_low
    # to `None` (pending_low is still empty at this point).
    candles[13] = make_candle(
        13, NEVER_FROZEN_HIGHS[13], NEVER_FROZEN_LOWS[13], close=212.0, taker_buy_volume=0.7
    )

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 50.0, 100.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 70.0, 50.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 215.0, 205.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 225.0, 215.0),
    ]
    # index 9 and index 15 silently re-bootstrap a retired (`None`) active
    # reference -- no event is emitted for either.
    timestamps = [e.timestamp for e in events]
    assert candles[9].timestamp not in timestamps
    assert candles[15].timestamp not in timestamps
    # active_high never gets stuck on the pre-drop high (200): the
    # bullish BOS/CHoCH events reference the recent highs (200, 205, 215)
    # in turn.
    bullish_refs = [
        e.reference_price_level
        for e in events
        if e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
        and e.direction is MarketDirection.BULLISH
    ]
    assert bullish_refs == [200.0, 205.0, 215.0]


def test_internal_structure_detector_returns_empty_for_short_series() -> None:
    candles = make_series(HIGHS[:2], LOWS[:2])

    assert InternalStructureDetector(swing_lookback=1).detect(candles) == []


def test_internal_structure_detector_rejects_mixed_symbols() -> None:
    candles = _series_with_confirmed_choch()
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        InternalStructureDetector(swing_lookback=1).detect(candles)


def test_internal_structure_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        InternalStructureDetector().detect([])


def test_internal_structure_detector_rejects_invalid_min_volume_delta_ratio() -> None:
    with pytest.raises(ValueError, match="min_volume_delta_ratio must be between 0 and 1"):
        InternalStructureDetector(min_volume_delta_ratio=1.5)

    with pytest.raises(ValueError, match="min_volume_delta_ratio must be between 0 and 1"):
        InternalStructureDetector(min_volume_delta_ratio=-0.1)


def test_internal_structure_detector_rejects_invalid_volume_spike_params() -> None:
    with pytest.raises(ValueError, match="volume_spike_lookback must be at least 1"):
        InternalStructureDetector(volume_spike_lookback=0)

    with pytest.raises(ValueError, match="volume_spike_multiplier must be positive"):
        InternalStructureDetector(volume_spike_multiplier=0.0)


# A short sequence (lookback=1) ending in a CHoCH-candidate whose close
# clears the active low (100) but whose `volume_delta` ratio is 0 (balanced
# taker volume) -- inconclusive on its own:
#
#   index 1: swing high 200 -> bootstraps active_high = 200; no event
#   index 3: swing low  100 -> bootstraps active_low = 100; pending_low
#                                seeded with this pivot (100); no event
#   index 5: swing high 210 -> above active_high (200); trend NEUTRAL ->
#                                BREAK_OF_STRUCTURE bullish; trend becomes
#                                BULLISH; active_low promoted from pending_low
#                                (100, unchanged); active_high = 210
#   index 6: swing low   50 -> below active_low (100); trend BULLISH ->
#                                CHoCH-candidate. close (95) clears 100, but
#                                taker_buy_volume=0.5 (default) gives a
#                                volume_delta ratio of 0 -- not confirmed by
#                                volume_delta alone.
_SPIKE_HIGHS = [150.0, 200.0, 150.0, 150.0, 150.0, 210.0, 150.0, 150.0]
_SPIKE_LOWS = [140.0, 140.0, 140.0, 100.0, 140.0, 140.0, 50.0, 140.0]


def _spike_test_series() -> list[Candle]:
    candles = make_series(_SPIKE_HIGHS, _SPIKE_LOWS)
    candles[6] = make_candle(6, _SPIKE_HIGHS[6], _SPIKE_LOWS[6], close=95.0, taker_buy_volume=0.5)
    return candles


def _finer_candles_with_spike(*, spike_index: int = 12) -> list[Candle]:
    """M30 candles covering 00:00-06:30 (indices 0-13), with a 10x volume
    spike at `spike_index` (12 = 06:00, within index 6's [06:00, 07:00) H1
    window)."""
    return [
        make_candle(
            i,
            100.0,
            99.0,
            volume=10.0 if i == spike_index else 1.0,
            timeframe=TimeFrame.M30,
            interval=timedelta(minutes=30),
        )
        for i in range(14)
    ]


def test_volume_spike_confirms_choch_when_volume_delta_ratio_is_inconclusive() -> None:
    candles = _spike_test_series()

    # Without finer_candles: close-beyond but inconclusive volume_delta ->
    # LIQUIDITY_SWEEP, active_low stays unchanged (folded into pending_high
    # instead).
    swept = InternalStructureDetector(swing_lookback=1).detect(candles)
    assert swept[-1].event is StructureEvent.LIQUIDITY_SWEEP
    assert swept[-1].direction is MarketDirection.BEARISH
    assert swept[-1].price_level == 50.0
    assert swept[-1].reference_price_level == 100.0

    # With finer_candles showing a volume spike within index 6's time window:
    # confirmed -> CHANGE_OF_CHARACTER.
    confirmed = InternalStructureDetector(
        swing_lookback=1,
        finer_candles=_finer_candles_with_spike(),
        volume_spike_lookback=2,
        volume_spike_multiplier=2.0,
    ).detect(candles)
    assert confirmed[-1].event is StructureEvent.CHANGE_OF_CHARACTER
    assert confirmed[-1].direction is MarketDirection.BEARISH
    assert confirmed[-1].price_level == 50.0
    assert confirmed[-1].reference_price_level == 100.0


def test_volume_spike_outside_window_does_not_confirm() -> None:
    candles = _spike_test_series()

    # The spike is at index 0 (00:00), outside index 6's [06:00, 07:00)
    # window -- still a LIQUIDITY_SWEEP.
    events = InternalStructureDetector(
        swing_lookback=1,
        finer_candles=_finer_candles_with_spike(spike_index=0),
        volume_spike_lookback=2,
        volume_spike_multiplier=2.0,
    ).detect(candles)
    assert events[-1].event is StructureEvent.LIQUIDITY_SWEEP
