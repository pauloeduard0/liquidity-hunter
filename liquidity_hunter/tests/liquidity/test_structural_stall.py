"""Tests for `liquidity.structural_stall.detect_structural_stall`."""

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    detect_structural_stall,
    frozen_atr_pct,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
BULLISH, BEARISH = MarketDirection.BULLISH, MarketDirection.BEARISH


def at(index: int) -> datetime:
    return START + timedelta(hours=index)


def candle(index: int, close: float, *, spread: float = 0.01) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=at(index),
        open=close,
        high=close * (1 + spread),
        low=close * (1 - spread),
        close=close,
        volume=1.0,
        taker_buy_volume=0.5,
    )


def series(closes: list[float], *, spread: float = 0.01) -> list[Candle]:
    return [candle(index, close, spread=spread) for index, close in enumerate(closes)]


def rising_then_flat(rise: int, flat: int, *, step: float = 1.0) -> list[Candle]:
    closes = [100.0 + step * index for index in range(rise)]
    closes += [closes[-1]] * flat
    return series(closes)


def rising_then_falling(rise: int, fall: int, *, step: float = 1.0) -> list[Candle]:
    """Sobe `rise` velas e devolve `fall` velas (o preço tem de seguir > 0)."""
    closes = [100.0 + step * index for index in range(rise)]
    peak = closes[-1]
    closes += [max(peak - step * index, 1.0) for index in range(1, fall + 1)]
    return series(closes)


def event(
    index: int,
    kind: StructureEvent,
    direction: MarketDirection,
    price: float,
    *,
    provisional: bool = False,
) -> MarketStructure:
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=at(index),
        event=kind,
        direction=direction,
        price_level=price,
        reference_price_level=price * 0.99,
        scope=StructureScope.INTERNAL,
        provisional=provisional,
    )


def bos(
    index: int, direction: MarketDirection, price: float, *, provisional: bool = False
) -> MarketStructure:
    return event(
        index, StructureEvent.BREAK_OF_STRUCTURE, direction, price, provisional=provisional
    )


def test_defaults_are_the_validated_setting() -> None:
    assert DEFAULT_STALL_BARS == 50
    assert DEFAULT_STALL_RETRACEMENT_ATR == 6.0


# --- the two conditions ----------------------------------------------------


def test_a_leg_that_has_not_been_quiet_long_enough_is_not_stale() -> None:
    candles = rising_then_falling(10, 20)
    assert (
        detect_structural_stall(candles, [bos(9, BULLISH, 109.0)], n=50, k_atr=1.0) is None
    )


def test_a_quiet_leg_that_gave_nothing_back_is_not_stale() -> None:
    candles = rising_then_flat(10, 100)
    assert (
        detect_structural_stall(candles, [bos(9, BULLISH, 109.0)], n=20, k_atr=1.0) is None
    )


def test_a_quiet_leg_that_gave_enough_back_is_stale() -> None:
    candles = rising_then_falling(10, 100)
    stall = detect_structural_stall(candles, [bos(9, BULLISH, 109.0)], n=20, k_atr=1.0)
    assert stall is not None
    assert stall.direction is BULLISH
    assert stall.last_advance_timestamp == at(9)
    assert stall.last_advance_price == 109.0
    assert stall.bars_since_advance >= 20
    assert stall.retracement_atr >= 1.0
    assert stall.stale_since == at(9 + stall.bars_since_advance)


def test_a_bearish_leg_measures_the_give_back_upward() -> None:
    closes = [100.0 - index for index in range(10)]
    closes += [closes[-1] + index for index in range(1, 101)]
    candles = series(closes)
    stall = detect_structural_stall(candles, [bos(9, BEARISH, 91.0)], n=20, k_atr=1.0)
    assert stall is not None
    assert stall.direction is BEARISH
    leg = candles[9 : 9 + stall.bars_since_advance + 1]
    assert stall.leg_extreme_price == min(c.close for c in leg)


# --- the boundary ----------------------------------------------------------


def test_exactly_n_bars_and_exactly_k_atr_trigger() -> None:
    candles = rising_then_falling(10, 100)
    events = [bos(9, BULLISH, 109.0)]
    # The retracement standing at exactly bar N, whatever it is.
    at_n = detect_structural_stall(candles, events, n=30, k_atr=0.0)
    assert at_n is not None and at_n.bars_since_advance == 30
    exact = detect_structural_stall(candles, events, n=30, k_atr=at_n.retracement_atr)
    assert exact is not None
    assert exact.bars_since_advance == 30       # `>=` on both sides, not `>`
    later = detect_structural_stall(
        candles, events, n=30, k_atr=at_n.retracement_atr * 1.01
    )
    assert later is None or later.bars_since_advance > 30


# --- what does and does not open a leg -------------------------------------


def test_no_advance_means_no_leg_to_stall() -> None:
    assert detect_structural_stall(rising_then_falling(10, 100), [], n=20, k_atr=1.0) is None


def test_a_provisional_bos_does_not_open_a_leg() -> None:
    candles = rising_then_falling(10, 100)
    events = [bos(9, BULLISH, 109.0, provisional=True)]
    assert detect_structural_stall(candles, events, n=20, k_atr=1.0) is None


def test_a_leg_closed_by_a_choch_is_not_stale() -> None:
    """The machine already ended it; there is no standing leg to call quiet."""
    candles = rising_then_falling(10, 100)
    events = [
        bos(9, BULLISH, 109.0),
        event(20, StructureEvent.CHANGE_OF_CHARACTER, BEARISH, 105.0),
    ]
    assert detect_structural_stall(candles, events, n=20, k_atr=1.0) is None


def test_a_later_advance_restarts_the_count() -> None:
    candles = rising_then_falling(10, 200, step=0.1)
    events = [bos(9, BULLISH, 100.9)]
    first = detect_structural_stall(candles, events, n=20, k_atr=1.0)
    assert first is not None

    events_with_second = [*events, bos(100, BULLISH, 91.8)]
    second = detect_structural_stall(candles, events_with_second, n=20, k_atr=1.0)
    assert second is not None
    assert second.last_advance_timestamp == at(100)
    assert second.stale_since > first.stale_since
    # A contagem recomeça: as barras são medidas do novo advance, não do antigo.
    assert second.bars_since_advance <= len(candles) - 100


# --- degenerate input ------------------------------------------------------


def test_a_series_too_short_to_measure_volatility_is_safe() -> None:
    assert detect_structural_stall([], [], n=1, k_atr=1.0) is None
    one = [candle(0, 100.0)]
    assert detect_structural_stall(one, [bos(0, BULLISH, 100.0)], n=0, k_atr=0.0) is None


def test_a_flat_series_has_no_volatility_unit_and_does_not_stall() -> None:
    candles = series([100.0] * 200, spread=0.0)
    assert frozen_atr_pct(candles, 50) == 0.0
    assert detect_structural_stall(candles, [bos(9, BULLISH, 100.0)], n=20, k_atr=0.0) is None


def test_an_advance_on_a_timestamp_outside_the_series_is_ignored() -> None:
    candles = rising_then_falling(10, 100)
    stray = bos(5_000, BULLISH, 109.0)
    assert detect_structural_stall(candles, [stray], n=20, k_atr=1.0) is None


def test_negative_parameters_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        detect_structural_stall(rising_then_falling(10, 50), [], n=-1)


# --- the external gate seam ------------------------------------------------


def test_an_ineligible_leg_short_circuits() -> None:
    candles = rising_then_falling(10, 100)
    events = [bos(9, BULLISH, 109.0)]
    assert detect_structural_stall(candles, events, n=20, k_atr=1.0) is not None
    assert detect_structural_stall(candles, events, n=20, k_atr=1.0, eligible=False) is None


# --- causality -------------------------------------------------------------


def test_frozen_atr_only_reads_candles_up_to_its_index() -> None:
    candles = rising_then_falling(10, 100)
    assert frozen_atr_pct(candles, 9) == frozen_atr_pct(candles[:10], 9)


def test_the_frozen_atr_does_not_move_as_the_leg_goes_on() -> None:
    candles = rising_then_falling(10, 100)
    events = [bos(9, BULLISH, 109.0)]
    early = detect_structural_stall(candles[:60], events, n=20, k_atr=1.0)
    late = detect_structural_stall(candles, events, n=20, k_atr=1.0)
    assert early is not None and late is not None
    assert early.frozen_atr_pct == late.frozen_atr_pct == frozen_atr_pct(candles, 9)


def test_the_result_does_not_depend_on_candles_after_the_trigger() -> None:
    candles = rising_then_falling(10, 150, step=0.5)
    events = [bos(9, BULLISH, 109.0)]
    full = detect_structural_stall(candles, events, n=20, k_atr=1.0)
    assert full is not None
    cut = 9 + full.bars_since_advance + 1
    truncated = detect_structural_stall(candles[:cut], events, n=20, k_atr=1.0)
    assert truncated == full


def test_the_leg_extreme_never_looks_past_the_trigger() -> None:
    """A higher close after the stall must not rewrite the extreme it reported."""
    closes = [100.0 + index for index in range(10)]
    closes += [109.0 - index for index in range(1, 61)]   # give-back
    closes += [500.0] * 10                                # a later spike
    candles = series(closes)
    events = [bos(9, BULLISH, 109.0)]
    stall = detect_structural_stall(candles, events, n=20, k_atr=1.0)
    assert stall is not None
    assert stall.leg_extreme_price == 109.0
    assert stall.leg_extreme_timestamp == at(9)


# --- close basis -----------------------------------------------------------


def test_a_wick_alone_does_not_stall_the_leg() -> None:
    """Closes hold the high; only the lows dip. A stop-hunt must not end a leg."""
    candles = [candle(index, 100.0 + index, spread=0.001) for index in range(10)]
    candles += [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=at(index),
            open=109.0,
            high=109.2,
            low=60.0,          # a deep wick, every candle
            close=109.0,       # ...that never closes down
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for index in range(10, 120)
    ]
    assert detect_structural_stall(candles, [bos(9, BULLISH, 109.0)], n=20, k_atr=1.0) is None
