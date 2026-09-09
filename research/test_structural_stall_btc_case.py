"""O caso de referencia BTC H1, pelo `DashboardData` (Etapa 2).

A Etapa 1 provou a funcao pura contra a Etapa 0.6. Isto prova o WIRING: com a
flag ligada, o snapshot que o dashboard monta expoe aquele mesmo stall -- e,
depois que o BOS de retomada imprime, deixa de expo-lo.

Serie real do cache de research, sem rede:

    poetry run pytest research/test_structural_stall_btc_case.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

LAST_BOS = "2026-08-25 02:00:00+00:00"
EXPECTED_STALE_SINCE = "2026-08-28 15:00:00+00:00"
EXPECTED_BARS = 85
EXPECTED_RETRACEMENT = 6.3
RESUMPTION_BOS = "2026-09-03 15:00:00+00:00"


def _snapshot(end_timestamp: str | None = None):
    """Um snapshot de producao do BTC H1, opcionalmente truncado em uma vela."""
    if not (CACHE_DIR / "BTCUSDT_1h.json").exists():
        pytest.skip("sem cache BTCUSDT 1h")
    series = load_series("BTCUSDT", TimeFrame.H1)
    if end_timestamp is not None:
        cut = next(
            (i for i, c in enumerate(series) if str(c.timestamp) == end_timestamp), None
        )
        if cut is None:
            pytest.skip(f"{end_timestamp} fora do cache")
        series = series[: cut + 1]
    window = series[max(len(series) - LIMIT - BUFFER, 0) :]
    run = dd._run_internal_structure(
        provider=SliceProvider(window),
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=LIMIT,
        confluence_filter=True,
    )
    return run, detect_structural_stall(run.candles, run.events)


def test_the_snapshot_between_the_trigger_and_the_next_advance_reports_the_stall() -> None:
    """Uma vela antes do CHoCH que fecha a perna: o stall e o daquela perna."""
    run, stall = _snapshot("2026-09-01 15:00:00+00:00")
    assert stall is not None
    assert str(stall.last_advance_timestamp) == LAST_BOS
    assert str(stall.stale_since) == EXPECTED_STALE_SINCE
    assert stall.bars_since_advance == EXPECTED_BARS
    assert stall.retracement_atr == pytest.approx(EXPECTED_RETRACEMENT, abs=0.1)
    assert stall.direction is MarketDirection.BULLISH
    assert stall.last_advance_price == pytest.approx(81270.5)
    # E a maquina nao tinha dito nada nesse meio-tempo: e esse o ponto.
    advances = [
        e
        for e in run.events
        if not e.provisional
        and e.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
        and str(e.timestamp) > LAST_BOS
    ]
    assert advances == []


def test_after_the_resumption_bos_the_old_stall_is_gone() -> None:
    """O stall e da perna VIGENTE; retomada a perna, ele nao pode continuar."""
    _, stall = _snapshot("2026-09-03 20:00:00+00:00")
    if stall is not None:
        assert str(stall.last_advance_timestamp) != LAST_BOS
        assert str(stall.last_advance_timestamp) >= RESUMPTION_BOS
        assert str(stall.stale_since) != EXPECTED_STALE_SINCE
