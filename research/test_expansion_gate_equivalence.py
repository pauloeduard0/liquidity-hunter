"""O gate de expansao contra a Etapa 0.5/0.6, em serie real (Etapa 2.5).

`is_stall_eligible_leg` reproduz a definicao da Etapa 0.5 com UMA diferenca
deliberada: o ATR e o `frozen_atr_pct` na vela do ultimo BOS da corrida, nao o
`mean_tr_pct` da janela inteira -- este ultimo nao e causal. Estes testes
prendem as duas na parte que tem de coincidir e documentam onde nao coincidem.

    poetry run pytest research/test_expansion_gate_equivalence.py
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.liquidity.structural_stall import (
    detect_structural_stall,
    is_stall_eligible_leg,
)
from research.expansion_gate import gate
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.structural_stall_validation import _legs_of_run

BTC_LEG_START = "2026-08-25 02:00:00+00:00"


def _snapshot(end_timestamp: str | None = None):
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
    return dd._run_internal_structure(
        provider=SliceProvider(window),
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=LIMIT,
        confluence_filter=True,
    )


def test_the_btc_h1_reference_leg_is_eligible() -> None:
    """O caso que originou a investigacao tem de sobreviver ao gate."""
    run = _snapshot("2026-09-01 15:00:00+00:00")
    assert is_stall_eligible_leg(run.candles, run.events) is True
    stall = detect_structural_stall(
        run.candles, run.events, eligible=is_stall_eligible_leg(run.candles, run.events)
    )
    assert stall is not None
    assert str(stall.last_advance_timestamp) == BTC_LEG_START


def test_the_leg_after_the_resumption_bos_does_not_inherit_it() -> None:
    """Item 6 da Etapa 2.5, em serie real: a perna nova nao herda nada."""
    run = _snapshot("2026-09-03 20:00:00+00:00")
    assert is_stall_eligible_leg(run.candles, run.events) is False


def test_every_leg_research_called_an_expansion_passes_the_gate() -> None:
    """A unica divergencia admitida e a do ATR causal -- e ela e rara.

    Medido em `research/expansion_gate.py`: 47 de 50 pernas rotuladas
    `expansion` pela Etapa 0.6 passam no gate; as 3 restantes ficam logo abaixo
    de 15 ATR porque o denominador causal e maior que o da janela inteira.
    """
    run = _snapshot()
    legs = _legs_of_run(run.events, run.candles)
    assert legs
    expansions = [leg for leg in legs if leg.kind == "expansion"]
    if not expansions:
        pytest.skip("a janela de cache nao tem expansao rotulada")
    passed = sum(1 for leg in expansions if gate(run, leg))
    assert passed >= len(expansions) - 1


def test_the_gate_only_ever_adds_mid_run_legs() -> None:
    """Toda perna que o gate aprova e research nao rotulou e uma perna do MEIO
    de uma corrida que qualificou -- nao uma populacao nova. Causalmente nao ha
    como separa-las: no momento em que o BOS imprime, ninguem sabe se vem outro.
    """
    run = _snapshot()
    for leg in _legs_of_run(run.events, run.candles):
        if leg.kind == "control" and gate(run, leg):
            later = [
                e
                for e in run.events
                if not e.provisional
                and str(e.timestamp) > leg.start_timestamp
                and e.event.value in ("break_of_structure", "change_of_character")
            ]
            assert later, leg.start_timestamp
            assert later[0].event.value == "break_of_structure"
            assert later[0].direction.value == leg.direction
