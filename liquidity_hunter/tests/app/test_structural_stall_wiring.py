"""`DashboardData.structural_stall`: the field, the flag, and what must not move.

The load-bearing test here is `test_the_event_stream_is_identical_on_and_off`:
the stall is descriptive, so turning it on may add a field and nothing else.
"""

from dataclasses import replace

import pytest

from liquidity_hunter.api.schemas import DashboardDataResponse
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from liquidity_hunter.tests.app.test_dashboard_data import _FAKE_FUTURES, _FakeProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle

ADVANCES = (
    StructureEvent.BREAK_OF_STRUCTURE,
    StructureEvent.CHANGE_OF_CHARACTER,
    StructureEvent.CHOCH_FAILED,
)


def staircase_then_fade(
    *,
    bullish: bool,
    fade: int,
    legs: int = 4,
    run: int = 12,
    pullback: int = 8,
    step: float = 5.0,
    fade_step: float = 0.3,
) -> list[Candle]:
    """A staircase of BOS, then a long quiet give-back.

    The leg's runs and pullbacks are longer than the production H1
    `swing_lookback` (5), so pivots actually form and the detector emits real
    BOS -- this exercises the whole pipeline, not a hand-built event list.
    """
    rows: list[tuple[float, float, float]] = []
    price = 100.0 if bullish else 300.0
    sign = 1 if bullish else -1
    for _ in range(legs):
        for _ in range(run):
            price += sign * step
            rows.append((price + 2, price - 2, price + sign * 1.5))
        for _ in range(pullback):
            price -= sign * 3
            rows.append((price + 2, price - 2, price - sign * 1.5))
    extreme = price
    for index in range(fade):
        level = extreme - sign * index * fade_step
        rows.append((level + 2, level - 2, level - sign * 1.5))
    return [
        make_candle(index, high, low, "BTCUSDT", close=close)
        for index, (high, low, close) in enumerate(rows)
    ]


def load(candles: list[Candle]) -> dd.DashboardData:
    return load_dashboard_data(
        provider=_FakeProvider(candles),
        symbol="BTCUSDT",
        futures_provider=_FAKE_FUTURES,
    )


@pytest.fixture
def stall_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dd, "_STRUCTURAL_STALL_ENABLED", True)


@pytest.fixture
def stall_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The off path, pinned explicitly.

    It used to ride on the module default. The default flipped on (2026-09-08,
    after the visual validation), so the tests that are *about* the off path
    now set it -- the behaviour they assert is unchanged, and the flag is still
    the way to turn the reading off.
    """
    monkeypatch.setattr(dd, "_STRUCTURAL_STALL_ENABLED", False)


# --- flag OFF --------------------------------------------------------------


def test_the_flag_is_on_by_default() -> None:
    assert dd._STRUCTURAL_STALL_ENABLED is True


def test_the_field_is_none_while_the_flag_is_off(stall_off: None) -> None:
    data = load(staircase_then_fade(bullish=False, fade=140))
    assert data.structural_stall is None


def test_the_function_is_not_even_called_while_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch, stall_off: None
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("detect_structural_stall ran with the flag off")

    monkeypatch.setattr(dd, "detect_structural_stall", explode)
    assert load(staircase_then_fade(bullish=False, fade=140)).structural_stall is None


# --- flag ON ---------------------------------------------------------------


def test_a_stalled_bearish_leg_is_reported(stall_on: None) -> None:
    candles = staircase_then_fade(bullish=False, fade=140)
    data = load(candles)
    stall = data.structural_stall
    assert stall is not None
    assert stall.direction is MarketDirection.BEARISH

    last_advance = [
        event
        for event in data.internal_structure_events
        if event.event in ADVANCES and not event.provisional
    ][-1]
    assert last_advance.event is StructureEvent.BREAK_OF_STRUCTURE
    assert stall.last_advance_timestamp == last_advance.timestamp
    assert stall.last_advance_price == last_advance.price_level

    index = {candle.timestamp: i for i, candle in enumerate(data.candles)}
    assert (
        stall.bars_since_advance
        == index[stall.stale_since] - index[stall.last_advance_timestamp]
    )
    assert stall.bars_since_advance >= 50
    assert stall.retracement_atr >= 6.0
    assert stall.frozen_atr_pct > 0
    # A bearish leg's extreme is its lowest close, and it is never later than
    # the moment the stall was declared.
    assert stall.leg_extreme_timestamp <= stall.stale_since


def test_a_stalled_bullish_leg_is_reported(stall_on: None) -> None:
    stall = load(staircase_then_fade(bullish=True, fade=140)).structural_stall
    assert stall is not None
    assert stall.direction is MarketDirection.BULLISH
    assert stall.bars_since_advance >= 50
    assert stall.retracement_atr >= 6.0


def test_a_leg_still_advancing_reports_nothing(stall_on: None) -> None:
    """Same structure, a give-back too short to qualify."""
    data = load(staircase_then_fade(bullish=False, fade=20))
    assert [
        e for e in data.internal_structure_events if e.event in ADVANCES and not e.provisional
    ]
    assert data.structural_stall is None


def test_a_new_advance_starts_a_new_leg(stall_on: None) -> None:
    """The stall belongs to the standing leg, never to a superseded one."""
    candles = staircase_then_fade(bullish=False, fade=140)
    before = load(candles).structural_stall
    assert before is not None

    # Extend with a fresh bearish impulse: whatever the machine now emits, the
    # old stall may not be presented as if it were still the standing one.
    extended = staircase_then_fade(bullish=False, fade=140, legs=5)
    after = load(extended).structural_stall
    if after is not None:
        assert after.last_advance_timestamp > before.last_advance_timestamp
        assert after.stale_since > before.stale_since


def test_the_snapshot_serializes_the_field(stall_on: None) -> None:
    data = load(staircase_then_fade(bullish=False, fade=140))
    payload = DashboardDataResponse.model_validate(data).model_dump(mode="json")
    assert set(payload["structural_stall"]) == {
        "stale_since",
        "direction",
        "last_advance_timestamp",
        "last_advance_price",
        "bars_since_advance",
        "retracement_atr",
        "frozen_atr_pct",
        "leg_extreme_price",
        "leg_extreme_timestamp",
    }
    assert payload["structural_stall"]["direction"] == "bearish"


def test_the_field_serializes_as_null_when_absent(stall_off: None) -> None:
    data = load(staircase_then_fade(bullish=False, fade=140))
    payload = DashboardDataResponse.model_validate(data).model_dump(mode="json")
    assert payload["structural_stall"] is None


# --- the invariant ---------------------------------------------------------


def test_the_event_stream_is_identical_on_and_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = staircase_then_fade(bullish=False, fade=140)
    monkeypatch.setattr(dd, "_STRUCTURAL_STALL_ENABLED", False)
    off = load(candles)
    monkeypatch.setattr(dd, "_STRUCTURAL_STALL_ENABLED", True)
    on = load(candles)

    assert on.internal_structure_events == off.internal_structure_events
    assert on.market_structure_events == off.market_structure_events
    assert on.higher_timeframe_direction == off.higher_timeframe_direction
    assert on.consolidation_ranges == off.consolidation_ranges
    assert on.candles == off.candles
    # ...and the whole snapshot, once the new field is set aside.
    assert replace(on, structural_stall=None) == off
    assert on.structural_stall is not None


def test_the_stall_reads_the_final_stream_the_chart_draws(stall_on: None) -> None:
    """The wiring must feed the composed events, not an intermediate stream."""
    data = load(staircase_then_fade(bullish=False, fade=140))
    assert data.structural_stall == detect_structural_stall(
        data.candles, data.internal_structure_events
    )
