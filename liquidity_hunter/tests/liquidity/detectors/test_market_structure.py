"""Tests for `SwingStructureDetector`."""

import pytest

from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent, StructureScope
from liquidity_hunter.liquidity.detectors.market_structure import SwingStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Main sequence (lookback=2, spacing=5 so each pivot needs 2 flat candles on
# each side):
#
#   index  2: swing high 200 -> bootstrap active_high
#   index  7: swing low   90 -> bootstrap active_low; pending_low=90
#   index 12: swing high 180 -> LOWER_HIGH(180/200);
#               candidate_choch_high=180, baseline=active_low=90
#   index 17: swing low   95 -> HIGHER_LOW(95/90);
#               candidate_choch_low=95, baseline=active_high=180;
#               pending_high=180
#   index 22: swing low   80 -> BOS bearish(80/95) [trend NEUTRAL->BEARISH];
#               active_high = pending_high = 180;
#               candidate_choch_high=180, baseline=90: 80<90 -> promotes
#               validated_choch_high=180
#   index 27: swing high 185 -> CHoCH bullish(185/180) [break > 180 sustains];
#               active_low = pending_low = 90 (accumulated at LH-12)
#   index 32: swing low   95 -> HIGHER_LOW(95/90); pending_high=185;
#               candidate_choch_low=95, baseline=185
#   index 37: swing high 195 -> BOS bullish(195/185) [continuation];
#               candidate_choch_low=95, baseline=185: 195>185 -> promotes
#               validated_choch_low=95; active_low = pending_low = None
MAIN_HIGHS = [150.0] * 40
for _i, _v in {2: 200.0, 12: 180.0, 27: 185.0, 37: 195.0}.items():
    MAIN_HIGHS[_i] = _v

MAIN_LOWS = [145.0] * 40
for _i, _v in {7: 90.0, 17: 95.0, 22: 80.0, 32: 95.0}.items():
    MAIN_LOWS[_i] = _v

# Strictly descending highs / ascending lows — no pivot ever exceeds the
# trailing active reference, so every pivot is just a LH or HL label.
#
#   index  2: swing high 300 -> bootstrap active_high
#   index  7: swing low  100 -> bootstrap active_low
#   index 12: swing high 270 -> LH(270/300); active_high becomes 270
#   index 17: swing low  120 -> HL(120/100); active_low becomes 120
#   index 22: swing high 250 -> LH(250/270); active_high becomes 250
#   index 27: swing low  140 -> HL(140/120); active_low becomes 140
LABELS_HIGHS = [200.0] * 30
for _i, _v in {2: 300.0, 12: 270.0, 22: 250.0}.items():
    LABELS_HIGHS[_i] = _v

LABELS_LOWS = [190.0] * 30
for _i, _v in {7: 100.0, 17: 120.0, 27: 140.0}.items():
    LABELS_LOWS[_i] = _v

# Two equal swing highs (lookback=2): neither a BOS nor a label is emitted.
EQUAL_HIGHS = [200.0] * 20
for _i in (2, 12):
    EQUAL_HIGHS[_i] = 300.0
EQUAL_LOWS = [190.0] * 20
EQUAL_LOWS[7] = 100.0


def _confirmed_main_series() -> list[Candle]:
    """`make_series(MAIN_HIGHS, MAIN_LOWS)` with close/low overrides for
    BOS/CHoCH confirmation and persistence windows."""
    candles = make_series(MAIN_HIGHS, MAIN_LOWS)
    # BOS bearish at index 22 (ref=95): close must be < 95.
    candles[22] = make_candle(22, MAIN_HIGHS[22], MAIN_LOWS[22], close=88.0)
    # CHoCH bullish at index 27 (ref=180): close must be > 180 at [27] and
    # [28] (persistence_candles=1 means the window is [pivot, pivot+1]).
    candles[27] = make_candle(27, MAIN_HIGHS[27], MAIN_LOWS[27], close=183.0)
    candles[28] = make_candle(28, 183.0, MAIN_LOWS[28], close=182.0)
    # BOS bullish at index 37 (ref=185): close must be > 185.
    candles[37] = make_candle(37, MAIN_HIGHS[37], MAIN_LOWS[37], close=190.0)
    return candles


# ---------------------------------------------------------------------------
# Full-sequence and label tests
# ---------------------------------------------------------------------------

def test_swing_structure_detector_full_sequence() -> None:
    """End-to-end: LH -> HL -> BOS -> CHoCH -> HL -> BOS (major scope).

    The LH at index 12 sets candidate_choch_high=180 with baseline=90.
    The HL at index 17 sets candidate_choch_low=95 with baseline=180 and
    accumulates pending_high=180.
    The BOS bearish at index 22 promotes validated_choch_high=180 (BOS pivot
    80 < baseline 90 ✓).
    The CHoCH bullish at index 27 fires against validated_choch_high=180.
    """
    candles = _confirmed_main_series()

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 180.0, 200.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 95.0, 90.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 80.0, 95.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 185.0, 180.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 95.0, 90.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 195.0, 185.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[12].timestamp,
        candles[17].timestamp,
        candles[22].timestamp,
        candles[27].timestamp,
        candles[32].timestamp,
        candles[37].timestamp,
    ]
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_candidate_does_not_fire_choch_until_validated() -> None:
    """The LH at index 12 is a candidate, not an immediate CHoCH reference.
    It only becomes `validated_choch_high` once the BOS at index 22 confirms
    structure continuation (BOS pivot 80 < baseline 90).  The CHoCH at
    index 27 then uses 180.0 as `reference_price_level`, not 200.0 or any
    other level.
    """
    candles = _confirmed_main_series()

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    lh_events = [e for e in events if e.timestamp == candles[12].timestamp]
    assert [e.event for e in lh_events] == [StructureEvent.LOWER_HIGH]

    choch = next(e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER)
    assert choch.reference_price_level == 180.0


def test_lh_hl_labels_when_no_bos_fires() -> None:
    """Strictly descending highs and ascending lows produce only LH/HL labels
    — no BOS or CHoCH, because no pivot ever exceeds the trailing reference."""
    candles = make_series(LABELS_HIGHS, LABELS_LOWS)

    events = SwingStructureDetector(swing_lookback=2).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 270.0, 300.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 120.0, 100.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 250.0, 270.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 140.0, 120.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[12].timestamp,
        candles[17].timestamp,
        candles[22].timestamp,
        candles[27].timestamp,
    ]


def test_no_label_for_equal_pivots() -> None:
    candles = make_series(EQUAL_HIGHS, EQUAL_LOWS)

    assert SwingStructureDetector(swing_lookback=2).detect(candles) == []


def test_detector_stamps_major_scope() -> None:
    candles = _confirmed_main_series()

    events = SwingStructureDetector(
        swing_lookback=2, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert events
    assert all(event.scope is StructureScope.MAJOR for event in events)


# ---------------------------------------------------------------------------
# BOS confirmation and sweep tests
# ---------------------------------------------------------------------------

def test_bos_confirmed_sweep_then_choch() -> None:
    """BOS close confirmed -> SWEEP (persistence fails) -> BOS (validates
    candidate) -> CHoCH (persistence holds).

    Sequence (lookback=1, persistence_candles=1, confluence_filter=False):

      index 1: high 200 -> bootstrap active_high
      index 3: low  140 -> bootstrap active_low; pending_low=140
      index 5: high 210 -> BOS bullish(210/200); close(205)>200 confirms.
                             active_low=140; trend=BULLISH.
      index 7: low  130 -> SWEEP bearish(130/140); validated_choch_low=None
                             so CHoCH check skipped; persistence fails
                             (close=145 not < 140). pending_high=210.
      index 9: high 215 -> BOS bullish(215/210); close(212)>210 confirms.
                             active_low=None (pending_low cleared); trend=BULLISH.
      index 11: low 133 -> re-bootstrap active_low=133 (silent);
                              133 > last_low_pivot(130) -> candidate_choch_low=133,
                              baseline=215. pending_low=133.
      index 13: high 220 -> BOS bullish(220/215); close(217)>215 confirms;
                              candidate_choch_low=133, 220>215=baseline -> promotes
                              validated_choch_low=133. active_low=pending_low=133.
      index 15: low  120 -> CHoCH bearish(120/133); close(125)<133 at [15] and
                              close(126)<133 at [16] -> persistence holds.
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 210.0, 150.0, 150.0, 150.0,
             215.0, 150.0, 150.0, 150.0, 220.0, 150.0, 150.0, 150.0]
    lows = [145.0, 145.0, 145.0, 140.0, 145.0, 145.0, 145.0, 130.0, 145.0,
            145.0, 145.0, 133.0, 145.0, 145.0, 145.0, 120.0, 145.0]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=205.0)
    candles[9] = make_candle(9, highs[9], lows[9], close=212.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=217.0)
    candles[15] = make_candle(15, highs[15], lows[15], close=125.0)
    candles[16] = make_candle(16, 145.0, 125.0, close=126.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 210.0, 200.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 130.0, 140.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 215.0, 210.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 215.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 120.0, 133.0),
    ]


def test_wick_only_break_freezes_reference_until_close_confirms() -> None:
    """A wick-only in-trend break (the pivot pokes beyond the active reference
    but no candle closes beyond it) does NOT advance the state: the trend does
    not flip and the reference stays frozen at its level. A later pivot whose
    leg contains a candle that *closes* beyond that same frozen level then
    activates the BOS, emitted at the original reference.

    Sequence (lookback=1): high(200) bootstrap, low(140) bootstrap,
    high(210) breaks 200 by wick only (close=177.5 < 200 -> pending, no state
    change, active_high frozen at 200), high(215) whose candle closes at 205
    (> 200) -> BOS bullish confirmed at reference 200.
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 210.0, 150.0, 215.0, 150.0]
    lows = [145.0, 145.0, 145.0, 140.0, 145.0, 145.0, 145.0, 145.0, 145.0]
    candles = make_series(highs, lows)
    # index 5 close = (210+145)/2 = 177.5 < 200 -> wick only.
    assert candles[5].close < 200.0
    # index 7 closes above the frozen 200 reference -> activates the BOS.
    candles[7] = make_candle(7, 215.0, 145.0, close=205.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    # The wick-only break at index 5 leaked no trend, so it produced no spurious
    # CHoCH/SWEEP; the only event is the BOS that fires once a close confirms,
    # against the still-frozen reference 200 (not the 210 wick pivot).
    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 215.0, 200.0),
    ]
    assert events[0].timestamp == candles[7].timestamp


def test_bos_staircase_blocks_higher_continuation_break() -> None:
    """A new continuation BOS must *extend* the leg beyond the previous BOS
    level. After a bearish BOS establishes a low, a retrace forms a higher low;
    breaking that higher low (still above the last BOS low) is NOT a structural
    BOS -- only a break below the previous BOS low is. This is the descending
    staircase: bearish BOS lows must keep making lower lows while the trend is
    unchanged.

    Sequence (lookback=1): high(200)/low(100) bootstrap, low(80) first bearish
    BOS (last BOS low = 80), high(170) lower high, low(120) higher low (active
    ratchets up), low(100) breaks 120 but stays above 80 -> blocked (no BOS),
    low(60) breaks below 80 -> genuine continuation BOS.
    """
    highs = [160.0] * 15
    lows = [140.0] * 15
    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0  # first bearish BOS low; last_bear_bos_low = 80
    highs[7] = 170.0  # lower high (retrace up)
    lows[9] = 120.0  # higher low; active_low ratchets up to 120
    lows[11] = 100.0  # breaks 120 but stays above 80 -> staircase blocks
    lows[13] = 60.0  # breaks below 80 -> genuine continuation BOS

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 160.0, 80.0, close=85.0)  # close < 100 ref
    candles[11] = make_candle(11, 160.0, 100.0, close=110.0)  # blocked before close
    candles[13] = make_candle(13, 160.0, 60.0, close=80.0)  # close < 100 ref

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert all(e.direction is MarketDirection.BEARISH for e in bos)
    # Only the first BOS (index 5) and the genuine continuation (index 13). The
    # higher-low break at index 11 (above the last BOS low 80) is NOT a BOS.
    assert [e.timestamp for e in bos] == [candles[5].timestamp, candles[13].timestamp]


def test_bos_staircase_floored_at_choch_level() -> None:
    """After a CHoCH, a continuation BOS must break *beyond the CHoCH level*,
    not re-break a reference that trailed onto the wrong side of it. Here a
    bearish CHoCH fires at reference 133; price then retraces up (active_low
    ratchets to 138, above the CHoCH level), and a break of that higher low at
    135 -- still ABOVE the CHoCH level 133 -- is NOT a bearish BOS. Only the
    break below 133 (at 125) is. Without the CHoCH floor, the 135 break would
    be the (unconstrained) first BOS of the leg.

    The leading sequence reproduces ``test_bos_confirmed_sweep_then_choch`` up
    to the bearish CHoCH at index 15; indices 17-23 add the retrace + the two
    candidate continuation breaks.
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 210.0, 150.0, 150.0, 150.0,
             215.0, 150.0, 150.0, 150.0, 220.0, 150.0, 150.0, 150.0,
             160.0, 150.0, 150.0, 150.0, 150.0, 150.0, 150.0, 150.0, 150.0]
    lows = [145.0, 145.0, 145.0, 140.0, 145.0, 145.0, 145.0, 130.0, 145.0,
            145.0, 145.0, 133.0, 145.0, 145.0, 145.0, 120.0, 145.0,
            145.0, 145.0, 138.0, 145.0, 135.0, 145.0, 125.0, 145.0, 145.0]
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=205.0)
    candles[9] = make_candle(9, highs[9], lows[9], close=212.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=217.0)
    candles[15] = make_candle(15, highs[15], lows[15], close=125.0)
    candles[16] = make_candle(16, 145.0, 125.0, close=126.0)
    candles[21] = make_candle(21, 150.0, 135.0, close=136.0)  # above floor 133
    candles[23] = make_candle(23, 150.0, 125.0, close=128.0)  # below floor 133

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    # The break at index 21 (135, above the CHoCH level 133) is blocked; the
    # only post-CHoCH BOS is at index 23 (125, below the CHoCH level).
    bos_after_choch = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.timestamp >= candles[16].timestamp
    ]
    assert [(e.direction, e.timestamp) for e in bos_after_choch] == [
        (MarketDirection.BEARISH, candles[23].timestamp),
    ]
    assert candles[21].timestamp not in [e.timestamp for e in events]


def test_unconfirmed_bullish_choch_fails_when_origin_broken() -> None:
    """A bullish CHoCH that is not confirmed by a BOS and whose origin (the low
    the CHoCH rally launched from) is broken back through is invalidated: a
    CHOCH_FAILED event fires and the trend flips back to bearish.

    Bearish BOSes (indices 5, 9) build a bearish leg; the rally from 100
    (index 9) breaks the trailing high -> bullish CHoCH at index 11
    (origin = 100). Price drops back below 100 (index 13, sustained) before any
    bullish BOS -> CHOCH_FAILED.
    """
    highs = [150.0] * 20
    lows = [140.0] * 20
    highs[1], highs[7], highs[11] = 200.0, 160.0, 200.0
    lows[3], lows[5], lows[9], lows[13] = 130.0, 110.0, 100.0, 90.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 110.0, close=120.0)
    candles[9] = make_candle(9, 150.0, 100.0, close=105.0)
    candles[11] = make_candle(11, 200.0, 140.0, close=190.0)
    candles[12] = make_candle(12, 195.0, 140.0, close=188.0)  # CHoCH persistence
    candles[13] = make_candle(13, 150.0, 90.0, close=95.0)  # breaks origin 100
    candles[14] = make_candle(14, 145.0, 92.0, close=95.0)  # failure persistence

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    choch = next(e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER)
    assert choch.direction is MarketDirection.BULLISH

    failed = [e for e in events if e.event is StructureEvent.CHOCH_FAILED]
    assert len(failed) == 1
    assert failed[0].direction is MarketDirection.BULLISH  # the failed CHoCH's direction
    assert failed[0].reference_price_level == 100.0  # the origin, not the CHoCH ref 160
    assert failed[0].timestamp == candles[13].timestamp


def test_failed_choch_resumes_staircase_from_genuine_prior_bos() -> None:
    """After a failed CHoCH, the resumed trend's BOS staircase continues from
    its *genuine* last BOS extreme, not the (higher-low) CHoCH origin -- so no
    bearish BOS can print above the previous bearish BOS.

    Sequence (lookback=1, persistence=1): a bearish BOS at index 5 makes a deep
    low of 100; the rally launches from a *higher* low of 120 (index 9) ->
    bullish CHoCH at index 11 (origin = 120). Price drops back below 120 but
    stays above 100 (index 13) -> CHOCH_FAILED, bearish resumes. A later break
    to 105 (index 15) clears the origin (120) but NOT the genuine prior BOS
    (100): it must NOT be a BOS. Only the break to 95 (index 19), below 100, is.
    """
    n = 26
    highs = [150.0] * n
    lows = [140.0] * n
    highs[1], highs[7], highs[11], highs[17] = 200.0, 160.0, 200.0, 160.0
    lows[3], lows[5], lows[9], lows[13], lows[15], lows[19] = (
        130.0, 100.0, 120.0, 110.0, 105.0, 95.0,
    )

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 100.0, close=110.0)  # genuine bearish BOS, low 100
    candles[9] = make_candle(9, 150.0, 120.0, close=125.0)  # rally launch / CHoCH origin 120
    candles[11] = make_candle(11, 200.0, 140.0, close=190.0)  # bullish CHoCH
    candles[12] = make_candle(12, 195.0, 140.0, close=188.0)  # CHoCH persistence
    candles[13] = make_candle(13, 150.0, 110.0, close=115.0)  # breaks origin 120 -> failed
    candles[14] = make_candle(14, 145.0, 112.0, close=115.0)  # failure persistence
    candles[15] = make_candle(15, 150.0, 105.0, close=108.0)  # clears 120, not 100 -> NOT a BOS
    candles[16] = make_candle(16, 150.0, 130.0, close=145.0)
    candles[19] = make_candle(19, 150.0, 95.0, close=98.0)  # genuine continuation, below 100

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    assert any(e.event is StructureEvent.CHOCH_FAILED for e in events)
    failed_time = next(
        e.timestamp for e in events if e.event is StructureEvent.CHOCH_FAILED
    )

    bearish_bos_after = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.timestamp > failed_time
    ]
    # The non-extending break to 105 (above the prior BOS at 100) must not print;
    # only the genuine continuation to 95 (below 100) survives the staircase.
    assert [e.price_level for e in bearish_bos_after] == [95.0]


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


# ---------------------------------------------------------------------------
# Candidate / validated CHoCH reference tests
# ---------------------------------------------------------------------------

def test_candidate_choch_promotion_requires_bos_below_baseline() -> None:
    """The baseline gate: a BOS bearish whose pivot does NOT go below
    `candidate_choch_high_baseline` does NOT promote the candidate;
    only a BOS below the baseline does.

    Sequence (lookback=1, confluence_filter=False):

      index 1: high 200 -> bootstrap active_high
      index 3: low   90 -> bootstrap active_low; pending_low=90
      index 5: high 180 -> LH(180/200); candidate_choch_high=180, baseline=90
      index 7: low   95 -> HL(95/90); pending_high=180; candidate_choch_low=95
      index 9: low   93 -> BOS bearish(93/95) [NEUTRAL->BEARISH];
                             candidate_choch_high=180, baseline=90: 93 < 90?
                             NO -> does NOT promote validated_choch_high.
      index 12: high 185 -> ... re-bootstrap active_high; no event (185>180=last)
      index 11: low   80 -> BOS bearish(80/93) [continuation];
                              candidate_choch_high=180, baseline=90: 80<90?
                              YES -> promotes validated_choch_high=180. ✓
      index 14: high 190 -> CHoCH bullish(190/180); reference=validated_choch_high=180.
    """
    highs = [100.0] * 16
    for i, v in {1: 200.0, 5: 180.0, 12: 185.0, 14: 190.0}.items():
        highs[i] = v
    lows = [100.0] * 16
    for i, v in {3: 90.0, 7: 95.0, 9: 93.0, 11: 80.0}.items():
        lows[i] = v

    candles = make_series(highs, lows)
    candles[9] = make_candle(9, highs[9], lows[9], close=94.0)
    candles[11] = make_candle(11, highs[11], lows[11], close=85.0)
    candles[14] = make_candle(14, highs[14], lows[14], close=183.0)
    candles[15] = make_candle(15, 183.0, lows[15], close=182.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    # Verify the CHoCH fires against the candidate (180), not the previous
    # active_high or any other level.
    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 180.0, 200.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 95.0, 90.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 93.0, 95.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 80.0, 93.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 190.0, 180.0),
    ]


def test_candidate_choch_low_promotion_requires_bos_above_baseline() -> None:
    """Mirror of `test_candidate_choch_promotion_requires_bos_below_baseline`:
    a BOS bullish whose pivot does NOT exceed `candidate_choch_low_baseline`
    does NOT promote the candidate; only a BOS above the baseline does.

    Sequence (lookback=1, confluence_filter=False):

      index  1: high 210 -> bootstrap active_high
      index  3: low  100 -> bootstrap active_low; pending_low=100
      index  5: high 220 -> BOS bullish(220/210); close(215)>210 confirms.
                              active_low=100; active_high=220; trend=BULLISH.
      index  7: low  120 -> HL(120/100); candidate_choch_low=120,
                              baseline=active_high=220; pending_high=220.
      index  9: high 195 -> LH(195/220); accumulates pending_low=120.
      index 11: high 200 -> BOS bullish(200/195); close(198)>195 confirms.
                              candidate_choch_low=120, baseline=220: 200>220?
                              NO -> does NOT promote validated_choch_low.
      index 13: high 225 -> BOS bullish(225/200); close(222)>200 confirms.
                              candidate_choch_low=120, baseline=220: 225>220?
                              YES -> promotes validated_choch_low=120. ✓
      index 15: low   85 -> CHoCH bearish(85/120); persistence holds.
    """
    highs = [150.0] * 17
    for i, v in {1: 210.0, 5: 220.0, 9: 195.0, 11: 200.0, 13: 225.0}.items():
        highs[i] = v
    lows = [145.0] * 17
    for i, v in {3: 100.0, 7: 120.0, 15: 85.0}.items():
        lows[i] = v

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    candles[11] = make_candle(11, highs[11], lows[11], close=198.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=222.0)
    # CHoCH confirmation: close < 120 at [15] and [16], but close >= low.
    candles[15] = make_candle(15, highs[15], lows[15], close=95.0)
    candles[16] = make_candle(16, 150.0, 90.0, close=95.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    choch = next(
        (e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER), None
    )
    assert choch is not None
    assert choch.direction is MarketDirection.BEARISH
    # reference must be 120.0 (validated after BOS at 225 > baseline 220),
    # not the phantom level that the BOS at 200 would have set if baseline
    # were not checked.
    assert choch.reference_price_level == 120.0


def test_re_bootstrap_pivot_worse_than_retired_becomes_choch_candidate() -> None:
    """When a BOS/CHoCH retires `active_high` to `None` (pending_high was
    empty), the next high pivot re-bootstraps it silently.  If that pivot is
    *lower* than `last_high_pivot` (the just-retired value), it is
    functionally a LOWER_HIGH and becomes `candidate_choch_high`, so a
    future bullish CHoCH can be validated against it.

    Sequence (lookback=1):

      index  1: high 200 -> bootstrap active_high
      index  3: low   90 -> bootstrap active_low (seeds pending_low=90)
      index  5: low   80 -> BOS bearish(80/90) [NEUTRAL->BEARISH]; close(85)<90.
                              pending_high empty -> active_high discarded to None.
      index  7: high 250 -> re-bootstrap active_high=250 (silent);
                              250 > last_high_pivot(200) -> NOT functionally LH.
      index  9: high 220 -> LH(220/250) [trailing active_high=250];
                              candidate_choch_high=220, baseline=active_low=80.
      index 11: low   70 -> BOS bearish(70/80) [continuation]; close(75)<80;
                              pending_high=250 (from re-bootstrap at 7) ->
                              active_high=250; validates candidate: 70<80=baseline
                              -> validated_choch_high=220.
      index 13: high 260 -> CHoCH bullish(260/220); close(255)>220 at [13]
                              and [14] -> persistence holds.
    """
    highs = [100.0] * 15
    for i, v in {1: 200.0, 7: 250.0, 9: 220.0, 13: 260.0}.items():
        highs[i] = v
    lows = [100.0] * 15
    for i, v in {3: 90.0, 5: 80.0, 11: 70.0}.items():
        lows[i] = v

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
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 220.0, 250.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BEARISH, 70.0, 80.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BULLISH, 260.0, 220.0),
    ]


def test_re_bootstrap_pivot_worse_than_retired_becomes_choch_candidate_low_side() -> None:
    """Mirror of `test_re_bootstrap_pivot_worse_than_retired_becomes_choch_candidate`:
    BOS bullish retires active_low to None; re-bootstrap pivot higher than
    last_low_pivot is functionally a HIGHER_LOW; later CHoCH bearish fires
    against that candidate's validated reference.

    Sequence (lookback=1):

      index  1: low  100 -> bootstrap active_low
      index  3: high 210 -> bootstrap active_high (seeds pending_high=210)
      index  5: high 220 -> BOS bullish(220/210) [NEUTRAL->BULLISH]; close(215)>210;
                              pending_low empty -> active_low discarded to None.
      index  7: low   50 -> re-bootstrap active_low=50 (silent);
                              50 < last_low_pivot(100) -> NOT functionally HL.
      index  9: low   80 -> HL(80/50) [trailing active_low=50];
                              candidate_choch_low=80, baseline=active_high=220.
      index 11: high 230 -> BOS bullish(230/220) [continuation]; close(225)>220;
                              pending_low=50 -> active_low=50; validates candidate:
                              230>220=baseline -> validated_choch_low=80.
      index 13: low   40 -> CHoCH bearish(40/80); close(45)<80 at [13] and [14].
    """
    highs = [200.0] * 15
    for i, v in {3: 210.0, 5: 220.0, 11: 230.0}.items():
        highs[i] = v
    lows = [200.0] * 15
    for i, v in {1: 100.0, 7: 50.0, 9: 80.0, 13: 40.0}.items():
        lows[i] = v

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
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 80.0, 50.0),
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 230.0, 220.0),
        (StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, 40.0, 80.0),
    ]


# ---------------------------------------------------------------------------
# Ghost-candidate fix: sweep updates unvalidated candidate
# ---------------------------------------------------------------------------

def test_sweep_above_candidate_choch_high_updates_candidate() -> None:
    """A LIQUIDITY_SWEEP that reaches *above* the unvalidated
    `candidate_choch_high` updates the candidate to the sweep pivot (and its
    baseline to the current trailing `active_low`), so the CHoCH reference
    reflects the actual structure extreme rather than a phantom level that
    was already breached.

    Sequence (lookback=1):

      index  1: high 200 -> bootstrap active_high=200
      index  3: low  100 -> bootstrap active_low=100; pending_low=100
      index  5: low   90 -> BOS bearish(90/100) [NEUTRAL->BEARISH]; close(95).
                              active_high=None (pending_high empty).
      index  7: high 170 -> re-bootstrap active_high=170 (silent);
                              170 < last_high(200) -> candidate_choch_high=170,
                              baseline=active_low=90.
      index 11: high 180 -> SWEEP bullish(180/170) [BEARISH];
                              ghost fix: 180 > candidate(170) -> update!
                              candidate_choch_high=180, baseline=active_low=90.
      index 13: low   80 -> BOS bearish(80/90) [continuation]; close(85)<90.
                              80 < 90=baseline -> promotes! validated_choch_high=180.
      index 15: high 185 -> CHoCH bullish(185/180); close(183)>180,
                              close[16]=182>180 -> sustained (persistence=1).
    """
    highs = [150.0] * 17
    for i, v in {1: 200.0, 7: 170.0, 11: 180.0, 15: 185.0}.items():
        highs[i] = v
    lows = [145.0] * 17
    for i, v in {3: 100.0, 5: 90.0, 13: 80.0}.items():
        lows[i] = v

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=95.0)
    candles[13] = make_candle(13, highs[13], lows[13], close=85.0)
    candles[15] = make_candle(15, highs[15], lows[15], close=183.0)
    candles[16] = make_candle(16, 183.0, 145.0, close=182.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    # The CHoCH must fire against 180.0 (the sweep level), not 170.0 (phantom)
    choch = next(
        (e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER), None
    )
    assert choch is not None
    assert choch.direction is MarketDirection.BULLISH
    assert choch.reference_price_level == 180.0


def test_sweep_below_candidate_choch_low_updates_candidate() -> None:
    """Mirror: a LIQUIDITY_SWEEP below the unvalidated `candidate_choch_low`
    updates the candidate so the eventual CHoCH fires against the true
    structural extreme, not the phantom original level.

    Sequence (lookback=1):

      index  1: low  100 -> bootstrap active_low
      index  3: high 210 -> bootstrap active_high; pending_high=210
      index  5: high 220 -> BOS bullish(220/210) [NEUTRAL->BULLISH]; close(215).
                              active_low=None (pending_low empty); active_high=220.
      index  7: low  140 -> re-bootstrap active_low=140 (silent);
                              140 > last_low(100) -> candidate_choch_low=140,
                              baseline=active_high=220.
      index  9: high 225 -> BOS bullish(225/220); close(222)>220 BUT
                              225 > baseline(220)? YES -> validates candidate_choch_low=140.
                              So no ghost-fix scenario... let me use a baseline > BOS level.
    """
    # Build a scenario where baseline=230 so the first BOS (at 225) does NOT
    # validate the candidate, then the sweep happens, then a second BOS validates.
    highs = [200.0] * 20
    for i, v in {3: 210.0, 5: 220.0, 9: 225.0, 13: 235.0}.items():
        highs[i] = v
    lows = [200.0] * 20
    for i, v in {1: 100.0, 7: 140.0, 11: 130.0, 15: 110.0}.items():
        lows[i] = v

    # index 5: h 220 -> BOS bullish(220/210); close(215). pending_low empty -> active_low=None.
    # index 7: l 140 -> re-bootstrap; 140>100=last_low -> candidate_choch_low=140, baseline=220
    # index 9: h 225 -> BOS bullish(225/220); 225>220=baseline -> validates candidate_choch_low=140!
    # Ghost fix can't fire here because candidate is already promoted.
    #
    # For ghost fix to fire, need candidate baseline > all BOS levels until the sweep.
    # Alternative: build the scenario directly where sweep updates candidate.
    highs = [200.0] * 20
    for i, v in {3: 210.0, 5: 220.0, 11: 225.0, 15: 235.0}.items():
        highs[i] = v
    lows = [200.0] * 20
    for i, v in {1: 100.0, 7: 155.0, 9: 140.0, 13: 110.0}.items():
        lows[i] = v

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    # index 7: l 155 -> re-bootstrap active_low=155 (silent); 155>100=last_low -> candidate=155, baseline=220
    # index 9: l 140 -> SWEEP bearish(140/155) [BULLISH]; 140<155=candidate -> ghost fix: candidate=140, baseline=220
    # index 11: h 225 -> BOS bullish(225/220); 225>220=baseline -> validates candidate_choch_low=140 ✓
    candles[11] = make_candle(11, highs[11], lows[11], close=222.0)
    # index 13: l 110 -> CHoCH bearish(110/140); persistence_candles=1
    candles[13] = make_candle(13, highs[13], lows[13], close=115.0)
    candles[14] = make_candle(14, highs[14], 115.0, close=116.0)

    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    choch = next(
        (e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER), None
    )
    assert choch is not None
    assert choch.direction is MarketDirection.BEARISH
    # reference must be 140.0 (sweep level), not phantom 155.0
    assert choch.reference_price_level == 140.0


# ---------------------------------------------------------------------------
# Edge cases and validation
# ---------------------------------------------------------------------------

def test_swing_structure_detector_returns_empty_for_short_series() -> None:
    candles = make_series(MAIN_HIGHS[:4], MAIN_LOWS[:4])

    assert SwingStructureDetector(swing_lookback=2).detect(candles) == []


def test_swing_structure_detector_rejects_mixed_symbols() -> None:
    candles = make_series(MAIN_HIGHS, MAIN_LOWS)
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        SwingStructureDetector(swing_lookback=2).detect(candles)


def test_swing_structure_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        SwingStructureDetector().detect([])


def test_swing_structure_detector_rejects_invalid_persistence_candles() -> None:
    with pytest.raises(ValueError, match="persistence_candles must be at least 1"):
        SwingStructureDetector(persistence_candles=0)
