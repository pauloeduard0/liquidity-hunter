"""A auditoria mede o que diz medir? (obrigatorio antes de ler o numero)

`research/choch_failed_stall_audit.py` COPIA a aritmetica de
`detect_structural_stall` para poder impor um opener -- e uma copia pode
divergir em silencio da funcao de producao, que e justamente a referencia da
medida. Estes testes prendem as duas sobre serie real e verificam as invariantes
da classificacao.

Fora de `liquidity_hunter/tests` porque dependem do cache de klines de research:

    poetry run pytest research/test_choch_failed_stall_audit.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import (
    _last_advance,
    detect_structural_stall,
)
from research.choch_failed_stall_audit import (
    BTC_CASE,
    K,
    N,
    _counterfactual,
    audit_run,
    reaffirmed,
    summarize,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series


def _run(symbol: str, timeframe: TimeFrame, window: int = 0):
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    if not path.exists():
        pytest.skip(f"sem cache: {symbol} {timeframe.value}")
    series = load_series(symbol, timeframe)
    end = len(series) - window * LIMIT
    start = end - LIMIT - BUFFER
    if start < 0:
        pytest.skip(f"serie curta: {symbol} {timeframe.value}")
    return dd._run_internal_structure(
        provider=SliceProvider(series[start:end]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


def test_reaffirmed_inverts_the_failed_choch_direction() -> None:
    """`✕.direction` e a do CHoCH que falhou; a estrutura retomada e a oposta."""
    assert reaffirmed(MarketDirection.BULLISH) is MarketDirection.BEARISH
    assert reaffirmed(MarketDirection.BEARISH) is MarketDirection.BULLISH


def test_counterfactual_reproduces_production_when_opener_is_the_real_one() -> None:
    """Com o opener que `_last_advance` escolhe, a copia = `detect_structural_stall`.

    O unico grau de liberdade do contrafactual e o opener. Impondo o opener REAL,
    as duas tem que devolver o mesmo candle e a mesma retracao -- se divergirem, o
    numero da auditoria nao mede o guard, mede a copia.
    """
    checked = 0
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for timeframe in (TimeFrame.M15, TimeFrame.H1, TimeFrame.H4):
            for window in (0, 1, 2):
                run = _run(symbol, timeframe, window)
                index_by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
                advance = _last_advance(run.events, index_by_ts)
                if advance is None:
                    continue
                opener, opener_index = advance
                if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
                    continue
                production = detect_structural_stall(run.candles, run.events, n=N, k_atr=K)
                copy = _counterfactual(run.candles, opener, opener_index, n=N, k=K)
                checked += 1
                if production is None:
                    assert copy is None
                    continue
                assert copy is not None
                index, bars, retracement = copy
                assert run.candles[index].timestamp == production.stale_since
                assert bars == production.bars_since_advance
                assert retracement == pytest.approx(production.retracement_atr)
    assert checked > 0


def test_btc_h1_case_is_class_a_with_the_additive_continuation_bos() -> None:
    """O caso que originou a auditoria: `✕` seguido do BOS bullish 3 velas depois."""
    symbol, timeframe, timestamp = BTC_CASE
    run = _run(symbol, TimeFrame(timeframe))
    cases = audit_run(symbol, TimeFrame(timeframe), 0, run.events, run.candles)
    case = next((c for c in cases if c.timestamp == timestamp), None)
    if case is None:
        pytest.skip("cache anterior a 2026-09-03")
    assert case.klass == "A"
    assert case.direction == "bearish"
    assert case.reaffirmed == "bullish"
    assert case.next_event == "break_of_structure"
    assert case.next_direction == "bullish"
    assert case.next_provisional is False
    assert case.next_bars == 3


def test_classification_invariants_hold_on_real_series() -> None:
    """A/B/C sao exaustivos e A significa exatamente o que o relatorio afirma."""
    cases = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timeframe in (TimeFrame.M15, TimeFrame.H1, TimeFrame.H4):
            run = _run(symbol, timeframe)
            cases.extend(audit_run(symbol, timeframe, 0, run.events, run.candles))
    assert cases
    for case in cases:
        assert case.klass in ("A", "B", "C")
        assert case.reaffirmed != case.direction
        if case.klass == "A":
            assert case.next_event == "break_of_structure"
            assert case.next_direction == case.reaffirmed
        if case.klass == "C":
            # Nenhum advance depois: o ✕ e o ultimo, e o gap vai ate a borda.
            assert case.next_event is None
            assert case.gap_bars == case.bars_to_window_end
        else:
            assert case.next_bars is not None and case.next_bars > 0
            assert case.gap_bars == case.next_bars
        # Uma perna so conta como perdida se o gatilho cai onde o guard manda.
        if case.cf_in_gap:
            assert case.cf_trigger_index is not None
            assert case.index <= case.cf_trigger_index <= case.index + case.gap_bars


def test_summary_totals_are_consistent() -> None:
    cases = []
    for timeframe in (TimeFrame.M15, TimeFrame.H1):
        run = _run("BTCUSDT", timeframe)
        cases.extend(audit_run("BTCUSDT", timeframe, 0, run.events, run.candles))
    summary = summarize(cases, runs=2)
    assert (
        summary["class_A_bos_reaffirmed"]
        + summary["class_B_other_advance"]
        + summary["class_C_no_advance"]
        == summary["total_choch_failed"]
        == len(cases)
    )
    assert summary["class_C_truncated_by_window"] <= summary["class_C_no_advance"]
    assert summary["blocked_stales_class_C"] <= summary["blocked_stales"]
    assert sum(summary["blocked_outcomes"].values()) == summary["blocked_stales"]
