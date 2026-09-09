"""A funcao de producao reproduz a Etapa 0.6? (obrigatorio)

`liquidity.structural_stall.detect_structural_stall` foi escrita a partir de
`research/structural_stall_validation.py`, e as duas podem divergir em silencio
-- a de producao NAO importa a de research (camadas diferentes) e re-deriva
tanto os advances quanto o ATR. Estes testes prendem uma na outra sobre serie
real, incluindo o caso BTC H1 de 2026-08-25 que originou a investigacao.

Uma diferenca de escopo, deliberada e verificada aqui: research varre a perna
inteira (que termina no proximo advance) porque estava medindo a populacao; a
funcao de producao le a perna VIGENTE, a que nao acabou. Para compara-las, a
serie e truncada no fim da perna -- que e o mesmo que perguntar "o que a
producao teria dito ao vivo, na ultima vela antes do proximo advance".

Fora de `liquidity_hunter/tests` porque depende do cache de klines de research:

    poetry run pytest research/test_structural_stall_equivalence.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.structural_stall_validation import (
    _legs_of_run,
    trailing_atr,
    triggers_of_leg,
)

N, K = 50, 6.0
BTC_LEG_START = "2026-08-25 02:00:00+00:00"


def _run(symbol: str, timeframe: TimeFrame, window: int = 0):
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    if not path.exists():
        pytest.skip(f"sem cache: {symbol} {timeframe.value}")
    series = load_series(symbol, timeframe)
    end = len(series) - window * LIMIT
    start = end - LIMIT - BUFFER
    return dd._run_internal_structure(
        provider=SliceProvider(series[start:end]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


def _compare_leg(run, leg) -> tuple[object, object]:
    """(trigger de research, stall de producao) para a mesma perna.

    A serie da producao para uma vela ANTES do advance que fecha a perna. Nao e
    conveniencia: incluir esse advance e perguntar outra coisa -- com ele na
    serie a perna vigente ja e a seguinte, e a producao devolve `None` porque
    quem fecha a perna e a maquina, nao o stall. A pergunta certa e "o que a
    producao teria dito na ultima vela em que essa perna ainda era a vigente".
    A perna final da janela nao tem advance de fechamento e vai inteira.
    """
    research = triggers_of_leg(
        leg, run.events, run.candles, None, n=N, k=K, price_mode="close", atr_mode="frozen"
    )
    if research is not None and leg.start_index + research.bars_since_advance >= leg.end_index:
        # O trigger cairia na propria vela do advance de fechamento: a producao
        # nao tem como ve-lo ao vivo, e comparar ali seria comparar cenarios
        # diferentes. Fora da amostra, contado no teste que chama.
        return None, None
    closed = leg.end_index < len(run.candles) - 1
    candles = run.candles[: leg.end_index] if closed else run.candles[: leg.end_index + 1]
    if not candles:
        return None, None
    events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
    production = detect_structural_stall(candles, events, n=N, k_atr=K)
    return research, production


def test_the_advance_rule_matches_the_pipelines_own() -> None:
    """O `_last_advance` da producao espelha `dd._advance_boundaries`."""
    from liquidity_hunter.liquidity.structural_stall import _last_advance

    run = _run("BTCUSDT", TimeFrame.H1)
    advances = dd._advance_boundaries(list(run.events), list(run.candles))
    assert advances, "a janela deveria ter advances"
    index_by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
    found = _last_advance(run.events, index_by_ts)
    assert found is not None
    assert found[1] == advances[-1][0]


def test_the_frozen_atr_matches_the_research_trailing_atr() -> None:
    from liquidity_hunter.liquidity.structural_stall import frozen_atr_pct

    run = _run("BTCUSDT", TimeFrame.H1)
    reference = trailing_atr(run.candles)
    for index in (1, 50, 300, len(run.candles) - 1):
        assert frozen_atr_pct(run.candles, index) == pytest.approx(reference[index])


def test_the_btc_h1_reference_case_reproduces() -> None:
    """O caso que originou a investigacao, no par validado N=50/K=6."""
    run = _run("BTCUSDT", TimeFrame.H1)
    leg = next(
        (
            leg
            for leg in _legs_of_run(run.events, run.candles)
            if leg.start_timestamp == BTC_LEG_START
        ),
        None,
    )
    if leg is None:
        pytest.skip("a perna BTC H1 de referencia saiu da janela de cache")
    research, production = _compare_leg(run, leg)
    assert research is not None
    assert production is not None
    assert str(production.stale_since) == research.timestamp
    assert production.bars_since_advance == research.bars_since_advance
    assert production.direction.value == research.direction


def test_every_leg_of_the_btc_h1_window_agrees() -> None:
    run = _run("BTCUSDT", TimeFrame.H1)
    legs = _legs_of_run(run.events, run.candles)
    assert legs
    checked = 0
    for leg in legs:
        research, production = _compare_leg(run, leg)
        if research is None:
            assert production is None, f"producao disparou onde research nao: {leg.start_timestamp}"
            continue
        assert production is not None, f"research disparou onde producao nao: {leg.start_timestamp}"
        assert str(production.stale_since) == research.timestamp
        assert production.bars_since_advance == research.bars_since_advance
        assert production.retracement_atr == pytest.approx(research.retracement_atr, abs=0.05)
        checked += 1
    assert checked, "nenhuma perna disparou -- o teste nao provaria nada"


@pytest.mark.parametrize(
    ("symbol", "timeframe"),
    [
        ("BTCUSDT", TimeFrame.M15),
        ("ETHUSDT", TimeFrame.H1),
        ("SOLUSDT", TimeFrame.H4),
        ("NEARUSDT", TimeFrame.H1),
        ("AAVEUSDT", TimeFrame.H4),
    ],
)
def test_the_two_implementations_agree_across_the_matrix(
    symbol: str, timeframe: TimeFrame
) -> None:
    run = _run(symbol, timeframe)
    for leg in _legs_of_run(run.events, run.candles):
        research, production = _compare_leg(run, leg)
        if research is None:
            assert production is None, f"{symbol} {timeframe.value} {leg.start_timestamp}"
        else:
            assert production is not None, f"{symbol} {timeframe.value} {leg.start_timestamp}"
            assert str(production.stale_since) == research.timestamp
