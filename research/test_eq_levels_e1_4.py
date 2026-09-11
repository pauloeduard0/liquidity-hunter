"""Compact restore must be exact before any storage design is promoted."""

import copy
import json

import pytest
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e1_4 import (
    TAIL,
    compact_export,
    compact_restore,
    ids,
    validate_compact,
)
from research.test_eq_levels_audit import series


def run(candles):
    replay = Replay()
    for candle in candles:
        replay.feed(candle)
    return replay


def test_compact_restore_matches_continuous_replay_at_all_observable_layers():
    candles = series((102.0, 102.0, 102.0), count=50)
    candles += [make_candle(i, high=100.1, low=99.9, close=100.0) for i in range(50, 70)]
    cut = 50
    before = run(candles[:cut])
    resumed = compact_restore(json.loads(json.dumps(compact_export(before))))
    for candle in candles[cut:]:
        resumed.feed(candle)
    whole = run(candles)
    assert ids(resumed) == ids(whole)
    assert resumed.records == whole.records
    assert resumed.events == whole.events
    assert resumed.sweeps == whole.sweeps
    assert resumed.snapshots == whole.snapshots


def test_compact_restore_refuses_new_membership_without_area_history():
    candles = series((102.0, 102.01, 102.02, 102.03), count=70)
    before = run(candles[:50])
    resumed = compact_restore(compact_export(before))
    with pytest.raises(RuntimeError, match="area history"):
        for candle in candles[50:]:
            resumed.feed(candle)


def test_compact_checkpoint_keeps_only_discovery_tail_but_all_version_state():
    run_state = run(series((102.0, 102.01, 102.02, 102.03), count=70))
    compact = compact_export(run_state)
    assert len(compact["candles"]) == TAIL
    assert len(compact["records"]) == len(run_state.records)
    assert compact["candle_count"] == len(run_state.candles)


def test_compact_checksum_and_tail_guards_reject_corruption():
    state = compact_export(run(series((102.0, 102.0, 102.0), count=70)))
    for mutate in [
        lambda value: value.update(sha256="0" * 64),
        lambda value: value["candles"].reverse(),
        lambda value: value.update(candle_count=1),
    ]:
        broken = copy.deepcopy(state)
        mutate(broken)
        with pytest.raises(ValueError):
            validate_compact(broken)


def test_compact_restore_rejects_duplicate_closed_bar():
    run_state = run(series((102.0, 102.0, 102.0), count=70))
    restored = compact_restore(compact_export(run_state))
    with pytest.raises(ValueError, match="strictly increasing"):
        restored.feed(restored.candles[-1])
