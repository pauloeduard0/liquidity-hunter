"""E1.6 causal event journal, research-only.

The journal is intentionally independent from the EQ detector.  It specifies
the ordering and replay contract a future snapshot/event implementation would
need without changing the production lifecycle.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from research.eq_levels_e1_2 import ContractError


class JournalError(ContractError):
    """An event violates ordering, identity, or idempotency rules."""


def event_key(event: dict) -> str:
    required = {"symbol", "timeframe", "seq", "event_id", "kind", "observed_at", "payload"}
    if not isinstance(event, dict) or set(event) != required:
        raise JournalError("invalid event shape")
    if not isinstance(event["seq"], int) or event["seq"] < 1:
        raise JournalError("invalid event sequence")
    if not isinstance(event["event_id"], str) or not event["event_id"]:
        raise JournalError("invalid event id")
    if not isinstance(event["payload"], dict):
        raise JournalError("invalid event payload")
    encoded = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class EventJournal:
    """In-memory ordered journal with explicit backfill and exact retries."""

    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self._events: list[dict] = []
        self._by_id: dict[str, str] = {}

    @property
    def events(self) -> list[dict]:
        return deepcopy(self._events)

    def _check_identity(self, event: dict) -> None:
        if event.get("symbol") != self.symbol or event.get("timeframe") != self.timeframe:
            raise JournalError("event identity mismatch")

    def append(self, event: dict) -> str:
        digest = event_key(event)
        self._check_identity(event)
        prior = self._by_id.get(event["event_id"])
        if prior:
            if prior == digest:
                return digest
            raise JournalError("event id reused with different payload")
        expected = len(self._events) + 1
        if event["seq"] != expected:
            raise JournalError(f"event gap: expected {expected}, got {event['seq']}")
        self._events.append(deepcopy(event))
        self._by_id[event["event_id"]] = digest
        return digest

    def append_backfill(self, events: list[dict], *, missing_from: int, missing_to: int) -> None:
        """Fill a declared contiguous gap, then resume normal append mode."""
        if missing_from != len(self._events) + 1 or missing_to < missing_from:
            raise JournalError("invalid backfill range")
        if len(events) != missing_to - missing_from + 1:
            raise JournalError("backfill range does not match event count")
        for expected, event in enumerate(events, missing_from):
            if event.get("seq") != expected:
                raise JournalError("backfill is not contiguous")
            self.append(event)

    def replay(self, reducer, initial):
        state = deepcopy(initial)
        for event in self._events:
            state = reducer(state, deepcopy(event))
        return state
