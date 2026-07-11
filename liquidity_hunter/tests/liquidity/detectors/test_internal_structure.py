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


def test_first_bos_of_leg_references_choch_extreme_not_trailing() -> None:
    """The FIRST BOS of a new leg must reference the CHoCH's confirming extreme
    (the fundo/topo the reversal formed), not the trailing reference that
    ratchets to a shallow retrace pivot during the pullback.

    Setup: bearish -> bullish CHoCH (confirming high = 180) -> a lower-high
    retrace at 176 trails ``active_high`` down -> a bullish continuation BOS.
    Before the CHoCH-seed fix the BOS reported 176 (the trailing lower-high);
    it must report 180 (the CHoCH high) so, via the close-break re-anchor, it
    confirms only on a close above the level the reversal actually launched from.
    """
    highs = [150.0] * 44
    lows = [140.0] * 44
    highs[1] = 200.0
    lows[3] = 100.0
    lows[5] = 80.0
    highs[7] = 170.0
    lows[9] = 60.0
    highs[11] = 165.0
    # A lower-high retrace after the CHoCH: trails active_high down to 176 (the
    # shallow level the pre-fix code would wrongly report as the BOS reference).
    highs[18] = 176.0
    # Bullish continuation BOS, then an HL pullback (L100) to confirm it.
    highs[21] = 250.0
    lows[23] = 100.0

    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[9] = make_candle(9, 150.0, 60.0, close=70.0)
    # Bullish CHoCH: sustained break above validated_choch_high=170, confirming
    # high (the topo the reversal formed) = 180.
    candles[14] = make_candle(14, high=175.0, low=140.0, close=172.0)
    candles[15] = make_candle(15, high=180.0, low=150.0, close=175.0)
    candles[16] = make_candle(16, high=178.0, low=150.0, close=171.0)
    candles[21] = make_candle(21, high=250.0, low=140.0, close=248.0)

    detector = InternalStructureDetector(
        swing_lookback=1, persistence_candles=2, confluence_filter=False
    )
    events = detector.detect(candles)

    bullish_bos = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
    ]
    assert bullish_bos, "Expected a bullish continuation BOS."
    first_bos = bullish_bos[0]
    assert first_bos.price_level == 250.0
    # The CHoCH confirming high (180), NOT the trailing lower-high retrace (176).
    assert first_bos.reference_price_level == 180.0


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


def test_stage_wick_rejected_bos_adds_continuation_mark_without_confirming_state() -> None:
    """`stage_wick_rejected_bos` adds an *additive* mark for a *continuation* BOS
    the wick filter kept out of the state machine, without confirming it into the
    CHoCH promotion.

    A first bearish BOS confirms off a bodied pullback (candle 7), establishing the
    staircase floor (80). A second, continuation advance (candle 9, low 60) is
    confirmed only by a wick pullback (candle 11 spikes to 155 but closes at 142),
    which `bos_pullback_max_wick_pct` rejects -- so the state machine emits no BOS
    for it. With `stage_wick_rejected_bos` that break gets a mark at the breaking
    low (60) referencing the floor it broke (80), because the leg genuinely closed
    beyond the level. Only continuations with a real staircase floor are staged
    (the first-of-leg break, floor `None`, is never staged from the stale
    `ref_price` fallback). The mark is purely visual -- it does not seed a
    `candidate_choch_<side>`, so no `change_of_character` appears."""

    highs = [150.0] * 15
    lows = [140.0] * 15
    highs[1] = 200.0  # origin high
    lows[3] = 90.0  # bootstrap active_low
    lows[5] = 80.0  # advance 1 (first BOS of the leg, floor None)
    highs[7] = 160.0  # bodied pullback -> confirms BOS 1, floor becomes 80
    lows[9] = 60.0  # advance 2 (continuation, floor 80)
    highs[11] = 155.0  # wick pullback -> would confirm BOS 2 but is rejected
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 150.0, 80.0, close=85.0)
    candles[7] = make_candle(7, 160.0, 145.0, close=158.0)  # bodied (small wick)
    candles[9] = make_candle(9, 150.0, 60.0, close=65.0)
    candles[11] = make_candle(11, 155.0, 140.0, close=142.0)  # wick (large upper wick)

    def run(stage: bool) -> list[MarketStructure]:
        return InternalStructureDetector(
            swing_lookback=1,
            persistence_candles=1,
            confluence_filter=False,
            bos_pullback_max_wick_pct=0.4,
            stage_wick_rejected_bos=stage,
        ).detect(candles)

    def bos(events: list[MarketStructure]) -> list[MarketStructure]:
        return [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]

    off = bos(run(False))
    on = bos(run(True))
    # Off: only the first (bodied-confirmed) BOS; the wick continuation is suppressed.
    assert [(e.price_level, e.reference_price_level) for e in off] == [(80.0, 90.0)]
    # On: the same real BOS plus one additive continuation mark at 60 referencing 80.
    assert [(e.price_level, e.reference_price_level) for e in on] == [(80.0, 90.0), (60.0, 80.0)]
    assert on[1].direction is MarketDirection.BEARISH
    assert on[1].scope is StructureScope.INTERNAL
    # The staged mark is purely visual -- it does not seed a CHoCH.
    assert [e for e in run(True) if e.event is StructureEvent.CHANGE_OF_CHARACTER] == []


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


# --- Leg-origin CHoCH reference (`bos_leg_origin_choch_ref`) ----------------
#
# Sequence (lookback=1, persistence_candles=2):
#   index  1: high 200 -> bootstrap active_high
#   index  3: low   90 -> bootstrap active_low (and pending_low)
#   index  5: high 210 -> BOS bullish state advance (close 205 > 200);
#                         leg-origin snapshot (pullback_ref) = 90
#   index  7: low  100 -> HL (100 > 90) confirms the BOS -> emission.
#                         Flag ON: validated_choch_low := 90 (the leg origin,
#                         structural). Flag OFF: no validated ref (no
#                         continuation yet).
#   index  9: low   95 -> breaks the trailing active_low (100), closes hold
#                         below it (98, 97).
#                         Flag OFF: CHoCH bearish vs the trailing 100
#                         (cold-start fallback). Flag ON: 95 > 90 does not
#                         reach the leg origin -> LIQUIDITY_SWEEP only.
#   index 11: low   80 -> sustained break below 90 (closes 85, 86, 86).
#                         Flag ON: CHoCH bearish vs the leg origin 90.
_LEG_ORIGIN_HIGHS = [150.0] * 18
for _i, _v in {1: 200.0, 5: 210.0}.items():
    _LEG_ORIGIN_HIGHS[_i] = _v
_LEG_ORIGIN_LOWS = [140.0] * 18
for _i, _v in {3: 90.0, 7: 100.0, 9: 95.0, 11: 80.0}.items():
    _LEG_ORIGIN_LOWS[_i] = _v


def _leg_origin_series() -> list[Candle]:
    candles = make_series(_LEG_ORIGIN_HIGHS, _LEG_ORIGIN_LOWS)
    candles[5] = make_candle(5, 210.0, 140.0, close=205.0)
    candles[9] = make_candle(9, 150.0, 95.0, close=98.0)
    candles[10] = make_candle(10, 150.0, 96.0, close=97.0)
    candles[11] = make_candle(11, 150.0, 80.0, close=85.0)
    candles[12] = make_candle(12, 150.0, 84.0, close=86.0)
    candles[13] = make_candle(13, 150.0, 85.0, close=86.0)
    return candles


def test_leg_origin_choch_ref_uses_bos_leg_origin() -> None:
    """With `bos_leg_origin_choch_ref`, the confirmed BOS promotes the low its
    leg rose from (90) to the bearish-CHoCH reference at emission: the shallow
    break of the trailing HL (95 < 100) is only a sweep, and the CHoCH fires on
    the sustained break of the leg origin, referencing it."""
    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
        bos_leg_origin_choch_ref=True,
    ).detect(_leg_origin_series())

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BEARISH
    assert chochs[0].reference_price_level == 90.0
    sweeps = [e for e in events if e.event is StructureEvent.LIQUIDITY_SWEEP]
    assert any(s.price_level == 95.0 for s in sweeps)


def test_leg_origin_choch_ref_off_falls_back_to_trailing_reference() -> None:
    """Same series with the flag off: no continuation ever promotes a
    validated reference, so the CHoCH falls back to the trailing active_low
    (100) and fires on the shallower break -- the behavior the flag changes."""
    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
    ).detect(_leg_origin_series())

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert chochs
    assert chochs[0].direction is MarketDirection.BEARISH
    assert chochs[0].reference_price_level == 100.0


def test_invalid_bos_leg_origin_release_gap_pct_raises() -> None:
    with pytest.raises(ValueError, match="bos_leg_origin_release_gap_pct"):
        InternalStructureDetector(bos_leg_origin_release_gap_pct=0)


def test_invalid_bos_leg_origin_release_gap_atr_raises() -> None:
    with pytest.raises(ValueError, match="bos_leg_origin_release_gap_atr"):
        InternalStructureDetector(bos_leg_origin_release_gap_atr=-1.0)


def test_invalid_choch_weak_ref_persistence_candles_raises() -> None:
    with pytest.raises(ValueError, match="choch_weak_ref_persistence_candles"):
        InternalStructureDetector(choch_weak_ref_persistence_candles=0)


# --- Leg-origin promotion when a pending BOS dies on an origin reclaim ------
#
# Replicates the ETHUSDT H1 2026-06-06 missing-CHoCH case. Sequence
# (lookback=1, persistence_candles=2, bos_pullback_max_wick_pct=0.4):
#   index  1: high 300 -> bootstrap active_high
#   index  3: low  150 -> bootstrap active_low
#   index  5: low  140 (close 145) -> bearish BOS advance #1 (pb=300)
#   index  7: high 280 (close 278, clean body) -> confirms/emits BOS #1;
#                        leg-origin promotes validated_choch_high := 300,
#                        candidate_choch_high := 280
#   index  9: low  130 (close 133) -> advance #2; continuation gate promotes
#                        candidate 280 -> validated_choch_high := 280;
#                        new pending BOS pb=280
#   index 11: high 200 (close 160, upper wick 50% > 40%) -> pullback
#                        WICK-REJECTED; pending stays alive; LOWER_HIGH;
#                        active_high trails to 200 (the true leg-3 origin)
#   index 13: low  120 (close 124) -> advance #3; pending BOS pb=200
#   index 15: high 250 (closes 245, 246 hold above 200) -> 250 > pb 200:
#                        the pending BOS is discarded -- price reclaimed the
#                        leg origin with no pullback ever confirming it. The
#                        origin must be promoted at the kill, so the CHoCH
#                        fires against 200; without the promotion the
#                        reference stays at the stale 280 and the reversal
#                        never confirms (the ETHUSDT 1618.85-vs-1793.66 miss).
_ORIGIN_RECLAIM_HIGHS = [160.0] * 18
for _i, _v in {1: 300.0, 7: 280.0, 11: 200.0, 15: 250.0, 16: 248.0, 17: 246.0}.items():
    _ORIGIN_RECLAIM_HIGHS[_i] = _v
_ORIGIN_RECLAIM_LOWS = [155.0] * 18
for _i, _v in {3: 150.0, 5: 140.0, 9: 130.0, 13: 120.0, 16: 240.0, 17: 238.0}.items():
    _ORIGIN_RECLAIM_LOWS[_i] = _v


def _origin_reclaim_series() -> list[Candle]:
    candles = make_series(_ORIGIN_RECLAIM_HIGHS, _ORIGIN_RECLAIM_LOWS)
    candles[5] = make_candle(5, 160.0, 140.0, close=145.0)
    candles[7] = make_candle(7, 280.0, 155.0, close=278.0)
    candles[9] = make_candle(9, 160.0, 130.0, close=133.0)
    candles[11] = make_candle(11, 200.0, 155.0, close=160.0)
    candles[13] = make_candle(13, 160.0, 120.0, close=124.0)
    # The reclaim: three consecutive closes above the 200 leg origin
    # (persistence window = breaking candle + persistence_candles).
    candles[15] = make_candle(15, 250.0, 155.0, close=245.0)
    candles[16] = make_candle(16, 248.0, 240.0, close=246.0)
    candles[17] = make_candle(17, 246.0, 238.0, close=244.0)
    return candles


def test_leg_origin_promoted_when_pending_bos_origin_reclaimed() -> None:
    """A pending BOS killed because price reclaimed its leg origin promotes
    that origin to the CHoCH reference: the CHoCH fires against the reclaimed
    origin (200) instead of degrading to sweeps below the stale 280."""
    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
        bos_leg_origin_choch_ref=True,
        bos_pullback_max_wick_pct=0.4,
    ).detect(_origin_reclaim_series())

    chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert len(chochs) == 1
    assert chochs[0].reference_price_level == 200.0


def test_pending_bos_leg_origin_blocks_premature_choch() -> None:
    """A still-pending BOS (all pullback attempts wick-rejected) contributes
    its leg origin to the CHoCH reference chain: a shallow reclaim above the
    trailing active_high but below the origin is only a sweep, and the CHoCH
    fires once the origin itself is reclaimed (the ETHUSDT H1 2026-06-25
    premature-CHoCH case).

    Sequence (lookback=1, persistence=2, wick filter 0.4):
      index  1: high 200 -> bootstrap active_high
      index  3: low  150 -> bootstrap active_low
      index  5: low  140 (close 145) -> BOS advance out of NEUTRAL; pending
                          BOS carries leg origin 200
      index  7: high 170 (close 157, wick 50%) -> pullback WICK-REJECTED;
                          pending alive; active_high trails to 170
      index  9: high 185 (close 172, wick 43%) -> wick-rejected again; the
                          break above active_high 170 sustains, but 185 < 200
                          (the pending leg origin) -> LIQUIDITY_SWEEP, not a
                          CHoCH at the shallow 170
      index 12: high 210 (closes 207/206/205 hold above 200) -> origin
                          reclaimed: pending killed, origin promoted, CHoCH
                          fires against 200.
    """
    highs = [160.0] * 16
    high_spikes = {
        1: 200.0, 7: 170.0, 9: 185.0, 10: 175.0, 11: 176.0,
        12: 210.0, 13: 208.0, 14: 207.0,
    }
    for i, v in high_spikes.items():
        highs[i] = v
    lows = [155.0] * 16
    for i, v in {3: 150.0, 5: 140.0, 10: 168.0, 11: 167.0, 12: 175.0, 13: 202.0, 14: 202.0}.items():
        lows[i] = v
    candles = make_series(highs, lows)
    candles[5] = make_candle(5, 160.0, 140.0, close=145.0)
    candles[7] = make_candle(7, 170.0, 155.0, close=157.0)
    candles[9] = make_candle(9, 185.0, 155.0, close=172.0)
    candles[10] = make_candle(10, 175.0, 168.0, close=173.0)
    candles[11] = make_candle(11, 176.0, 167.0, close=174.0)
    candles[12] = make_candle(12, 210.0, 175.0, close=207.0)
    candles[13] = make_candle(13, 208.0, 202.0, close=206.0)
    candles[14] = make_candle(14, 207.0, 202.0, close=205.0)

    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
        bos_leg_origin_choch_ref=True,
        bos_pullback_max_wick_pct=0.4,
    ).detect(candles)

    chochs = [e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert len(chochs) == 1
    assert chochs[0].direction is MarketDirection.BULLISH
    assert chochs[0].reference_price_level == 200.0
    sweeps = [
        e
        for e in events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BULLISH
    ]
    assert any(s.price_level == 185.0 for s in sweeps)

    # Flag off: the blind side falls back to the trailing active_high (170)
    # and the shallow reclaim fires a premature CHoCH -- the behavior the
    # pending-leg-origin chain inclusion suppresses.
    events_off = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
        bos_pullback_max_wick_pct=0.4,
    ).detect(candles)
    chochs_off = [e for e in events_off if e.event is StructureEvent.CHANGE_OF_CHARACTER]
    assert chochs_off
    assert chochs_off[0].reference_price_level == 170.0


def test_leg_origin_reclaim_promotion_requires_flag() -> None:
    """Same series with `bos_leg_origin_choch_ref` off: the kill does not
    promote, the reference stays at the continuation-promoted 280, and no
    bullish CHoCH fires (250 < 280)."""
    events = InternalStructureDetector(
        swing_lookback=1,
        persistence_candles=2,
        confluence_filter=False,
        bos_pullback_max_wick_pct=0.4,
    ).detect(_origin_reclaim_series())

    chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
    ]
    assert chochs == []


# --- Real-data regression: fallback CHoCH inside an unconfirmed-CHoCH window
_SOL_WINDOW_DATA = Path(__file__).parent / "data" / "solusdt_1h_2026_06_13_27.json"


def _load_sol_window_candles() -> list[Candle]:
    rows = json.loads(_SOL_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def test_real_window_unconfirmed_choch_suppresses_fallback_choch() -> None:
    """Real SOLUSDT H1 regression (2026-06-23): while a bearish CHoCH is
    unconfirmed (its origin armed at 74.97, no bearish BOS yet), the bullish
    exit from the provisional structure is CHOCH_FAILED at that origin -- the
    `active_high` cold-start fallback must not fire a premature CHoCH at the
    shallow trailing 69.63 LH. The side was fully blind (the 06-22 bearish
    CHoCH was itself fallback-triggered after a CHOCH_FAILED reset, so it
    armed no blind-spot origin, and no BOS had emitted to promote anything).

    With the suppression: the 70.36 rally is a sweep, the drop to 64.66
    prints the bearish continuation BOS the trend called for, and the
    genuine bullish CHoCH fires on 06-26 against 69.64 -- the leg origin of
    the newest activated BOS. Detector args mirror `load_dashboard_data`'s
    H1 wiring.
    """
    events = InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_pct=0.04,
    ).detect(_load_sol_window_candles())

    june_23 = datetime(2026, 6, 23, tzinfo=UTC)
    june_26 = datetime(2026, 6, 26, tzinfo=UTC)
    premature = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and june_23 <= e.timestamp < june_26
    ]
    assert premature == []

    reclaim_sweeps = [
        e
        for e in events
        if e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BULLISH
        and e.price_level == 70.36
    ]
    assert len(reclaim_sweeps) == 1

    continuation_bos = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and e.price_level == 64.66
    ]
    assert len(continuation_bos) == 1

    genuine_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp >= june_26
    ]
    assert genuine_chochs
    assert genuine_chochs[0].reference_price_level == 69.64


_BTC_30M_WINDOW_DATA = (
    Path(__file__).parent / "data" / "btcusdt_30m_2026_06_05_07_02.json"
)


def _load_btc_30m_window_candles() -> list[Candle]:
    rows = json.loads(_BTC_30M_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.M30,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _btc_30m_detector(
    *,
    release_gap_pct: float | None = None,
    release_gap_atr: float | None = None,
    weak_choch_persistence: int | None = None,
) -> InternalStructureDetector:
    """Production M30 wiring (`load_dashboard_data`), release gap injectable."""
    return InternalStructureDetector(
        swing_lookback=5,
        persistence_candles=2,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_pct=release_gap_pct,
        bos_leg_origin_release_gap_atr=release_gap_atr,
        choch_weak_ref_persistence_candles=weak_choch_persistence,
    )


def test_release_gap_atr_matches_equivalent_pct_and_takes_precedence() -> None:
    """`bos_leg_origin_release_gap_atr=N` must behave exactly like a fixed
    `bos_leg_origin_release_gap_pct` equal to N x the series' mean true-range
    fraction, and must take precedence over a simultaneously-passed pct: on
    this window the fixed 4% and the ATR gap produce different event streams
    (see the whipsaw regression test below), so if the pct silently won the
    equality against the ATR-equivalent run would fail.
    """
    candles = _load_btc_30m_window_candles()
    mean_tr_pct = sum(
        max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )
        / curr.close
        for prev, curr in zip(candles, candles[1:], strict=False)
    ) / (len(candles) - 1)

    atr_events = _btc_30m_detector(
        release_gap_pct=0.04, release_gap_atr=3.0
    ).detect(candles)
    equivalent_pct_events = _btc_30m_detector(
        release_gap_pct=3.0 * mean_tr_pct
    ).detect(candles)
    assert atr_events == equivalent_pct_events

    fixed_pct_events = _btc_30m_detector(release_gap_pct=0.04).detect(candles)
    assert fixed_pct_events != atr_events


def test_real_window_atr_release_gap_resolves_choch_whipsaw() -> None:
    """Real BTCUSDT M30 regression (2026-06-23..26): with the fixed 4% release
    gap (~8.5 ATR on this timeframe) the structural reference stayed pinned
    through the June drop and every bounce fired a bullish CHoCH that then
    failed -- three whipsaw CHoCH/CHOCH_FAILED pairs across a 63k -> 58k
    decline. The volatility-normalized gap (3 x mean true-range%) lets the
    staleness re-anchor act at the same "typical candle" distance as on
    coarser timeframes, resolving the drop into one bearish CHoCH at the leg
    origin plus a bearish BOS staircase.
    """
    candles = _load_btc_30m_window_candles()
    events = _btc_30m_detector(
        release_gap_pct=0.04, release_gap_atr=3.0
    ).detect(candles)

    drop_start = datetime(2026, 6, 23, tzinfo=UTC)
    drop_end = datetime(2026, 6, 27, tzinfo=UTC)
    whipsaws = [
        e
        for e in events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BULLISH
        and drop_start <= e.timestamp < drop_end
    ]
    assert whipsaws == []

    bearish_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and drop_start <= e.timestamp < drop_end
    ]
    assert len(bearish_chochs) == 1
    assert bearish_chochs[0].reference_price_level == 63833.4

    staircase = [
        e.price_level
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and drop_start <= e.timestamp < drop_end
    ]
    assert staircase == [59060.0, 58030.0]


def test_weak_ref_choch_needs_barrier_persistence() -> None:
    """A CHoCH firing against a *weak* reference (here the cold-start
    fallback during the fixture's bootstrap) must hold for
    `choch_weak_ref_persistence_candles` instead of the base persistence: the
    same 64179.5 bullish CHoCH against 62942.4 confirms at 06-07 23:00 with
    the base persistence of 2, but only at 06-08 01:30 (a window that holds 4
    candles) with the barrier -- delayed, not lost, and against the same
    reference.
    """
    candles = _load_btc_30m_window_candles()

    def first_choch(events: list[MarketStructure]) -> MarketStructure:
        return next(
            e for e in events if e.event is StructureEvent.CHANGE_OF_CHARACTER
        )

    base_choch = first_choch(
        _btc_30m_detector(
            release_gap_pct=0.04, release_gap_atr=3.0
        ).detect(candles)
    )
    barrier_choch = first_choch(
        _btc_30m_detector(
            release_gap_pct=0.04, release_gap_atr=3.0, weak_choch_persistence=4
        ).detect(candles)
    )

    assert base_choch.timestamp == datetime(2026, 6, 7, 23, 0, tzinfo=UTC)
    assert barrier_choch.timestamp == datetime(2026, 6, 8, 1, 30, tzinfo=UTC)
    assert base_choch.price_level == barrier_choch.price_level == 64179.5
    assert (
        base_choch.reference_price_level
        == barrier_choch.reference_price_level
        == 62942.4
    )


def test_structural_ref_choch_exempt_from_barrier() -> None:
    """The new-cycle barrier applies only to weak references: on the SOLUSDT
    H1 window every CHoCH fires via a structural reference (leg origin
    family) or holds far past the barrier anyway, so even an absurd barrier
    of 10 candles leaves the output byte-for-byte identical -- including the
    genuine 06-26 bullish CHoCH against the 69.64 leg origin.
    """
    candles = _load_sol_window_candles()

    def sol_detector(barrier: int | None) -> InternalStructureDetector:
        return InternalStructureDetector(
            swing_lookback=4,
            persistence_candles=2,
            reanchor_mode="chain",
            reanchor_chain_threshold=2,
            reanchor_chain_establish_only=True,
            reanchor_min_price_gap_pct=0.003,
            stale_reanchor_candles=80,
            impulse_bos_displacement_pct=0.015,
            bos_pullback_max_wick_pct=0.4,
            stage_wick_rejected_bos=True,
            bos_leg_origin_choch_ref=True,
            bos_leg_origin_release_gap_pct=0.04,
            choch_weak_ref_persistence_candles=barrier,
        )

    base_events = sol_detector(None).detect(candles)
    barrier_events = sol_detector(10).detect(candles)
    assert barrier_events == base_events

    structural_choch = next(
        e
        for e in barrier_events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp >= datetime(2026, 6, 26, tzinfo=UTC)
    )
    assert structural_choch.reference_price_level == 69.64


def test_invalid_bos_leg_origin_min_pullback_atr_raises() -> None:
    with pytest.raises(ValueError, match="bos_leg_origin_min_pullback_atr"):
        InternalStructureDetector(bos_leg_origin_min_pullback_atr=0)


# --- Shallow-pullback leg-origin promotion (AAVEUSDT H1 2026-07-02) ---------
#
# The bearish leg that dropped from ~86.6 to 82.7 launched from a shallow
# secondary lower-high (86.59) sitting well below the correction's true top
# (the 87.82 swing high). Its immediate pullback (active_low 84.91 ->
# active_high 86.59) retraced only 1.94% of price -- 1.42 x the series' mean
# true range -- so `bos_leg_origin_min_pullback_atr=1.5` promotes the CHoCH
# reference to the correction's extreme pivot (87.82) instead of the shallow
# 86.59. Raising the reference then reclassifies the premature 07-02 poke
# (which spiked to 88.49 and fell straight back to 84.28) as a sweep, and the
# bullish CHoCH fires only once price reclaims the true top on 07-03 (-> 91.05).
_AAVE_1H_WINDOW_DATA = (
    Path(__file__).parent / "data" / "aaveusdt_1h_2026_06_20_07_04.json"
)


def _load_aave_1h_window_candles() -> list[Candle]:
    rows = json.loads(_AAVE_1H_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="AAVEUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _aave_1h_detector(min_pullback_atr: float | None) -> InternalStructureDetector:
    """Production H1 wiring (`load_dashboard_data`), shallow-pullback flag
    injectable."""
    return InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_atr=3.0,
        bos_leg_origin_min_pullback_atr=min_pullback_atr,
        choch_weak_ref_persistence_candles=4,
    )


def _late_bullish_chochs(events: list[MarketStructure]) -> list[MarketStructure]:
    return [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp >= datetime(2026, 7, 1, tzinfo=UTC)
    ]


def test_shallow_pullback_off_anchors_choch_at_secondary_high() -> None:
    """With the promotion off, the bullish CHoCH anchors at the shallow
    secondary high (86.59), firing early on the 07-02 poke."""
    events = _aave_1h_detector(None).detect(_load_aave_1h_window_candles())

    chochs = _late_bullish_chochs(events)
    assert len(chochs) == 1
    assert chochs[0].timestamp == datetime(2026, 7, 2, 11, tzinfo=UTC)
    assert chochs[0].reference_price_level == 86.59


def test_shallow_pullback_promotes_choch_to_correction_top() -> None:
    """`bos_leg_origin_min_pullback_atr=1.5` promotes the shallow leg origin to
    the correction's true top: the CHoCH reference is 87.82 (not 86.59) and it
    fires only once price reclaims it on 07-03 (the 07-02 poke is now a sweep)."""
    events = _aave_1h_detector(1.5).detect(_load_aave_1h_window_candles())

    chochs = _late_bullish_chochs(events)
    assert len(chochs) == 1
    assert chochs[0].timestamp == datetime(2026, 7, 3, 13, tzinfo=UTC)
    assert chochs[0].reference_price_level == 87.82
    # The reference is anchored at the pivot that formed the correction top, so
    # the frontend draws the CHoCH line from the leg's origin, not the break.
    assert chochs[0].reference_timestamp == datetime(2026, 7, 1, 2, tzinfo=UTC)


# --- Leg origin promoted as *structural* only on a close-confirmed break -------
# Real AAVEUSDT H1 window (2026-06-05 .. 06-24). Bullish trend from the 06-08
# CHoCH. A pullback bottoms at 72.61 (06-16 14:00); the leg then rises and its
# only new high over the prior 77.70 BOS top is a single-candle *wick* to 77.94
# (06-17 02:00, close 76.94) -- no candle closes above 77.70. The state machine
# still emits that continuation BOS (its close-break was against the lower
# trailing 76.35), promoting the 72.61 leg origin to the bearish-CHoCH
# reference. With `bos_leg_origin_require_close_break` off, that origin is
# *structural* (base persistence), so the 06-18 poke to 70.64 fires a premature
# bearish CHoCH that fails 06-20 (whipsaw). On, the wick-only break promotes
# 72.61 as a *weak* reference, so the new-cycle barrier governs it and the
# genuine bearish CHoCH fires once 06-23, at the same 72.61 level.
_AAVE_1H_WICK_WINDOW_DATA = (
    Path(__file__).parent / "data" / "aaveusdt_1h_2026_06_05_24.json"
)


def _load_aave_1h_wick_window_candles() -> list[Candle]:
    rows = json.loads(_AAVE_1H_WICK_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="AAVEUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _aave_1h_wick_detector(require_close_break: bool) -> InternalStructureDetector:
    """Production H1 wiring, close-break-structural flag injectable."""
    return InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_atr=3.0,
        choch_weak_ref_persistence_candles=4,
        bos_leg_origin_require_close_break=require_close_break,
    )


def _bear_chochs(events: list[MarketStructure]) -> list[MarketStructure]:
    return [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
    ]


def test_wick_only_leg_origin_structural_fires_premature_choch_when_off() -> None:
    """With the flag off, the wick-only continuation's leg origin (72.61) is a
    structural reference at base persistence, so the 06-18 poke fires a premature
    bearish CHoCH that then fails 06-20 (a whipsaw pair)."""
    events = _aave_1h_wick_detector(require_close_break=False).detect(
        _load_aave_1h_wick_window_candles()
    )

    bear = _bear_chochs(events)
    assert bear[0].timestamp == datetime(2026, 6, 18, 15, tzinfo=UTC)
    assert bear[0].reference_price_level == 72.61
    # The premature reversal is invalidated two days later.
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 20, 21, tzinfo=UTC)
        for e in events
    )


def test_wick_only_leg_origin_weak_defers_choch_to_barrier_when_on() -> None:
    """On, the wick-only break promotes 72.61 as a *weak* reference: the premature
    06-18 CHoCH and its 06-20 failure are gone, and the genuine bearish CHoCH
    fires once on 06-23 against the same 72.61 level."""
    events = _aave_1h_wick_detector(require_close_break=True).detect(
        _load_aave_1h_wick_window_candles()
    )

    bear = _bear_chochs(events)
    assert len(bear) == 1
    assert bear[0].timestamp == datetime(2026, 6, 23, 6, tzinfo=UTC)
    assert bear[0].reference_price_level == 72.61
    # No premature reversal to fail: the 06-20 CHOCH_FAILED is gone.
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED and e.direction is MarketDirection.BEARISH
        for e in events
    )


# --- Reported staircase floor only ratchets on close-confirmed breaks ----------
# Real AAVEUSDT H1 window (2026-06-05 .. 06-27), extending the fixture above.
# The bullish leg tops at 77.70 (06-15 16:00 advance, close-confirmed). The
# leg's only later break of that top is a single-candle *wick* to 77.94
# (06-17 02:00, close 76.94) -- no candle ever closes above 77.70, but the
# advance still ratchets the reported floor tracker to 77.94, and the wick then
# survives the provisional bearish CHoCH (06-23) via the failed-CHoCH staircase
# restore (06-24 restores the reported tracker from the gate). The breakout BOS
# (06-26 06:00, px 87.99) therefore reports the swept wick 77.94 as the formed
# level it broke instead of the close-confirmed 77.70 top. With
# `bos_floor_require_close_break` on, a wick-only sweep of the reported floor
# neither ratchets it nor gets reinjected by the restore (the tracker restores
# from its own stash), so the BOS references 77.70.
_AAVE_1H_FLOOR_WINDOW_DATA = (
    Path(__file__).parent / "data" / "aaveusdt_1h_2026_06_05_27.json"
)


def _load_aave_1h_floor_window_candles() -> list[Candle]:
    rows = json.loads(_AAVE_1H_FLOOR_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="AAVEUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _aave_1h_floor_detector(floor_require_close_break: bool) -> InternalStructureDetector:
    """Production H1 wiring, close-break floor-ratchet flag injectable."""
    return InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_atr=3.0,
        choch_weak_ref_persistence_candles=4,
        bos_leg_origin_min_pullback_atr=1.5,
        bos_leg_origin_require_close_break=True,
        bos_floor_require_close_break=floor_require_close_break,
    )


def _breakout_bos(events: list[MarketStructure]) -> MarketStructure:
    [bos] = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 26, 6, tzinfo=UTC)
    ]
    return bos


def test_breakout_bos_references_wick_swept_top_when_off() -> None:
    """With the flag off, the 06-17 wick (77.94, never closed above the 77.70
    top) ratchets the reported floor and survives the failed-CHoCH restore, so
    the 06-26 breakout BOS reports the wick as the level it broke."""
    events = _aave_1h_floor_detector(floor_require_close_break=False).detect(
        _load_aave_1h_floor_window_candles()
    )

    assert _breakout_bos(events).reference_price_level == 77.94


def test_breakout_bos_references_close_confirmed_top_when_on() -> None:
    """On, a wick-only sweep of the reported floor does not ratchet it (and the
    failed-CHoCH restore uses the tracker's own stash, not the wick-ratcheted
    gate), so the 06-26 breakout BOS references the close-confirmed 77.70 top.
    The rest of the staircase is untouched (the 06-26 13:00 continuation still
    references the 87.99 breakout pivot)."""
    events = _aave_1h_floor_detector(floor_require_close_break=True).detect(
        _load_aave_1h_floor_window_candles()
    )

    assert _breakout_bos(events).reference_price_level == 77.70
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 26, 13, tzinfo=UTC)
        and e.reference_price_level == 87.99
        for e in events
    )


# --- Candidate-continuation promotion is weak on a wick-only staircase break ---
# Real AAVEUSDT H1, the full production internal-detector window (structural
# anchor 2026-05-10 20:00 .. 07-04; the release-gap/min-pullback guards use the
# series-wide mean true range, so a truncated window would not reproduce the
# production state). Same wick as the fixtures above, but here the wick-leg BOS
# never *emits* (window-dependent state), so the CHoCH reference comes from the
# OTHER promotion path: the 06-17 02:00 state-advance (close above the trailing
# 76.35; pivot wick 77.94 over the unbroken 77.70 staircase floor) promotes the
# candidate pullback 72.25 via the continuation-gated step. With the flag off
# that promotion is structural even though nothing ever closed above 77.70 --
# so a premature bearish CHoCH fires 06-18 at base persistence and fails 06-20,
# then a second whipsaw pair follows (06-23 05:00 at a degraded 74.45 ref,
# failed 06-24 20:00). With the flag on, a wick-only staircase break promotes
# the candidate as *weak*: both premature CHoCHs demote to sweeps, the one
# honest bearish CHoCH fires 06-23 15:00 against 72.25 via the barrier (price
# genuinely sustained below), fails honestly 06-24 10:00 when the rally
# invalidates it -- and the breakout BOS confirms 06-24 22:00, on the first
# close above the wick-swept 77.70 top, ~30h before the old 06-26 mark.
_AAVE_1H_PRODUCTION_WINDOW_DATA = (
    Path(__file__).parent / "data" / "aaveusdt_1h_2026_05_10_07_04.json"
)


def _load_aave_1h_production_window_candles() -> list[Candle]:
    rows = json.loads(_AAVE_1H_PRODUCTION_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="AAVEUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _aave_1h_production_detector(require_close_break: bool) -> InternalStructureDetector:
    """Full production H1 wiring, close-break-structural flag injectable."""
    return InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        confluence_filter=True,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_pct=0.04,
        bos_leg_origin_release_gap_atr=3.0,
        choch_weak_ref_persistence_candles=4,
        bos_leg_origin_min_pullback_atr=1.5,
        bos_leg_origin_require_close_break=require_close_break,
        bos_floor_require_close_break=True,
    )


def test_wick_advance_candidate_promotion_fires_premature_chochs_when_off() -> None:
    """Off: the candidate promoted at the wick advance is structural, so two
    premature bearish CHoCH/CHOCH_FAILED whipsaw pairs print inside the window
    where nothing ever closed above the 77.70 top."""
    events = _aave_1h_production_detector(require_close_break=False).detect(
        _load_aave_1h_production_window_candles()
    )

    bear = _bear_chochs(events)
    in_window = [
        e
        for e in bear
        if datetime(2026, 6, 17, tzinfo=UTC) <= e.timestamp <= datetime(2026, 6, 25, tzinfo=UTC)
    ]
    assert [(e.timestamp, e.reference_price_level) for e in in_window] == [
        (datetime(2026, 6, 18, 15, tzinfo=UTC), 72.25),
        (datetime(2026, 6, 23, 5, tzinfo=UTC), 74.45),
    ]
    # The premature CHoCH fired at base persistence precisely because the
    # wick-advance promotion wrongly marked its reference structural.
    assert in_window[0].reference_structural is True
    # The breakout BOS only prints 06-26, referencing the close-confirmed top.
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 26, 6, tzinfo=UTC)
        and e.reference_price_level == 77.70
        for e in events
    )


def test_wick_advance_candidate_promotion_weak_defers_choch_when_on() -> None:
    """On: the wick-advance candidate promotion is weak, so the premature CHoCHs
    demote to sweeps; one honest bearish CHoCH fires via the barrier and fails
    honestly, and the breakout BOS confirms on the first close above 77.70."""
    events = _aave_1h_production_detector(require_close_break=True).detect(
        _load_aave_1h_production_window_candles()
    )

    bear = _bear_chochs(events)
    in_window = [
        e
        for e in bear
        if datetime(2026, 6, 17, tzinfo=UTC) <= e.timestamp <= datetime(2026, 6, 25, tzinfo=UTC)
    ]
    assert [(e.timestamp, e.reference_price_level) for e in in_window] == [
        (datetime(2026, 6, 23, 15, tzinfo=UTC), 72.25),
    ]
    # The surviving CHoCH is classified (and rendered) as a weak-reference one:
    # it fired via the new-cycle barrier, not the conservative sequence.
    assert in_window[0].reference_structural is False
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        and e.direction is MarketDirection.BEARISH
        and e.timestamp == datetime(2026, 6, 24, 10, tzinfo=UTC)
        for e in events
    )
    # The breakout BOS confirms at the first close above the wick-swept top.
    assert any(
        e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 24, 22, tzinfo=UTC)
        and e.reference_price_level == 77.70
        for e in events
    )


def _provisional_base_series() -> list[Candle]:
    """Bearish BOS at index 7 (low 90, broke ref 100, confirmed by the LH at
    index 9), then a live-edge tail: a bounce (index 11) and a final candle
    (index 12) whose *close* (86) drops below the BOS floor (90) but which
    cannot yet be a confirmed swing low (no candle follows it)."""
    highs = [150.0, 200.0, 150.0, 150.0, 150.0, 200.0, 150.0, 150.0, 150.0, 180.0, 150.0]
    lows = [140.0, 140.0, 140.0, 100.0, 140.0, 140.0, 140.0, 90.0, 140.0, 140.0, 140.0]
    candles = make_series(highs, lows)
    candles[7] = make_candle(7, 150.0, 90.0, close=95.0)
    candles.append(make_candle(11, 150.0, 140.0, close=145.0))
    candles.append(make_candle(12, 88.0, 85.0, close=86.0))
    return candles


def test_provisional_bos_emitted_on_live_edge_floor_close_break() -> None:
    # The continuation closed below the staircase floor (90) but its confirming
    # pivots have not formed yet, so under `emit_provisional_bos` a single
    # provisional BOS marks the forming continuation at the floor.
    candles = _provisional_base_series()

    events = InternalStructureDetector(
        swing_lookback=1, confluence_filter=False, emit_provisional_bos=True
    ).detect(candles)

    provisional = [e for e in events if e.provisional]
    assert len(provisional) == 1
    prov = provisional[0]
    assert prov.event is StructureEvent.BREAK_OF_STRUCTURE
    assert prov.direction is MarketDirection.BEARISH
    assert prov.reference_price_level == 90.0  # the staircase floor it broke
    assert prov.price_level == 85.0  # the live leg's extreme low
    assert prov.timestamp == candles[12].timestamp  # first close below the floor
    assert prov.reference_timestamp == candles[7].timestamp  # the floor's origin


def test_provisional_bos_is_purely_additive() -> None:
    # With the flag off the output is byte-for-byte the confirmed stream; the
    # flag only *adds* provisional marks, never changes the confirmed events.
    candles = _provisional_base_series()

    off = InternalStructureDetector(
        swing_lookback=1, confluence_filter=False, emit_provisional_bos=False
    ).detect(candles)
    on = InternalStructureDetector(
        swing_lookback=1, confluence_filter=False, emit_provisional_bos=True
    ).detect(candles)

    assert not any(e.provisional for e in off)
    assert [e.model_dump() for e in off] == [e.model_dump() for e in on if not e.provisional]


def test_provisional_bos_not_emitted_on_choch_seed_level() -> None:
    # A fresh CHoCH seeds the new leg's staircase floor with the CHoCH's own
    # level, which price has necessarily already closed beyond (that is what
    # confirmed the CHoCH). A provisional BOS there would just double the CHoCH
    # line, so the continuation gate suppresses it: no provisional until a real
    # BOS steps the leg beyond that seed.
    candles = make_series(_TIEBREAK_HIGH_HIGHS, _TIEBREAK_HIGH_LOWS)
    candles[5] = make_candle(5, 150.0, 80.0, close=90.0)
    candles[11] = make_candle(11, 150.0, 60.0, close=70.0)
    candles[15] = make_candle(15, 195.0, 140.0, close=194.0)  # bullish CHoCH (ref 190)
    candles[16] = make_candle(16, 194.0, 191.0, close=193.0)  # closes above the seed

    events = InternalStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False, emit_provisional_bos=True
    ).detect(candles)

    assert any(e.event is StructureEvent.CHANGE_OF_CHARACTER for e in events)
    assert not any(e.provisional for e in events)


# --- CHoCH origin = deepest leg extreme, not the trailing reference -----------
# Real NEARUSDT M5 window (2026-07-04 00:00 .. 07-05 07:00). A bullish CHoCH
# confirms 07-04 14:10 (a genuine +4% reversal to 2.039). The prior bearish
# leg's true fundo is 1.967, but the trailing `active_low` had ratcheted UP
# through the reversal rally's higher-lows to 2.004 (a HL near the new top).
# With `choch_origin_leg_extreme` off, the CHoCH origin is that 2.004 trailing
# low, so the first minor pullback below it "fails" the CHoCH (CHOCH_FAILED @
# 2.004) and the trend ping-pongs into a weak CHoCH* and a second failure --
# and the genuine CHoCH line never terminates (no opposite CHoCH), stretching
# across the chart. On, the origin is the deepest leg low (1.967), so the CHoCH
# holds through the pullbacks and fails once, honestly, when price finally
# breaks the true fundo.
_NEAR_5M_ORIGIN_WINDOW_DATA = (
    Path(__file__).parent / "data" / "nearusdt_5m_2026_07_04_05.json"
)


def _load_near_5m_origin_window_candles() -> list[Candle]:
    rows = json.loads(_NEAR_5M_ORIGIN_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="NEARUSDT",
            timeframe=TimeFrame.M5,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _near_5m_origin_detector(choch_origin_leg_extreme: bool) -> InternalStructureDetector:
    # M5 production base params; the origin flag is what this test toggles.
    return InternalStructureDetector(
        swing_lookback=6,
        persistence_candles=4,
        choch_origin_leg_extreme=choch_origin_leg_extreme,
    )


def _bull_choch_at(events: list[MarketStructure], when: datetime) -> MarketStructure:
    [choch] = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == when
    ]
    return choch


def test_choch_origin_trailing_low_ping_pongs_when_off() -> None:
    """With the flag off, the bullish CHoCH origin is the ratcheted trailing low
    (2.004), so it fails immediately (CHOCH_FAILED @ 2.004) and the trend
    ping-pongs: a weak CHoCH* and a second failure appear, and no single clean
    failure resolves the leg."""
    events = _near_5m_origin_detector(choch_origin_leg_extreme=False).detect(
        _load_near_5m_origin_window_candles()
    )

    # The genuine bullish reversal still fires.
    assert _bull_choch_at(events, datetime(2026, 7, 4, 14, 10, tzinfo=UTC))
    # It fails at the ratcheted trailing origin 2.004, immediately.
    failed = [e for e in events if e.event is StructureEvent.CHOCH_FAILED]
    assert any(e.reference_price_level == 2.004 for e in failed)
    # Ping-pong: a second weak bullish CHoCH forms on top and also fails.
    bull_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp >= datetime(2026, 7, 4, 14, 10, tzinfo=UTC)
    ]
    assert len(bull_chochs) >= 2
    assert len(failed) >= 2


def test_choch_origin_leg_extreme_holds_and_fails_once_when_on() -> None:
    """On, the origin is the deepest leg low (1.967): the CHoCH holds through the
    shallow pullbacks and fails exactly once, when price breaks the true fundo --
    so the reversal line terminates cleanly and the weak-CHoCH ping-pong is
    gone."""
    events = _near_5m_origin_detector(choch_origin_leg_extreme=True).detect(
        _load_near_5m_origin_window_candles()
    )

    # The same genuine bullish reversal.
    assert _bull_choch_at(events, datetime(2026, 7, 4, 14, 10, tzinfo=UTC))
    # It is the ONLY bullish CHoCH in the window (no weak CHoCH* ping-pong).
    bull_chochs = [
        e
        for e in events
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp >= datetime(2026, 7, 4, 14, 10, tzinfo=UTC)
    ]
    assert len(bull_chochs) == 1
    # Exactly one failure, at the true fundo 1.967 (not the ratcheted 2.004).
    failed = [e for e in events if e.event is StructureEvent.CHOCH_FAILED]
    assert len(failed) == 1
    assert failed[0].reference_price_level == 1.967
    assert not any(e.reference_price_level == 2.004 for e in failed)


_SOL_15M_PROV_WINDOW_DATA = (
    Path(__file__).parent / "data" / "solusdt_15m_2026_06_30_07_06.json"
)


def _load_sol_15m_prov_candles() -> list[Candle]:
    rows = json.loads(_SOL_15M_PROV_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _sol_15m_prov_detector(emit_provisional_choch: bool) -> InternalStructureDetector:
    # The production leg-origin flags build the structural CHoCH reference the
    # provisional emission gates on; only `emit_provisional_choch` is toggled.
    return InternalStructureDetector(
        swing_lookback=6,
        persistence_candles=2,
        confluence_filter=False,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_require_close_break=True,
        bos_floor_require_close_break=True,
        choch_origin_leg_extreme=True,
        emit_provisional_choch=emit_provisional_choch,
    )


def test_provisional_choch_off_is_byte_for_byte_identical() -> None:
    """With the flag off, no provisional CHoCH is emitted: the SOL M15 live-edge
    close-break below the 80.72 leg-origin reference stays invisible (the fundo
    is too fresh to be a confirmed swing-low pivot), exactly as before."""
    candles = _load_sol_15m_prov_candles()
    events = _sol_15m_prov_detector(emit_provisional_choch=False).detect(candles)
    assert not any(e.provisional for e in events)


def test_provisional_choch_marks_forming_live_edge_reversal() -> None:
    """On, a single provisional (dimmed) bearish CHoCH is appended at the live
    edge: price sustained a close-break below the structural 80.72 reference (the
    bullish BOS's leg origin) for `persistence_candles` closes, but its confirming
    swing-low pivot has not formed yet. The emission is purely additive -- every
    non-provisional event is byte-for-byte identical to the flag-off run."""
    candles = _load_sol_15m_prov_candles()
    off = _sol_15m_prov_detector(emit_provisional_choch=False).detect(candles)
    on = _sol_15m_prov_detector(emit_provisional_choch=True).detect(candles)

    provisional = [e for e in on if e.provisional]
    assert len(provisional) == 1
    prov = provisional[0]
    assert prov.event is StructureEvent.CHANGE_OF_CHARACTER
    assert prov.direction is MarketDirection.BEARISH
    # Anchored at the structural leg-origin reference it broke (the 80.72 fundo
    # the bullish BOS launched from), flagged structural, not weak.
    assert prov.reference_price_level == 80.72
    assert prov.reference_structural is True
    assert prov.timestamp == datetime(2026, 7, 6, 3, 15, tzinfo=UTC)

    # Purely additive: dropping the provisional recovers the flag-off stream.
    non_provisional = [e for e in on if not e.provisional]
    assert [(e.timestamp, e.event, e.price_level) for e in non_provisional] == [
        (e.timestamp, e.event, e.price_level) for e in off
    ]


# The SOL M15 stale-CHoCH case: a bearish CHoCH fires 07-06 11:45 at 80.72, is
# confirmed by a continuation BOS that is *wick-only* (dropped from the chart by
# the composition re-anchor, so its line looks unbroken), then price reclaims the
# 80.72 level and ranges above it for over a day -- yet the CHoCH never fails,
# because the closes never clear the far 82.3 leg origin the normal CHOCH_FAILED
# checks. The fast-fizzle marker disregards it: price reclaimed 80.72 (a sustained
# close) 9 candles after the CHoCH, well inside the window.
_SOL_15M_FIZZLE_WINDOW_DATA = (
    Path(__file__).parent / "data" / "solusdt_15m_2026_06_24_07_07.json"
)


def _load_sol_15m_fizzle_candles() -> list[Candle]:
    rows = json.loads(_SOL_15M_FIZZLE_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="SOLUSDT",
            timeframe=TimeFrame.M15,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _sol_15m_fizzle_detector(
    choch_fizzle_reclaim_candles: int | None,
) -> InternalStructureDetector:
    # Production M15 params + the leg-origin machinery that builds the standing
    # bearish CHoCH at 80.72; only `choch_fizzle_reclaim_candles` is toggled.
    return InternalStructureDetector(
        swing_lookback=5,
        persistence_candles=12,
        confluence_filter=False,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_require_close_break=True,
        bos_floor_require_close_break=True,
        choch_origin_leg_extreme=True,
        choch_fizzle_reclaim_candles=choch_fizzle_reclaim_candles,
    )


def test_choch_fizzle_off_is_byte_for_byte_identical() -> None:
    """With the flag off, no fizzle marker is emitted: the standing bearish CHoCH
    at 80.72 hangs unfailed (its closes never cleared the far 82.3 leg origin),
    exactly as before."""
    events = _sol_15m_fizzle_detector(None).detect(_load_sol_15m_fizzle_candles())
    assert not any(e.event is StructureEvent.CHOCH_FAILED for e in events)


def test_choch_fizzle_marks_reclaimed_standing_choch() -> None:
    """On, a single additive CHOCH_FAILED disregards the standing bearish CHoCH:
    price reclaimed its own broken level (80.72, a sustained close) within the
    window, so the reversal fizzled. The marker is same-direction and fires after
    the CHoCH, so the frontend pairs it and terminates the stale line -- and it is
    purely additive: dropping it recovers the flag-off stream (the state-machine
    trend never flipped)."""
    candles = _load_sol_15m_fizzle_candles()
    off = _sol_15m_fizzle_detector(None).detect(candles)
    on = _sol_15m_fizzle_detector(30).detect(candles)

    markers = [e for e in on if e.event is StructureEvent.CHOCH_FAILED]
    assert len(markers) == 1
    marker = markers[0]
    assert marker.direction is MarketDirection.BEARISH
    assert marker.reference_price_level == 80.72
    assert marker.timestamp == datetime(2026, 7, 6, 15, 15, tzinfo=UTC)
    # Flagged provisional so the hunt/narrative replay skips it: the state-machine
    # trend never flipped, so those readings must not flip either (the marker is
    # purely a chart-line disregard, not a structural reversal).
    assert marker.provisional is True
    # It pairs with the standing bearish CHoCH it disregards (a bearish CHoCH at
    # 80.72 earlier, with no intervening same-direction CHoCH).
    standing = [
        e
        for e in on
        if e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BEARISH
        and e.reference_price_level == 80.72
        and e.timestamp < marker.timestamp
    ]
    assert standing

    # Purely additive: dropping the marker recovers the flag-off stream.
    assert [
        (e.timestamp, e.event, e.price_level)
        for e in on
        if e.event is not StructureEvent.CHOCH_FAILED
    ] == [(e.timestamp, e.event, e.price_level) for e in off]


def test_choch_fizzle_ignores_reclaim_after_window() -> None:
    """A reclaim *after* the window is genuine follow-through, not a fizzle. The
    80.72 reclaim lands 9 candles after the CHoCH, so a window of 8 leaves it
    unmarked while 30 marks it -- the window gate on real data."""
    candles = _load_sol_15m_fizzle_candles()
    assert not any(
        e.event is StructureEvent.CHOCH_FAILED
        for e in _sol_15m_fizzle_detector(8).detect(candles)
    )
    assert any(
        e.event is StructureEvent.CHOCH_FAILED
        for e in _sol_15m_fizzle_detector(9).detect(candles)
    )


def test_choch_fizzle_reclaim_candles_must_be_positive() -> None:
    with pytest.raises(ValueError, match="choch_fizzle_reclaim_candles must be at least 1"):
        InternalStructureDetector(choch_fizzle_reclaim_candles=0)


def test_final_trend_exposes_state_machine_trend() -> None:
    detector = InternalStructureDetector(swing_lookback=1, confluence_filter=False)
    # Read through annotated locals: asserting on the attribute directly would
    # let mypy's literal narrowing persist across `detect()` (which it cannot
    # see mutating the attribute) and flag the second assert as non-overlapping.
    trend_before: MarketDirection = detector.final_trend
    assert trend_before is MarketDirection.NEUTRAL

    candles = make_series(HIGHS, LOWS)
    candles[5] = make_candle(5, 220.0, 140.0, close=205.0)
    candles[13] = make_candle(13, 230.0, 140.0, close=215.0)
    detector.detect(candles)

    # The bullish state advance at index 5 sets the standing trend; the later
    # bearish LIQUIDITY_SWEEPs (indexes 11/15/19) are wick events and do not
    # flip it -- `final_trend` mirrors the state machine, not the last event's
    # direction.
    trend_after: MarketDirection = detector.final_trend
    assert trend_after is MarketDirection.BULLISH


# --- Real-data regression: failed-CHoCH whipsaw eating a crash's BOS staircase
_BTC_1H_CRASH_WINDOW_DATA = Path(__file__).parent / "data" / "btcusdt_1h_2026_05_18_07_04.json"


def _load_btc_1h_crash_window_candles() -> list[Candle]:
    rows = json.loads(_BTC_1H_CRASH_WINDOW_DATA.read_text())
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, open_, high, low, close in rows
    ]


def _btc_1h_crash_detector(new_flags: bool) -> InternalStructureDetector:
    """Full production H1 wiring; the two failed-CHoCH flags injectable."""
    return InternalStructureDetector(
        swing_lookback=4,
        persistence_candles=2,
        confluence_filter=True,
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        reanchor_chain_establish_only=True,
        reanchor_min_price_gap_pct=0.003,
        stale_reanchor_candles=80,
        impulse_bos_displacement_pct=0.015,
        bos_pullback_max_wick_pct=0.4,
        stage_wick_rejected_bos=True,
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_pct=0.04,
        bos_leg_origin_release_gap_atr=3.0,
        choch_weak_ref_persistence_candles=4,
        bos_leg_origin_min_pullback_atr=1.5,
        bos_leg_origin_require_close_break=True,
        bos_floor_require_close_break=True,
        choch_origin_leg_extreme=True,
        choch_failed_fallback_suppress_candles=20 if new_flags else None,
        stage_choch_failed_window_bos=new_flags,
    )


def test_failed_choch_whipsaw_eats_crash_bos_staircase_when_off() -> None:
    """Real BTCUSDT H1 regression (2026-06-18..25 crash): two weak bullish
    CHoCHs flip the trend mid-crash, so the whole 62232 -> 58030 decline
    prints a single bearish BOS and then only sweeps.

    The second flip (06-25 04:00) is the pathological one: it fires via the
    cold-start `active_high` fallback one day after the previous bullish CHoCH
    failed -- a failed-CHoCH flip arms no origin (one-shot), so the fallback
    suppression that governs unconfirmed-CHoCH windows lapses at the failure
    and a 4-candle bounce re-flips the trend, eating the final flush's BOS.
    """
    events = _btc_1h_crash_detector(new_flags=False).detect(
        _load_btc_1h_crash_window_candles()
    )

    window_start = datetime(2026, 6, 19, tzinfo=UTC)
    window_end = datetime(2026, 7, 1, tzinfo=UTC)
    bear_bos = [
        e
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and window_start <= e.timestamp <= window_end
    ]
    assert bear_bos == []

    # The post-failure fallback CHoCH that ate the flush.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 25, 4, tzinfo=UTC)
        for e in events
    )
    # Both whipsaw flips end in failure.
    failed = [
        e
        for e in events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.timestamp >= datetime(2026, 6, 17, tzinfo=UTC)
    ]
    assert [e.timestamp for e in failed] == [
        datetime(2026, 6, 24, 13, tzinfo=UTC),
        datetime(2026, 6, 30, 12, tzinfo=UTC),
    ]


def test_failed_choch_window_bos_staged_and_fallback_suppressed_when_on() -> None:
    """On: the retro-staging resurrects the continuation BOS the failed CHoCH's
    window ate (61870 breaking 62232, the failure pivot 59060 breaking 61870),
    and the post-failure fallback suppression turns the 06-25 04:00 whipsaw
    CHoCH into a sweep -- so the final flush to 58030 prints as a bearish BOS
    and the second CHOCH_FAILED never happens. The genuine recovery CHoCH
    still fires (07-02, via the staleness re-anchor once the reversal is real).
    """
    events = _btc_1h_crash_detector(new_flags=True).detect(
        _load_btc_1h_crash_window_candles()
    )

    bear_bos = [
        (e.timestamp, e.price_level, e.reference_price_level)
        for e in events
        if e.event is StructureEvent.BREAK_OF_STRUCTURE
        and e.direction is MarketDirection.BEARISH
        and datetime(2026, 6, 19, tzinfo=UTC) <= e.timestamp <= datetime(2026, 7, 1, tzinfo=UTC)
    ]
    assert bear_bos == [
        (datetime(2026, 6, 23, 8, tzinfo=UTC), 61870.0, 62232.1),
        (datetime(2026, 6, 24, 13, tzinfo=UTC), 59060.0, 61870.0),
        (datetime(2026, 6, 25, 13, tzinfo=UTC), 58030.0, 59060.0),
    ]

    # The fallback whipsaw is suppressed: its bounce is a mere sweep now.
    assert not any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and datetime(2026, 6, 23, tzinfo=UTC) <= e.timestamp < datetime(2026, 7, 1, tzinfo=UTC)
        for e in events
    )
    assert any(
        e.event is StructureEvent.LIQUIDITY_SWEEP
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 6, 25, 4, tzinfo=UTC)
        for e in events
    )
    # One honest failure remains; the phantom second one is gone.
    failed = [
        e
        for e in events
        if e.event is StructureEvent.CHOCH_FAILED
        and e.timestamp >= datetime(2026, 6, 17, tzinfo=UTC)
    ]
    assert [e.timestamp for e in failed] == [datetime(2026, 6, 24, 13, tzinfo=UTC)]
    # The genuine recovery reversal still prints.
    assert any(
        e.event is StructureEvent.CHANGE_OF_CHARACTER
        and e.direction is MarketDirection.BULLISH
        and e.timestamp == datetime(2026, 7, 2, 9, tzinfo=UTC)
        for e in events
    )


def test_choch_failed_fallback_suppress_candles_must_be_positive() -> None:
    with pytest.raises(
        ValueError, match="choch_failed_fallback_suppress_candles must be at least 1"
    ):
        InternalStructureDetector(choch_failed_fallback_suppress_candles=0)
