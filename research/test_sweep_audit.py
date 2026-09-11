"""Testes diagnosticos da auditoria de sweep.

Dois grupos, com propositos diferentes:

* **helpers da auditoria** -- `classify_wick`, `_excursions`: garantem que a
  leitura nao esta errada antes de qualquer conclusao ser tirada dela.
* **semantica atual do detector** -- o que o `LIQUIDITY_SWEEP` de producao
  *hoje* exige e nao exige. Sao testes de caracterizacao: se um deles quebrar,
  a semantica mudou, e essa mudanca precisa ser deliberada. Nenhum deles
  afirma que a semantica atual esta certa.
"""

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.detectors import SwingStructureDetector
from liquidity_hunter.tests.liquidity.detectors._factories import (
    make_candle,
    make_series,
)
from research.sweep_audit import HORIZONS, _excursions, classify_wick

START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(i, o, h, low, c):
    return Candle(
        symbol="TEST",
        timeframe=TimeFrame.H1,
        timestamp=START + timedelta(hours=i),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=100.0,
        taker_buy_volume=50.0,
    )


# --------------------------------------------------------------------------
# classify_wick
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "o,h,low,c,bullish,expected",
    [
        # A: pavio atravessa por cima, close volta aquem
        (100, 112, 99, 101, True, "A"),
        # B: abriu aquem, fechou alem
        (100, 112, 99, 111, True, "B"),
        # C: o corpo inteiro ja estava alem
        (111, 115, 110, 113, True, "C"),
        # D: nao atravessou
        (100, 109, 99, 101, True, "D"),
        # espelho bearish sobre o nivel 110 -> nivel 90
        (100, 101, 88, 99, False, "A"),
        (100, 101, 88, 89, False, "B"),
        (89, 90, 85, 87, False, "C"),
        (100, 101, 91, 99, False, "D"),
    ],
)
def test_wick_classes(o, h, low, c, bullish, expected):
    level = 110.0 if bullish else 90.0
    assert classify_wick(candle(0, o, h, low, c), level, bullish=bullish) == expected


def test_touching_the_level_exactly_is_not_a_crossing():
    """Fronteira de igualdade: o detector usa `>` / `<` estritos."""
    assert classify_wick(candle(0, 100, 110, 99, 101), 110.0, bullish=True) == "D"
    assert classify_wick(candle(0, 100, 101, 90, 99), 90.0, bullish=False) == "D"


# --------------------------------------------------------------------------
# _excursions
# --------------------------------------------------------------------------


def test_excursions_are_measured_after_the_event_candle_only():
    """A vela do evento nao entra na janela -- senao mede o proprio sweep."""
    candles = [candle(0, 100, 200, 50, 100)] + [
        candle(i, 100, 110, 95, 100) for i in range(1, 60)
    ]
    mfe, mae = _excursions(candles, 0, 100.0, atr=10.0, expect_bullish=True)
    assert mfe[5] == pytest.approx(1.0)   # (110 - 100) / 10
    assert mae[5] == pytest.approx(0.5)   # (100 - 95) / 10


def test_horizon_without_enough_candles_is_absent_not_truncated():
    candles = [candle(i, 100, 110, 95, 100) for i in range(8)]
    mfe, _mae = _excursions(candles, 0, 100.0, atr=10.0, expect_bullish=True)
    assert 5 in mfe and 10 not in mfe
    assert set(mfe) <= set(HORIZONS)


def test_bearish_expectation_mirrors_the_sides():
    candles = [candle(0, 100, 100, 100, 100)] + [
        candle(i, 100, 110, 90, 100) for i in range(1, 20)
    ]
    mfe, mae = _excursions(candles, 0, 100.0, atr=10.0, expect_bullish=False)
    assert mfe[5] == pytest.approx(1.0)   # favoravel = queda
    assert mae[5] == pytest.approx(1.0)   # adverso = alta


# --------------------------------------------------------------------------
# caracterizacao: o que o LIQUIDITY_SWEEP de producao exige hoje
# --------------------------------------------------------------------------


def _persistence_route(sweep_close: float):
    """A rota "persistencia falhou": quebra contra-tendencia que nao se sustenta.

    Mesma serie do teste de producao
    `test_liquidity_sweep_when_persistence_fails`, com o close do candle da
    quebra parametrizado.
    """
    highs = [150.0, 200.0, 150.0, 210.0, 150.0, 220.0, 150.0]
    lows = [145.0, 145.0, 140.0, 145.0, 130.0, 145.0, 120.0]
    candles = make_series(highs, lows)
    candles[3] = make_candle(3, highs[3], lows[3], close=205.0)
    candles[4] = make_candle(4, highs[4], lows[4], close=sweep_close)
    candles[5] = make_candle(5, highs[5], lows[5], close=215.0)
    events = SwingStructureDetector(
        swing_lookback=1, persistence_candles=1, confluence_filter=False
    ).detect(candles)
    return candles, [e for e in events if e.event is StructureEvent.LIQUIDITY_SWEEP]


def test_sweep_emitted_when_the_close_comes_back_above_the_level():
    """Reclaim verdadeiro: o close volta acima do nivel varrido (classe A)."""
    _candles, sweeps = _persistence_route(145.0)
    assert len(sweeps) == 1
    assert sweeps[0].direction is MarketDirection.BEARISH
    assert sweeps[0].reference_price_level == 140.0


def test_sweep_emitted_IDENTICALLY_when_the_close_is_BEYOND_the_level():
    """Achado S2, caracterizado: o evento nao exige reclaim.

    Com o close *abaixo* do nivel varrido -- a quebra so nao se sustentou na
    janela de persistencia -- sai o mesmo `LIQUIDITY_SWEEP`, com a mesma
    direcao e a mesma referencia. Nenhum campo do evento distingue um
    reclaim de um close-through nao sustentado; na amostra medida esse caso
    e 41,7% dos sweeps confirmados.
    """
    _c_a, rejected = _persistence_route(145.0)
    candles, closed_through = _persistence_route(135.0)
    assert len(closed_through) == 1
    i = [c.timestamp for c in candles].index(closed_through[0].timestamp)
    assert candles[i].close < closed_through[0].reference_price_level
    assert (
        closed_through[0].event,
        closed_through[0].direction,
        closed_through[0].reference_price_level,
    ) == (
        rejected[0].event,
        rejected[0].direction,
        rejected[0].reference_price_level,
    )


def test_a_close_exactly_at_the_level_is_not_a_sustained_break():
    """Fronteira de igualdade: `<` estrito, entao o close no nivel varre."""
    _candles, sweeps = _persistence_route(140.0)
    assert len(sweeps) == 1


def test_sweep_timestamp_is_the_wick_break_candle():
    """O evento e datado no candle que cruzou o nivel.

    Esse candle e anterior ao pivo que confirma o sweep, entao o timestamp
    nao e o momento em que o evento passou a ser conhecivel -- o item 6 da
    auditoria. Caracterizacao: a data e a da quebra, por desenho.
    """
    candles, sweeps = _persistence_route(145.0)
    i = [c.timestamp for c in candles].index(sweeps[0].timestamp)
    level = sweeps[0].reference_price_level
    assert candles[i].low < level
    assert all(c.low >= level for c in candles[:i])
