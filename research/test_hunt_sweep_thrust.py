"""Testes de `research.hunt_sweep_thrust`.

Rodar:
    poetry run pytest research/test_hunt_sweep_thrust.py
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, MarketDirection
from research.hunt_sweep_thrust import S0, S1, sweep_thrust_signals
from research.test_hunt_score_redundancy import _continuation_snapshot, _hunt_snapshot

SNAPSHOTS = (_hunt_snapshot, _continuation_snapshot)


def _upd(c: Candle, **kw: object) -> Candle:
    return c.model_copy(update=kw)


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_s0_reproduz_a_producao(snapshot: object) -> None:
    data = snapshot()  # type: ignore[operator]
    producao = LiquidityHuntEngine()
    assert S0().build_history(data) == producao.build_history(data)
    assert S0().build_continuation_history(data) == producao.build_continuation_history(data)


def test_serie_plana_nao_gera_sinal() -> None:
    data = _hunt_snapshot()
    start, end = data.candles[0].timestamp, data.candles[-1].timestamp
    # O snapshot tem um sweep bullish em candles[40]; o candle e plano, sem thrust.
    assert sweep_thrust_signals(data, True, MarketDirection.BULLISH, start, end) == []


def test_thrust_no_candle_do_sweep_vira_vsa_sem_gate() -> None:
    """Um up-thrust no candle do sweep entra como `vsa` mesmo sem ser a maxima
    dos 20 anteriores (o gate de producao o descartaria)."""
    data = _hunt_snapshot()
    candles = list(data.candles)
    c = candles[40]
    # Maxima mais alta em candles[30] para o gate de 20 rejeitar candles[40].
    candles[30] = _upd(candles[30], high=c.high + 5.0)
    # Up-thrust: pavio superior dominante, close baixo, volume acima da media.
    candles[40] = _upd(c, high=c.open + 3.0, low=c.open - 0.1, close=c.open + 0.2, volume=400.0)
    data = replace(data, candles=candles)
    start, end = candles[0].timestamp, candles[-1].timestamp
    sigs = sweep_thrust_signals(data, True, MarketDirection.BULLISH, start, end)
    assert [(ts, src) for ts, _w, src in sigs] == [(c.timestamp, "vsa")]



def test_s1_nao_conta_o_vsa_duas_vezes() -> None:
    """Um VSA que a producao ja emitiu no candle do sweep nao muda o score."""
    data = _hunt_snapshot()
    base = LiquidityHuntEngine().build_history(data)
    assert [e.capture_score for e in S1().build_history(data)] == [e.capture_score for e in base]


def test_continuation_nao_e_tocada() -> None:
    data = _continuation_snapshot()
    assert S1().build_continuation_history(
        data
    ) == LiquidityHuntEngine().build_continuation_history(data)
