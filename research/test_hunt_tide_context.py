"""Testes de `research.hunt_tide_context`.

O painel so anota os episodios de producao; o que tem de estar certo e a
leitura dos dois canais do Tide (fase e agressao) e a orientacao a captura.

Rodar:
    poetry run pytest research/test_hunt_tide_context.py
"""

from __future__ import annotations

from dataclasses import replace

from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import RetailPositioning, TimeFrame
from liquidity_hunter.indicators.vwap import vwap
from research.hunt_tide_context import (
    PHASE_BUCKETS,
    PHASE_PER_SIGMA,
    _bucket,
    aggression_by_timestamp,
    episode_rows,
    phase_by_timestamp,
)
from research.test_hunt_score_redundancy import _candles, _hunt_snapshot


def test_fase_le_50_na_borda_de_um_sigma() -> None:
    """A escala e a da linha de fase do Tide: fechar em +1 sigma vale +50."""
    data = _hunt_snapshot()
    series = vwap(data.candles, symbol="TESTUSDT", timeframe=TimeFrame.M15)
    assert series is not None
    data = replace(data, vwap=series)
    phase = phase_by_timestamp(data)
    closes = {c.timestamp: c.close for c in data.candles}
    checked = 0
    for p in series.points:
        if p.timestamp not in phase or p.upper_1 is None:
            continue
        expected = PHASE_PER_SIGMA * (closes[p.timestamp] - p.value) / (p.upper_1 - p.value)
        assert abs(phase[p.timestamp] - expected) < 1e-9
        checked += 1
    assert checked > 0


def test_fase_sem_dispersao_nao_existe() -> None:
    """Sem sigma nao ha fita (o guard `MIN_SPAN_FRAC` do Tide)."""
    data = _hunt_snapshot()
    series = vwap(data.candles, symbol="TESTUSDT", timeframe=TimeFrame.M15)
    assert series is not None
    flat = series.model_copy(
        update={"points": [p.model_copy(update={"upper_1": p.value}) for p in series.points]}
    )
    assert phase_by_timestamp(replace(data, vwap=flat)) == {}


def test_agressao_e_o_cvd_da_janela_em_pct_do_volume() -> None:
    candles = _candles(6)
    # Metade compradora em todos: delta 0 -> agressao 0.
    zero = aggression_by_timestamp(candles, 3)
    assert set(zero) == {c.timestamp for c in candles[2:]}
    assert all(abs(v) < 1e-9 for v in zero.values())
    # Tudo comprador: delta = volume -> +100%.
    bought = [c.model_copy(update={"taker_buy_volume": c.volume}) for c in candles]
    assert all(abs(v - 100.0) < 1e-9 for v in aggression_by_timestamp(bought, 3).values())
    assert aggression_by_timestamp(candles[:2], 3) == {}


def test_baldes_cobrem_a_reta_sem_furo() -> None:
    names = [b[0] for b in PHASE_BUCKETS]
    assert [_bucket(v) for v in (-150, -100, -99, -50, -49, 0, 49, 50, 99, 100, 300)] == [
        names[0], names[0], names[1], names[1], names[2], names[2], names[2],
        names[3], names[3], names[4], names[4],
    ]


def test_episodios_sao_os_de_producao_e_orientados_a_captura() -> None:
    data = _hunt_snapshot()
    series = vwap(data.candles, symbol="TESTUSDT", timeframe=TimeFrame.M15)
    assert series is not None
    data = replace(data, vwap=series)
    rows = episode_rows(data, "TESTUSDT", "M15")
    engine = LiquidityHuntEngine()
    producao = {
        (stream, e.end_timestamp.isoformat())
        for stream, eps in (
            ("hunt", engine.build_history(data)),
            ("continuation", engine.build_continuation_history(data)),
        )
        for e in eps
    }
    assert {(r["stream"], r["anchor"]) for r in rows.values()} == producao
    assert rows
    raw_phase = phase_by_timestamp(data)
    hunts = {e.end_timestamp.isoformat(): e for e in engine.build_history(data)}
    for row in rows.values():
        episode = hunts.get(row["anchor"]) if row["stream"] == "hunt" else None
        if episode is None or row["phase"] is None:
            continue
        sign = 1.0 if row["up"] else -1.0
        assert row["phase"] == sign * raw_phase[episode.end_timestamp]
        assert row["up"] == (episode.hunted_side is RetailPositioning.SHORT)
