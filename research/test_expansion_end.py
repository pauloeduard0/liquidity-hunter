"""Testes do dataset e das regras da Etapa 2.6.

O que precisa estar certo aqui e uma coisa so: as FEATURES nao podem enxergar
uma vela alem do BOS, e a regra nao pode enxergar uma vela alem da confirmacao.
O rotulo pode -- e so rotulo.

    poetry run pytest research/test_expansion_end.py
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import TimeFrame
from research.expansion_end import (
    FEATURES,
    Observation,
    Rule,
    _split,
    observations_of_run,
    score,
    univariate,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

FUTURE_FIELDS = {"label", "bars_available", "bars_to_next_bos", "bars_to_next_advance"}


@pytest.fixture(scope="module")
def run():
    if not (CACHE_DIR / "BTCUSDT_1h.json").exists():
        pytest.skip("sem cache BTCUSDT 1h")
    series = load_series("BTCUSDT", TimeFrame.H1)
    window = series[max(len(series) - LIMIT - BUFFER, 0) :]
    return dd._run_internal_structure(
        provider=SliceProvider(window),
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        limit=LIMIT,
        confluence_filter=True,
    )


def test_the_dataset_is_not_empty(run) -> None:
    observations = observations_of_run(run.events, run.candles)
    assert observations
    assert {o.label for o in observations} <= {"TERMINAL", "CONTINUES"}


def test_every_run_has_exactly_one_terminal(run) -> None:
    """Por definicao: o ultimo BOS da corrida, e so ele."""
    observations = observations_of_run(run.events, run.candles)
    by_run: dict[int, list[Observation]] = {}
    for observation in observations:
        by_run.setdefault(observation.index - observation.run_bars, []).append(observation)
    for group in by_run.values():
        assert sum(1 for o in group if o.label == "TERMINAL") == 1
        assert group[-1].label == "TERMINAL"


def test_a_continues_observation_really_has_a_later_bos(run) -> None:
    for observation in observations_of_run(run.events, run.candles):
        if observation.label == "CONTINUES":
            assert observation.bars_to_next_bos is not None
            assert observation.bars_to_next_bos > 0
        else:
            assert observation.bars_to_next_bos is None


def test_the_features_do_not_read_a_candle_past_the_bos(run) -> None:
    """Truncar a serie na propria vela do BOS nao muda nenhuma feature."""
    observations = observations_of_run(run.events, run.candles)
    checked = 0
    for observation in observations:
        cut = observation.index + 1
        candles = run.candles[:cut]
        events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
        truncated = [
            o for o in observations_of_run(events, candles) if o.timestamp == observation.timestamp
        ]
        assert truncated, observation.timestamp
        for field in fields(Observation):
            if field.name in FUTURE_FIELDS:
                continue
            assert getattr(truncated[0], field.name) == pytest.approx(
                getattr(observation, field.name)
            ), f"{field.name} mudou em {observation.timestamp}"
        checked += 1
        if checked >= 12:
            break
    assert checked, "nenhuma observacao verificada"


def test_the_rule_only_reads_up_to_its_confirmation(run) -> None:
    """`espera>=M` decidida em T+M nao muda quando a serie continua."""
    observations = observations_of_run(run.events, run.candles)
    rule = Rule(wait=20)
    for observation in observations:
        cut = observation.index + rule.wait + 1
        if cut >= len(run.candles):
            continue
        candles = run.candles[:cut]
        events = [e for e in run.events if e.timestamp <= candles[-1].timestamp]
        live = [
            o for o in observations_of_run(events, candles) if o.timestamp == observation.timestamp
        ]
        if not live:
            continue
        assert rule.marks(live[0]) == rule.marks(observation), observation.timestamp


def test_a_rule_with_a_longer_wait_marks_a_subset() -> None:
    """Monotonia: esperar mais so pode marcar menos."""

    def observation(bars: int | None) -> Observation:
        blank = {
            field.name: (0 if field.type in ("int", "int | None") else 0.0)
            for field in fields(Observation)
        }
        blank.update(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp="2026-01-01",
            direction="bullish",
            label="TERMINAL",
            bars_available=10_000,
            bars_to_next_bos=None,
            bars_to_next_advance=bars,
        )
        return Observation(**blank)

    for bars in (None, 3, 10, 25, 45):
        marked = [Rule(wait=wait).marks(observation(bars)) for wait in (5, 10, 20, 40)]
        assert marked == sorted(marked, reverse=True)


def test_the_score_of_a_rule_that_marks_nothing_is_zero(run) -> None:
    observations = observations_of_run(run.events, run.candles)
    row = score(Rule(wait=10_000_000), observations)
    assert row["marcados"] == 0
    assert row["precision"] == 0.0


def test_the_univariate_table_covers_every_feature(run) -> None:
    rows = univariate(observations_of_run(run.events, run.candles))
    assert {row["feature"] for row in rows} == set(FEATURES)


def test_the_holdout_split_is_chronological(run) -> None:
    observations = observations_of_run(run.events, run.candles)
    discovery, holdout = _split(observations)
    assert len(discovery) + len(holdout) == len(observations)
    if discovery and holdout:
        assert discovery[-1].timestamp <= holdout[0].timestamp
