"""Testes do S7 — o fator de sweep e o lado da quebra.

Tres grupos:

* **S7.0, a regra de alinhamento** -- a definicao formal, que todo o resto
  pendura nela. Se ela estiver invertida, o estudo inteiro inverte.
* **classificacao** -- aligned / wrong_side / ambiguous / missing / none, e a
  versao so-para-tras (`klass_bwd`), que e o teste que separa "lado errado" de
  "ainda nao aconteceu".
* **o mecanismo** -- que `direction` de um sweep e a tendencia vigente
  invertida, e nao um fato independente. E o achado que decide o S7, entao e
  testado sobre serie real, nao so em fixture.

Os testes que precisam de serie real pulam quando nao ha cache local.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app.structure_confluence import _FACTOR_WEIGHTS, _SWEEP_LOOKBACK
from liquidity_hunter.core.domain import (
    ConfluenceFactor,
    MarketDirection,
    TimeFrame,
)
from research._offline import OfflineKlinesProvider
from research.sweep_directional_confluence import (
    BLOCKS,
    SWEEP_WEIGHT,
    Event,
    extract,
    is_aligned,
)

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH
NEUT = MarketDirection.NEUTRAL


# --------------------------------------------------------------------------
# S7.0 — a regra
# --------------------------------------------------------------------------


def test_a_bullish_break_is_fuelled_by_a_bearish_sweep():
    """"a bullish reversal sweeps the lows, a wick-down/bearish-labeled sweep"."""
    assert is_aligned(BULL, BEAR) is True


def test_a_bearish_break_is_fuelled_by_a_bullish_sweep():
    assert is_aligned(BEAR, BULL) is True


def test_a_sweep_on_the_same_side_as_the_break_is_not_aligned():
    """Tomou a liquidez do lado para onde o preco foi: e o alvo, nao o combustivel."""
    assert is_aligned(BULL, BULL) is False
    assert is_aligned(BEAR, BEAR) is False


def test_alignment_is_symmetric_between_the_two_directions():
    assert is_aligned(BULL, BEAR) == is_aligned(BEAR, BULL)
    assert is_aligned(BULL, BULL) == is_aligned(BEAR, BEAR)


def test_a_neutral_direction_is_missing_not_wrong():
    """"missing" e uma classe propria: nao se conta como lado errado."""
    assert is_aligned(NEUT, BULL) is None
    assert is_aligned(BULL, NEUT) is None


# --------------------------------------------------------------------------
# score e ✦N
# --------------------------------------------------------------------------


def _event(**kw) -> Event:
    base = dict(
        symbol="BTCUSDT", timeframe="1h", kind="choch", direction="bullish",
        provisional=False, ev_idx=100, timestamp="t", score=40.0,
        factors=["htf_alignment", "vsa_volume", "liquidity_sweep"],
        n_credits=1, n_aligned=0, n_wrong=1, n_missing=0,
        klass="wrong_side", klass_bwd="wrong_side", block=0,
        co_bos=None, co_grab=None, co_raid=None, co_supertrend=None, co_vsa=None,
    )
    return Event(**(base | kw))


def test_the_confluence_cap_never_binds():
    """A soma de TODOS os pesos e 100, entao remover 9 nunca esbarra no min()."""
    assert sum(_FACTOR_WEIGHTS.values()) == 100.0
    assert SWEEP_WEIGHT == _FACTOR_WEIGHTS[ConfluenceFactor.LIQUIDITY_SWEEP]


def test_an_event_with_only_wrong_side_credits_loses_the_factor():
    e = _event()
    assert e.keeps_under_directional is False
    assert e.directional_score() == 40.0 - SWEEP_WEIGHT
    assert e.n_factors_directional() == 2


def test_one_aligned_credit_is_enough_to_keep_the_factor():
    """O engine e um `any()`: um sweep alinhado na janela basta."""
    e = _event(n_aligned=1, n_wrong=1, n_credits=2, klass="ambiguous")
    assert e.keeps_under_directional is True
    assert e.directional_score() == 40.0
    assert e.n_factors_directional() == 3


def test_an_event_without_any_sweep_cannot_lose_the_factor():
    e = _event(n_credits=0, n_wrong=0, klass="none",
               factors=["htf_alignment", "vsa_volume"])
    assert e.had_sweep is False
    assert e.directional_score() == 40.0
    assert e.n_factors_directional() == 2


# --------------------------------------------------------------------------
# ponta a ponta, sobre serie real
# --------------------------------------------------------------------------


@pytest.fixture
def panel():
    from liquidity_hunter.app.dashboard_data import load_dashboard_data
    from research._paginated import NoFuturesProvider

    provider = OfflineKlinesProvider()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        if provider.has(symbol, TimeFrame.H1):
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=TimeFrame.H1,
                limit=1200, compute_narrative=False,
                futures_provider=NoFuturesProvider(),
            )
            return extract(data)
    pytest.skip("sem cache local de klines")


def test_the_panel_produces_events_and_credits(panel):
    evs, crs = panel
    assert evs and crs


def test_every_credit_is_classified(panel):
    _evs, crs = panel
    assert all(c.aligned in (True, False, None) for c in crs)
    assert all(c.when in ("before", "same", "after") for c in crs)


def test_the_bos_window_never_reaches_past_the_break(panel):
    """`sweep_lo, sweep_hi = ev_idx - 10, ev_idx - 1`: estritamente para tras."""
    _evs, crs = panel
    bos = [c for c in crs if c.kind == "bos"]
    assert bos
    assert all(c.offset < 0 for c in bos)
    assert all(-_SWEEP_LOOKBACK <= c.offset for c in bos)


def test_a_bos_credit_is_never_in_the_forward_window(panel):
    _evs, crs = panel
    assert not any(c.in_forward for c in crs if c.kind == "bos")


def test_sweeps_are_emitted_against_the_standing_trend(panel):
    """O mecanismo do S7.7: `direction` e a tendencia vigente, invertida.

    Se isto cair, a leitura central do S7 -- que 'alinhado' e um proxy de
    'depois do flip' -- deixa de valer.
    """
    _evs, crs = panel
    known = [c for c in crs if c.counter_trend is not None]
    assert known
    rate = sum(1 for c in known if c.counter_trend) / len(known)
    assert rate > 0.85, f"so {rate:.1%} dos sweeps sao contra-tendencia"


def test_alignment_tracks_timing_for_choch(panel):
    """S7.7: para o CHoCH, 'alinhado' e quase o mesmo teste que 'depois'."""
    _evs, crs = panel
    ch = [c for c in crs if c.kind == "choch" and c.aligned is not None]
    if len(ch) < 10:
        pytest.skip("poucos creditos de CHoCH nesta serie")
    agree = sum(1 for c in ch if c.aligned is (c.when == "after")) / len(ch)
    assert agree > 0.80, f"concordancia de so {agree:.1%}"


def test_the_backward_class_never_invents_a_credit(panel):
    """`klass_bwd` so pode ser mais pobre que `klass`, nunca mais rico."""
    evs, _crs = panel
    for e in evs:
        if e.klass == "none":
            assert e.klass_bwd == "none"


def test_a_bos_class_is_identical_backward(panel):
    """A janela do BOS ja e para tras, entao reclassificar nao muda nada."""
    evs, _crs = panel
    for e in evs:
        if e.kind == "bos":
            assert e.klass == e.klass_bwd


def test_blocks_partition_the_series(panel):
    evs, _crs = panel
    assert {e.block for e in evs} <= set(range(BLOCKS))
