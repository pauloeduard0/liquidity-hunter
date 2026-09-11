"""Retention decisions are explicit: full is exact; tail divergence is measured."""

import json

import pytest
from research.eq_levels_e1_2 import ContractError, export_state, restore_state
from research.eq_levels_e1_3 import TAILS, ids, json_size, ledger, run_bars
from research.test_eq_levels_audit import series


def test_full_state_restart_is_exact_and_ledger_is_smaller():
    candles = series((102.0, 102.01, 102.02, 102.03), count=70)
    full = run_bars(candles)
    state = export_state(full)
    resumed = restore_state(state)
    assert ids(resumed) == ids(full)
    assert json_size(ledger(full)) < json_size(state)


def test_tail_retention_is_an_invalid_restart_control():
    candles = series((102.0, 102.01, 102.02, 102.03), count=70)
    oracle = run_bars(candles)
    cut = 50
    short = run_bars(candles[cut - 30 : cut])
    for bar in candles[cut:]:
        short.feed(bar)
    assert ids(short) != ids(oracle)


def test_ledger_contains_only_json_safe_versioned_material():
    run = run_bars(series((102.0, 102.01, 102.02, 102.03), count=70))
    value = ledger(run)
    assert value["schema"] == 1
    assert len(value["pivot_tail"]) == 11
    encoded = json.loads(json.dumps(value))
    assert encoded["last_timestamp"] == value["last_timestamp"]


def test_tail_windows_are_declared_and_deterministic():
    candles = series((102.0, 102.01, 102.02, 102.03), count=70)
    assert TAILS == (30, 100, 300)
    assert run_bars(candles[-30:]).candles[0].timestamp == candles[-30].timestamp


def test_corrupt_compact_material_cannot_be_promoted_as_full_state():
    run = run_bars(series((102.0, 102.01, 102.02), count=70))
    state = export_state(run)
    state["checkpoint"] = ledger(run)
    with pytest.raises(ContractError):
        restore_state(state)
