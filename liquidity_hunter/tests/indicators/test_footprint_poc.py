"""POC do footprint: a geometria do sino, os átomos e a grade de amostragem."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.enums import TimeFrame
from liquidity_hunter.indicators.footprint_poc import (
    footprint_poc,
    footprint_poc_series,
)


def _candle(
    index: int,
    *,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
        open=low,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume / 2,
    )


def test_single_candle_poc_sits_between_the_two_bell_centers() -> None:
    # Fechamento no meio: sino comprador em 102.5, vendedor em 107.5, mesma
    # massa -> o pico da soma cai no meio dos dois, o próprio fechamento.
    reading = footprint_poc([_candle(0, high=110, low=100, close=105)], period=2)
    assert reading is not None
    assert reading.profile_low == 100.0
    assert reading.profile_high == 110.0
    assert reading.price == pytest.approx(105.0, abs=0.1)


def test_close_at_an_extreme_pushes_the_poc_toward_the_middle_half() -> None:
    # A geometria do script: o sino comprador cobre low..close e o vendedor
    # close..high. Um fechamento no topo dá quase todo o volume ao sino
    # comprador, cujo centro e o MEIO de low..close -- ou seja, o POC cai
    # ABAIXO do meio da vela, nao acima dele. O fechamento no fundo espelha.
    up = footprint_poc([_candle(0, high=110, low=100, close=109)], period=2)
    down = footprint_poc([_candle(0, high=110, low=100, close=101)], period=2)
    assert up is not None and down is not None
    assert up.price == pytest.approx(104.5, abs=0.5)
    assert down.price == pytest.approx(105.5, abs=0.5)


def test_poc_lands_on_the_price_two_candles_share() -> None:
    # Duas velas empilhadas na mesma faixa estreita e uma vela larga de volume
    # menor: o POC fica onde as duas se somam.
    candles = [
        _candle(0, high=101, low=99, close=100, volume=500.0),
        _candle(1, high=101, low=99, close=100, volume=500.0),
        _candle(2, high=130, low=99, close=115, volume=100.0),
    ]
    reading = footprint_poc(candles, period=3)
    assert reading is not None
    assert 99.0 <= reading.price <= 101.0


def test_flat_candles_do_not_win_the_poc() -> None:
    # A vela de range zero é átomo: fica fora das curvas por mais volume que
    # carregue, exatamente como no script.
    candles = [
        _candle(0, high=110, low=100, close=105, volume=10.0),
        _candle(1, high=200, low=200, close=200, volume=10_000.0),
    ]
    reading = footprint_poc(candles, period=2)
    assert reading is not None
    assert reading.profile_high == 200.0
    assert reading.price == pytest.approx(105.0, abs=0.5)


def test_all_flat_window_has_no_profile() -> None:
    flat = [_candle(i, high=100, low=100, close=100) for i in range(3)]
    assert footprint_poc(flat, period=3) is None


def test_zero_volume_window_has_no_profile() -> None:
    empty = [_candle(i, high=110, low=100, close=105, volume=0.0) for i in range(3)]
    assert footprint_poc(empty, period=3) is None


def test_poc_is_a_point_of_the_sampling_grid() -> None:
    candles = [_candle(i, high=110 + i, low=100, close=105) for i in range(5)]
    reading = footprint_poc(candles, period=5, resolution=20)
    assert reading is not None
    step = (reading.profile_high - reading.profile_low) / 20
    offset = (reading.price - reading.profile_low) / step
    assert offset == pytest.approx(round(offset), abs=1e-9)


def test_series_warms_up_and_never_looks_ahead() -> None:
    candles = [_candle(i, high=110 + i, low=100 + i, close=105 + i) for i in range(6)]
    series = footprint_poc_series(candles, period=4)
    assert len(series) == len(candles)
    assert series[:3] == [None, None, None]
    # Cada posição é o perfil calculado só com o que já fechou até ali.
    assert series[3] == footprint_poc(candles[:4], period=4)
    assert series[5] == footprint_poc(candles, period=4)


def test_series_without_warmup_opens_on_a_partial_period() -> None:
    candles = [_candle(i, high=110, low=100, close=105) for i in range(3)]
    series = footprint_poc_series(candles, period=10, warmup_full_period=False)
    assert all(reading is not None for reading in series)


def test_invalid_parameters_are_rejected() -> None:
    candles = [_candle(0, high=110, low=100, close=105)]
    with pytest.raises(ValueError):
        footprint_poc(candles, period=1)
    with pytest.raises(ValueError):
        footprint_poc(candles, resolution=0)
    with pytest.raises(ValueError):
        footprint_poc(candles, concentration=0.0)
