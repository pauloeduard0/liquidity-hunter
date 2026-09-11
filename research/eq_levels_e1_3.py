"""E1.3 retention study for the causal EQ state.

The full replay is the oracle.  A tail restart is intentionally an invalid
control: it shows the cost of dropping origin candles.  The compact ledger is
measured as a storage envelope of the information N actually publishes; it is
not silently presented as a working restore until an engine can consume it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import ROOT, Replay
from research.eq_levels_e1_2 import export_state, zone_payload

TAILS = (30, 100, 300)


def json_size(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def ids(run):
    return {
        side: {key for key, zone in run.normal[side].items() if not zone.is_mitigated}
        for side in ["EQH", "EQL"]
    }


def ledger(run):
    """The minimum version/lineage material N publishes, without candles."""
    return {
        "schema": 1,
        "last_timestamp": run.candles[-1].timestamp.isoformat(),
        "tr_sum": run.tr_sum,
        "volume_sum": run.volume_sum,
        "records": list(run.records.values()),
        "parents": [[list(k), list(v)] for k, v in run.parents.items()],
        "consumed": [[list(k), v] for k, v in run.consumed.items()],
        "frozen_strength": {
            side: [[list(key), value] for key, value in values.items()]
            for side, values in run.frozen_strength.items()
        },
        # Eleven bars are required to discover the next five-sided fractal.
        "pivot_tail": [c.model_dump(mode="json") for c in run.candles[-11:]],
    }


def run_bars(bars):
    run = Replay()
    for bar in bars:
        run.feed(bar)
    return run


def compare(candles):
    cut = len(candles) // 2
    prefix, tail = candles[:cut], candles[cut:]
    continued = run_bars(candles)
    rows = []
    full = run_bars(prefix)
    full_state = export_state(full)
    # Full policy restores all origin candles, then receives the remaining
    # candles. It must equal a continuous run exactly.
    resumed = Replay.restore(full_state["checkpoint"])
    for bar in tail:
        resumed.feed(bar)
    assert ids(resumed) == ids(continued)
    for window in TAILS:
        start = max(0, cut - window)
        short = run_bars(candles[start:cut])
        for bar in tail:
            short.feed(bar)
        oracle_ids, short_ids = ids(continued), ids(short)
        rows.append(
            dict(
                cut=cut,
                window=window,
                state_bytes=json_size(export_state(full)),
                tail_bytes=json_size(export_state(run_bars(candles[start:cut]))),
                oracle_records=len(full.records),
                tail_records=len(run_bars(candles[start:cut]).records),
                added={side: len(short_ids[side] - oracle_ids[side]) for side in oracle_ids},
                removed={side: len(oracle_ids[side] - short_ids[side]) for side in oracle_ids},
                exact=short_ids == oracle_ids,
                tail_start=start,
            )
        )
    compact = ledger(full)
    rows.append(
        dict(
            cut=cut,
            window="compact-ledger",
            state_bytes=json_size(full_state),
            tail_bytes=json_size(compact),
            compact_ratio=json_size(compact) / json_size(full_state),
            records=len(full.records),
            pivot_tail=len(compact["pivot_tail"]),
            restore_supported=False,
            note="size only; restore engine not implemented",
        )
    )
    return rows


def process(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    candles = [Candle.model_validate(c) for c in data["candles"][:-1]]
    # Use a quarter as a retention cut and the rest as the continuation. The
    # same cut is deterministic for every chart; no selection by outcome.
    rows = compare(candles)
    full = run_bars(candles)
    return dict(
        file=path.name,
        symbol=data["symbol"],
        timeframe=data["timeframe"],
        candles=len(candles),
        sha256=hashlib.sha256(raw).hexdigest(),
        full_state_bytes=json_size(export_state(full)),
        full_payload_bytes=json_size(zone_payload(full)),
        rows=rows,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=ROOT / "frontend/research/fixtures")
    parser.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_e1_3_baseline.json")
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args()
    files = sorted(args.fixtures.glob("*.json"))
    selected = [p for p in files if not args.symbols or p.name.split("_")[0] in args.symbols]
    results = [
        process(p) for p in selected if len(json.loads(p.read_text()).get("candles", [])) >= 100
    ]
    args.json.write_text(
        json.dumps(
            dict(
                schema=1,
                policies={
                    "full": "all origin candles and replay checkpoint",
                    "compact-ledger": (
                        "published versions, lineage, accumulators and eleven-bar pivot tail; "
                        "size only"
                    ),
                    "tail": "last N candles; intentionally invalid control",
                },
                tails=TAILS,
                results=results,
            ),
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    print(f"Wrote {args.json}: {len(results)} charts")


if __name__ == "__main__":
    main()
