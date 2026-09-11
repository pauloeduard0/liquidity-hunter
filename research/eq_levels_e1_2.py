"""E1.2 state and payload contract, research-only.

This module deliberately does not modify the API.  It defines the envelope a
future causal EQ consumer would have to persist and the narrow zone payload it
could expose to the existing dashboard.  The replay engine remains the source
of truth; the envelope is validated before a restart is accepted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from liquidity_hunter.core.domain import LiquidityZone
from pydantic import ValidationError
from research.eq_levels_e1 import Replay

STATE_SCHEMA = 1
PRODUCER = "eq-levels-causal-research"
REQUIRED_STATE = frozenset(
    {
        "schema",
        "producer",
        "symbol",
        "timeframe",
        "last_timestamp",
        "candle_count",
        "checkpoint",
        "checkpoint_sha256",
    }
)
ZONE_FIELDS = frozenset(LiquidityZone.model_fields)


class ContractError(ValueError):
    """The persisted state or public zone payload is not safe to consume."""


@dataclass(frozen=True)
class StateEnvelope:
    """Versioned restart envelope.

    `checkpoint` intentionally retains the source candles.  E1.2 measures the
    correctness and migration contract first; a compact event-store format is
    a later storage experiment and must not silently lose pivot history.
    """

    schema: int
    producer: str
    symbol: str
    timeframe: str
    last_timestamp: str
    candle_count: int
    checkpoint: dict[str, Any]
    checkpoint_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "producer": self.producer,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "last_timestamp": self.last_timestamp,
            "candle_count": self.candle_count,
            "checkpoint": self.checkpoint,
            "checkpoint_sha256": self.checkpoint_sha256,
        }


def _digest(checkpoint: dict[str, Any]) -> str:
    encoded = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def export_state(run: Replay) -> dict[str, Any]:
    """Export a JSON-safe, self-identifying restart envelope."""
    if not run.candles:
        raise ContractError("cannot persist an empty stream")
    checkpoint = run.checkpoint()
    first, last = run.candles[0], run.candles[-1]
    checkpoint_last = checkpoint["candles"][-1]
    envelope = StateEnvelope(
        schema=STATE_SCHEMA,
        producer=PRODUCER,
        symbol=last.symbol,
        timeframe=last.timeframe.value,
        last_timestamp=checkpoint_last["timestamp"],
        candle_count=len(run.candles),
        checkpoint=checkpoint,
        checkpoint_sha256=_digest(checkpoint),
    )
    if first.symbol != last.symbol or first.timeframe != last.timeframe:
        raise ContractError("mixed stream cannot be persisted")
    return envelope.as_dict()


def validate_state(value: dict[str, Any]) -> StateEnvelope:
    """Validate schema, identity, ordering metadata and tamper checksum."""
    if not isinstance(value, dict) or not REQUIRED_STATE.issubset(value):
        raise ContractError("state envelope is missing required fields")
    if value["schema"] != STATE_SCHEMA or value["producer"] != PRODUCER:
        raise ContractError("unsupported state schema or producer")
    checkpoint = value["checkpoint"]
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != 1:
        raise ContractError("unsupported replay checkpoint")
    if value["checkpoint_sha256"] != _digest(checkpoint):
        raise ContractError("checkpoint checksum mismatch")
    candles = checkpoint.get("candles")
    if not isinstance(candles, list) or len(candles) != value["candle_count"] or not candles:
        raise ContractError("checkpoint candle count mismatch")
    if candles[-1]["symbol"] != value["symbol"] or candles[-1]["timeframe"] != value["timeframe"]:
        raise ContractError("checkpoint identity mismatch")
    if candles[-1]["timestamp"] != value["last_timestamp"]:
        raise ContractError("checkpoint timestamp mismatch")
    times = [c["timestamp"] for c in candles]
    if times != sorted(times) or len(set(times)) != len(times):
        raise ContractError("checkpoint candles are not strictly ordered")
    return StateEnvelope(**{k: value[k] for k in REQUIRED_STATE})


def restore_state(value: dict[str, Any]) -> Replay:
    """Validate then restore; malformed state never reaches the engine."""
    envelope = validate_state(value)
    return Replay.restore(envelope.checkpoint)


def migrate_state(value: dict[str, Any]) -> dict[str, Any]:
    """Explicit migration gate; no implicit downgrade or field guessing."""
    if not isinstance(value, dict):
        raise ContractError("state must be an object")
    if value.get("schema") == STATE_SCHEMA:
        validate_state(value)
        return value
    raise ContractError(f"no migration exists for schema {value.get('schema')!r}")


def zone_payload(run: Replay) -> list[dict[str, Any]]:
    """Project N's current zones through the existing LiquidityZone shape."""
    zones = [z for side in run.normal.values() for z in side.values()]
    payload = []
    for zone in zones:
        row = zone.model_dump(mode="json")
        if set(row) != ZONE_FIELDS:
            raise ContractError("zone payload fields diverged from domain")
        LiquidityZone.model_validate(row)
        payload.append(row)
    return sorted(payload, key=lambda z: (z["zone_type"], z["formed_at"], z["price_low"]))


def validate_zone_payload(payload: list[dict[str, Any]]) -> None:
    """Check that an N payload remains compatible with current clients."""
    if not isinstance(payload, list):
        raise ContractError("liquidity_zones must be a list")
    for row in payload:
        if set(row) != ZONE_FIELDS:
            raise ContractError("unknown or missing LiquidityZone field")
        try:
            LiquidityZone.model_validate(row)
        except ValidationError as exc:
            raise ContractError("invalid LiquidityZone payload") from exc


def append_state(state: dict[str, Any], run: Replay, *, expected_previous: str) -> dict[str, Any]:
    """CAS-style append guard for a single-writer research store."""
    previous = state.get("checkpoint_sha256")
    if previous != expected_previous:
        raise ContractError("stale state writer")
    return export_state(run)


def state_fingerprint(value: dict[str, Any]) -> str:
    """Stable identifier suitable for an idempotency key."""
    envelope = validate_state(value)
    return hashlib.sha256(
        f"{envelope.symbol}|{envelope.timeframe}|{envelope.last_timestamp}|{envelope.checkpoint_sha256}".encode()
    ).hexdigest()
