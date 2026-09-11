"""E1.4 compact checkpoint restore, research-only.

The compact form keeps the last eleven candles plus all causal state required
by the N replay.  It is intentionally separate from the API until parity is
proven.  The serializer uses JSON-safe lists for tuple keys and domain-model
dumps for zones/candles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from liquidity_hunter.core.domain import Candle, LiquidityZone
from research.eq_levels_e1 import FACTORIES, ROOT, Replay, token

SCHEMA = 1
TAIL = 11


def _key(key):
    return list(key)


def _unkey(value):
    return tuple(value)


def _zone(z):
    return z.model_dump(mode="json")


def _zones(values):
    return {tuple(k): LiquidityZone.model_validate(v) for k, v in values}


def _map_values(values):
    return [[_key(k), v] for k, v in values.items()]


def _unmap_values(values):
    return {_unkey(k): v for k, v in values}


def compact_export(run: Replay) -> dict:
    """Export all causal state while retaining only the pivot-discovery tail."""
    if not run.candles:
        raise ValueError("cannot checkpoint empty replay")
    state = {
        "schema": SCHEMA,
        "symbol": run.candles[-1].symbol,
        "timeframe": run.candles[-1].timeframe.value,
        "base_index": run._base_index + len(run.candles) - min(TAIL, len(run.candles)),
        "candle_count": run._base_index + len(run.candles),
        "candles": [c.model_dump(mode="json") for c in run.candles[-TAIL:]],
        "tr_sum": run.tr_sum,
        "volume_sum": run.volume_sum,
        "swings": {side: [_zone(z) for z in values] for side, values in run.swings.items()},
        "raw": {
            side: [[_key(k), _zone(z)] for k, z in values.items()]
            for side, values in run.raw.items()
        },
        "normal": {
            side: [[_key(k), _zone(z)] for k, z in values.items()]
            for side, values in run.normal.items()
        },
        "strength_coeff": {
            side: _map_values(values) for side, values in run.strength_coeff.items()
        },
        "frozen_strength": {
            side: _map_values(values) for side, values in run.frozen_strength.items()
        },
        "records": run.records,
        "parents": [[list(k), list(v)] for k, v in run.parents.items()],
        "consumed": [[list(k), v] for k, v in run.consumed.items()],
        "events": run.events,
        "seen_lifecycle": [[x[0], x[1], list(x[2]), x[3]] for x in run.seen_lifecycle],
        "sweeps": run.sweeps,
        "snapshots": run.snapshots,
        "diagnostics": dict(run.diagnostics),
        "index": [[k.isoformat(), v] for k, v in run.index.items()],
    }
    state["sha256"] = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return state


def validate_compact(state: dict) -> None:
    required = {"schema", "symbol", "timeframe", "base_index", "candle_count", "candles", "sha256"}
    if not isinstance(state, dict) or not required <= set(state):
        raise ValueError("compact checkpoint missing fields")
    digest = state["sha256"]
    body = {k: v for k, v in state.items() if k != "sha256"}
    if (
        digest
        != hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    ):
        raise ValueError("compact checkpoint checksum mismatch")
    if state["schema"] != SCHEMA or not 1 <= len(state["candles"]) <= TAIL:
        raise ValueError("unsupported compact checkpoint")
    if state["candle_count"] < len(state["candles"]):
        raise ValueError("compact count is smaller than retained tail")
    times = [c["timestamp"] for c in state["candles"]]
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError("compact tail is not ordered")
    if state["candles"][-1]["symbol"] != state["symbol"]:
        raise ValueError("compact symbol mismatch")
    if state["candles"][-1]["timeframe"] != state["timeframe"]:
        raise ValueError("compact timeframe mismatch")


def compact_restore(state: dict) -> Replay:
    """Restore an engine without replaying origin candles."""
    validate_compact(state)
    run = Replay()
    run._compact_mode = True
    run._base_index = state["base_index"]
    run._seen_count = state["candle_count"]
    run.candles = [Candle.model_validate(c) for c in state["candles"]]
    run.index = {__import__("datetime").datetime.fromisoformat(k): v for k, v in state["index"]}
    run.tr_sum = state["tr_sum"]
    run.volume_sum = state["volume_sum"]
    run.swings = {
        side: [LiquidityZone.model_validate(z) for z in values]
        for side, values in state["swings"].items()
    }
    run.raw = {side: _zones(values) for side, values in state["raw"].items()}
    run.normal = {side: _zones(values) for side, values in state["normal"].items()}
    run.strength_coeff = {
        side: _unmap_values(values) for side, values in state["strength_coeff"].items()
    }
    run.frozen_strength = {
        side: _unmap_values(values) for side, values in state["frozen_strength"].items()
    }
    run.records = state["records"]
    run.parents = {_unkey(k): _unkey(v) for k, v in state["parents"]}
    run.consumed = {_unkey(k): v for k, v in state["consumed"]}
    run.events = state["events"]
    run.seen_lifecycle = {(x[0], x[1], tuple(x[2]), x[3]) for x in state["seen_lifecycle"]}
    run.sweeps = state["sweeps"]
    run.snapshots = state["snapshots"]
    run.diagnostics = Counter(state["diagnostics"])
    return run


def ids(run):
    return {
        side: {token(side, key) for key, z in run.normal[side].items() if not z.is_mitigated}
        for side in FACTORIES
    }


def process(candles):
    cut = len(candles) // 2
    whole = Replay()
    for c in candles:
        whole.feed(c)
    before = Replay()
    for c in candles[:cut]:
        before.feed(c)
    state = compact_export(before)
    resumed = compact_restore(json.loads(json.dumps(state)))
    refusal = None
    try:
        for c in candles[cut:]:
            resumed.feed(c)
    except RuntimeError as exc:
        refusal = str(exc)
    if refusal is not None:
        return {
            "cut": cut,
            "full_state_bytes": len(json.dumps(whole.checkpoint(), separators=(",", ":"))),
            "compact_bytes": len(json.dumps(state, separators=(",", ":"))),
            "compact_ratio": len(json.dumps(state, separators=(",", ":")))
            / len(json.dumps(whole.checkpoint(), separators=(",", ":"))),
            "records": len(whole.records),
            "ids_equal": False,
            "records_equal": False,
            "events_equal": False,
            "snapshots_equal": False,
            "payload_equal": False,
            "refused": refusal,
        }
    return {
        "cut": cut,
        "full_state_bytes": len(json.dumps(whole.checkpoint(), separators=(",", ":"))),
        "compact_bytes": len(json.dumps(state, separators=(",", ":"))),
        "compact_ratio": len(json.dumps(state, separators=(",", ":")))
        / len(json.dumps(whole.checkpoint(), separators=(",", ":"))),
        "records": len(whole.records),
        "ids_equal": ids(resumed) == ids(whole),
        "records_equal": resumed.records == whole.records,
        "events_equal": resumed.events == whole.events,
        "snapshots_equal": resumed.snapshots == whole.snapshots,
        "payload_equal": [
            z.model_dump(mode="json") for side in resumed.normal.values() for z in side.values()
        ]
        == [z.model_dump(mode="json") for side in whole.normal.values() for z in side.values()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_e1_4_baseline.json")
    parser.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    args = parser.parse_args()
    rows = []
    for path in sorted((ROOT / "frontend/research/fixtures").glob("*.json")):
        if path.name.split("_")[0] not in args.symbols:
            continue
        data = json.loads(path.read_text())
        if len(data.get("candles", [])) < 100:
            continue
        candles = [Candle.model_validate(c) for c in data["candles"][:-1]]
        rows.append(
            dict(
                file=path.name,
                symbol=data["symbol"],
                timeframe=data["timeframe"],
                **process(candles),
            )
        )
    args.json.write_text(
        json.dumps({"schema": 1, "tail": TAIL, "results": rows}, separators=(",", ":")) + "\n"
    )
    print(f"Wrote {args.json}: {len(rows)} charts")


if __name__ == "__main__":
    main()
