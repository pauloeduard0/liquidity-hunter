"""E3.4: disk checkpoint/restart continuity across the complete EQ panel."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e3_shadow import shadow_metrics

ROOT = Path(__file__).resolve().parents[1]


def verify_restarts(candles, directory: Path):
    """Three successive restarts, full state equality and an uninterrupted reference."""
    if len(candles) < 4:
        raise ValueError("at least four closed candles required")
    reference = Replay()
    for candle in candles:
        reference.feed(candle)
    boundaries = {len(candles) * q // 4 for q in (1, 2, 3)}
    resumed = Replay()
    checkpoints = []
    for end, candle in enumerate(candles, 1):
        resumed.feed(candle)
        if resumed.snapshots[-1] != reference.snapshots[end - 1]:
            raise ValueError(f"snapshot mismatch at bar {end - 1}")
        if end not in boundaries:
            continue
        path = directory / f"checkpoint-{end}.json"
        started = time.perf_counter()
        path.write_text(json.dumps(resumed.checkpoint(), allow_nan=False))
        restored = Replay.restore(json.loads(path.read_text()))
        # Includes R/N zones, strength, lineage, consumption, candles, records,
        # sweeps, event order and snapshots; no lossy hashing of domain objects.
        if restored.__dict__ != resumed.__dict__:
            fields = sorted(
                k for k in resumed.__dict__ if restored.__dict__.get(k) != resumed.__dict__[k]
            )
            raise ValueError(f"restored state mismatch at {end}: {fields}")
        checkpoints.append(
            {
                "closed_bars": end,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "restore_with_io_seconds": time.perf_counter() - started,
                "state_equal": True,
            }
        )
        resumed = restored
    if resumed.__dict__ != reference.__dict__:
        raise ValueError("final resumed state differs from uninterrupted reference")
    return reference, {
        "closed_bars": len(candles),
        "checkpoints": checkpoints,
        "snapshots_compared": len(reference.snapshots),
        "events_compared": len(reference.events),
        "sweeps_compared": len(reference.sweeps),
        "records_compared": len(reference.records),
        "final_state_equal": True,
    }


def audit_chart(path: Path, expected: dict, baseline: dict) -> dict:
    started = time.perf_counter()
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture hash changed: {path.name}")
    source = json.loads(content)
    if (source["symbol"], source["timeframe"]) != (expected["symbol"], expected["tf"]):
        raise ValueError(f"fixture identity changed: {path.name}")
    candles = [Candle.model_validate(row) for row in source["candles"][:-1]]
    with tempfile.TemporaryDirectory(prefix="eq-e3-4-") as directory:
        reference, result = verify_restarts(candles, Path(directory))
    if any(baseline.get(k) != v for k, v in shadow_metrics(reference).items()):
        raise ValueError(f"shadow baseline mismatch: {path.name}")
    return {
        "symbol": source["symbol"],
        "timeframe": source["timeframe"],
        "sha256": expected["sha256"],
        "baseline_match": True,
        "elapsed_seconds": time.perf_counter() - started,
        **result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/.replay_cache/eq_e3_4.json")
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
    # Partial journal is explicitly not a completed panel and survives interruption.
    with args.output.with_suffix(".partial.jsonl").open("w") as journal:
        for identity, row in expected.items():
            result = audit_chart(
                ROOT / "frontend/research/fixtures" / row["file"], row, observed[identity]
            )
            charts.append(result)
            journal.write(json.dumps(result, allow_nan=False) + "\n")
            journal.flush()
            print(f"{len(charts)}/{len(expected)} {row['file']}", flush=True)
    source_paths = [
        Path(__file__),
        ROOT / "research/eq_levels_e1.py",
        ROOT / "research/eq_levels_e3_shadow.py",
    ]
    report = {
        "schema": 1,
        "complete": True,
        "expected_charts": len(expected),
        "production_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
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
