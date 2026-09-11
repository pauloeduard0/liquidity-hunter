"""E1.2 persistence, migration, CAS and payload contract tests."""

import copy
import json

import pytest
from liquidity_hunter.core.domain import Candle, LiquidityZone
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e1_2 import (
    ContractError,
    append_state,
    export_state,
    migrate_state,
    restore_state,
    state_fingerprint,
    validate_state,
    validate_zone_payload,
    zone_payload,
)
from research.test_eq_levels_audit import series


def run(c):
    replay = Replay()
    for bar in c:
        replay.feed(bar)
    return replay


def test_export_is_json_safe_self_identifying_and_roundtrips():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    original = run(c)
    state = export_state(original)
    encoded = json.loads(json.dumps(state))
    restored = restore_state(encoded)
    assert validate_state(encoded).symbol == "BTCUSDT"
    assert restored.records == original.records
    assert restored.events == original.events
    assert state_fingerprint(state) == state_fingerprint(encoded)


def test_checksum_identity_count_and_order_are_hard_guards():
    state = export_state(run(series((102.0, 102.0, 102.0))))
    for mutate in [
        lambda x: x["checkpoint"]["candles"].pop(),
        lambda x: x["checkpoint"]["candles"][0].update(symbol="OTHER"),
        lambda x: x["checkpoint"]["candles"][1].update(
            timestamp=x["checkpoint"]["candles"][0]["timestamp"]
        ),
        lambda x: x.update(checkpoint_sha256="0" * 64),
    ]:
        broken = copy.deepcopy(state)
        mutate(broken)
        with pytest.raises(ContractError):
            validate_state(broken)


def test_schema_migration_is_explicit_and_does_not_guess_old_fields():
    state = export_state(run(series((102.0, 102.0, 102.0))))
    assert migrate_state(state) == state
    old = copy.deepcopy(state)
    old["schema"] = 0
    with pytest.raises(ContractError, match="no migration"):
        migrate_state(old)


def test_cas_append_rejects_stale_writer_and_accepts_current_writer():
    first = run(series((102.0, 102.0, 102.0)))
    state = export_state(first)
    second = run(
        series((102.0, 102.0, 102.0)) + [make_candle(60, high=100.1, low=99.9, close=100.0)]
    )
    with pytest.raises(ContractError, match="stale"):
        append_state(state, second, expected_previous="stale")
    updated = append_state(state, second, expected_previous=state["checkpoint_sha256"])
    assert updated["candle_count"] == state["candle_count"] + 1


def test_zone_payload_matches_existing_domain_and_roundtrips():
    replay = run(series((102.0, 102.0, 102.0)))
    payload = zone_payload(replay)
    validate_zone_payload(payload)
    assert all(set(row) == set(LiquidityZone.model_fields) for row in payload)
    assert all(row["strength"] >= 0 for row in payload)


def test_payload_rejects_unknown_or_missing_fields():
    payload = zone_payload(run(series((102.0, 102.0, 102.0))))
    assert payload
    missing = copy.deepcopy(payload)
    missing[0].pop("strength")
    with pytest.raises(ContractError):
        validate_zone_payload(missing)
    unknown = copy.deepcopy(payload)
    unknown[0]["new_field"] = True
    with pytest.raises(ContractError):
        validate_zone_payload(unknown)


def test_closed_incremental_tail_preserves_payload_and_fingerprint():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    base = run(c[:50])
    resumed = restore_state(export_state(base))
    for bar in c[50:]:
        resumed.feed(bar)
    whole = run(c)
    assert zone_payload(resumed) == zone_payload(whole)
    assert state_fingerprint(export_state(resumed)) == state_fingerprint(export_state(whole))


def test_stream_identity_cannot_change_between_symbols_or_timeframes():
    replay = run(series((102.0, 102.0, 102.0)))
    with pytest.raises(ValueError, match="strictly increasing"):
        replay.feed(Candle.model_validate(replay.candles[-1].model_dump()))


def test_bounded_candle_tail_is_not_a_safe_checkpoint_without_lineage():
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    whole = run(c)
    tail = run(c[-30:])
    assert tail.records != whole.records
    assert tail.snapshots[-1]["sides"]["EQH"]["N"] != whole.snapshots[-1]["sides"]["EQH"]["N"]
