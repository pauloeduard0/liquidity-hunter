"""Testes de `research.hunt_displacement_capture`.

O primeiro teste e o que sustenta o resto: `D0` (sinal desligado) tem de
reproduzir a producao episodio a episodio nos dois snapshots -- a copia de
`_capture_grabs` da variante so vale se, sem o sinal, ela for a producao.

Rodar:
    poetry run pytest research/test_hunt_displacement_capture.py
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import Candle, MarketDirection
from research.hunt_displacement_capture import (
    D0,
    D4,
    D7,
    DISPLACEMENT_LOOKBACK,
    displacement_candles,
)
from research.test_hunt_score_redundancy import (
    _candles,
    _continuation_snapshot,
    _hunt_snapshot,
)

SNAPSHOTS = (_hunt_snapshot, _continuation_snapshot)


def _upd(c: Candle, **kw: object) -> Candle:
    """`Candle` e pydantic, nao dataclass: `replace` nao serve."""
    return c.model_copy(update=kw)


@pytest.mark.parametrize("snapshot", SNAPSHOTS)
def test_d0_reproduz_a_producao(snapshot: object) -> None:
    data = snapshot()  # type: ignore[operator]
    producao = LiquidityHuntEngine()
    assert D0().build_history(data) == producao.build_history(data)
    assert D0().build_continuation_history(data) == producao.build_continuation_history(data)


def _with_displacement(candles: list[Candle], i: int, up: bool = True) -> list[Candle]:
    """Substitui o candle `i` por um deslocamento inequivoco na direcao pedida."""
    c = candles[i]
    prev_high = max(p.high for p in candles[i - DISPLACEMENT_LOOKBACK : i])
    prev_low = min(p.low for p in candles[i - DISPLACEMENT_LOOKBACK : i])
    if up:
        big = _upd(
            c,
            open=c.open,
            high=prev_high + 6.0,
            low=c.open - 0.2,
            close=prev_high + 5.5,
            volume=1000.0,
            taker_buy_volume=700.0,
        )
    else:
        big = _upd(
            c,
            open=c.open,
            high=c.open + 0.2,
            low=prev_low - 6.0,
            close=prev_low - 5.5,
            volume=1000.0,
            taker_buy_volume=300.0,
        )
    out = list(candles)
    out[i] = big
    return out


def test_serie_plana_nao_tem_deslocamento() -> None:
    assert displacement_candles(_candles(120), MarketDirection.BULLISH) == []
    assert displacement_candles(_candles(120), MarketDirection.BEARISH) == []


def test_deslocamento_e_detectado_na_direcao_certa() -> None:
    candles = _with_displacement(_candles(120), 50, up=True)
    assert displacement_candles(candles, MarketDirection.BULLISH) == [candles[50].timestamp]
    assert displacement_candles(candles, MarketDirection.BEARISH) == []


def test_deslocamento_sem_volume_nao_conta() -> None:
    candles = _with_displacement(_candles(120), 50, up=True)
    candles[50] = _upd(candles[50], volume=120.0, taker_buy_volume=80.0)
    assert displacement_candles(candles, MarketDirection.BULLISH) == []


def test_deslocamento_que_nao_rompe_o_extremo_anterior_nao_conta() -> None:
    candles = _with_displacement(_candles(120), 50, up=True)
    # Mesmo range e volume, mas fecha abaixo do extremo dos 12 anteriores.
    c = candles[50]
    candles[50] = _upd(c, high=c.open + 6.0, low=c.open - 0.2, close=c.open + 0.3)
    assert displacement_candles(candles, MarketDirection.BULLISH) == []


def test_d4_ancora_no_candle_de_deslocamento() -> None:
    """Com um deslocamento dentro do cluster do grab, o episodio termina nele."""
    data = _hunt_snapshot()
    base = LiquidityHuntEngine().build_history(data)
    assert base, "o snapshot de teste tem de produzir um grab em producao"
    grab = base[0].end_timestamp
    candles = data.candles
    i = next(k for k, c in enumerate(candles) if c.timestamp == grab)
    data = replace(data, candles=_with_displacement(candles, i + 1, up=True))
    episodes = D4().build_history(data)
    assert episodes[0].end_timestamp == candles[i + 1].timestamp
    assert "displacement" in episodes[0].capture_sources
    # +4 do displacement; o candle de deslocamento tambem pode trazer o +1 de delta.
    assert episodes[0].capture_score >= base[0].capture_score + 4.0


def test_d7_deslocamento_sozinho_fecha_a_caca() -> None:
    """Longe de qualquer outro sinal, so o D7 abre um grab; o D4 precisa de parceiro."""
    data = _hunt_snapshot()
    base = LiquidityHuntEngine().build_history(data)
    grab = base[0].end_timestamp
    candles = data.candles
    i = next(k for k, c in enumerate(candles) if c.timestamp == grab)
    # 10 candles depois do grab de producao: fora do merge gap (3 candles).
    data = replace(data, candles=_with_displacement(candles, i + 10, up=True))
    d4 = D4().build_history(data)
    d7 = D7().build_history(data)
    assert [e.end_timestamp for e in d4] == [e.end_timestamp for e in base]
    assert candles[i + 10].timestamp in [e.end_timestamp for e in d7]


@pytest.mark.parametrize("cls", (D4, D7))
def test_continuation_nao_e_tocada(cls: type[LiquidityHuntEngine]) -> None:
    data = _continuation_snapshot()
    candles = data.candles
    data = replace(data, candles=_with_displacement(candles, 50, up=False))
    assert cls().build_continuation_history(
        data
    ) == LiquidityHuntEngine().build_continuation_history(data)
