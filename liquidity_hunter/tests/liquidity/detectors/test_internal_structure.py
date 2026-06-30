"""Tests for `InternalStructureDetector`."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# ---------------------------------------------------------------------------
# Pivot sequence (lookback=1, so each pivot sits at its index with 1 flat
# candle on either side). State advances on break, BOS emitted on pullback.
#
#   index  1: high 200 -> bootstraps active_high
#   index  3: low   90 -> bootstraps active_low
#   index  5: high 220 -> BOS bullish state advance; pending BOS created
#   index  7: low  100 -> HL (100 > pullback_ref 90) -> BOS emitted at idx 5;
#                          then also HIGHER_LOW event
#   index  9: high 210 -> below active_high (220) -> LOWER_HIGH
#   index 11: low   80 -> LIQUIDITY_SWEEP bearish (ref 100)
#   index 13: high 230 -> BOS bullish state advance; pending BOS created
#   index 15: low   70 -> NOT HL (70 < pullback_ref 100) -> pending discarded;
#                          LIQUIDITY_SWEEP bearish (ref 100)
#   index 17: high 215 -> below active_high (230) -> LOWER_HIGH
#   index 19: low   60 -> LIQUIDITY_SWEEP bearish (ref 70)
HIGHS = [150.0] * 21
for _index, _value in {1: 200.0, 5: 220.0, 9: 210.0, 13: 230.0, 17: 215.0}.items():
    HIGHS[_index] = _value

LOWS = [140.0] * 21
for _index, _value in {3: 90.0, 7: 100.0, 11: 80.0, 15: 70.0, 19: 60.0}.items():
    LOWS[_index] = _value


def test_internal_structure_detector_full_sequence() -> None:
    candles = make_series(HIGHS, LOWS)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)
    candles[13] = make_candle(13, 230.0, 140.0, close=215.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    assert [(e.event, e.direction, e.price_level, e.reference_price_level) for e in events] == [
        (StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, 220.0, 200.0),
        (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH, 100.0, 90.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 210.0, 220.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 80.0, 100.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 70.0, 100.0),
        (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH, 215.0, 230.0),
        (StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, 60.0, 70.0),
    ]
    assert [e.timestamp for e in events] == [
        candles[5].timestamp,   # BOS at close-break candle
        candles[7].timestamp,   # HL
        candles[9].timestamp,
        candles[11].timestamp,
        candles[15].timestamp,
        candles[17].timestamp,
        candles[19].timestamp,
    ]
    bos = events[0]
    assert bos.origin_price_level == 100.0
    for event in events:
        assert event.symbol == "BTCUSDT"


def test_first_pivot_of_each_kind_produces_no_event() -> None:
    """Index 1 (first high) and index 3 (first low) bootstrap the active
    references without emitting any event.
    """
    candles = make_series(HIGHS, LOWS)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert candles[1].timestamp not in [e.timestamp for e in events]
    assert candles[3].timestamp not in [e.timestamp for e in events]


def test_pivot_equal_to_active_reference_produces_no_event() -> None:
    """A pivot exactly equal to the current active reference (neither a
    break nor a label) produces no event. The bearish BOS (index 7) is
    confirmed by a LH pullback (index 9, high 180 < active_high 200).
    """
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 200.0, 150.0, 150.0, 150.0, 180.0, 150.0]
    lows = [140.0, 140.0, 140.0, 100.0, 140.0, 140.0, 140.0, 90.0, 140.0, 140.0, 140.0]
    candles = make_series(highs, lows)
    candles[7] = make_candle(7, 150.0, 90.0, close=95.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos_events = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos_events) == 1
    assert bos_events[0].direction is MarketDirection.BEARISH
    assert bos_events[0].price_level == 90.0
    assert bos_events[0].reference_price_level == 100.0
    assert bos_events[0].timestamp == candles[7].timestamp
    assert bos_events[0].origin_price_level == 180.0


def test_internal_structure_detector_stamps_internal_scope() -> None:
    candles = make_series(HIGHS, LOWS)

    events = InternalStructureDetector(swing_lookback=1).detect(candles)

    assert events
    assert all(event.scope is StructureScope.INTERNAL for event in events)


def test_internal_structure_detector_returns_empty_for_short_series() -> None:
    candles = make_series(HIGHS[:2], LOWS[:2])

    assert InternalStructureDetector(swing_lookback=1).detect(candles) == []


def test_internal_structure_detector_rejects_mixed_symbols() -> None:
    candles = make_series(HIGHS, LOWS)
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        InternalStructureDetector(swing_lookback=1).detect(candles)


def test_internal_structure_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        InternalStructureDetector().detect([])


def test_internal_structure_detector_rejects_invalid_persistence_candles() -> None:
    with pytest.raises(ValueError, match="persistence_candles must be at least 1"):
        InternalStructureDetector(persistence_candles=0)


def test_internal_structure_detector_rejects_invalid_impulse_displacement() -> None:
    with pytest.raises(ValueError, match="impulse_bos_displacement_pct must be positive"):
        InternalStructureDetector(impulse_bos_displacement_pct=0.0)


# --- The validated CHoCH reference -----------------------------------------
#
# To test CHoCH with pullback-based BOS, every bearish BOS needs an LH pullback.
# candidate_choch_high keeps the *highest* LH since the last promotion (the
# pullback that confirmed the BOS), NOT the most recent one -- a weaker, more
# recent LH must not ratchet the candidate down to a level no BOS reached.
#
# Sequence (lookback=1):
#   index  1: high 200 -> bootstraps active_high
#   index  3: low  100 -> bootstraps active_low
#   index  5: low   80 -> pending bearish BOS (close < 100)
#   index  7: high 190 -> LH confirms BOS -> candidate_choch_high=190
#   index  9: high 170 -> weaker LH (170 < 190) -> does NOT replace candidate
#   index 11: low   60 -> pending bearish BOS -> promotes candidate=190 to validated
#   index 13: high 165 -> later, weaker LH -> candidate=165, validated frozen at 190
#   index 15: high 195 -> sustained break above validated_choch_high (190) ->
#                          CHANGE_OF_CHARACTER bullish
_TIEBREAK_HIGH_HIGHS = [150.0] * 17
for _index, _value in {1: 200.0, 7: 190.0, 9: 170.0, 13: 165.0, 15: 195.0}.items():
    _TIEBREAK_HIGH_HIGHS[_index] = _value
_TIEBREAK_HIGH_LOWS = [140.0] * 17
for _index, _value in {3: 100.0, 5: 80.0, 11: 60.0}.items():
    _TIEBREAK_HIGH_LOWS[_index] = _value


def test_bullish_choch_validated_freeze_preserves_first_promoted() -> None:
    candles = make_series(_TIEBREAK_HIGH_HIGHS, _TIEBREAK_HIGH_LOWS)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[11] = make_candle(11, 150.0, 60.0, close=70.0)
    candles[15] = make_candle(15, 195.0, 140.0, close=194.0)
    candles[16] = make_candle(16, 194.0, 191.0, close=193.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    choch = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(choch) == 1
    assert choch[0].direction is MarketDirection.BULLISH
    assert choch[0].price_level == 195.0
    # candidate keeps the highest LH (190, the pullback that confirmed the BOS);
    # the weaker, more recent LHs (170, 165) cannot ratchet it down to a level
    # no BOS reached. 195 breaks above 190.
    assert choch[0].reference_price_level == 190.0


def test_bearish_choch_validated_freeze_preserves_first_promoted() -> None:
    # Mirror: in a bullish leg, candidate_choch_low keeps the *lowest* HL (the
    # pullback floor that confirmed the BOS); a higher, more recent HL can't
    # ratchet it up to a level no BOS reached.
    highs = [150.0] * 17
    for index, value in {3: 200.0, 5: 250.0, 11: 280.0}.items():
        highs[index] = value
    lows = [140.0] * 17
    for index, value in {1: 100.0, 7: 110.0, 9: 130.0, 13: 135.0, 15: 105.0}.items():
        lows[index] = value
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 250.0, 140.0, close=210.0)
    candles[11] = make_candle(11, 280.0, 140.0, close=260.0)
    candles[15] = make_candle(15, 150.0, 105.0, close=108.0)
    candles[16] = make_candle(16, 150.0, 106.0, close=107.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    choch = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(choch) == 1
    assert choch[0].direction is MarketDirection.BEARISH
    assert choch[0].price_level == 105.0
    # First promoted HL (110, the BOS-confirming pullback floor) stays; the
    # higher HLs (130, 135) cannot ratchet it up. 105 breaks below 110.
    assert choch[0].reference_price_level == 110.0


def test_break_above_trailing_high_below_validated_is_a_sweep() -> None:
    """In a bearish leg, a high that breaks the trailing active_high but not
    validated_choch_high is an internal bounce -> LIQUIDITY_SWEEP, trend
    unchanged (no CHoCH).
    """
    highs = [150.0] * 12
    for index, value in {1: 200.0, 7: 160.0, 9: 180.0}.items():
        highs[index] = value
    lows = [140.0] * 12
    for index, value in {3: 120.0, 5: 90.0}.items():
        lows[index] = value
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 90.0, close=110.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    sweep = events[-1]
    assert sweep.event is StructureEvent.LIQUIDITY_SWEEP
    assert sweep.direction is MarketDirection.BULLISH
    assert sweep.price_level == 180.0
    assert sweep.reference_price_level == 160.0
    assert all(e.event is not StructureEvent.CHANGE_OF_CHARACTER for e in events)


# --- Persistence-based CHoCH confirmation ----------------------------------
#
# With pullback-based BOS, the bullish BOS at index 5 (H210 breaking
# active_high 200) needs an HL pullback. Index 7 (L60) is far below
# active_low (90), so it is NOT an HL -> the pending BOS is discarded.
# Without a confirmed BOS, validated_choch_low is never promoted, so the
# counter-trend break is always LIQUIDITY_SWEEP.
#
# To exercise persistence, we redesign: add a HL pullback (index 6.5 equiv.)
# by restructuring so the BOS is confirmed, then the counter-trend break
# at index 9 exercises persistence against validated_choch_low.
#
# New sequence (lookback=1):
#   index 1: high 200 -> bootstrap active_high
#   index 3: low   90 -> bootstrap active_low
#   index 5: high 210 -> pending bullish BOS (close 205 > 200)
#   index 7: low   95 -> HL (95 > active_low 90) -> BOS confirmed
#   index 9: high 220 -> BOS bullish continuation pending (close 215 > 210)
#   index 11: low  100 -> HL (100 > 95) -> BOS confirmed; promotes
#                         candidate_choch_low(95) -> validated_choch_low=95
#   index 13: low   60 -> breaks validated_choch_low(95);
#                         persistence-based confirmation follows.
_PERSISTENCE_HIGHS = [150.0] * 18
for _i, _v in {1: 200.0, 5: 210.0, 9: 220.0}.items():
    _PERSISTENCE_HIGHS[_i] = _v
_PERSISTENCE_LOWS = [140.0] * 18
for _i, _v in {3: 90.0, 7: 95.0, 11: 100.0, 13: 60.0}.items():
    _PERSISTENCE_LOWS[_i] = _v


def _persistence_test_series(
    *, index_14_close: float, index_15_close: float | None
) -> list[Candle]:
    candles = make_series(_PERSISTENCE_HIGHS, _PERSISTENCE_LOWS)
    candles[5] = make_candle(5, 210.0, 140.0, close=205.0)
    candles[9] = make_candle(9, 220.0, 140.0, close=215.0)
    candles[13] = make_candle(13, 150.0, 60.0, close=65.0)
    candles[14] = make_candle(14, 85.0, 70.0, close=index_14_close)
    if index_15_close is not None:
        high_15 = max(150.0, index_15_close)
        candles[15] = make_candle(15, high_15, 70.0, close=index_15_close)
    return candles


def test_persistence_confirmed_choch() -> None:
    """With persistence_candles=2, the break of validated_choch_low (95)
    holds for enough candles -> CHANGE_OF_CHARACTER bearish.
    """
    candles = _persistence_test_series(index_14_close=75.0, index_15_close=80.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    ).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BEARISH
    assert chochs[0].reference_price_level == 95.0


def test_reversal_within_persistence_window_yields_liquidity_sweep() -> None:
    """The pivot's close (65) clears validated_choch_low (95), but the second
    following candle closes back above it (96) -- a 'false break' ->
    LIQUIDITY_SWEEP. Note: close=96 requires high >= 96."""
    candles = _persistence_test_series(index_14_close=75.0, index_15_close=96.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    ).detect(candles)

    sweeps = [
        e for e in events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BEARISH
    ]
    assert len(sweeps) >= 1
    assert sweeps[-1].price_level == 60.0


def test_insufficient_trailing_candles_yields_liquidity_sweep() -> None:
    """The CHoCH-candidate pivot is too close to the end: there aren't
    `persistence_candles` candles after it to evaluate, so the break is
    treated as unconfirmed regardless of its own close."""
    candles = _persistence_test_series(index_14_close=75.0, index_15_close=None)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    ).detect(candles)

    assert events[-1].event is StructureEvent.LIQUIDITY_SWEEP
    assert events[-1].direction is MarketDirection.BEARISH
    assert events[-1].price_level == 60.0
    assert events[-1].reference_price_level == 100.0


# --- Real-data regression -------------------------------------------------
_WINDOW_DATA = Path(__file__).parent / "data" / "btcusdt_1h_2026_06_02_08.json"


def _load_window_candles() -> list[Candle]:
    rows = json.loads(_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=close,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, high, low, close in rows
    ]


def test_real_window_weak_lh_break_is_sweep_not_choch() -> None:
    """Real BTCUSDT data regression: a rally that only reclaims a weak, recent
    LH -- but not the higher pullback high that actually confirmed the bearish
    BOS -- must be a LIQUIDITY_SWEEP, not a premature bullish CHoCH.

    Previously candidate_choch_high ratcheted down to the weakest staircase LH
    (61,547.24), so the rally to 62,960 fired a bullish CHoCH there. With the
    candidate fixed to the strongest LH of its window (the BOS-confirming
    pullback, ~63,259.90), that rally no longer reaches the validated level and
    is correctly reported as a sweep.
    """
    candles = _load_window_candles()

    events = InternalStructureDetector(
        swing_lookback=2, persistence_candles=3, confluence_filter=False
    ).detect(candles)

    bullish_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert bullish_chochs == []

    weak_lh_breaks = [
        e
        for e in events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BULLISH
        and e.price_level == 62960.0
    ]
    assert len(weak_lh_breaks) == 1


def test_real_window_production_lookback_emits_no_choch() -> None:
    """At the production default swing_lookback=10 this window is too coarse to
    surface the swing-high structure, so no (spurious) CHoCH is emitted."""
    candles = _load_window_candles()

    events = InternalStructureDetector(swing_lookback=10, persistence_candles=3).detect(candles)

    assert all(e.event is not StructureEvent.CHANGE_OF_CHARACTER for e in events)


def test_choch_detected_when_confirmation_extends_beyond_pivot_index() -> None:
    """Bearish BOS events need LH pullbacks. The CHoCH break is validated
    against validated_choch_high, which is promoted when the second bearish
    BOS is confirmed by its LH pullback.
    """
    highs = [150.0] * 22
    lows = [140.0] * 22

    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # LH -> confirms 1st BOS; candidate_choch_high
    lows[9] = 60.0    # pending 2nd bearish BOS
    highs[11] = 165.0  # LH -> confirms 2nd BOS; promotes validated_choch_high=170

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[9] = make_candle(9, 150.0, 60.0, close=70.0)

    # CHoCH break starts at index 14, pivot at 15
    candles[14] = make_candle(14, high=173.0, low=140.0, close=172.0)
    candles[15] = make_candle(15, high=175.0, low=140.0, close=174.0)
    candles[16] = make_candle(16, high=173.0, low=140.0, close=171.0)

    detector = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    )
    events = detector.detect(candles)

    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]
    assert len(choch_events) > 0, "CHoCH not detected."
    assert choch_events[0].reference_price_level == 170.0


def test_state_machine_hierarchy_choch_then_bos() -> None:
    """Hierarchy: bearish setup -> CHoCH bullish -> BOS bullish continuation.
    All BOS events require pullback confirmation.
    """
    highs = [150.0] * 40
    lows = [140.0] * 40

    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0   # LH -> confirms 1st bearish BOS
    lows[9] = 60.0      # pending 2nd bearish BOS
    highs[11] = 165.0   # LH -> confirms 2nd bearish BOS; promotes validated_choch_high=170

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[9] = make_candle(9, 150.0, 60.0, close=70.0)

    # CHoCH bullish (breaks above validated_choch_high=170 with persistence)
    candles[14] = make_candle(14, high=175.0, low=140.0, close=172.0)
    candles[15] = make_candle(15, high=180.0, low=150.0, close=175.0)
    candles[16] = make_candle(16, high=178.0, low=150.0, close=171.0)

    # Bullish BOS: high above active_high (from CHoCH)
    highs[19] = 190.0
    candles[19] = make_candle(19, high=190.0, low=140.0, close=188.0)
    # HL pullback to confirm bullish BOS
    lows[21] = 100.0
    candles[21] = make_candle(21, high=150.0, low=100.0)

    detector = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    )
    events = detector.detect(candles)

    bullish_events = [
        e
        for e in events
        if e.direction == MarketDirection.BULLISH
        and e.event in (StructureEvent.CHANGE_OF_CHARACTER, StructureEvent.BREAK_OF_STRUCTURE)
    ]

    assert len(bullish_events) >= 2, "Should detect at least CHoCH + BOS bullish."

    first_bullish = bullish_events[0]
    assert first_bullish.event is StructureEvent.CHANGE_OF_CHARACTER
    assert first_bullish.reference_price_level == 170.0

    second_bullish = bullish_events[1]
    assert second_bullish.event is StructureEvent.BREAK_OF_STRUCTURE


def test_trend_state_does_not_leak_on_liquidity_sweep() -> None:
    """A sweep of the CHoCH level must not flip the trend.  With pullback-
    based BOS, the second bearish BOS (at L50) needs an LH pullback to
    be confirmed. The first bearish BOS (L80) is confirmed by LH 170.
    """
    highs = [150.0] * 30
    lows = [140.0] * 30

    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # LH -> confirms 1st bearish BOS
    lows[9] = 60.0
    highs[11] = 165.0  # LH -> confirms 2nd bearish BOS; promotes validated_choch_high=170

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[9] = make_candle(9, 150.0, 60.0, close=70.0)

    # Sweep (doesn't hold)
    candles[14] = make_candle(14, high=175.0, low=140.0, close=165.0)

    # Continuation of bearish trend
    candles[17] = make_candle(17, high=100.0, low=50.0, close=55.0)
    candles[18] = make_candle(18, high=90.0, low=40.0, close=45.0)
    candles[19] = make_candle(19, high=80.0, low=45.0, close=50.0)

    detector = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    )
    events = detector.detect(candles)

    sweep_events = [e for e in events if e.event == StructureEvent.LIQUIDITY_SWEEP]
    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]

    assert len(sweep_events) >= 1
    assert len(choch_events) == 0, "Sweep must not flip the trend."


def test_state_machine_does_not_shift_references_on_multiple_sweeps() -> None:
    """Multiple sweeps against the CHoCH level must not produce phantom CHoCHs.
    """
    highs = [150.0] * 35
    lows = [140.0] * 35

    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0  # LH -> confirms 1st BOS
    lows[9] = 60.0
    highs[11] = 165.0  # LH -> confirms 2nd BOS; promotes validated_choch_high=170

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[9] = make_candle(9, 150.0, 60.0, close=70.0)

    # First sweep
    candles[14] = make_candle(14, high=172.0, low=140.0, close=160.0)
    # Second sweep
    candles[17] = make_candle(17, high=175.0, low=135.0, close=155.0)

    # Aggressive bearish continuation
    candles[20] = make_candle(20, high=110.0, low=50.0, close=52.0)
    candles[21] = make_candle(21, high=90.0, low=40.0, close=42.0)
    candles[22] = make_candle(22, high=80.0, low=35.0, close=38.0)

    detector = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    )
    events = detector.detect(candles)

    choch_events = [e for e in events if e.event == StructureEvent.CHANGE_OF_CHARACTER]
    assert len(choch_events) == 0, "Multiple sweeps must not create phantom CHoCHs."


# --- Streaming / live-update behavior ---------------------------------------
_STREAM_HIGHS = [150.0] * 30
_stream_high_overrides = {1: 200.0, 5: 220.0, 9: 210.0, 13: 230.0, 17: 215.0, 21: 240.0, 25: 235.0}
for _index, _value in _stream_high_overrides.items():
    _STREAM_HIGHS[_index] = _value
_STREAM_LOWS = [140.0] * 30
for _index, _value in {3: 90.0, 7: 100.0, 11: 80.0, 15: 70.0, 19: 60.0, 23: 90.0, 27: 50.0}.items():
    _STREAM_LOWS[_index] = _value


def test_streaming_append_only_growth_is_prefix_stable() -> None:
    """Simulates streaming with no truncation: `detect()` is called on an
    ever-growing prefix of the same series. Earlier emitted events must not
    change (or disappear) as later candles are appended."""
    candles = make_series(_STREAM_HIGHS, _STREAM_LOWS)

    detector = InternalStructureDetector(swing_lookback=1)
    previous_events: list[
        tuple[datetime, StructureEvent, MarketDirection, float, float | None]
    ] = []
    for n in range(10, len(candles) + 1):
        events = detector.detect(candles[:n])
        current_events = [
            (e.timestamp, e.event, e.direction, e.price_level, e.reference_price_level)
            for e in events
        ]
        common_length = min(len(previous_events), len(current_events))
        assert previous_events[:common_length] == current_events[:common_length], (
            f"event prefix changed when growing the series to {n} candles"
        )
        previous_events = current_events


def test_streaming_sliding_window_reclassifies_same_candle() -> None:
    """A fixed-size sliding window re-bootstraps references from the new
    window's first pivots. With pullback-based BOS, a candle that was a
    LIQUIDITY_SWEEP in one window may become something different in another.
    """
    candles = make_series(_STREAM_HIGHS, _STREAM_LOWS)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)
    candles[11] = make_candle(11, 150.0, 80.0, close=85.0)
    window_size = 15

    window_0 = candles[0:window_size]
    window_1 = candles[1 : window_size + 1]

    events_0 = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(window_0)
    events_1 = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(window_1)

    # In window_0: index 11 is a LIQUIDITY_SWEEP (counter-trend in BULLISH)
    pivot_in_w0 = next(e for e in events_0 if e.timestamp == candles[11].timestamp)
    assert pivot_in_w0.event is StructureEvent.LIQUIDITY_SWEEP

    # In window_1: the bootstrap shifts -- index 11 is no longer in a confirmed
    # bullish trend, so the event type changes.
    ts_11_events_in_w1 = [e for e in events_1 if e.timestamp == candles[11].timestamp]
    # Verify something appears for this timestamp and it's NOT a BOS (pullback needed)
    # or is entirely absent (no BOS because no pullback in range).
    bos_at_11 = [e for e in ts_11_events_in_w1 if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos_at_11) == 0, (
        "BOS should not be emitted at the break candle itself; pullback needed."
    )


def test_continuation_chain_promotes_through_multiple_bos() -> None:
    """Three bearish BOS events making progressively deeper lows. Each BOS
    is a genuine continuation (new leg low), so the second promotes BOS 1's
    pullback to validated, and the third promotes BOS 2's pullback.

    The CHoCH reference is the last promoted pullback (165, from BOS 2) --
    BOS 3's pullback (155) is still provisional (needs a fourth BOS to
    promote), so validated_choch_high stays frozen at 165.

    Sequence:
      BOS 1 (L80, pullback LH=180) -> candidate=180
      continuation L60 < bear_leg_low(80) -> promotes validated=180
      BOS 2 (L60, pullback LH=165) -> candidate=165
      continuation L50 < bear_leg_low(60) -> promotes validated=165
      BOS 3 (L50, pullback LH=155) -> candidate=155 (provisional)
      break above 165 -> CHoCH bullish ref=165
    """
    highs = [150.0] * 27
    lows = [140.0] * 27

    highs[1] = 200.0
    lows[3] = 100.0

    lows[5] = 80.0
    highs[7] = 180.0

    highs[9] = 170.0
    lows[11] = 60.0
    highs[13] = 165.0

    highs[15] = 160.0
    lows[17] = 50.0
    highs[19] = 155.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[11] = make_candle(11, 150.0, 60.0, close=70.0)
    candles[17] = make_candle(17, 150.0, 50.0, close=55.0)

    candles[22] = make_candle(22, high=170.0, low=145.0, close=168.0)
    candles[23] = make_candle(23, high=175.0, low=150.0, close=172.0)
    candles[24] = make_candle(24, high=170.0, low=152.0, close=169.0)

    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
    ).detect(candles)

    bos_bear = [
        e for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
    ]
    assert len(bos_bear) == 3, "All three bearish BOS should be emitted."

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BULLISH
    assert chochs[0].reference_price_level == 165.0


def test_sweep_then_expansion_reanchors_choch_reference() -> None:
    """A SWEEP below the current CHoCH pullback candidate, *followed by an
    expansion to a new leg high*, re-anchors the bearish-CHoCH reference DOWN to
    the swept low (the origin the expansion rose from) -- not the pre-sweep HL.
    This is the SMC "sweep then expand" pattern: once price takes out the old
    higher-low and makes a new high, the swept low is the structure-defining
    level a reversal must break.

    Sequence:
      BOS 1 (H210, pullback HL=100) -> candidate_choch_low=100
      SWEEP to 80 (below candidate 100) -> re-anchors candidate down to 80
      BOS 2 (H220, continuation 220 > bull_leg_high(210)) -> promotes validated=80
      CHoCH at L75 breaks validated(80) -> ref=80
    """
    highs = [150.0] * 20
    lows = [140.0] * 20
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 210.0
    lows[7] = 100.0
    lows[9] = 80.0
    highs[11] = 220.0
    lows[13] = 105.0
    lows[15] = 75.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 210.0, 140.0, close=205.0)
    candles[11] = make_candle(11, 220.0, 140.0, close=215.0)
    candles[15] = make_candle(15, 150.0, 75.0, close=78.0)
    candles[16] = make_candle(16, 150.0, 78.0, close=79.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BEARISH
    assert chochs[0].reference_price_level == 80.0


def test_sweep_then_expansion_reanchors_choch_reference_bearish() -> None:
    """Bearish mirror: a SWEEP above the current CHoCH pullback candidate,
    *followed by an expansion to a new leg low*, re-anchors the bullish-CHoCH
    reference UP to the swept high (the origin the expansion fell from) -- not
    the pre-sweep LH.

    Sequence:
      BOS 1 (L80, pullback LH=175) -> candidate_choch_high=175
      SWEEP to 185 (above candidate 175) -> re-anchors candidate up to 185
      BOS 2 (L65, continuation 65 < bear_leg_low(80)) -> promotes validated=185
      CHoCH at H190 breaks validated(185) -> ref=185
    """
    highs = [150.0] * 20
    lows = [140.0] * 20
    lows[1] = 90.0
    highs[3] = 200.0
    lows[5] = 80.0
    highs[7] = 175.0
    highs[9] = 185.0
    lows[11] = 65.0
    highs[13] = 170.0
    highs[15] = 190.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=85.0)
    candles[11] = make_candle(11, 150.0, 65.0, close=70.0)
    candles[15] = make_candle(15, 190.0, 140.0, close=187.0)
    candles[16] = make_candle(16, 187.0, 140.0, close=186.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BULLISH
    assert chochs[0].reference_price_level == 185.0


def test_sweep_without_continuation_does_not_reanchor_choch_reference() -> None:
    """The re-anchor is gated on a continuation: a SWEEP that is NOT followed by
    a new leg high leaves the candidate provisional, so the validated reference
    stays at the prior continuation-confirmed pullback. The swept low is only
    promoted once an expansion confirms it -- a lone sweep remains noise.

    Sequence:
      BOS 1 (H210, pullback HL=100), continuation BOS (H215) -> validated=100
      SWEEP to 80 (re-anchors candidate to 80, but never promoted: no new high)
      CHoCH at L95 breaks validated(100) -> ref=100 (NOT 80)
    """
    highs = [150.0] * 22
    lows = [140.0] * 22
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 210.0   # BOS 1
    lows[7] = 100.0     # HL pullback -> candidate=100
    highs[9] = 215.0   # continuation BOS (new leg high) -> validated=100
    lows[11] = 105.0    # HL pullback (candidate=105)
    lows[13] = 80.0     # SWEEP below candidate (re-anchors candidate, not validated)
    lows[15] = 95.0     # break of validated(100), sustained -> CHoCH ref=100
    lows[17] = 95.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 210.0, 140.0, close=205.0)
    candles[9] = make_candle(9, 215.0, 140.0, close=212.0)
    candles[15] = make_candle(15, 150.0, 95.0, close=98.0)
    candles[16] = make_candle(16, 150.0, 96.0, close=99.0)
    candles[17] = make_candle(17, 150.0, 95.0, close=98.0)

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BEARISH
    assert chochs[0].reference_price_level == 100.0


def test_real_window_choch_validated_freeze_prevents_ratchet() -> None:
    """Real BTCUSDT data: the validated bullish-CHoCH reference is anchored to
    the pullback high that confirmed the bearish BOS (~63,259.90), not the
    weaker, more recent staircase LHs the leg printed on its way down. Within
    this window price never sustainably reclaims that level, so no premature
    bullish CHoCH fires (the leg's bullish breaks stay sweeps).
    """
    candles = _load_window_candles()

    events = InternalStructureDetector(
        swing_lookback=2,
        persistence_candles=3,
        confluence_filter=False,
    ).detect(candles)

    bullish_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert bullish_chochs == []


# --- New pullback-specific tests -------------------------------------------


def test_bullish_bos_requires_hl_pullback() -> None:
    """A clean bullish BOS: break above active_high, then HL confirms."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 220.0   # pending BOS
    lows[7] = 100.0     # HL (100 > 90) -> confirms

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 1
    assert bos[0].direction is MarketDirection.BULLISH
    assert bos[0].price_level == 220.0
    assert bos[0].reference_price_level == 200.0
    assert bos[0].timestamp == candles[5].timestamp
    assert bos[0].origin_price_level == 100.0


def test_bearish_bos_requires_lh_pullback() -> None:
    """Mirror: bearish BOS confirmed by LH pullback."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    lows[1] = 90.0
    highs[3] = 200.0
    lows[5] = 80.0      # pending bearish BOS
    highs[7] = 180.0    # LH (180 < 200) -> confirms

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=85.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 1
    assert bos[0].direction is MarketDirection.BEARISH
    assert bos[0].price_level == 80.0
    assert bos[0].reference_price_level == 90.0
    assert bos[0].timestamp == candles[5].timestamp
    assert bos[0].origin_price_level == 180.0


def test_bos_not_emitted_without_pullback() -> None:
    """Break above active_high, but next low pivot is a LL (not HL) ->
    pending BOS is discarded, no BOS emitted."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    highs[1] = 200.0
    lows[3] = 100.0
    highs[5] = 220.0   # pending BOS
    lows[7] = 80.0      # LL (80 < 100) -> discard

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 0


def test_impulse_bos_staging_marks_steps_in_an_impulsive_leg() -> None:
    """A clean impulsive bearish leg (consecutive lower lows, no intervening
    high pivot) advances the state machine at each step but emits no BOS -- the
    deferred pending BOS never confirms without a pullback. With
    `impulse_bos_displacement_pct` set, each advance whose displacement beyond
    the prior BOS level clears the threshold is staged as a BOS, so the descent
    shows a staircase. Purely additive: the off events are unchanged."""
    highs = [150.0] * 13
    lows = [140.0] * 13
    highs[1] = 200.0   # bootstraps active_high
    lows[3] = 90.0     # bootstraps active_low
    lows[5] = 80.0     # first advance (floor None -> not staged)
    lows[7] = 60.0     # impulsive advance, no high pivot -> staged (vs floor 80)
    lows[9] = 40.0     # impulsive advance, no high pivot -> staged (vs floor 60)

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=85.0)
    candles[7] = make_candle(7, 150.0, 60.0, close=65.0)
    candles[9] = make_candle(9, 150.0, 40.0, close=45.0)

    off = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)
    on = InternalStructureDetector(
        swing_lookback=1, confluence_filter=False, impulse_bos_displacement_pct=0.015
    ).detect(candles)

    # No BOS without staging (no pullback ever confirms the pending BOS).
    assert [e for e in off if e.event is StructureEvent.BREAK_OF_STRUCTURE] == []
    # Existing events are untouched -- staging only adds.
    off_keys = {(e.timestamp, e.event, e.direction) for e in off}
    assert off_keys <= {(e.timestamp, e.event, e.direction) for e in on}

    staged = [e for e in on if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(staged) == 2
    assert all(e.direction is MarketDirection.BEARISH for e in staged)
    assert [e.price_level for e in staged] == [60.0, 40.0]
    assert [e.reference_price_level for e in staged] == [80.0, 60.0]
    assert [e.timestamp for e in staged] == [candles[7].timestamp, candles[9].timestamp]
    assert all(e.scope is StructureScope.INTERNAL for e in staged)


def test_impulse_bos_staging_off_by_default_is_identical() -> None:
    """With the flag unset the detector is byte-for-byte unchanged."""
    highs = [150.0] * 13
    lows = [140.0] * 13
    highs[1] = 200.0
    lows[3] = 90.0
    lows[5] = 80.0
    lows[7] = 60.0
    lows[9] = 40.0
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=85.0)
    candles[7] = make_candle(7, 150.0, 60.0, close=65.0)
    candles[9] = make_candle(9, 150.0, 40.0, close=45.0)

    default = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)
    explicit_off = InternalStructureDetector(
        swing_lookback=1, confluence_filter=False, impulse_bos_displacement_pct=None
    ).detect(candles)
    assert [e.model_dump() for e in default] == [e.model_dump() for e in explicit_off]


def test_pending_bos_updated_on_higher_break() -> None:
    """Two consecutive HH while bullish BOS is pending -> the latest is used."""
    highs = [150.0] * 15
    lows = [140.0] * 15
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 220.0   # pending BOS (breaks 200)
    highs[7] = 240.0   # higher break (breaks 220) -> updates pending
    lows[9] = 100.0     # HL (100 > 90) -> confirms with latest pivot (240)

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)
    candles[7] = make_candle(7, 240.0, 140.0, close=225.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 1
    assert bos[0].price_level == 240.0
    assert bos[0].reference_price_level == 220.0
    assert bos[0].timestamp == candles[7].timestamp


def test_wick_only_in_trend_break_ignored() -> None:
    """A wick-only break (no close beyond the level) does not create a
    pending BOS, so no BOS is ever emitted."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 220.0   # wick breaks 200 but close stays below

    candles = make_series(highs, lows)
    # close = midpoint = (220+140)/2 = 180 < 200 -> wick only
    assert candles[5].close < 200.0

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 0


def test_wick_only_break_freezes_reference_until_close_confirms() -> None:
    """A wick-only break does not advance the state (no trend leak) and freezes
    the reference at its level; a later leg whose candle *closes* beyond that
    same frozen level activates the BOS, emitted against the original reference
    (200), not the wick pivot (220)."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 220.0  # wick breaks 200 (close=180), no close beyond -> pending
    highs[7] = 230.0  # candle closes at 205 (> 200) -> activates the BOS
    lows[9] = 100.0  # HL pullback (> 90) confirms the pending BOS

    candles = make_series(highs, lows)
    assert candles[5].close < 200.0  # wick-only
    candles[7] = make_candle(7, 230.0, 140.0, close=205.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)

    bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]
    assert len(bos) == 1
    assert bos[0].direction is MarketDirection.BULLISH
    assert bos[0].price_level == 230.0
    assert bos[0].reference_price_level == 200.0  # frozen reference, not 220
    assert bos[0].timestamp == candles[7].timestamp


def test_unconfirmed_bullish_choch_fails_when_origin_broken() -> None:
    """A bullish CHoCH that is not confirmed by a BOS and whose origin (the low
    the CHoCH rally launched from) is broken back through is invalidated: a
    CHOCH_FAILED event fires and the trend flips back to bearish.

    Sequence (lookback=1, persistence_candles=1): bearish BOS at index 5
    establishes a bearish leg; the rally from the low 100 (index 9) breaks the
    trailing high -> bullish CHoCH at index 11 (origin = 100). Price then drops
    back below 100 (index 13, sustained) before any bullish BOS -> CHOCH_FAILED.
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

    events = InternalStructureDetector(swing_lookback=1, persistence_candles=1).detect(candles)

    choch = next(e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER)
    assert choch.direction is MarketDirection.BULLISH

    failed = [e for e in events if e.event is StructureEvent.CHOCH_FAILED]
    assert len(failed) == 1
    # direction is the failed CHoCH's direction (bullish); it broke back below
    # the origin (100), not the CHoCH reference (160).
    assert failed[0].direction is MarketDirection.BULLISH
    assert failed[0].reference_price_level == 100.0
    assert failed[0].timestamp == candles[13].timestamp


def test_bos_fields_on_confirmed_event() -> None:
    """Verify all fields on a confirmed BOS event: timestamp, price_level,
    reference_price_level, reference_timestamp, origin_price_level."""
    highs = [150.0] * 11
    lows = [140.0] * 11
    highs[1] = 200.0
    lows[3] = 90.0
    highs[5] = 220.0
    lows[7] = 100.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)

    events = InternalStructureDetector(swing_lookback=1, confluence_filter=False).detect(candles)
    bos = next(e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE)

    assert bos.timestamp == candles[5].timestamp
    assert bos.price_level == 220.0
    assert bos.reference_price_level == 200.0
    assert bos.origin_price_level == 100.0
    assert bos.scope is StructureScope.INTERNAL


# --- Online re-anchor (flavor B) -------------------------------------------


def test_invalid_reanchor_mode_raises() -> None:
    with pytest.raises(ValueError, match="reanchor_mode"):
        InternalStructureDetector(reanchor_mode="nope")
    with pytest.raises(ValueError, match="reanchor_chain_threshold"):
        InternalStructureDetector(reanchor_mode="chain", reanchor_chain_threshold=0)


def test_reanchor_off_matches_default() -> None:
    """`reanchor_mode="off"` is byte-for-byte identical to the default: the new
    machinery is inert unless a trigger mode is selected (regression-safe)."""
    candles = _load_window_candles()
    default = InternalStructureDetector(
        swing_lookback=2, persistence_candles=3, confluence_filter=False
    ).detect(candles)
    off = InternalStructureDetector(
        swing_lookback=2, persistence_candles=3, confluence_filter=False, reanchor_mode="off"
    ).detect(candles)
    assert default == off


def test_displacement_surfaces_local_choch_where_off_finds_none() -> None:
    """On the real 1h window the bearish impulse leaves the high-side references
    parked at the leg origin, so `off` never fires a reversal CHoCH. The
    displacement trigger re-anchors them to a local FVG edge, so the eventual
    reclaim lands as a *local* bullish CHoCH instead of being missed."""
    candles = _load_window_candles()

    off = InternalStructureDetector(
        swing_lookback=2, persistence_candles=3, confluence_filter=False, reanchor_mode="off"
    ).detect(candles)
    displacement = InternalStructureDetector(
        swing_lookback=2,
        persistence_candles=3,
        confluence_filter=False,
        reanchor_mode="displacement",
    ).detect(candles)

    off_chochs = [e for e in off if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    disp_chochs = [e for e in displacement if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert off_chochs == []
    bullish = [e for e in disp_chochs if e.direction is MarketDirection.BULLISH]
    assert len(bullish) >= 1
    # The local CHoCH breaks a re-anchored level well below the leg origin
    # (64,766), not the stale top.
    ref = bullish[0].reference_price_level
    assert ref is not None and ref < 64766.0


def test_chain_reanchors_stale_reference_to_local_level() -> None:
    """A clean bearish impulse of consecutive lower-low pivots with no
    intervening high pivot chains state-advances; at the threshold the stale
    `active_high`/`validated_choch_high` (parked at the 200 origin) re-anchor
    down to the local 150 high, so a modest reclaim to 160 fires a bullish CHoCH
    there. Under `off` the same reclaim never reaches the stale 200 reference, so
    no CHoCH fires."""
    highs = [150.0] * 14
    lows = [140.0] * 14
    highs[1] = 200.0   # bootstraps active_high at the leg origin
    lows[3] = 130.0    # bootstraps active_low
    lows[5] = 110.0    # bearish advance 1
    lows[7] = 90.0     # bearish advance 2
    lows[9] = 70.0     # bearish advance 3 -> chain threshold -> re-anchor to 150
    highs[11] = 160.0  # reclaim above the re-anchored 150 -> bullish CHoCH
    highs[12] = 155.0  # persistence candle (still above 150, not a new pivot)

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 110.0, close=120.0)
    candles[7] = make_candle(7, 150.0, 90.0, close=100.0)
    candles[9] = make_candle(9, 150.0, 70.0, close=80.0)
    candles[11] = make_candle(11, 160.0, 140.0, close=158.0)
    candles[12] = make_candle(12, 155.0, 140.0, close=152.0)

    off = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False, reanchor_mode="off"
    ).detect(candles)
    chain = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=1,
        confluence_filter=False,
        reanchor_mode="chain",
        reanchor_chain_threshold=3,
    ).detect(candles)

    off_bull_choch = [
        e
        for e in off
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    chain_bull_choch = [
        e
        for e in chain
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert off_bull_choch == []
    assert len(chain_bull_choch) == 1
    assert chain_bull_choch[0].reference_price_level == 150.0


def test_chain_establish_only_does_not_tighten_a_fresh_validated_reference() -> None:
    """`reanchor_chain_establish_only` stops the chain trigger from *tightening*
    a freshly promoted `validated_choch_high` down to a shallower in-leg high.

    A real LH pullback (180) confirms a bearish BOS and a continuation low (90)
    promotes it to `validated_choch_high=180`. That same continuation advance hits
    the chain threshold (2): with `establish_only=False` the chain re-anchors the
    reference down to the local default high (150), so a reclaim to 160 fires a
    bullish CHoCH at the degraded 150 level; with `establish_only=True` the fresh
    180 reference is left intact, so the same 160 reclaim (below 180) fires no
    CHoCH — exactly the weak-pullback CHoCH the gate suppresses."""
    highs = [150.0] * 14
    lows = [140.0] * 14
    highs[1] = 200.0  # origin high
    lows[3] = 130.0  # bootstrap active_low
    lows[5] = 110.0  # bearish advance 1 -> pending BOS (pullback ref 200)
    highs[6] = 180.0  # LH pullback -> confirms BOS, candidate_choch_high=180
    lows[7] = 90.0  # advance 2 -> promotes validated_choch_high=180, chain hits 2
    highs[11] = 160.0  # reclaim (above the degraded 150, below the fresh 180)
    highs[12] = 155.0  # persistence candle

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 110.0, close=120.0)
    candles[6] = make_candle(6, 180.0, 140.0, close=175.0)
    candles[7] = make_candle(7, 150.0, 90.0, close=100.0)
    candles[11] = make_candle(11, 160.0, 140.0, close=158.0)
    candles[12] = make_candle(12, 155.0, 140.0, close=152.0)

    def bull_choch(events: list[MarketStructure]) -> list[MarketStructure]:
        return [
            e
            for e in events
            if e.event is StructureEvent.CHANGE_OF_CHARACTER
            and e.direction is MarketDirection.BULLISH
        ]

    tightening = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=1,
        confluence_filter=False,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
    ).detect(candles)
    establish_only = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=1,
        confluence_filter=False,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
    ).detect(candles)

    assert [e.reference_price_level for e in bull_choch(tightening)] == [150.0]
    assert bull_choch(establish_only) == []


def test_invalid_bos_pullback_max_wick_pct_raises() -> None:
    with pytest.raises(ValueError, match="bos_pullback_max_wick_pct"):
        InternalStructureDetector(bos_pullback_max_wick_pct=0)
    with pytest.raises(ValueError, match="bos_pullback_max_wick_pct"):
        InternalStructureDetector(bos_pullback_max_wick_pct=1.5)


def test_bos_pullback_wick_filter_rejects_wick_only_pullback() -> None:
    """A BOS confirmed by a single-candle wick pullback is suppressed by
    `bos_pullback_max_wick_pct`, but the same BOS confirms when a *real*-bodied
    pullback forms instead.

    A bearish advance (close below 130) leaves a pending BOS; the confirming high
    pivot is candle 7. In the wick case candle 7 spikes to 180 intrabar but its
    body closes near the low (a rejection wick), so with the filter it does not
    confirm; in the body case candle 7 closes near its 180 high (a real bounce),
    so it confirms in both."""

    def series(pullback_close: float) -> list[Candle]:
        highs = [150.0] * 10
        lows = [140.0] * 10
        highs[1] = 200.0  # origin high
        lows[3] = 130.0  # bootstrap active_low
        lows[5] = 110.0  # bearish advance -> pending BOS (pullback ref 200)
        highs[7] = 180.0  # confirming high pivot (LH)
        c = make_series(highs, lows)
        c[5] = make_candle(5, 150.0, 110.0, close=120.0)
        # candle 7 spikes to 180; its close decides wick vs body.
        c[7] = make_candle(7, 180.0, 140.0, close=pullback_close)
        return c

    def bearish_bos(events: list[MarketStructure]) -> list[MarketStructure]:
        return [
            e
            for e in events
            if e.event is StructureEvent.BREAK_OF_STRUCTURE
            and e.direction is MarketDirection.BEARISH
        ]

    # Wick pullback: close 145 -> upper wick (180-145=35) is 0.78 of range (45).
    wick = make_candle(7, 180.0, 140.0, close=145.0)
    assert (wick.high - max(wick.open, wick.close)) / (wick.high - wick.low) > 0.4
    # Body pullback: close 178 -> upper wick (180-178=2) is ~0.04 of range.

    def run(pullback_close: float, max_wick: float | None) -> list[MarketStructure]:
        return InternalStructureDetector(
            swing_lookback=1,
            persistence_candles=1,
            confluence_filter=False,
            bos_pullback_max_wick_pct=max_wick,
        ).detect(series(pullback_close))

    # No filter: both wick and body pullbacks confirm the BOS.
    assert len(bearish_bos(run(145.0, None))) == 1
    # Filter on: the wick pullback no longer confirms, the bodied one still does.
    assert bearish_bos(run(145.0, 0.4)) == []
    assert len(bearish_bos(run(178.0, 0.4))) == 1


def test_invalid_reanchor_min_price_gap_pct_raises() -> None:
    with pytest.raises(ValueError, match="reanchor_min_price_gap_pct"):
        InternalStructureDetector(reanchor_min_price_gap_pct=0)


def test_reanchor_min_price_gap_suppresses_hair_trigger_choch() -> None:
    """`reanchor_min_price_gap_pct` blocks a re-anchor whose local level sits
    almost on top of price (the hair-trigger that produces a mid-range CHoCH
    that then fails). This is the chain scenario of
    `test_chain_reanchors_stale_reference_to_local_level` shifted up by 10,000
    so the same vertical structure spans a much smaller *fraction* of price: the
    re-anchor level (10,150) is only ~0.69% above the advance close (10,080). A
    0.02 gap requires a wider separation, so the re-anchor is refused and no
    bullish CHoCH fires; without the gate it fires (as in the base scenario)."""
    base = 10000.0
    highs = [base + 150.0] * 14
    lows = [base + 140.0] * 14
    highs[1] = base + 200.0  # bootstraps active_high at the leg origin
    lows[3] = base + 130.0  # bootstraps active_low
    lows[5] = base + 110.0  # bearish advance 1
    lows[7] = base + 90.0  # bearish advance 2
    lows[9] = base + 70.0  # bearish advance 3 -> chain threshold -> re-anchor

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, base + 150.0, base + 110.0, close=base + 120.0)
    candles[7] = make_candle(7, base + 150.0, base + 90.0, close=base + 100.0)
    candles[9] = make_candle(9, base + 150.0, base + 70.0, close=base + 80.0)
    candles[11] = make_candle(11, base + 160.0, base + 140.0, close=base + 158.0)
    candles[12] = make_candle(12, base + 155.0, base + 140.0, close=base + 152.0)

    def bull_choch(events: list[MarketStructure]) -> list[MarketStructure]:
        return [
            e
            for e in events
            if e.event is StructureEvent.CHANGE_OF_CHARACTER
            and e.direction is MarketDirection.BULLISH
        ]

    no_gate = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=1,
        confluence_filter=False,
        reanchor_mode="chain",
        reanchor_chain_threshold=3,
    ).detect(candles)
    gated = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=1,
        confluence_filter=False,
        reanchor_mode="chain",
        reanchor_chain_threshold=3,
        reanchor_min_price_gap_pct=0.02,
    ).detect(candles)

    assert len(bull_choch(no_gate)) == 1
    assert bull_choch(no_gate)[0].reference_price_level == base + 150.0
    assert bull_choch(gated) == []


def test_invalid_stale_reanchor_candles_raises() -> None:
    with pytest.raises(ValueError, match="stale_reanchor_candles"):
        InternalStructureDetector(stale_reanchor_candles=0)


def test_stale_reanchor_surfaces_local_choch_where_off_finds_none() -> None:
    """On the real 1h window the bearish leg leaves the high-side reversal
    reference parked at the leg origin (64,766), so `off` never fires a reversal
    CHoCH as price grinds back up. After `stale_reanchor_candles` candles with no
    fresh BOS/CHoCH the staleness re-anchor pulls that reference down to the
    recent local high, so the reclaim lands as a *local* bullish CHoCH well below
    the origin instead of being missed."""
    candles = _load_window_candles()

    off = InternalStructureDetector(
        swing_lookback=2, persistence_candles=3, confluence_filter=False
    ).detect(candles)
    stale = InternalStructureDetector(
        swing_lookback=2,
        persistence_candles=3,
        confluence_filter=False,
        stale_reanchor_candles=30,
    ).detect(candles)

    off_bull_choch = [
        e
        for e in off
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    stale_bull_choch = [
        e
        for e in stale
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert off_bull_choch == []
    assert len(stale_bull_choch) >= 1
    ref = stale_bull_choch[0].reference_price_level
    assert ref is not None and ref < 64766.0
