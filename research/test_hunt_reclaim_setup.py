"""Testes de `research.hunt_reclaim_setup`.

O que tem de estar certo: o gatilho so le candles ate i (causal), a
classificacao dos bracos segue o registro e o custo entra em R.

Rodar:
    poetry run pytest research/test_hunt_reclaim_setup.py
"""

from __future__ import annotations

from research.hunt_knowability import TRADE_HORIZON, trade_r
from research.hunt_reclaim_setup import (
    ROUND_TRIP_COST,
    SWEEP_LOOKBACK,
    arms_for,
    net_r,
    sweep_trigger,
)
from research.test_hunt_score_redundancy import _candles


def _series(n: int = SWEEP_LOOKBACK + TRADE_HORIZON + 10) -> list:
    return _candles(n)


def test_sweep_long_fura_a_minima_e_fecha_acima() -> None:
    candles = _series()
    i = SWEEP_LOOKBACK + 2
    level = min(c.low for c in candles[i - SWEEP_LOOKBACK : i])
    candles[i] = candles[i].model_copy(
        update={
            "low": level - 1.0,
            "close": level + 0.2,
            "open": level + 0.1,
            "high": max(candles[i].high, level + 0.3),
        }
    )
    assert sweep_trigger(candles, i, True)
    assert not sweep_trigger(candles, i, False)


def test_sweep_que_fecha_abaixo_nao_e_gatilho() -> None:
    candles = _series()
    i = SWEEP_LOOKBACK + 2
    level = min(c.low for c in candles[i - SWEEP_LOOKBACK : i])
    candles[i] = candles[i].model_copy(
        update={"low": level - 1.0, "close": level - 0.5, "open": level - 0.4, "high": level - 0.3}
    )
    assert not sweep_trigger(candles, i, True)


def test_gatilho_nao_le_o_futuro() -> None:
    candles = _series()
    i = SWEEP_LOOKBACK + 2
    before = sweep_trigger(candles, i, True)
    for k in range(i + 1, len(candles)):
        candles[k] = candles[k].model_copy(update={"low": 0.01})
    assert sweep_trigger(candles, i, True) == before


def test_bracos() -> None:
    assert arms_for(True, None, 0.0) == ["S0"]
    assert arms_for(True, "neutral", 0.0) == ["S0"]
    assert arms_for(True, "bearish", 10.0) == ["S0", "S1", "S2"]
    assert arms_for(True, "bearish", 80.0) == ["S0", "S1"]
    assert arms_for(True, "bullish", -10.0) == ["S0", "S1c", "S2c"]
    # short: fase orientada inverte o sinal
    assert arms_for(False, "bullish", 60.0) == ["S0", "S1"]
    assert arms_for(False, "bullish", -30.0) == ["S0", "S1", "S2"]


def test_custo_em_r() -> None:
    candles = _series()
    risk = 1.0
    gross = trade_r(candles, 0, True, risk)
    assert gross is not None
    net = net_r(candles, 0, True, risk)
    assert net is not None
    assert abs(net - (gross - ROUND_TRIP_COST * candles[0].close / risk)) < 1e-12
