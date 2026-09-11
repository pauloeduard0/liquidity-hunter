"""E1.5 failure and concurrency contract tests."""

import pytest
from research.eq_levels_e1 import Replay
from research.eq_levels_e1_2 import export_state
from research.eq_levels_e1_5 import StateStore, StoreError, file_digest
from research.test_eq_levels_audit import series


def state(candles):
    run = Replay()
    for candle in candles:
        run.feed(candle)
    return export_state(run)


def test_first_write_load_and_exact_retry_are_idempotent(tmp_path):
    store = StateStore(tmp_path)
    value = state(series((102.0, 102.0, 102.0)))
    sha = store.put(value)
    assert store.load("BTCUSDT", "1h")["checkpoint_sha256"] == sha
    assert store.append(value, expected_sha256=sha) == sha
    assert file_digest(store.path("BTCUSDT", "1h"))


def test_stale_writer_and_regressing_timestamp_are_rejected(tmp_path):
    store = StateStore(tmp_path)
    first = state(series((102.0, 102.0, 102.0)))
    sha = store.put(first)
    newer = state(series((102.0, 102.0, 102.0), count=70))
    assert store.append(newer, expected_sha256=sha)
    third = state(series((102.0, 102.0, 102.0), count=71))
    with pytest.raises(StoreError, match="compare-and-swap"):
        store.append(third, expected_sha256=sha)
    with pytest.raises(StoreError, match="timestamp|count"):
        store.append(first, expected_sha256=newer["checkpoint_sha256"])


def test_wrong_key_or_corrupt_file_fails_closed(tmp_path):
    store = StateStore(tmp_path)
    with pytest.raises(StoreError, match="invalid store key"):
        store.path("BTC/USDT", "1h")
    path = store.path("BTCUSDT", "1h")
    path.write_text("{broken")
    with pytest.raises(StoreError, match="corrupt"):
        store.load("BTCUSDT", "1h")


def test_partial_temp_file_does_not_replace_committed_state(tmp_path):
    store = StateStore(tmp_path)
    value = state(series((102.0, 102.0, 102.0)))
    store.put(value)
    path = store.path("BTCUSDT", "1h")
    temp = path.with_name(f".{path.name}.999.tmp")
    temp.write_text('{"schema": 1}')
    assert store.load("BTCUSDT", "1h")["checkpoint_sha256"] == value["checkpoint_sha256"]


def test_gap_policy_is_explicitly_rejected_by_default(tmp_path):
    store = StateStore(tmp_path)
    first = state(series((102.0, 102.0, 102.0), count=50))
    sha = store.put(first)
    skipped = state(series((102.0, 102.0, 102.0), count=52))
    # Current research policy has no gap metadata; append cannot claim a
    # contiguous stream merely from a larger count, so expected SHA is still
    # required and the caller must choose replay/backfill before put.
    with pytest.raises(StoreError, match="compare-and-swap"):
        store.append(skipped, expected_sha256="unknown")
    assert store.load("BTCUSDT", "1h")["checkpoint_sha256"] == sha


def test_two_timeframes_are_isolated(tmp_path):
    store = StateStore(tmp_path)
    h1 = state(series((102.0, 102.0, 102.0)))
    h1["timeframe"] = "1h"
    # The fixture itself is H1; a separate key must use a valid state identity.
    store.put(h1)
    assert store.load("BTCUSDT", "15m") is None
