"""Tests for `liquidity.structural_stall.is_stall_eligible_leg` (Etapa 2.5).

The gate answers one question -- "was the standing leg born of a measured
expansion?" -- and the tests that matter are the ones about *whose* leg it is:
eligibility must never leak from an expansion into the leg that follows it.
"""

import pytest

from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_MIN_EXPANSION_ATR,
    DEFAULT_MIN_EXPANSION_BOS,
    frozen_atr_pct,
    is_stall_eligible_leg,
)
from liquidity_hunter.tests.liquidity.test_structural_stall import (
    BEARISH,
    BULLISH,
    bos,
    event,
    series,
)

STEP = 0.5


def rising(bars: int = 400) -> list[Candle]:
    return series([100.0 + STEP * index for index in range(bars)])


def falling(bars: int = 400) -> list[Candle]:
    return series([300.0 - STEP * index for index in range(bars)])


def price_for(
    candles: list[Candle], index: int, start: float, atr_units: float, *, up: bool
) -> float:
    """The BOS level that puts the run exactly `atr_units` from `start`."""
    move = start * frozen_atr_pct(candles, index) * atr_units
    return start + move if up else start - move


def test_defaults_are_the_etapa_05_definition() -> None:
    assert DEFAULT_MIN_EXPANSION_BOS == 3
    assert DEFAULT_MIN_EXPANSION_ATR == 15.0


# --- the run ---------------------------------------------------------------


def test_fewer_than_three_bos_is_not_an_expansion() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 40.0, up=True)
    events = [bos(50, BULLISH, 100.0), bos(200, BULLISH, far)]
    assert is_stall_eligible_leg(candles, events) is False


def test_exactly_three_bos_with_enough_displacement_is_an_expansion() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        bos(200, BULLISH, far),
    ]
    assert is_stall_eligible_leg(candles, events) is True


def test_a_provisional_bos_neither_counts_nor_breaks_the_run() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    ghost = bos(150, BEARISH, 101.0, provisional=True)
    two_real = [bos(50, BULLISH, 100.0), ghost, bos(200, BULLISH, far)]
    assert is_stall_eligible_leg(candles, two_real) is False   # still only 2

    three_real = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        ghost,
        bos(200, BULLISH, far),
    ]
    assert is_stall_eligible_leg(candles, three_real) is True   # the ghost is ignored


def test_a_confirmed_choch_between_bos_breaks_the_run() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        event(150, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, 110.0),
        bos(200, BULLISH, far),
    ]
    assert is_stall_eligible_leg(candles, events) is False


def test_a_failed_choch_does_not_break_the_run() -> None:
    """The pipeline's own semantics: a failed CHoCH reaffirms the leg."""
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        event(150, StructureEvent.CHOCH_FAILED, BEARISH, 110.0),
        bos(200, BULLISH, far),
    ]
    # ...but it IS an advance, so it would have to be the leg opener -- here the
    # later BOS is, and the run survives it.
    assert is_stall_eligible_leg(candles, events) is True


def test_an_opposite_direction_bos_breaks_the_run() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BEARISH, 105.0),
        bos(200, BULLISH, far),
    ]
    assert is_stall_eligible_leg(candles, events) is False


# --- the displacement ------------------------------------------------------


def test_a_run_that_did_not_travel_far_enough_is_not_an_expansion() -> None:
    candles = rising()
    near = price_for(candles, 200, 100.0, 14.9, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + near) / 2),
        bos(200, BULLISH, near),
    ]
    assert is_stall_eligible_leg(candles, events) is False


def test_exactly_fifteen_atr_qualifies() -> None:
    candles = rising()
    exact = price_for(candles, 200, 100.0, DEFAULT_MIN_EXPANSION_ATR, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + exact) / 2),
        bos(200, BULLISH, exact),
    ]
    assert is_stall_eligible_leg(candles, events) is True


def test_a_bearish_expansion_qualifies() -> None:
    candles = falling()
    far = price_for(candles, 200, 300.0, 20.0, up=False)
    events = [
        bos(50, BEARISH, 300.0),
        bos(120, BEARISH, (300.0 + far) / 2),
        bos(200, BEARISH, far),
    ]
    assert is_stall_eligible_leg(candles, events) is True


# --- whose leg it is -------------------------------------------------------


def test_a_leg_opened_after_a_choch_does_not_inherit_the_expansion() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 30.0, up=True)
    expansion = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        bos(200, BULLISH, far),
    ]
    assert is_stall_eligible_leg(candles, expansion) is True
    reversed_leg = [
        *expansion,
        event(250, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, far * 0.99),
        bos(300, BEARISH, far * 0.98),
    ]
    assert is_stall_eligible_leg(candles, reversed_leg) is False


def test_an_old_expansion_does_not_contaminate_an_unrelated_later_leg() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 30.0, up=True)
    events = [
        bos(20, BULLISH, 100.0),
        bos(60, BULLISH, (100.0 + far) / 2),
        bos(100, BULLISH, far),
        event(150, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, far * 0.99),
        bos(200, BEARISH, far * 0.98),
        event(250, StructureEvent.CHANGE_OF_CHARACTER, BULLISH, far),
        bos(300, BULLISH, far * 1.01),      # a single BOS: a run of one
    ]
    assert is_stall_eligible_leg(candles, events) is False


def test_a_leg_whose_opener_is_a_choch_is_never_eligible() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 30.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        bos(200, BULLISH, far),
        event(250, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, far * 0.99),
    ]
    assert is_stall_eligible_leg(candles, events) is False


# --- causality -------------------------------------------------------------


def test_the_gate_does_not_read_a_single_candle_past_the_opener() -> None:
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    events = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        bos(200, BULLISH, far),
    ]
    full = is_stall_eligible_leg(candles, events)
    truncated = is_stall_eligible_leg(candles[:201], events)
    assert full is truncated is True


def test_a_later_event_cannot_change_an_earlier_verdict() -> None:
    """Truncating the stream at the opener gives the same answer as the full one."""
    candles = rising()
    far = price_for(candles, 200, 100.0, 20.0, up=True)
    expansion = [
        bos(50, BULLISH, 100.0),
        bos(120, BULLISH, (100.0 + far) / 2),
        bos(200, BULLISH, far),
    ]
    live = is_stall_eligible_leg(candles[:201], expansion)
    with_future = is_stall_eligible_leg(
        candles[:201],
        [*expansion, event(300, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, far)],
    )
    assert live is with_future is True


# --- degenerate input ------------------------------------------------------


def test_no_advance_is_not_eligible() -> None:
    assert is_stall_eligible_leg(rising(), []) is False
    assert is_stall_eligible_leg([], []) is False


def test_negative_parameters_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        is_stall_eligible_leg(rising(), [], min_bos=-1)


def test_an_advance_outside_the_series_is_ignored() -> None:
    assert is_stall_eligible_leg(rising(10), [bos(5_000, MarketDirection.BULLISH, 100.0)]) is False
