"""Testes das features de fluxo da Etapa 2.7.

A exigencia central e a mesma das etapas anteriores: `feature(T, serie
completa) == feature(T, serie truncada em T)`. Como as features desta etapa
leem volume e agressao vela a vela, o teste de truncamento e o unico que
garante que nenhuma delas espia o que veio depois do `stale_since`.

    poetry run pytest research/test_stall_participation.py
"""

from __future__ import annotations

from dataclasses import fields

import pytest
from liquidity_hunter.core.domain import TimeFrame
from research.range_choch import CACHE_DIR, LIMIT
from research.stall_participation import (
    FEATURES,
    K_STALL,
    N_STALL,
    Rule,
    StallObservation,
    _split,
    collect,
    observe,
    score,
    trade_counts,
    univariate,
)
from research.structural_stall_validation import collect as svv_collect
from research.structural_stall_validation import triggers_of_leg

TARGETS = {"outcome", "bars_to_outcome", "resumed_within"}


@pytest.fixture(scope="module")
def runs():
    if not (CACHE_DIR / "BTCUSDT_1h.json").exists():
        pytest.skip("sem cache BTCUSDT 1h")
    return svv_collect(["BTCUSDT"], [TimeFrame.H1], 4, LIMIT)


@pytest.fixture(scope="module")
def observations(runs):
    return collect(["BTCUSDT"], [TimeFrame.H1], 4, LIMIT)


def test_the_population_is_not_empty(observations) -> None:
    assert observations
    assert {o.outcome for o in observations} <= {"resumed", "reversed", "open"}


def test_the_trade_column_is_present_in_the_cache() -> None:
    counts = trade_counts("BTCUSDT", TimeFrame.H1)
    assert counts
    assert all(value >= 0 for value in list(counts.values())[:100])


def test_the_features_do_not_read_a_candle_past_the_stale(runs) -> None:
    """Truncar a serie no proprio `stale_since` nao muda nenhuma feature."""
    trades = trade_counts("BTCUSDT", TimeFrame.H1)
    checked = 0
    for run in runs:
        starts = sorted(leg.start_index for leg in run.legs)
        for leg in run.legs:
            trigger = triggers_of_leg(
                leg,
                run.events,
                run.candles,
                run.event_indices,
                n=N_STALL,
                k=K_STALL,
                price_mode="close",
                atr_mode="frozen",
            )
            if trigger is None:
                continue
            previous = [i for i in starts if i < leg.start_index]
            previous_index = previous[-1] if previous else max(leg.start_index - 20, 0)
            full = observe(trigger, run, leg.start_index, previous_index, trades)
            if full is None:
                continue
            stale_index = next(
                i for i, c in enumerate(run.candles) if str(c.timestamp) == trigger.timestamp
            )
            truncated_run = type(run)(
                legs=run.legs,
                events=run.events,
                candles=run.candles[: stale_index + 1],
                event_indices=run.event_indices,
            )
            cut = observe(trigger, truncated_run, leg.start_index, previous_index, trades)
            assert cut is not None
            for field in fields(StallObservation):
                if field.name in TARGETS:
                    continue
                assert getattr(cut, field.name) == pytest.approx(
                    getattr(full, field.name)
                ), f"{field.name} mudou em {trigger.timestamp}"
            checked += 1
    assert checked, "nenhum STALE verificado -- o teste nao provaria nada"


def test_no_resume_targets_are_consistent(observations) -> None:
    for observation in observations:
        flags = observation.resumed_within
        # Monotonia: se nao retomou em 80, tambem nao retomou em 5.
        assert flags["no_resume_80"] <= flags["no_resume_40"] <= flags["no_resume_20"]
        if observation.outcome != "resumed":
            assert all(value == 1 for value in flags.values())


def test_the_delta_ratios_are_bounded(observations) -> None:
    """`delta / volume` vive em [-1, 1] -- se sair disso, a normalizacao quebrou."""
    for observation in observations:
        for name in ("delta_ratio_a", "delta_ratio_b", "delta_ratio_last10"):
            assert -1.0 <= getattr(observation, name) <= 1.0


def test_the_ratios_use_the_sentinel_instead_of_dividing_by_zero(observations) -> None:
    for observation in observations:
        for name in FEATURES:
            value = float(getattr(observation, name))
            assert value == value  # nao e NaN
            assert abs(value) != float("inf")


def test_a_rule_with_no_conditions_marks_everything(observations) -> None:
    row = score(Rule(()), observations, 80)
    assert row["marcados"] == len(observations)
    assert row["precision"] == pytest.approx(row["base_rate"])


def test_the_univariate_table_covers_every_feature(observations) -> None:
    rows = univariate(observations, "no_resume_80", "resume_80")
    assert {row["feature"] for row in rows} == set(FEATURES)


def test_the_holdout_split_is_chronological(observations) -> None:
    discovery, holdout = _split(observations)
    assert len(discovery) + len(holdout) == len(observations)
    if discovery and holdout:
        assert discovery[-1].stale_since <= holdout[0].stale_since
