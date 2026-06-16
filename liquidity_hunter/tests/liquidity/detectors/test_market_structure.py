"""Tests for `SwingStructureDetector`."""

import pytest

from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent, StructureScope
from liquidity_hunter.liquidity.detectors.market_structure import SwingStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Pivot sequence (lookback=2, so each pivot sits at its index with 2 flat
# candles on either side):
#
#   index  2: swing high 200  -> bootstraps active_high
#   index  7: swing low  140  -> bootstraps active_low (and seeds
#                                  pending_low, since active_high already
#                                  exists)
#   index 12: swing high 190  -> below active_high (200) -> pending_high,
#                                  labeled LOWER_HIGH vs. previous high (200)
#   index 17: swing low  130  -> below active_low (140) -> BOS bearish,
#                                  promotes pending_high (190) to active_high
#   index 22: swing high 193  -> above active_high (190) -> CHoCH bullish;
#                                  pending_low is empty (no low pivot has
#                                  formed in this leg yet), so active_low is
#                                  discarded to None rather than left stale
#   index 27: swing low  120  -> active_low is None -> not a CHoCH
#                                  candidate; labeled LOWER_LOW vs. the
#                                  previous low (130) and accumulates into
#                                  pending_low
#   index 32: swing high 205  -> above active_high (193); trend is still
#                                  BULLISH (unchanged since index 22) -> BOS
#                                  bullish (continuation), promoting
#                                  pending_low (120) to active_low
HIGHS = [150.0] * 35
for _index, _value in {2: 200.0, 12: 190.0, 22: 193.0, 32: 205.0}.items():
    HIGHS[_index] = _value

LOWS = [145.0] * 35
for _index, _value in {7: 140.0, 17: 130.0, 27: 120.0}.items():
    LOWS[_index] = _value

# Candle-level overrides applied by `_confirmed_series`.
#
# BOS candles need a close beyond the active level for event emission.
# The CHoCH at index 22 (reference=190) requires a 2-candle persistence
# window: candles[22] and [23] must both close > 190.  Candle 23 gets
# high=192 (strictly below HIGHS[22]=193) so it is not detected as a new
# swing pivot, and close=191 > 190 satisfies the persistence check.
# The BOS at 17 and 32 need close below 140 / above 193 respectively.
def _confirmed_series(highs: list[float], lows: list[float]) -> list[Candle]:
    """`make_series(highs, lows)` with close/high overrides for BOS/CHoCH confirmation."""
    candles = make_series(highs, lows)
    candles[17] = make_candle(17, highs[17], lows[17], close=135.0)
    candles[22] = make_candle(22, highs[22], lows[22], close=192.0)
    candles[23] = make_candle(23, 192.0, lows[23], close=191.0)
    candles[32] = make_candle(32, highs[32], lows[32], close=200.0)
    return candles


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
    candles = _confirmed_series(HIGHS, LOWS)

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 190.0, 200.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 193.0, 190.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 120.0, 130.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 205.0, 193.0),
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
    candles = _confirmed_series(HIGHS, LOWS)

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

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


def test_swing_structure_detector_rejects_invalid_persistence_candles() -> None:
    with pytest.raises(ValueError, match="persistence_candles must be at least 1"):
        SwingStructureDetector(persistence_candles=0)


# A BOS / sweep / BOS / CHoCH sequence (lookback=2):
#
#   index  2: swing high 200 -> bootstraps active_high
#   index  7: swing low  140 -> bootstraps active_low
#   index 12: swing high 210 -> above active_high (200); trend is NEUTRAL,
#                                 so this is a BOS bullish. Close (205) >
#                                 200 confirms. trend becomes BULLISH.
#   index 17: swing low  130 -> below active_low (140); trend is BULLISH
#                                 (reversal candidate). close (135) is below
#                                 140 but persistence fails (the very next
#                                 candle reverts above 140) ->
#                                 LIQUIDITY_SWEEP. active_low stays 140.
#                                 pending_low = 130.
#   index 22: swing high 215 -> above active_high (210); trend is still
#                                 BULLISH (continuation) -> BOS bullish.
#                                 close (211) > 210 confirms. Promotes the
#                                 swept pending_low (130) to active_low.
#   index 27: swing low  120 -> below active_low (130, promoted); trend is
#                                 BULLISH (reversal candidate). close (125)
#                                 and persistence (candle 28 also closes
#                                 below 130) confirm -> CHoCH bearish.
SWEEP_HIGHS = [150.0] * 30
for _index, _value in {2: 200.0, 12: 210.0, 22: 215.0}.items():
    SWEEP_HIGHS[_index] = _value

SWEEP_LOWS = [145.0] * 30
for _index, _value in {7: 140.0, 17: 130.0, 27: 120.0}.items():
    SWEEP_LOWS[_index] = _value


def test_bos_close_confirmed_and_sweep_on_persistence_failure() -> None:
    candles = make_series(SWEEP_HIGHS, SWEEP_LOWS)
    # index 12: close (205) > active_high (200) -> BOS bullish confirmed.
    candles[12] = make_candle(12, SWEEP_HIGHS[12], SWEEP_LOWS[12], close=205.0)
    # index 17: close (135) < active_low (140), but no 2-candle window holds
    # below 140 (the next candle reverts to 147.5) -> LIQUIDITY_SWEEP.
    candles[17] = make_candle(17, SWEEP_HIGHS[17], SWEEP_LOWS[17], close=135.0)
    # index 22: close (211) > active_high (210) -> BOS bullish confirmed.
    candles[22] = make_candle(22, SWEEP_HIGHS[22], SWEEP_LOWS[22], close=211.0)
    # index 27 + 28: persistence window below active_low (130) -> CHoCH bearish.
    candles[27] = make_candle(27, SWEEP_HIGHS[27], SWEEP_LOWS[27], close=125.0)
    candles[28] = make_candle(28, SWEEP_HIGHS[28], 121.0, close=126.0)

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 215.0, 210.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 120.0, 130.0),
    ]


def test_bos_state_updates_silently_when_close_does_not_confirm() -> None:
    """BOS state (trend, active refs) advances on any wick break of the
    active reference, even if no close beyond the level exists. In that case
    no BOS event is emitted, but the trend is updated -- which means the
    *next* counter-trend break is evaluated as a reversal (CHoCH candidate)
    against the new state, not as a continuation.

    Sequence (lookback=1): high(200) bootstrap, low(140) bootstrap,
    high(210) breaks active_high silently (close=177.5 < 200 -> no emit,
    but trend=BULLISH), low(130) CHoCH candidate (trend now BULLISH) ->
    persistence fails -> SWEEP. An extra trailing candle is needed so
    that index 4 falls within SwingLowDetector's valid pivot range.
    """
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0]
    # Default close for index 3 = (210+145)/2 = 177.5, which is < 200;
    # BOS state still advances to BULLISH but no event fires.
    # Default close for index 4 = (150+130)/2 = 140, which is not < 140
    # (strictly) -> persistence check also fails -> SWEEP.
    candles = make_series(highs, lows)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    # The BOS at index 3 fires no event (close doesn't confirm), but trend
    # becomes BULLISH, so the low at index 4 is treated as a CHoCH candidate.
    # With default close=140 (not strictly < 140), persistence fails -> SWEEP.
    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
    ]


def test_liquidity_sweep_when_persistence_fails() -> None:
    """A counter-trend break where the close crosses the reference but the
    persistence window does not hold is reported as a `LIQUIDITY_SWEEP`, not
    a confirmed CHoCH -- while a same-direction (BOS) break right after
    remains unaffected.
    """
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 220.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0, 120.0]
    candles = make_series(highs, lows)
    # index 3: close (205) > active_high (200); trend NEUTRAL -> BOS bullish.
    candles[3] = make_candle(3, highs[3], lows[3], close=205.0)
    # index 4: close (140) is exactly at active_low (140) -- not strictly
    # below -- so persistence_candles check fails -> LIQUIDITY_SWEEP.
    # (Default close = (150+130)/2 = 140, no explicit patch needed.)
    # index 5: close (215) > active_high (210); trend BULLISH -> BOS bullish.
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
    ]


def test_detector_stamps_major_scope() -> None:
    candles = _confirmed_series(HIGHS, LOWS)

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert events
    assert all(event.scope is StructureScope.MAJOR for event in events)


def test_promoted_active_high_is_the_highest_high_of_the_prior_leg() -> None:
    """`pending_high` accumulates the *highest* high seen since `active_low`
    was last set -- not merely the most recently formed one -- so a later
    BOS bearish promotes that true leg-high to `active_high`, even if a
    lower high formed more recently.

    Sequence (lookback=1):

      index  1: swing high 200 -> bootstraps active_high
      index  3: swing low   90 -> bootstraps active_low
      index  5: swing low   80 -> below active_low (90); trend NEUTRAL ->
                                    BOS bearish. close (85) < 90 confirms.
                                    trend=BEARISH; pending_high is empty ->
                                    active_high discarded to None.
      index  7: swing high 120 -> below active_high (None) -> LOWER_HIGH
                                    label vs. previous high (200);
                                    pending_high = 120.
      index  9: swing high 150 -> active_high still None -> HIGHER_HIGH
                                    (vs. 120); pending_high = max(120, 150)
                                    = 150.
      index 11: swing high 130 -> active_high still None -> LOWER_HIGH
                                    (vs. 150); pending_high stays 150
                                    (130 is not the new max).
      index 13: swing low   70 -> below active_low (80); trend BEARISH ->
                                    BOS bearish (continuation). close (75) <
                                    80 confirms. Promotes pending_high (150,
                                    the leg's highest high) to active_high
                                    -- NOT 130, the most recently formed high.
      index 15: swing high 140 -> 140 <= active_high (150) -> no BOS/CHoCH,
                                    just a HIGHER_HIGH label (vs. 130). Under
                                    the old "last pivot wins" rule,
                                    active_high would have been promoted to
                                    130, and 140 would have wrongly broken it
                                    as a CHoCH bullish.
    """
    highs = [
        100.0, 200.0, 100.0, 100.0, 100.0, 100.0, 100.0, 120.0,
        100.0, 150.0, 100.0, 130.0, 100.0, 100.0, 100.0, 140.0, 100.0,
    ]
    lows = [
        100.0, 100.0, 100.0, 90.0, 100.0, 80.0, 100.0, 100.0,
        100.0, 100.0, 100.0, 100.0, 100.0, 70.0, 100.0, 100.0, 100.0,
    ]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=85.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=75.0)

    events = SwingStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 80.0, 90.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 120.0, 200.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 150.0, 120.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 130.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 70.0, 80.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 140.0, 130.0),
    ]


def test_promoted_active_low_is_the_lowest_low_of_the_prior_leg() -> None:
    """Mirror of `test_promoted_active_high_is_the_highest_high_of_the_prior_leg`:
    `pending_low` accumulates the *lowest* low seen since `active_high` was
    last set, so a later BOS bullish promotes that true leg-low to
    `active_low`, even if a higher low formed more recently.

    Sequence (lookback=1):

      index  1: swing low   100 -> bootstraps active_low
      index  3: swing high  210 -> bootstraps active_high
      index  5: swing high  220 -> above active_high (210); trend NEUTRAL ->
                                     BOS bullish. close (215) > 210 confirms.
                                     trend=BULLISH; pending_low is empty ->
                                     active_low discarded to None.
      index  7: swing low   180 -> active_low None -> HIGHER_LOW label;
                                     pending_low = 180.
      index  9: swing low   150 -> active_low None -> LOWER_LOW (vs. 180);
                                     pending_low = min(180, 150) = 150.
      index 11: swing low   170 -> active_low None -> HIGHER_LOW (vs. 150);
                                     pending_low stays 150 (170 is not new min).
      index 13: swing high  230 -> above active_high (220); trend BULLISH ->
                                     BOS bullish (continuation). close (225) >
                                     220 confirms. Promotes pending_low (150,
                                     the leg's lowest low) to active_low --
                                     NOT 170, the most recently formed low.
      index 15: swing low   160 -> 160 >= active_low (150) -> no BOS/CHoCH,
                                     just a LOWER_LOW label (vs. 170). Under
                                     the old "last pivot wins" rule,
                                     active_low would have been promoted to
                                     170, and 160 would have wrongly broken
                                     it as a CHoCH bearish.
    """
    highs = [
        200.0, 200.0, 200.0, 210.0, 200.0, 220.0, 200.0, 200.0,
        200.0, 200.0, 200.0, 200.0, 200.0, 230.0, 200.0, 200.0, 200.0,
    ]
    lows = [
        200.0, 100.0, 200.0, 200.0, 200.0, 200.0, 200.0, 180.0,
        200.0, 150.0, 200.0, 170.0, 200.0, 200.0, 200.0, 160.0, 200.0,
    ]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=225.0)

    events = SwingStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 180.0, 100.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 150.0, 180.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 170.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 220.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 160.0, 170.0),
    ]


def test_choch_active_high_reconciles_with_a_more_extreme_prior_sweep() -> None:
    """When a CHoCH bullish confirms, the new `active_high` is the more
    extreme of (a) the confirming pivot itself and (b) `pending_high` (the
    highest high accumulated since `active_low` was last set) -- not simply
    the confirming pivot.

    This matters when an earlier same-direction `LIQUIDITY_SWEEP` reached
    *higher* than the pivot that goes on to confirm the CHoCH: the swept
    level becomes the new `active_high`, so a later BOS bullish reports
    *that* level as its `reference_price_level`, not the (less extreme)
    CHoCH pivot.

    Sequence (lookback=1), continuing from the same setup as
    `test_promoted_active_high_is_the_highest_high_of_the_prior_leg`
    (after index 13's BOS bearish: active_high=150, active_low=70,
    pending_high=None, trend=BEARISH):

      index 15: swing high 165 -> above active_high (150); close (145) does
                                    not confirm persistence -> LIQUIDITY_SWEEP
                                    bullish, 165/150. pending_high = 165.
      index 17: swing high 160 -> above active_high (150); close (155) > 150
                                    and candle 18 also closes > 150 ->
                                    persistence holds -> CHANGE_OF_CHARACTER
                                    bullish, 160/150. The new active_high is
                                    `_extreme(pending_high=165, pivot=160)`
                                    = 165 -- the swept level, not 160.
      index 19: swing high 170 -> above active_high (165); trend BULLISH ->
                                    BOS bullish. close (166) > 165 confirms.
                                    reference_price_level=165 (the reconciled
                                    active_high), not 160.
    """
    highs = [
        100.0, 200.0, 100.0, 100.0, 100.0, 100.0, 100.0, 120.0,
        100.0, 150.0, 100.0, 130.0, 100.0, 100.0, 100.0, 165.0,
        100.0, 160.0, 100.0, 170.0, 100.0,
    ]
    lows = [
        100.0, 100.0, 100.0, 90.0, 100.0, 80.0, 100.0, 100.0,
        100.0, 100.0, 100.0, 100.0, 100.0, 70.0, 100.0, 100.0,
        100.0, 100.0, 100.0, 100.0, 100.0,
    ]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=85.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=75.0)
    # index 15: sweep -- close (145) does not reach above 150.
    candles[15] = make_candle(15, highs[15], lows[15], close=145.0)
    # index 17: CHoCH -- close (155) > 150; candle 18 (high=155) also closes
    # > 150, forming the required 2-candle persistence window.
    candles[17] = make_candle(17, highs[17], lows[17], close=155.0)
    candles[18] = make_candle(18, 155.0, lows[18], close=152.0)
    # index 19: BOS -- close (166) > active_high (165, reconciled).
    candles[19] = make_candle(19, highs[19], lows[19], close=166.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 80.0, 90.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 120.0, 200.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 150.0, 120.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 130.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 70.0, 80.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BULLISH, 165.0, 150.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 160.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 170.0, 165.0),
    ]


def test_choch_active_low_reconciles_with_a_more_extreme_prior_sweep() -> None:
    """Mirror of `test_choch_active_high_reconciles_with_a_more_extreme_prior_sweep`:
    when a CHoCH bearish confirms, the new `active_low` is the more extreme
    of (a) the confirming pivot itself and (b) `pending_low` (the lowest low
    accumulated since `active_high` was last set) -- not simply the
    confirming pivot.

    Sequence (lookback=1), continuing from the same setup as
    `test_promoted_active_low_is_the_lowest_low_of_the_prior_leg`
    (after index 13's BOS bullish: active_high=230, active_low=150,
    pending_low=None, trend=BULLISH):

      index 15: swing low 135 -> below active_low (150); close (155) does
                                   not close below 150 -> LIQUIDITY_SWEEP
                                   bearish, 135/150. pending_low = 135.
      index 17: swing low 140 -> below active_low (150); close (145) < 150
                                   and candle 18 also closes < 150 ->
                                   persistence holds -> CHANGE_OF_CHARACTER
                                   bearish, 140/150. The new active_low is
                                   `_extreme(pending_low=135, pivot=140)`
                                   = 135 -- the swept level, not 140.
      index 19: swing low 130 -> below active_low (135); trend BEARISH ->
                                   BOS bearish. close (132) < 135 confirms.
                                   reference_price_level=135 (the reconciled
                                   active_low), not 140.
    """
    highs = [
        200.0, 200.0, 200.0, 210.0, 200.0, 220.0, 200.0, 200.0,
        200.0, 200.0, 200.0, 200.0, 200.0, 230.0, 200.0, 200.0,
        200.0, 200.0, 200.0, 200.0, 200.0,
    ]
    lows = [
        200.0, 100.0, 200.0, 200.0, 200.0, 200.0, 200.0, 180.0,
        200.0, 150.0, 200.0, 170.0, 200.0, 200.0, 200.0, 135.0,
        200.0, 140.0, 200.0, 130.0, 200.0,
    ]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=225.0)
    # index 15: sweep -- close (155) does not close below 150.
    candles[15] = make_candle(15, highs[15], lows[15], close=155.0)
    # index 17: CHoCH -- close (145) < 150; candle 18 (low=145) also closes
    # < 150, forming the required 2-candle persistence window.
    candles[17] = make_candle(17, highs[17], lows[17], close=145.0)
    candles[18] = make_candle(18, highs[18], 145.0, close=148.0)
    # index 19: BOS -- close (132) < active_low (135, reconciled).
    candles[19] = make_candle(19, highs[19], lows[19], close=132.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 180.0, 100.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 150.0, 180.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 170.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 220.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 135.0, 150.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 140.0, 150.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 130.0, 135.0),
    ]


def test_active_high_discarded_to_none_is_rebuilt_from_pending_then_broken() -> None:
    """When a bearish BOS/CHoCH promotes `pending_low` to `active_low` but
    `pending_high` is empty (no high pivot has formed in the new leg yet),
    `active_high` is discarded to `None` rather than left at its stale prior
    value. While `active_high is None`, subsequent high pivots are purely
    descriptive HH/LH labels that accumulate into `pending_high`, until the
    next bearish BOS/CHoCH promotes that accumulation to `active_high` --
    which can then itself be broken by a later CHoCH bullish.

    Sequence (lookback=1):

      index  1: swing high 200 -> bootstraps active_high
      index  3: swing low   90 -> bootstraps active_low (and seeds
                                    pending_low, since active_high already
                                    exists)
      index  5: swing low   80 -> below active_low (90); trend NEUTRAL ->
                                    BOS bearish. close (85) < 90 confirms.
                                    Promotes pending_low (90, seeded) to
                                    active_low via `_extreme(90, 80)` = 80;
                                    pending_high is empty -> active_high
                                    discarded to None; trend=BEARISH.
      index  7: swing high 250 -> active_high is None -> HIGHER_HIGH label
                                    vs. previous high (200); pending_high =
                                    250.
      index  9: swing high 220 -> active_high still None -> LOWER_HIGH
                                    label vs. previous high (250);
                                    pending_high stays 250 (220 < 250).
      index 11: swing low   70 -> below active_low (80); trend BEARISH ->
                                    BOS bearish (continuation). close (75) <
                                    80 confirms. Promotes pending_high (250)
                                    to active_high.
      index 13: swing high 260 -> above active_high (250); trend BEARISH ->
                                    CHoCH-candidate bullish. close (255) > 250
                                    and candle 14 also closes > 250 ->
                                    persistence holds -> CHANGE_OF_CHARACTER
                                    bullish, 260/250.
    """
    highs = [100.0] * 15
    for index, value in {1: 200.0, 7: 250.0, 9: 220.0, 13: 260.0}.items():
        highs[index] = value
    lows = [100.0] * 15
    for index, value in {3: 90.0, 5: 80.0, 11: 70.0}.items():
        lows[index] = value

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=85.0)
    candles[11] = make_candle(11, highs[11], lows[11], close=75.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=255.0)
    candles[14] = make_candle(14, 255.0, lows[14], close=252.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 80.0, 90.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 250.0, 200.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 220.0, 250.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 70.0, 80.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 260.0, 250.0),
    ]


def test_active_low_discarded_to_none_is_rebuilt_from_pending_then_broken() -> None:
    """Mirror of `test_active_high_discarded_to_none_is_rebuilt_from_pending_then_broken`:
    a bullish BOS/CHoCH that promotes `pending_high` to `active_high` while
    `pending_low` is empty discards `active_low` to `None`; it is then
    rebuilt from HL/LL labels accumulating into `pending_low`, promoted by
    the next bullish BOS/CHoCH, and finally broken by a CHoCH bearish.

    Sequence (lookback=1):

      index  1: swing low  100 -> bootstraps active_low
      index  3: swing high 210 -> bootstraps active_high (and seeds
                                    pending_high, since active_low already
                                    exists)
      index  5: swing high 220 -> above active_high (210); trend NEUTRAL ->
                                    BOS bullish. close (215) > 210 confirms.
                                    Promotes pending_high (210, seeded) to
                                    active_high via `_extreme(210, 220)` =
                                    220; pending_low is empty -> active_low
                                    discarded to None; trend=BULLISH.
      index  7: swing low   50 -> active_low is None -> LOWER_LOW label
                                    vs. previous low (100); pending_low = 50.
      index  9: swing low   80 -> active_low still None -> HIGHER_LOW label
                                    vs. previous low (50); pending_low stays
                                    50 (80 > 50).
      index 11: swing high 230 -> above active_high (220); trend BULLISH ->
                                    BOS bullish (continuation). close (225) >
                                    220 confirms. Promotes pending_low (50)
                                    to active_low.
      index 13: swing low   40 -> below active_low (50); trend BULLISH ->
                                    CHoCH-candidate bearish. close (45) < 50
                                    and candle 14 (low=45) also closes < 50
                                    -> persistence holds -> CHANGE_OF_CHARACTER
                                    bearish, 40/50.
    """
    highs = [200.0] * 15
    for index, value in {3: 210.0, 5: 220.0, 11: 230.0}.items():
        highs[index] = value
    lows = [200.0] * 15
    for index, value in {1: 100.0, 7: 50.0, 9: 80.0, 13: 40.0}.items():
        lows[index] = value

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    candles[11] = make_candle(11, highs[11], lows[11], close=225.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=45.0)
    candles[14] = make_candle(14, highs[14], 45.0, close=48.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 50.0, 100.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 80.0, 50.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 220.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 40.0, 50.0),
    ]
