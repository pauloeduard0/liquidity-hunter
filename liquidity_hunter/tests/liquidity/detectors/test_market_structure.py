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

# For each breaking pivot above, a (close, taker_buy_volume) override that
# confirms the break: `close` beyond the active level, and `taker_buy_volume`
# giving a `volume_delta` ratio of 0.4 (>= the detector's default
# `min_volume_delta_ratio` of 0.2) in the breakout direction. Only index 22's
# override is load-bearing for `test_swing_structure_detector_full_sequence`
# (its CHoCH bullish is the sequence's only counter-trend break); the others
# are harmless and kept for consistency with other tests reusing `HIGHS`/`LOWS`.
_CONFIRMATION_OVERRIDES = {
    17: (135.0, 0.3),  # close < active_low (140); bearish volume delta
    22: (192.0, 0.7),  # close > active_high (190); bullish volume delta
    27: (125.0, 0.3),  # close < active_low (130); bearish volume delta
    32: (200.0, 0.7),  # close > active_high (193); bullish volume delta
}


def _confirmed_series(highs: list[float], lows: list[float]) -> list[Candle]:
    """`make_series(highs, lows)` with `_CONFIRMATION_OVERRIDES` applied."""
    candles = make_series(highs, lows)
    for index, (close, taker_buy_volume) in _CONFIRMATION_OVERRIDES.items():
        candles[index] = make_candle(
            index, highs[index], lows[index], close=close, taker_buy_volume=taker_buy_volume
        )
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

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

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


def test_swing_structure_detector_rejects_invalid_min_volume_delta_ratio() -> None:
    with pytest.raises(ValueError, match="min_volume_delta_ratio must be between 0 and 1"):
        SwingStructureDetector(min_volume_delta_ratio=1.5)

    with pytest.raises(ValueError, match="min_volume_delta_ratio must be between 0 and 1"):
        SwingStructureDetector(min_volume_delta_ratio=-0.1)


# A BOS-via-shadow / swept-then-promoted-then-reversed sequence (lookback=2):
#
#   index  2: swing high 200 -> bootstraps active_high
#   index  7: swing low  140 -> bootstraps active_low
#   index 12: swing high 210 -> wick exceeds active_high (200); trend is
#                                 still NEUTRAL (continuation), so this is a
#                                 BOS bullish on price alone -- close (195)
#                                 and a neutral volume delta would NOT have
#                                 confirmed under the close+volume rule.
#                                 trend becomes BULLISH.
#   index 17: swing low  130 -> below active_low (140); trend is BULLISH, so
#                                 this is a CHoCH-candidate. Its close (145)
#                                 doesn't even close beyond 140 ->
#                                 LIQUIDITY_SWEEP, active_low stays 140,
#                                 pending_low = 130.
#   index 22: swing high 215 -> above active_high (210); trend is still
#                                 BULLISH (continuation) -> BOS bullish on
#                                 price alone, promoting the swept
#                                 pending_low (130) to active_low.
#   index 27: swing low  120 -> below active_low (130, promoted); trend is
#                                 BULLISH, so this is a CHoCH-candidate.
#                                 Its close (125) and bearish volume delta
#                                 confirm -> CHoCH bearish.
SWEEP_HIGHS = [150.0] * 30
for _index, _value in {2: 200.0, 12: 210.0, 22: 215.0}.items():
    SWEEP_HIGHS[_index] = _value

SWEEP_LOWS = [145.0] * 30
for _index, _value in {7: 140.0, 17: 130.0, 27: 120.0}.items():
    SWEEP_LOWS[_index] = _value


def test_bos_confirmed_by_price_alone_and_sweep_only_blocks_choch() -> None:
    candles = make_series(SWEEP_HIGHS, SWEEP_LOWS)
    # index 12: wick (210) > active_high (200), but close (195) and a
    # neutral volume delta would not confirm under the close+volume rule.
    # trend is NEUTRAL, so this is still a BOS bullish on price alone.
    candles[12] = make_candle(12, SWEEP_HIGHS[12], SWEEP_LOWS[12], close=195.0)
    # index 17: close (145) doesn't close beyond active_low (140) -> sweep.
    candles[17] = make_candle(17, SWEEP_HIGHS[17], SWEEP_LOWS[17], close=145.0)
    # index 27: close (125) < active_low (130) with bearish volume delta -> confirmed CHoCH.
    candles[27] = make_candle(
        27, SWEEP_HIGHS[27], SWEEP_LOWS[27], close=125.0, taker_buy_volume=0.2
    )

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 215.0, 210.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 120.0, 130.0),
    ]


def test_liquidity_sweep_when_volume_delta_too_weak() -> None:
    """A counter-trend break with a confirming close but a neutral (zero)
    volume delta is reported as a `LIQUIDITY_SWEEP`, not a confirmed CHoCH
    -- while a same-direction (BOS) break right after remains unaffected.
    """
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 220.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0, 120.0]
    candles = make_series(highs, lows)
    # index 3: high (210) breaks active_high (200) while trend is still
    # NEUTRAL -> BOS bullish on price alone; trend becomes BULLISH.
    # index 4: low (130) breaks active_low (140); trend is BULLISH, so this
    # is a CHoCH-candidate. close (135) closes beyond 140, but
    # taker_buy_volume=0.5 (the make_candle default) gives volume_delta ==
    # 0 -> LIQUIDITY_SWEEP, not CHoCH.
    candles[4] = make_candle(4, highs[4], lows[4], close=135.0)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
    ]


def test_detector_stamps_major_scope() -> None:
    candles = _confirmed_series(HIGHS, LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert events
    assert all(event.scope is StructureScope.MAJOR for event in events)


def test_min_volume_delta_ratio_zero_confirms_choch_on_sign_alone() -> None:
    """With `min_volume_delta_ratio=0.0`, any non-zero volume delta in the
    breakout direction is enough to confirm a counter-trend break as a
    CHoCH, regardless of magnitude. BOS (continuation) breaks are confirmed
    by price alone either way -- this setting only affects CHoCH.

    The CHoCH at index 4 discards `active_high` to `None`, since nothing had
    accumulated in `pending_high` for the new (bearish) leg yet. So index 5's
    higher high is reported as a descriptive `HIGHER_HIGH`, not evaluated
    against the now-discarded level (210).
    """
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 220.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0, 120.0]
    candles = make_series(highs, lows)
    # index 3: high (210) breaks active_high (200) while trend is NEUTRAL ->
    # BOS bullish on price alone; trend becomes BULLISH.
    # index 4: low (130) breaks active_low (140); trend is BULLISH, so this
    # is a CHoCH-candidate. close (139) closes beyond 140, with a
    # barely-negative volume delta (taker_buy_volume just below half) ->
    # confirmed CHANGE_OF_CHARACTER bearish; pending_high is empty, so
    # active_high (210) is discarded to None.
    candles[4] = make_candle(4, highs[4], lows[4], close=139.0, taker_buy_volume=0.49)

    events = SwingStructureDetector(swing_lookback=1, min_volume_delta_ratio=0.0).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH, 220.0, 210.0),
    ]


def test_promoted_active_high_is_the_highest_high_of_the_prior_leg() -> None:
    """`pending_high` accumulates the *highest* high seen since `active_low`
    was last set -- not merely the most recently formed one -- so a later
    BOS bearish promotes that true leg-high to `active_high`, even if a
    lower high formed more recently.

    Sequence (lookback=1):

      index  1: swing high 200 -> bootstraps active_high
      index  3: swing low   90 -> bootstraps active_low
      index  5: swing low   80 -> below active_low (90); trend NEUTRAL ->
                                    BOS bearish (price-only); trend=BEARISH;
                                    pending_high reset to None.
      index  7: swing high 120 -> below active_high (200) -> LOWER_HIGH;
                                    pending_high = 120.
      index  9: swing high 150 -> below active_high (200) -> HIGHER_HIGH
                                    (vs. 120); pending_high = max(120, 150)
                                    = 150.
      index 11: swing high 130 -> below active_high (200) -> LOWER_HIGH
                                    (vs. 150); pending_high stays 150
                                    (130 is not the new max).
      index 13: swing low   70 -> below active_low (80); trend BEARISH ->
                                    BOS bearish (price-only); promotes
                                    pending_high (150, the leg's highest
                                    high) to active_high -- NOT 130, the
                                    most recently formed high.
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
    # index 15: close beyond 130 with a strong bullish volume delta -- would
    # confirm a CHoCH bullish against a *stale* active_high of 130, but
    # active_high is correctly 150 here, so 140 doesn't break it.
    candles[15] = make_candle(15, highs[15], lows[15], close=135.0, taker_buy_volume=0.7)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

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
                                     BOS bullish (price-only); trend=BULLISH;
                                     pending_low reset to None.
      index  7: swing low   180 -> above active_low (100) -> HIGHER_LOW;
                                     pending_low = 180.
      index  9: swing low   150 -> above active_low (100) -> LOWER_LOW
                                     (vs. 180); pending_low = min(180, 150)
                                     = 150.
      index 11: swing low   170 -> above active_low (100) -> HIGHER_LOW
                                     (vs. 150); pending_low stays 150
                                     (170 is not the new min).
      index 13: swing high  230 -> above active_high (220); trend BULLISH ->
                                     BOS bullish (price-only); promotes
                                     pending_low (150, the leg's lowest low)
                                     to active_low -- NOT 170, the most
                                     recently formed low.
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
    # index 15: close beyond 170 with a strong bearish volume delta -- would
    # confirm a CHoCH bearish against a *stale* active_low of 170, but
    # active_low is correctly 150 here, so 160 doesn't break it.
    candles[15] = make_candle(15, highs[15], lows[15], close=165.0, taker_buy_volume=0.3)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

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

      index 15: swing high 165 -> above active_high (150); close=145 does
                                    not close beyond 150 -> NOT confirmed ->
                                    LIQUIDITY_SWEEP bullish, 165/150.
                                    pending_high = 165.
      index 17: swing high 160 -> above active_high (150) still; close=155
                                    beyond 150 with a strong bullish volume
                                    delta -> CONFIRMED -> CHANGE_OF_CHARACTER
                                    bullish, 160/150. The new active_high is
                                    `_extreme(pending_high=165, pivot=160)`
                                    = 165 -- the swept level, not 160.
      index 19: swing high 170 -> above active_high; trend is now BULLISH ->
                                    BOS bullish, reference_price_level=165
                                    (the reconciled active_high), not 160.
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
    # index 15: sweep -- high beyond active_high (150), but close (145) does
    # not confirm.
    candles[15] = make_candle(15, highs[15], lows[15], close=145.0)
    # index 17: confirmed CHoCH -- close (155) beyond active_high (150) with
    # a strong bullish volume delta, but its high (160) is below index 15's
    # swept high (165).
    candles[17] = make_candle(17, highs[17], lows[17], close=155.0, taker_buy_volume=0.7)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

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

      index 15: swing low 135 -> below active_low (150); close=155 does not
                                   close below 150 -> NOT confirmed ->
                                   LIQUIDITY_SWEEP bearish, 135/150.
                                   pending_low = 135.
      index 17: swing low 140 -> below active_low (150) still; close=145
                                   below 150 with a strong bearish volume
                                   delta -> CONFIRMED -> CHANGE_OF_CHARACTER
                                   bearish, 140/150. The new active_low is
                                   `_extreme(pending_low=135, pivot=140)`
                                   = 135 -- the swept level, not 140.
      index 19: swing low 130 -> below active_low; trend is now BEARISH ->
                                   BOS bearish, reference_price_level=135
                                   (the reconciled active_low), not 140.
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
    # index 15: sweep -- low beyond active_low (150), but close (155) does
    # not confirm.
    candles[15] = make_candle(15, highs[15], lows[15], close=155.0)
    # index 17: confirmed CHoCH -- close (145) below active_low (150) with a
    # strong bearish volume delta, but its low (140) is above index 15's
    # swept low (135).
    candles[17] = make_candle(17, highs[17], lows[17], close=145.0, taker_buy_volume=0.3)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

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
                                    BOS bearish (price-only); promotes
                                    pending_low (90, seeded) to active_low
                                    via `_extreme(90, 80)` = 80;
                                    pending_high is empty -> active_high
                                    discarded to None; trend=BEARISH
      index  7: swing high 250 -> active_high is None -> HIGHER_HIGH label
                                    vs. previous high (200); pending_high =
                                    250
      index  9: swing high 220 -> active_high still None -> LOWER_HIGH
                                    label vs. previous high (250);
                                    pending_high stays 250 (220 < 250)
      index 11: swing low   70 -> below active_low (80); trend BEARISH ->
                                    BOS bearish (continuation, price-only);
                                    promotes pending_high (250) to
                                    active_high
      index 13: swing high 260 -> above active_high (250); trend BEARISH ->
                                    CHoCH-candidate bullish; close (255) and
                                    a strong bullish volume delta confirm ->
                                    CHANGE_OF_CHARACTER bullish, 260/250
    """
    highs = [100.0] * 15
    for index, value in {1: 200.0, 7: 250.0, 9: 220.0, 13: 260.0}.items():
        highs[index] = value
    lows = [100.0] * 15
    for index, value in {3: 90.0, 5: 80.0, 11: 70.0}.items():
        lows[index] = value

    candles = make_series(highs, lows)
    # index 13: close beyond active_high (250) with a strong bullish volume
    # delta -> confirmed CHoCH.
    candles[13] = make_candle(13, highs[13], lows[13], close=255.0, taker_buy_volume=0.7)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

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
                                    BOS bullish (price-only); promotes
                                    pending_high (210, seeded) to
                                    active_high via `_extreme(210, 220)` =
                                    220; pending_low is empty -> active_low
                                    discarded to None; trend=BULLISH
      index  7: swing low   50  -> active_low is None -> LOWER_LOW label
                                    vs. previous low (100); pending_low = 50
      index  9: swing low   80  -> active_low still None -> HIGHER_LOW label
                                    vs. previous low (50); pending_low stays
                                    50 (80 > 50)
      index 11: swing high 230 -> above active_high (220); trend BULLISH ->
                                    BOS bullish (continuation, price-only);
                                    promotes pending_low (50) to active_low
      index 13: swing low   40  -> below active_low (50); trend BULLISH ->
                                    CHoCH-candidate bearish; close (45) and a
                                    strong bearish volume delta confirm ->
                                    CHANGE_OF_CHARACTER bearish, 40/50
    """
    highs = [200.0] * 15
    for index, value in {3: 210.0, 5: 220.0, 11: 230.0}.items():
        highs[index] = value
    lows = [200.0] * 15
    for index, value in {1: 100.0, 7: 50.0, 9: 80.0, 13: 40.0}.items():
        lows[index] = value

    candles = make_series(highs, lows)
    # index 13: close beyond active_low (50) with a strong bearish volume
    # delta -> confirmed CHoCH.
    candles[13] = make_candle(13, highs[13], lows[13], close=45.0, taker_buy_volume=0.3)

    events = SwingStructureDetector(swing_lookback=1).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 210.0),
        (StructureEvent.LOWER_LOW, MarketDirection.BEARISH, 50.0, 100.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 80.0, 50.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 220.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 40.0, 50.0),
    ]
