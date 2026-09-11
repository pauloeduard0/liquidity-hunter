"""Testes do S2 — a geometria de um sweep contra o nivel varrido.

Dois grupos:

* **S2.15, os limites** -- `classify` e uma funcao de 5 numeros e decide a
  taxonomia inteira do estudo. Toda fronteira dela e testada explicitamente:
  bullish e bearish, reclaim e close-through, o close exatamente NO nivel, o
  pavio exatamente NO nivel, a penetracao minima e o close-through enorme.
* **ponta a ponta** -- que sobre serie real a classificacao e exaustiva, que a
  medicao de outcome parte do `known_at` (e nunca do timestamp back-datado) e
  que as profundidades tem o sinal certo.

Os testes que precisam de serie real pulam quando nao ha cache local.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.core.domain import TimeFrame
from research._offline import OfflineKlinesProvider
from research.sweep_reclaim_audit import (
    AMBIGUOUS,
    BLOCKS,
    CLOSE_THROUGH,
    NO_REF,
    RECLAIM,
    TOUCH_ONLY,
    classify,
    extract,
)


def _c(bullish: bool, high: float, low: float, close: float, level: float | None):
    return classify(bullish=bullish, high=high, low=low, close=close, level=level)


# --------------------------------------------------------------------------
# S2.15 — as fronteiras
# --------------------------------------------------------------------------


def test_bullish_reclaim():
    """Pavio acima do nivel, close de volta abaixo."""
    assert _c(True, high=110.0, low=95.0, close=99.0, level=100.0) == RECLAIM


def test_bearish_reclaim():
    assert _c(False, high=105.0, low=90.0, close=101.0, level=100.0) == RECLAIM


def test_bullish_close_through():
    assert _c(True, high=110.0, low=95.0, close=105.0, level=100.0) == CLOSE_THROUGH


def test_bearish_close_through():
    assert _c(False, high=105.0, low=90.0, close=95.0, level=100.0) == CLOSE_THROUGH


def test_close_exactly_at_the_level_is_ambiguous_not_reclaim():
    """Nem alem nem aquem: nao se chuta um lado."""
    assert _c(True, high=110.0, low=95.0, close=100.0, level=100.0) == AMBIGUOUS
    assert _c(False, high=105.0, low=90.0, close=100.0, level=100.0) == AMBIGUOUS


def test_wick_exactly_at_the_level_never_crossed_it():
    """Tocar nao e atravessar: `>` e `<` estritos, como em `mitigation.py`."""
    assert _c(True, high=100.0, low=95.0, close=98.0, level=100.0) == TOUCH_ONLY
    assert _c(False, high=105.0, low=100.0, close=102.0, level=100.0) == TOUCH_ONLY


def test_a_wick_that_falls_short_is_not_a_touch():
    assert _c(True, high=99.0, low=95.0, close=98.0, level=100.0) == AMBIGUOUS


def test_the_smallest_possible_penetration_still_counts():
    assert _c(True, high=100.01, low=95.0, close=99.0, level=100.0) == RECLAIM


def test_the_smallest_possible_close_through_still_counts():
    assert _c(True, high=110.0, low=95.0, close=100.01, level=100.0) == CLOSE_THROUGH


def test_a_huge_close_through_is_still_just_close_through():
    """A classe nao tem limiar de magnitude -- a magnitude e medida a parte."""
    assert _c(True, high=900.0, low=95.0, close=800.0, level=100.0) == CLOSE_THROUGH


def test_no_reference_level_is_its_own_class():
    assert _c(True, high=110.0, low=95.0, close=99.0, level=None) == NO_REF


def test_the_two_directions_are_mirror_images():
    assert _c(True, 110.0, 95.0, 99.0, 100.0) == _c(False, 105.0, 90.0, 101.0, 100.0)
    assert _c(True, 110.0, 95.0, 105.0, 100.0) == _c(False, 105.0, 90.0, 95.0, 100.0)


# --------------------------------------------------------------------------
# ponta a ponta
# --------------------------------------------------------------------------


@pytest.fixture
def rows():
    from liquidity_hunter.app.dashboard_data import load_dashboard_data
    from research._paginated import NoFuturesProvider

    provider = OfflineKlinesProvider()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        if provider.has(symbol, TimeFrame.H1):
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=TimeFrame.H1,
                limit=1200, compute_narrative=False,
                futures_provider=NoFuturesProvider(),
            )
            return extract(data)
    pytest.skip("sem cache local de klines")


def test_every_sweep_gets_a_class(rows):
    assert rows
    valid = {RECLAIM, CLOSE_THROUGH, TOUCH_ONLY, AMBIGUOUS, NO_REF}
    assert all(r.klass in valid for r in rows)


def test_reclaim_and_close_through_are_mutually_exclusive_depths(rows):
    """Um evento nunca tem as duas profundidades ao mesmo tempo."""
    for r in rows:
        assert not (r.close_beyond_atr is not None and r.reclaim_atr is not None)


def test_close_through_carries_the_beyond_depth(rows):
    g = [r for r in rows if r.klass == CLOSE_THROUGH]
    if not g:
        pytest.skip("sem close-through nesta serie")
    assert all(r.close_beyond_atr is not None and r.close_beyond_atr > 0 for r in g)
    assert all(r.reclaim_atr is None for r in g)


def test_reclaim_carries_the_reclaim_depth(rows):
    g = [r for r in rows if r.klass == RECLAIM]
    assert g
    assert all(r.reclaim_atr is not None and r.reclaim_atr > 0 for r in g)
    assert all(r.close_beyond_atr is None for r in g)


def test_penetration_is_never_negative(rows):
    assert all(r.penetration_atr is None or r.penetration_atr >= 0 for r in rows)


def test_the_outcome_is_measured_from_known_at_not_from_the_timestamp(rows):
    """A licao do S1: medir do timestamp back-datado infla o resultado."""
    assert all(r.known_idx >= r.idx for r in rows)
    assert any(r.known_idx > r.idx for r in rows)


def test_blocks_partition_the_series(rows):
    assert {r.block for r in rows} <= set(range(BLOCKS))
