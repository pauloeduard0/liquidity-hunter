"""Fecha a Etapa 4.2 contra a producao e contra os proprios artefatos.

O modulo recalcula o caminho da perna por conta propria (`Leg.path`) para poder
avaliar dezenas de politicas de uma vez. Se esse caminho divergir do que
`detect_structural_stall` mede, toda a comparacao entre politicas fica sem
referencia -- entao a politica baseline e prendida contra a producao.

Prende tambem o que o desenho promete: causalidade do ritmo estrutural,
ausencia de fallback silencioso, e um holdout que realmente contem cada
timeframe.
"""

from __future__ import annotations

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import StructureEvent, TimeFrame
from liquidity_hunter.liquidity.structural_stall import detect_structural_stall
from research.choch_leg_opener import ADVANCES
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.stall_time_normalization import (
    BASELINE,
    K_GRID,
    R_GRID,
    K,
    Leg,
    N,
    Policy,
    apply_policy,
    collect,
    evaluate,
    legs_of_run,
    rhythm_profile,
    split_holdout,
    typical_intervals,
)

PANEL = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT"]
TFS = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1]
COMBOS = [
    (symbol, tf)
    for symbol in PANEL
    for tf in TFS
    if (CACHE_DIR / f"{symbol}_{tf.value}.json").exists()
]


def run_for(symbol: str, timeframe: TimeFrame):
    series = load_series(symbol, timeframe)
    return dd._run_internal_structure(
        provider=SliceProvider(series[-(LIMIT + BUFFER) :]),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )


@pytest.mark.parametrize(("symbol", "timeframe"), COMBOS)
def test_a_politica_baseline_reproduz_a_producao(symbol: str, timeframe: TimeFrame) -> None:
    """`Policy(bars, N=50, K=6)` == `detect_structural_stall` na mesma perna.

    A producao so responde sobre a perna VIGENTE, entao a comparacao e feita
    sobre o prefixo que termina no fim de cada perna: ali `_last_advance` e o
    opener daquela perna, e as duas leituras tem de coincidir em candle, barras
    e retracao. Se `Leg.path` errasse a aritmetica, divergiria aqui.
    """
    run = run_for(symbol, timeframe)
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    compared = 0
    for leg in legs_of_run(run, symbol, timeframe, 0):
        start = by_ts[
            next(
                e.timestamp
                for e in run.events
                if e.timestamp.isoformat() == leg.opener_timestamp
                and e.event is StructureEvent.BREAK_OF_STRUCTURE
            )
        ]
        end = start + leg.leg_bars
        prefix = candles[: end + 1]
        events = [e for e in run.events if e.timestamp <= prefix[-1].timestamp]
        production = detect_structural_stall(prefix, events)
        mine = apply_policy(leg, BASELINE)
        if production is None or production.last_advance_timestamp.isoformat() != (
            leg.opener_timestamp
        ):
            # A producao nao esta olhando esta perna neste prefixo: nada a
            # comparar (e a politica tambem nao pode disparar dentro dela).
            continue
        assert mine is not None and mine != "unavailable", leg.opener_timestamp
        assert mine.bars_since_advance == production.bars_since_advance
        assert mine.retracement_atr == pytest.approx(production.retracement_atr, abs=0.01)
        compared += 1
    if compared == 0:
        pytest.skip(f"{symbol} {timeframe.value}: nenhuma perna com stall")


def test_o_pinning_compara_alguma_coisa() -> None:
    """Guarda contra o teste acima virar skip em todos os combos."""
    total = 0
    for symbol, timeframe in COMBOS:
        run = run_for(symbol, timeframe)
        for leg in legs_of_run(run, symbol, timeframe, 0):
            if apply_policy(leg, BASELINE) not in (None, "unavailable"):
                total += 1
    assert total >= 5, f"apenas {total} pernas disparam o baseline"


def test_typical_intervals_e_causal() -> None:
    """Advances posteriores a `upto` nao podem mudar o ritmo lido em `upto`."""
    indices = [0, 10, 25, 39, 60, 88, 100, 140]
    for k in K_GRID:
        base = typical_intervals(indices, 100, k)
        extended = typical_intervals([*indices, 200, 260, 300], 100, k)
        assert base == extended


def test_sem_historico_e_unavailable_e_nao_um_default() -> None:
    """A ausencia de ritmo devolve `None` / `"unavailable"`, nunca um fallback."""
    assert typical_intervals([0, 10], 10, 5) is None
    assert typical_intervals([], 0, 3) is None
    leg = Leg(
        symbol="X",
        timeframe="1d",
        window=0,
        opener_timestamp="2026-01-01T00:00:00+00:00",
        direction="bullish",
        leg_bars=200,
        rhythm={f"advances_{k}": None for k in K_GRID},
        # Um caminho que satisfaz folgadamente qualquer limiar temporal.
        path=[(bars, 99.0, bars * 24.0) for bars in range(201)],
        later_bos=[],
        next_advance_offset=None,
    )
    ratio = Policy(kind="ratio", label="r", r=1.5, rhythm_key="advances_5")
    assert apply_policy(leg, ratio) == "unavailable"
    # E a mesma perna dispara na politica de barras: o `unavailable` e da falta
    # de ritmo, nao de uma perna que nao qualificaria.
    assert apply_policy(leg, BASELINE) is not None


@pytest.mark.parametrize(("symbol", "timeframe"), COMBOS)
def test_r_e_n_sao_monotonicos(symbol: str, timeframe: TimeFrame) -> None:
    """Exigir mais tempo nunca pode produzir MAIS stalls."""
    run = run_for(symbol, timeframe)
    legs = legs_of_run(run, symbol, timeframe, 0)
    counts = [
        evaluate(
            legs,
            Policy(kind="ratio", label=f"R={r}", r=r, rhythm_key="advances_5"),
            0,
        )["stalls"]
        for r in R_GRID
    ]
    assert counts == sorted(counts, reverse=True), counts
    n_counts = [
        evaluate(legs, Policy(kind="bars", label=f"N={n}", n=n), 0)["stalls"]
        for n in (10, 30, 50, 100)
    ]
    assert n_counts == sorted(n_counts, reverse=True), n_counts


def test_holdout_contem_todos_os_timeframes_e_nao_vaza() -> None:
    """O corte e por timeframe, e dentro de cada um o discovery vem antes."""
    legs, _ = collect(PANEL, TFS, windows=2)
    discovery, holdout = split_holdout(legs)
    tfs_d = {leg.timeframe for leg in discovery}
    tfs_h = {leg.timeframe for leg in holdout}
    assert tfs_d == tfs_h, (tfs_d, tfs_h)
    assert len(tfs_h) >= 4
    for timeframe in tfs_h:
        latest_discovery = max(
            leg.opener_timestamp for leg in discovery if leg.timeframe == timeframe
        )
        earliest_holdout = min(
            leg.opener_timestamp for leg in holdout if leg.timeframe == timeframe
        )
        assert latest_discovery <= earliest_holdout


def test_o_ritmo_estrutural_e_parecido_entre_timeframes() -> None:
    """O achado central da etapa, preso como fato mensuravel.

    Se o intervalo tipico entre advances fosse muito diferente por timeframe,
    N=50 significaria coisas diferentes e a normalizacao teria trabalho a
    fazer. Este teste falha se essa premissa mudar.
    """
    legs, _ = collect(PANEL, TFS, windows=2)
    profile = rhythm_profile(legs)
    assert set(profile) >= {"15m", "1h", "4h", "1d"}
    medians = [row["median_advance_interval_bars"] for row in profile.values()]
    assert max(medians) / min(medians) < 1.6, profile
    for row in profile.values():
        # N=50 vale entre 1,5 e 3 intervalos tipicos em todo timeframe.
        assert 1.5 <= row["n50_in_typical_intervals"] <= 3.0, profile


def test_as_constantes_sao_as_da_producao() -> None:
    """A etapa congela N e K de proposito -- so o eixo temporal varia."""
    from liquidity_hunter.liquidity import structural_stall as production

    assert N == production.DEFAULT_STALL_BARS
    assert K == production.DEFAULT_STALL_RETRACEMENT_ATR
    assert BASELINE.n == N and BASELINE.k_atr == K


def test_a_populacao_e_so_de_bos() -> None:
    """Secao 2: nenhuma perna aberta por CHoCH entra (a 4.1 reprovou isso)."""
    for symbol, timeframe in COMBOS:
        run = run_for(symbol, timeframe)
        openers = {leg.opener_timestamp for leg in legs_of_run(run, symbol, timeframe, 0)}
        by_ts = {
            e.timestamp.isoformat(): e
            for e in run.events
            if not e.provisional and e.event in ADVANCES
        }
        for opener in openers:
            assert by_ts[opener].event is StructureEvent.BREAK_OF_STRUCTURE
