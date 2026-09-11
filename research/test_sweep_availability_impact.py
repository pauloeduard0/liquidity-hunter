"""Testes do S1.1 — o gate de disponibilidade do sweep.

Tres grupos:

* **known_at** -- a reconstrucao do "quando o sweep passou a existir". Toda a
  medicao do S1.1 pendura nela; se ela errar, erra em silencio.
* **o gate (S1.1-10)** -- o invariante de truncamento: em QUALQUER prefixo `T`,
  nenhum sweep com ``known_at > T`` pode estar acessivel. Este e o teste
  permanente que o protocolo pediu.
* **os consumidores** -- que o braco causal mexe so no que devia mexer: o
  score cai exatamente o peso do fator, o resto do stream fica intacto, e o
  engine de hunt filtra sweep sem filtrar as outras fontes de captura.

Os testes que precisam de serie real pulam quando nao ha cache local.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app.structure_confluence import _FACTOR_WEIGHTS, _SWEEP_LOOKBACK
from liquidity_hunter.core.domain import (
    ConfluenceFactor,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.tests.liquidity.detectors._factories import make_series
from research._offline import OfflineKlinesProvider
from research.sweep_availability_impact import (
    SWEEP_WEIGHT,
    ConfRow,
    Panel,
    _diff_payload,
    _key,
    gated_events,
    known_at_map,
    measure,
)
from research.sweep_causality import lookback_of

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH
SWEEP = StructureEvent.LIQUIDITY_SWEEP
BOS = StructureEvent.BREAK_OF_STRUCTURE


def _events(candles, specs):
    """`specs` = [(indice, evento, direcao, price_level, provisional)]."""
    return [
        MarketStructure(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=candles[i].timestamp,
            event=ev,
            direction=d,
            price_level=level,
            provisional=prov,
        )
        for i, ev, d, level, prov in specs
    ]


# --------------------------------------------------------------------------
# known_at
# --------------------------------------------------------------------------


def test_known_at_is_the_pivot_plus_the_lookback():
    """O sweep e datado em 0, mas o pivo que o disparou e o topo em 3."""
    candles = make_series([10.0, 11.0, 12.0, 20.0, 12.0], [5.0] * 5)
    events = _events(candles, [(0, SWEEP, BULL, 20.0, False)])
    known = known_at_map(candles, events, TimeFrame.H1)
    assert known[candles[0].timestamp] == 3 + lookback_of(TimeFrame.H1)


def test_known_at_never_precedes_the_timestamp():
    candles = make_series([10.0, 11.0, 12.0], [5.0] * 3)
    events = _events(candles, [(2, SWEEP, BULL, 12.0, False)])
    known = known_at_map(candles, events, TimeFrame.H1)
    assert known[candles[2].timestamp] >= 2


def test_known_at_falls_back_to_the_sweep_candle_when_no_pivot_matches():
    """Sem casamento exato de extremo o piso e conservador, nao ausente."""
    candles = make_series([10.0, 11.0, 12.0], [5.0] * 3)
    events = _events(candles, [(0, SWEEP, BULL, 999.0, False)])
    known = known_at_map(candles, events, TimeFrame.H1)
    assert known[candles[0].timestamp] == 0 + lookback_of(TimeFrame.H1)


def test_known_at_skips_provisional_sweeps():
    """O S1 mediu apenas confirmados; um provisional nao entra no mapa."""
    candles = make_series([10.0, 11.0, 12.0], [5.0] * 3)
    events = _events(candles, [(0, SWEEP, BULL, 12.0, True)])
    assert known_at_map(candles, events, TimeFrame.H1) == {}


def test_known_at_ignores_non_sweep_events():
    candles = make_series([10.0, 11.0, 12.0], [5.0] * 3)
    events = _events(candles, [(0, BOS, BULL, 12.0, False)])
    assert known_at_map(candles, events, TimeFrame.H1) == {}


def test_bearish_sweep_matches_the_pivot_low_not_the_high():
    candles = make_series([20.0] * 5, [10.0, 9.0, 3.0, 9.0, 10.0])
    events = _events(candles, [(0, SWEEP, BEAR, 3.0, False)])
    known = known_at_map(candles, events, TimeFrame.H1)
    assert known[candles[0].timestamp] == 2 + lookback_of(TimeFrame.H1)


# --------------------------------------------------------------------------
# S1.1-10 — o invariante de truncamento
# --------------------------------------------------------------------------


def _gate_fixture():
    candles = make_series([10.0, 11.0, 12.0, 20.0, 12.0, 13.0], [5.0] * 6)
    events = _events(
        candles,
        [(0, SWEEP, BULL, 20.0, False), (1, BOS, BULL, 11.0, False)],
    )
    return candles, events, known_at_map(candles, events, TimeFrame.H1)


def test_no_sweep_with_known_at_beyond_the_prefix_ever_survives():
    """S1.1-10: o invariante, verificado em TODOS os prefixos."""
    candles, events, known = _gate_fixture()
    for upto in range(len(candles)):
        for e in gated_events(events, known, upto):
            if e.event is SWEEP:
                assert known[e.timestamp] <= upto


def test_the_gate_removes_nothing_that_is_not_a_sweep():
    candles, events, known = _gate_fixture()
    for upto in range(len(candles)):
        kept = gated_events(events, known, upto)
        assert [e for e in kept if e.event is not SWEEP] == [
            e for e in events if e.event is not SWEEP
        ]


def test_the_gate_is_monotonic_in_the_prefix():
    """Esperar mais nunca esconde o que ja estava visivel."""
    candles, events, known = _gate_fixture()
    for upto in range(len(candles) - 1):
        a = {e.timestamp for e in gated_events(events, known, upto)}
        b = {e.timestamp for e in gated_events(events, known, upto + 1)}
        assert a <= b


def test_a_sweep_becomes_visible_exactly_at_its_known_at():
    candles, events, known = _gate_fixture()
    k = known[candles[0].timestamp]
    assert not any(e.event is SWEEP for e in gated_events(events, known, k - 1))
    assert any(e.event is SWEEP for e in gated_events(events, known, k))


def test_a_sweep_with_no_measured_known_at_is_never_removed():
    """Sem medida nao se afirma indisponibilidade -- o gate nao chuta."""
    candles, events, _ = _gate_fixture()
    assert gated_events(events, {}, 0) == events


# --------------------------------------------------------------------------
# confluencia: o score cai exatamente o peso do fator
# --------------------------------------------------------------------------


def test_the_confluence_cap_never_binds():
    """A soma de TODOS os pesos e 100, entao remover 9 nunca esbarra no min()."""
    assert sum(_FACTOR_WEIGHTS.values()) == 100.0
    assert SWEEP_WEIGHT == _FACTOR_WEIGHTS[ConfluenceFactor.LIQUIDITY_SWEEP]


def _row(**kw) -> ConfRow:
    base = dict(
        symbol="BTCUSDT", timeframe="1h", kind="bos", direction="bullish",
        provisional=False, score=50.0, n_factors=3, had_sweep=True,
        causal_at_break=True, causal_at_window=True, forward_window=False,
        aligned_legacy=True, aligned_causal=True, aligned_causal_window=True,
    )
    return ConfRow(**(base | kw))


def test_losing_the_sweep_costs_exactly_the_factor_weight():
    row = _row(causal_at_break=False, causal_at_window=False)
    assert row.causal_score(at_window=False) == 50.0 - SWEEP_WEIGHT


def test_keeping_the_sweep_leaves_the_score_untouched():
    assert _row().causal_score(at_window=False) == 50.0


def test_an_event_that_never_had_the_factor_cannot_lose_it():
    row = _row(had_sweep=False, causal_at_break=False, causal_at_window=False)
    assert row.lost_at_break is False
    assert row.causal_score(at_window=False) == 50.0


def test_the_window_gate_is_stricter_than_the_break_gate_for_a_bos():
    """Um BOS afirma que o sweep PRECEDEU a quebra: a janela para em ev-1."""
    assert _SWEEP_LOOKBACK > 0
    row = _row(causal_at_break=True, causal_at_window=False)
    assert row.lost_at_break is False
    assert row.lost_at_window is True


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def test_the_diff_keys_rows_by_identity_not_by_position():
    a = [{"start_timestamp": "t1", "capture_score": 1.0},
         {"start_timestamp": "t2", "capture_score": 2.0}]
    b = [{"start_timestamp": "t2", "capture_score": 2.0}]
    d = _diff_payload(a, b)
    assert d["removed"] == 1 and d["added"] == 0 and d["changed"] == 0


def test_the_diff_reports_which_field_changed():
    a = [{"start_timestamp": "t1", "capture_score": 1.0}]
    b = [{"start_timestamp": "t1", "capture_score": 2.0}]
    d = _diff_payload(a, b)
    assert d["changed"] == 1 and d["fields"] == {"capture_score": 1}


def test_distinct_rows_get_distinct_keys():
    assert _key({"start_timestamp": "t1"}) != _key({"start_timestamp": "t2"})


def test_the_diff_handles_a_single_object_stream():
    d = _diff_payload({"phase": "a"}, {"phase": "b"})
    assert d["changed"] == 1 and d["fields"] == {"phase": 1}


# --------------------------------------------------------------------------
# ponta a ponta, sobre serie real
# --------------------------------------------------------------------------


@pytest.fixture
def cached():
    provider = OfflineKlinesProvider()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        if provider.has(symbol, TimeFrame.H1):
            return provider, symbol
    pytest.skip("sem cache local de klines")


def test_the_panel_measures_every_consumer_on_a_real_series(cached):
    provider, symbol = cached
    panel = Panel()
    measure(provider, symbol, TimeFrame.H1, 600, panel)
    assert panel.conf and panel.hunt and panel.oi
    assert not panel.errors


def test_every_sweep_on_a_real_series_is_dated_before_it_is_knowable(cached):
    """O achado central do S1, reconfirmado pelo mapa que o S1.1 usa."""
    from liquidity_hunter.app.dashboard_data import load_dashboard_data
    from research._paginated import NoFuturesProvider

    provider, symbol = cached
    data = load_dashboard_data(
        provider=provider, symbol=symbol, timeframe=TimeFrame.H1, limit=600,
        futures_provider=NoFuturesProvider(),
    )
    idx = {c.timestamp: i for i, c in enumerate(data.candles)}
    known = known_at_map(data.candles, data.internal_structure_events, TimeFrame.H1)
    assert known
    assert all(known[ts] > idx[ts] for ts in known if ts in idx)
