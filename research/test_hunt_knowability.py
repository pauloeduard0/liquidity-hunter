"""Testes de `research.hunt_knowability`.

O painel decide se o HUNT/CONT sobrevive a entrada no candle conhecivel; as
pecas que tem de estar certas sao a simulacao do trade (lado adverso primeiro)
e o ATR causal (nenhum candle futuro na distancia do stop).

Rodar:
    poetry run pytest research/test_hunt_knowability.py
"""

from __future__ import annotations

from research.hunt_knowability import ATR_PERIOD, TRADE_HORIZON, causal_atr, trade_r
from research.test_hunt_score_redundancy import _candles


def _flat(n: int = TRADE_HORIZON + 5) -> list:
    return _candles(n)


def test_atr_causal_nao_le_o_futuro() -> None:
    candles = _flat(40)
    base = causal_atr(candles)
    spiked = list(candles)
    spiked[30] = spiked[30].model_copy(update={"high": 500.0})
    after = causal_atr(spiked)
    assert base[:30] == after[:30]
    assert all(v is None for v in base[:ATR_PERIOD])


def test_stop_e_alvo_no_mesmo_candle_conta_stop() -> None:
    candles = _flat()
    i = 0
    entry = candles[i].close
    wide = candles[i + 1].model_copy(update={"high": entry + 10, "low": entry - 10})
    candles[i + 1] = wide
    assert trade_r(candles, i, True, 1.0) == -1.0
    assert trade_r(candles, i, False, 1.0) == -1.0


def test_alvo_de_2r() -> None:
    candles = _flat()
    entry = candles[0].close
    candles[1] = candles[1].model_copy(update={"high": entry + 2.1, "low": entry - 0.5})
    assert trade_r(candles, 0, True, 1.0) == 2.0


def test_sem_toque_marca_a_mercado_no_horizonte() -> None:
    candles = _flat()
    r = trade_r(candles, 0, True, 50.0)
    assert r is not None
    expected = (candles[TRADE_HORIZON].close - candles[0].close) / 50.0
    assert abs(r - expected) < 1e-12


def test_sem_candles_suficientes_nao_simula() -> None:
    assert trade_r(_candles(TRADE_HORIZON), 0, True, 1.0) is None
