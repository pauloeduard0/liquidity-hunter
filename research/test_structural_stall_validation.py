"""Testes das funcoes puras de `research/structural_stall_validation.py`.

Fora de `liquidity_hunter/tests` de proposito (a suite de regressao nao muda):

    poetry run pytest research/test_structural_stall_validation.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from research.structural_stall_validation import (
    _legs_of_run,
    trailing_atr,
    triggers_of_leg,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
BULL, BEAR = MarketDirection.BULLISH, MarketDirection.BEARISH


def ts(i: int) -> datetime:
    return START + timedelta(hours=i)


def candle(i: int, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts(i),
        open=close,
        high=high if high is not None else close * 1.01,
        low=low if low is not None else close * 0.99,
        close=close,
        volume=1.0,
        taker_buy_volume=0.5,
    )


def flat_then_drop(n: int, drop_from: int, per_bar: float) -> list[Candle]:
    """Sobe ate `drop_from`, depois cai `per_bar` por vela."""
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        if i > drop_from:
            price = max(price - per_bar, 10.0)   # o dominio exige preco > 0
        out.append(candle(i, price))
    return out


def ev(
    i: int,
    kind: StructureEvent,
    direction: MarketDirection,
    price: float = 100.0,
    *,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts(i),
        event=kind,
        direction=direction,
        price_level=price,
        reference_price_level=price - 1,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def bos(i: int, direction: MarketDirection, price: float = 100.0, **kw: bool) -> MarketStructure:
    return ev(i, StructureEvent.BREAK_OF_STRUCTURE, direction, price, **kw)


# --- ATR causal ------------------------------------------------------------


def test_trailing_atr_only_uses_past_candles() -> None:
    candles = [candle(i, 100.0) for i in range(10)]
    full = trailing_atr(candles)
    truncated = trailing_atr(candles[:6])
    assert full[:6] == truncated
    assert len(full) == len(candles)


# --- pernas ----------------------------------------------------------------


def test_a_leg_runs_from_its_bos_to_the_next_advance() -> None:
    candles = flat_then_drop(120, 10, 0.2)
    events = [bos(10, BULL), bos(60, BULL)]
    legs = _legs_of_run(events, candles)
    assert [(leg.start_index, leg.end_index) for leg in legs] == [(10, 60), (60, 119)]


def test_a_choch_closes_the_leg_but_does_not_open_one() -> None:
    candles = flat_then_drop(120, 10, 0.2)
    events = [bos(10, BULL), ev(50, StructureEvent.CHANGE_OF_CHARACTER, BEAR)]
    legs = _legs_of_run(events, candles)
    assert [(leg.start_index, leg.end_index) for leg in legs] == [(10, 50)]


def test_a_provisional_bos_opens_no_leg() -> None:
    candles = flat_then_drop(80, 5, 0.2)
    legs = _legs_of_run([bos(10, BULL, provisional=True)], candles)
    assert legs == []


# --- trigger ---------------------------------------------------------------


def _leg(candles: list[Candle], events: list[MarketStructure]) -> object:
    legs = _legs_of_run(events, candles)
    assert legs
    return legs[0]


def test_the_trigger_is_the_first_candle_where_both_conditions_hold() -> None:
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL)]
    leg = _leg(candles, events)
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    # Nunca antes de N, e nao mais tarde que o necessario.
    assert trigger.bars_since_advance >= 20
    earlier = leg.path[trigger.bars_since_advance - 1]
    assert not (earlier[0] >= 20 and earlier[2] >= 2.0)


def test_no_trigger_when_the_leg_never_retraces_enough() -> None:
    candles = [candle(i, 100.0) for i in range(200)]
    events = [bos(10, BULL)]
    leg = _leg(candles, events)
    assert (
        triggers_of_leg(
            leg, events, candles, n=20, k=5.0, price_mode="close", atr_mode="frozen"
        )
        is None
    )


def test_no_trigger_before_n_bars_however_deep_the_retracement() -> None:
    candles = flat_then_drop(200, 10, 5.0)
    events = [bos(10, BULL)]
    leg = _leg(candles, events)
    trigger = triggers_of_leg(
        leg, events, candles, n=50, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.bars_since_advance == 50


def test_the_trigger_does_not_look_at_the_future() -> None:
    """Truncar a serie logo apos o disparo nao pode mudar o disparo."""
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL)]
    leg = _leg(candles, events)
    full = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert full is not None
    cut = leg.start_index + full.bars_since_advance + 1
    short_candles = candles[:cut]
    short_leg = _leg(short_candles, events)
    short = triggers_of_leg(
        short_leg, events, short_candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert short is not None
    assert short.timestamp == full.timestamp
    assert short.bars_since_advance == full.bars_since_advance
    assert round(short.retracement_atr, 6) == round(full.retracement_atr, 6)


def test_wick_reading_triggers_no_later_than_close_reading() -> None:
    candles = flat_then_drop(200, 10, 0.4)
    events = [bos(10, BULL)]
    leg = _leg(candles, events)
    wick = triggers_of_leg(
        leg, events, candles, n=20, k=3.0, price_mode="wick", atr_mode="frozen"
    )
    close = triggers_of_leg(
        leg, events, candles, n=20, k=3.0, price_mode="close", atr_mode="frozen"
    )
    assert wick is not None and close is not None
    assert wick.bars_since_advance <= close.bars_since_advance


# --- desfecho (avaliacao) --------------------------------------------------


def test_a_later_same_direction_bos_is_a_resumption() -> None:
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL), bos(150, BULL)]
    leg = _legs_of_run(events, candles)[0]
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.outcome == "resumed"
    assert trigger.bars_to_outcome == 150 - (leg.start_index + trigger.bars_since_advance)


def test_a_later_opposite_bos_is_a_reversal() -> None:
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL), bos(150, BEAR)]
    leg = _legs_of_run(events, candles)[0]
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.outcome == "reversed"


def test_no_later_bos_leaves_the_outcome_open() -> None:
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL)]
    leg = _legs_of_run(events, candles)[0]
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.outcome == "open"
    assert trigger.bars_to_outcome is None


def test_a_provisional_bos_does_not_count_as_resumption() -> None:
    candles = flat_then_drop(200, 10, 0.5)
    events = [bos(10, BULL), bos(150, BULL, provisional=True)]
    leg = _legs_of_run(events, candles)[0]
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.outcome == "open"


def test_a_bearish_leg_measures_the_retracement_upward() -> None:
    candles = [candle(i, 100.0 - min(i, 10) * 2 + max(i - 10, 0) * 0.5) for i in range(200)]
    events = [bos(5, BEAR, 90.0)]
    leg = _legs_of_run(events, candles)[0]
    trigger = triggers_of_leg(
        leg, events, candles, n=20, k=2.0, price_mode="close", atr_mode="frozen"
    )
    assert trigger is not None
    assert trigger.direction == "bearish"
    assert trigger.retracement_atr >= 2.0
