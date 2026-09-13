"""Testes de `research.hunt_pivot_thrust`.

O sinal esta em producao desde 2026-09-13; aqui P0 e o motor anterior (gancho
desligado) e P1 e a producao. A versao de pesquisa do sinal tem de concordar
com a de producao candle a candle.

Rodar:
    poetry run pytest research/test_hunt_pivot_thrust.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import MarketDirection
from research.hunt_pivot_thrust import P0, P1, pivot_thrust_signals
from research.test_hunt_score_redundancy import _continuation_snapshot, _hunt_snapshot

SNAPSHOTS = (_hunt_snapshot, _continuation_snapshot)


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_p1_e_a_producao(snapshot: object) -> None:
    data = snapshot()  # type: ignore[operator]
    producao = LiquidityHuntEngine()
    assert P1().build_history(data) == producao.build_history(data)
    assert P1().build_continuation_history(data) == producao.build_continuation_history(data)


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_p0_nao_toca_o_hunt(snapshot: object) -> None:
    data = snapshot()  # type: ignore[operator]
    assert P0().build_history(data) == LiquidityHuntEngine().build_history(data)


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_sinal_de_pesquisa_concorda_com_a_producao(snapshot: object) -> None:
    data = snapshot()  # type: ignore[operator]
    start, end = data.candles[0].timestamp, data.candles[-1].timestamp
    for hunted_short, direction in (
        (True, MarketDirection.BULLISH),
        (False, MarketDirection.BEARISH),
    ):
        # Mesmos candles lidos; o peso difere desde a H9 (producao: 4 sempre).
        pesquisa = pivot_thrust_signals(data, hunted_short, direction, start, end)
        producao = LiquidityHuntEngine._pivot_vsa_signals(data, hunted_short, start, end)
        assert [(t, s) for t, _w, s in pesquisa] == [(t, s) for t, _w, s in producao]
