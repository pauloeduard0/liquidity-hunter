"""E1.5 failure-injection store for causal EQ state, research-only.

The store is deliberately tiny and local.  Its purpose is to make ordering,
idempotency and crash guarantees executable before selecting a production
backend.  It does not replace the API cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from research.eq_levels_e1_2 import ContractError, validate_state


class StoreError(ContractError):
    """A state transition is unsafe or the stored file is corrupt."""


class StateStore:
    """Single-writer, atomic JSON store keyed by symbol/timeframe."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str, timeframe: str) -> Path:
        if not symbol.isalnum() or not timeframe.replace("m", "").replace("h", "").replace("d", ""):
            raise StoreError("invalid store key")
        return self.root / f"{symbol}_{timeframe}.json"

    def load(self, symbol: str, timeframe: str) -> dict | None:
        path = self.path(symbol, timeframe)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
            validate_state(value)
            return value
        except (OSError, json.JSONDecodeError, ContractError) as exc:
            raise StoreError("stored state is corrupt") from exc

    def put(self, state: dict, *, expected_sha256: str | None = None) -> str:
        envelope = validate_state(state)
        path = self.path(envelope.symbol, envelope.timeframe)
        current = self.load(envelope.symbol, envelope.timeframe)
        current_sha = current["checkpoint_sha256"] if current else None
        if expected_sha256 != current_sha:
            raise StoreError("compare-and-swap mismatch")
        encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(encoded)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise StoreError("atomic state write failed") from exc
        return envelope.checkpoint_sha256

    def append(self, state: dict, *, expected_sha256: str | None = None) -> str:
        """Accept an exact retry, reject a stale or regressing writer."""
        envelope = validate_state(state)
        current = self.load(envelope.symbol, envelope.timeframe)
        if current and current["checkpoint_sha256"] == envelope.checkpoint_sha256:
            return envelope.checkpoint_sha256
        if current:
            if envelope.last_timestamp <= current["last_timestamp"]:
                raise StoreError("state timestamp does not advance")
            if envelope.candle_count <= current["candle_count"]:
                raise StoreError("state count does not advance")
        return self.put(state, expected_sha256=expected_sha256)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
