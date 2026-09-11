"""E1.6 event journal contract tests."""

import pytest
from research.eq_levels_e1_6 import EventJournal, JournalError, event_key


def event(seq, event_id=None, *, symbol="BTCUSDT", timeframe="1h", value=1):
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "seq": seq,
        "event_id": event_id or f"e{seq}",
        "kind": "membership",
        "observed_at": f"2026-01-01T00:{seq:02d}:00+00:00",
        "payload": {"value": value},
    }


def test_ordered_append_exact_retry_and_deterministic_replay():
    journal = EventJournal("BTCUSDT", "1h")
    first = event(1)
    digest = journal.append(first)
    assert digest == event_key(first)
    assert journal.append(first) == digest
    journal.append(event(2, value=3))
    assert journal.replay(lambda state, item: state + item["payload"]["value"], 0) == 4


def test_gap_and_out_of_order_are_rejected_until_explicit_backfill():
    journal = EventJournal("BTCUSDT", "1h")
    journal.append(event(1))
    with pytest.raises(JournalError, match="gap"):
        journal.append(event(3))
    journal.append_backfill([event(2, value=2), event(3, value=3)], missing_from=2, missing_to=3)
    assert [item["seq"] for item in journal.events] == [1, 2, 3]


def test_backfill_range_must_be_contiguous_and_declared():
    journal = EventJournal("BTCUSDT", "1h")
    with pytest.raises(JournalError, match="range"):
        journal.append_backfill([event(1)], missing_from=2, missing_to=2)
    with pytest.raises(JournalError, match="contiguous"):
        journal.append_backfill([event(2)], missing_from=1, missing_to=1)


def test_event_id_cannot_change_payload_or_identity():
    journal = EventJournal("BTCUSDT", "1h")
    journal.append(event(1, event_id="same"))
    with pytest.raises(JournalError, match="reused"):
        journal.append(event(1, event_id="same", value=9))
    with pytest.raises(JournalError, match="identity"):
        journal.append(event(2, symbol="ETHUSDT"))
