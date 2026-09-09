"""Testes da Etapa 4.3 -- o que precisa ser verdade para a medicao valer.

O risco desta etapa nao e o codigo quebrar, e ele medir a coisa errada sem
avisar: uma feature que le uma vela adiante, um alvo que vira feature, um
gate que aceita `None` como default. Cada teste aqui prende um desses.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import MarketDirection, StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import frozen_atr_pct
from research.choch_establishment import (
    COHERENT,
    HORIZONS,
    NUMERIC,
    ZEC_CASE,
    auc,
    choch_legs,
    features_at,
    gate_passes,
    quality,
    split_holdout,
    swing_level,
    swing_lookback_of,
    univariate,
)
from research.choch_leg_opener import advance_indices
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

PANEL = [
    ("BTCUSDT", TimeFrame.H1),
    ("ETHUSDT", TimeFrame.H4),
    ("SOLUSDT", TimeFrame.M15),
    ("NEARUSDT", TimeFrame.H4),
    ("AAVEUSDT", TimeFrame.H1),
    ("ZECUSDT", TimeFrame.D1),
]


def run_of(symbol: str, timeframe: TimeFrame, window: int = 0):
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    if not path.exists():
        pytest.skip(f"sem cache: {symbol} {timeframe.value}")
    series = load_series(symbol, timeframe)
    end = len(series) - window * LIMIT
    start = end - LIMIT - BUFFER
    if start < 0:
        pytest.skip("serie curta")
    return dd._run_internal_structure(
        provider=SliceProvider(series[start:end]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


@pytest.fixture(scope="module")
def panel():
    out = []
    for symbol, timeframe in PANEL:
        try:
            run = run_of(symbol, timeframe)
        except Exception:  # noqa: BLE001 - cache sujo nao invalida o teste
            continue
        out.append((symbol, timeframe, run, choch_legs(run, symbol, timeframe, 0)))
    if not out:
        pytest.skip("nenhum simbolo do painel disponivel")
    return out


# --------------------------------------------------------------------------
# causalidade
# --------------------------------------------------------------------------


def test_features_nao_leem_uma_vela_alem_do_cut(panel):
    """Truncar a serie exatamente no `cut` nao pode mudar nenhuma feature.

    Este e o teste que autoriza chamar as features de causais: se truncar
    mudasse alguma coisa, ela estaria lendo o futuro.
    """
    compared = 0
    for _symbol, _tf, run, legs in panel:
        candles = run.candles
        by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
        advances = {event.timestamp: event for event, _ in advance_indices(run.events, by_ts)}
        for leg in legs[:20]:
            opener = advances.get(
                next(
                    ts for ts in advances if ts.isoformat() == leg.opener_timestamp
                )
            )
            index = leg.opener_index
            atr = frozen_atr_pct(candles, index)
            for horizon in HORIZONS:
                cut = index + horizon
                if cut > index + leg.leg_bars or cut >= len(candles):
                    continue
                lookback = swing_lookback_of(_tf)
                full = features_at(
                    candles, run.events, opener, index, cut, atr, lookback
                )
                truncated = features_at(
                    candles[: cut + 1],
                    [e for e in run.events if e.timestamp <= candles[cut].timestamp],
                    opener,
                    index,
                    cut,
                    atr,
                    lookback,
                )
                assert full == truncated
                compared += 1
    assert compared >= 20, f"o teste de causalidade comparou so {compared} snapshots"


def test_o_nivel_do_proximo_bos_e_causal_e_do_lado_certo(panel):
    """O swing derivado nao pode ler alem do `cut`, nem sair do lado errado.

    Ele substituiu uma leitura do stream de eventos que devolvia `None`
    sempre: o detector nunca emite `lower_low` / `higher_high`, so os pivos de
    pullback. Sem este teste a feature voltaria a ser silenciosamente vazia.
    """
    compared = 0
    for _symbol, timeframe, run, legs in panel:
        candles = run.candles
        lookback = swing_lookback_of(timeframe)
        for leg in legs[:20]:
            index = leg.opener_index
            for horizon in HORIZONS:
                cut = index + horizon
                if cut > index + leg.leg_bars or cut >= len(candles):
                    continue
                level = swing_level(
                    candles, index, cut, lookback, MarketDirection(leg.direction)
                )
                if level is None:
                    continue
                truncated = swing_level(
                    candles[: cut + 1], index, cut, lookback,
                    MarketDirection(leg.direction),
                )
                assert level == truncated
                window = candles[index : cut + 1]
                if leg.direction == MarketDirection.BEARISH.value:
                    assert level >= min(candle.low for candle in window)
                else:
                    assert level <= max(candle.high for candle in window)
                compared += 1
    assert compared >= 10, f"so {compared} niveis derivados -- feature vazia de novo?"


def test_o_atr_congelado_e_o_da_producao(panel):
    """A unidade das features e a mesma que o `detect_structural_stall` usa."""
    for _symbol, _tf, run, legs in panel:
        for leg in legs[:10]:
            assert leg.frozen_atr == pytest.approx(
                frozen_atr_pct(run.candles, leg.opener_index), abs=1e-6
            )


# --------------------------------------------------------------------------
# populacao e alvo
# --------------------------------------------------------------------------


def test_a_populacao_e_so_de_choch_nao_provisional(panel):
    for _symbol, _tf, run, legs in panel:
        by_ts = {c.timestamp: i for i, c in enumerate(run.candles)}
        events = {
            event.timestamp.isoformat(): event
            for event, _ in advance_indices(run.events, by_ts)
        }
        for leg in legs:
            event = events[leg.opener_timestamp]
            assert event.event is StructureEvent.CHANGE_OF_CHARACTER
            assert not event.provisional


def test_established_significa_bos_da_mesma_direcao(panel):
    """O alvo e exatamente "o proximo advance e um BOS meu", e nada mais."""
    checked = 0
    for _symbol, _tf, _run, legs in panel:
        for leg in legs:
            if leg.established:
                assert leg.closed_by == StructureEvent.BREAK_OF_STRUCTURE.value
                assert leg.closed_by_direction == leg.direction
                assert leg.bars_to_first_bos is not None
                checked += 1
            else:
                assert leg.bars_to_first_bos is None
                assert not (
                    leg.closed_by == StructureEvent.BREAK_OF_STRUCTURE.value
                    and leg.closed_by_direction == leg.direction
                )
    assert checked >= 5


def test_failed_40_e_80_sao_consistentes_com_o_tempo(panel):
    for _symbol, _tf, _run, legs in panel:
        for leg in legs:
            if not leg.established:
                assert leg.failed_40 and leg.failed_80
                continue
            bars = leg.bars_to_first_bos
            assert leg.failed_40 == (bars > 40)
            assert leg.failed_80 == (bars > 80)
            # 80 e mais frouxo que 40: nunca pode falhar em 80 e passar em 40.
            assert not (leg.failed_80 and not leg.failed_40)


def test_o_alvo_nao_esta_entre_as_features():
    """`established` / `failed_*` sao alvo. Se vazarem para `NUMERIC`, a AUC
    de 1.0 seria tautologia -- este teste existe para isso nao passar calado."""
    for name in ("established", "failed_40", "failed_80", "bars_to_first_bos"):
        assert name not in NUMERIC


def test_pivos_coerentes_sao_os_da_direcao():
    assert COHERENT[MarketDirection.BEARISH] == frozenset(
        {StructureEvent.LOWER_HIGH, StructureEvent.LOWER_LOW}
    )
    assert COHERENT[MarketDirection.BULLISH] == frozenset(
        {StructureEvent.HIGHER_HIGH, StructureEvent.HIGHER_LOW}
    )


# --------------------------------------------------------------------------
# o gate
# --------------------------------------------------------------------------


def test_feature_ausente_nunca_satisfaz_o_gate(panel):
    """`None` nao vira default silencioso (a licao da Etapa 4.2)."""
    for _symbol, _tf, _run, legs in panel:
        for leg in legs:
            if "stale" not in leg.at:
                assert not gate_passes(leg, {"mfe_atr": ("<=", 999.0)})
            elif leg.at["stale"]["distance_to_bos_atr"] is None:
                assert not gate_passes(leg, {"distance_to_bos_atr": (">=", 0.0)})


def test_o_gate_e_monotonico(panel):
    """Afrouxar um limiar so pode manter ou aumentar o que passa."""
    legs = [leg for _s, _t, _r, group in panel for leg in group if "stale" in leg.at]
    if not legs:
        pytest.skip("nenhuma perna de CHoCH com STALE no painel")
    previous = -1
    for threshold in (0.0, 2.0, 5.0, 20.0, 1000.0):
        count = sum(1 for leg in legs if gate_passes(leg, {"mfe_atr": ("<=", threshold)}))
        assert count >= previous
        previous = count


# --------------------------------------------------------------------------
# estatistica
# --------------------------------------------------------------------------


def test_auc_conhece_os_extremos():
    assert auc([1.0, 2.0, 3.0], [4.0, 5.0]) == 0.0
    assert auc([4.0, 5.0], [1.0, 2.0, 3.0]) == 1.0
    assert auc([1.0, 1.0], [1.0, 1.0]) == 0.5
    assert auc([], [1.0]) is None


def test_a_univariada_compara_alguma_coisa(panel):
    """Se a univariada ficar sem amostra ela devolve lista vazia calada --
    o que pareceria "nenhuma feature separa". Este teste distingue os dois."""
    legs = [leg for _s, _t, _r, group in panel for leg in group]
    rows = univariate(legs, "h20", "established")
    assert rows, "a univariada nao teve amostra suficiente no painel"
    assert all(0.0 <= row["auc"] <= 1.0 for row in rows)


def test_quality_conta_quick_resume_como_a_41():
    rows = [
        {"outcome": "resumed", "bars_to_outcome": 3},
        {"outcome": "resumed", "bars_to_outcome": 60},
        {"outcome": "reversed", "bars_to_outcome": 10},
        {"outcome": "open", "bars_to_outcome": None},
    ]
    result = quality(rows)
    assert result["n"] == 4
    assert result["resumed"] == 50.0
    assert result["qr5"] == 25.0
    assert result["qr40"] == 25.0
    assert result["qr80"] == 50.0


def test_holdout_e_por_timeframe_e_nao_vaza(panel):
    legs = [leg for _s, _t, _r, group in panel for leg in group]
    discovery, holdout = split_holdout(legs)
    assert len(discovery) + len(holdout) == len(legs)
    keys = {(leg.symbol, leg.timeframe, leg.opener_timestamp) for leg in discovery}
    assert not keys & {
        (leg.symbol, leg.timeframe, leg.opener_timestamp) for leg in holdout
    }
    # Todo timeframe presente na amostra tem de aparecer nos dois lados.
    for timeframe in {leg.timeframe for leg in legs}:
        group = [leg for leg in legs if leg.timeframe == timeframe]
        if len(group) < 10:
            continue
        assert any(leg.timeframe == timeframe for leg in discovery)
        assert any(leg.timeframe == timeframe for leg in holdout)
    # E o holdout e mais recente que o discovery, dentro de cada timeframe.
    for timeframe in {leg.timeframe for leg in legs}:
        d = [leg.opener_timestamp for leg in discovery if leg.timeframe == timeframe]
        h = [leg.opener_timestamp for leg in holdout if leg.timeframe == timeframe]
        if d and h:
            assert max(d) <= min(h)


# --------------------------------------------------------------------------
# o caso obrigatorio
# --------------------------------------------------------------------------


def test_o_caso_zec_d1_existe_e_nao_se_estabeleceu():
    """Secao 10: a perna bearish de 2026-06-04 nunca produziu BOS bearish."""
    symbol, timeframe, timestamp = ZEC_CASE
    run = run_of(symbol, timeframe)
    legs = choch_legs(run, symbol, timeframe, 0)
    match = [leg for leg in legs if leg.opener_timestamp == timestamp]
    if not match:
        pytest.skip("a janela corrente nao contem o CHoCH de 2026-06-04")
    leg = match[0]
    assert leg.direction == MarketDirection.BEARISH.value
    assert not leg.established
    assert leg.failed_40 and leg.failed_80
    assert leg.stale_since is not None
