"""E3.5: retained Python state size versus history, research-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e3_shadow import shadow_metrics

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FIELDS = {"snapshots", "events", "sweeps"}


def retained_bytes(value):
    """Estimate owned Python object graph; count shared references once, skip classes."""
    seen = set()
    pending = [value]
    total = 0
    while pending:
        item = pending.pop()
        if id(item) in seen or isinstance(item, type):
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple | set | frozenset):
            pending.extend(item)
        else:
            attributes = getattr(item, "__dict__", None)
            if attributes is not None:
                pending.append(attributes)
            for cls in type(item).__mro__:
                slots = cls.__dict__.get("__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for slot in slots:
                    if slot not in {"__dict__", "__weakref__"} and hasattr(item, slot):
                        pending.append(getattr(item, slot))
    return total


def measure(run):
    # This is a projection for measurement, not a runnable/pruned candidate.
    without_logs = {k: v for k, v in run.__dict__.items() if k not in HISTORY_FIELDS}
    return {
        "closed_bars": len(run.candles),
        "retained_python_bytes": retained_bytes(run),
        "state_without_output_logs_bytes": retained_bytes(without_logs),
        "checkpoint_json_bytes": len(json.dumps(run.checkpoint()).encode()),
        "snapshots": len(run.snapshots),
        "events": len(run.events),
        "sweeps": len(run.sweeps),
        "records": len(run.records),
        "pivots": sum(len(v) for v in run.swings.values()),
        "lineage_nodes": len(run.parents),
    }


def audit_chart(path, expected, baseline):
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture hash changed: {path.name}")
    source = json.loads(content)
    if (source["symbol"], source["timeframe"]) != (expected["symbol"], expected["tf"]):
        raise ValueError(f"fixture identity changed: {path.name}")
    candles = [Candle.model_validate(row) for row in source["candles"][:-1]]
    boundaries = {len(candles) * q // 4 for q in (1, 2, 3, 4)}
    run = Replay()
    samples = []
    for end, candle in enumerate(candles, 1):
        run.feed(candle)
        if end in boundaries:
            samples.append(measure(run))
    if any(baseline.get(k) != v for k, v in shadow_metrics(run).items()):
        raise ValueError(f"shadow baseline mismatch: {path.name}")
    return {
        "symbol": source["symbol"],
        "timeframe": source["timeframe"],
        "sha256": expected["sha256"],
        "baseline_match": True,
        "samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/.replay_cache/eq_e3_5.json")
    args = parser.parse_args()
    manifest_path = ROOT / "research/eq_levels_baseline.json"
    baseline_path = ROOT / "research/eq_levels_e3_panel_baseline.json"
    manifest = json.loads(manifest_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    expected = {(r["symbol"], r["tf"]): r for r in manifest["charts"]}
    observed = {(r["symbol"], r["timeframe"]): r for r in baseline["rows"]}
    if (
        len(expected) != len(manifest["charts"])
        or len(observed) != len(baseline["rows"])
        or expected.keys() != observed.keys()
    ):
        raise ValueError("manifest and shadow chart identities differ")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    charts = []
    with args.output.with_suffix(".partial.jsonl").open("w") as journal:
        for identity, row in expected.items():
            result = audit_chart(
                ROOT / "frontend/research/fixtures" / row["file"], row, observed[identity]
            )
            charts.append(result)
            journal.write(json.dumps(result, allow_nan=False) + "\n")
            journal.flush()
            print(f"{len(charts)}/{len(expected)} {row['file']}", flush=True)
    paths = [
        Path(__file__),
        ROOT / "research/eq_levels_e1.py",
        ROOT / "research/eq_levels_e3_shadow.py",
    ]
    report = {
        "schema": 1,
        "complete": True,
        "expected_charts": len(expected),
        "python": sys.version,
        "platform": platform.platform(),
        "production_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "shadow_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "elapsed_seconds": time.perf_counter() - started,
        "charts": charts,
    }
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temporary.replace(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
