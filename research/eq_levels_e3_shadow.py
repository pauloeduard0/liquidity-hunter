"""E3 shadow comparison between native-equivalent R and causal N, research-only."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import Replay
from research.eq_levels_e2 import validate_candidate

ROOT = Path(__file__).resolve().parents[1]


def shadow_metrics(run: Replay) -> dict:
    validate_candidate(run)
    differences = [
        snapshot["sides"][side]["difference_R_N"]
        for snapshot in run.snapshots
        for side in ("EQH", "EQL")
    ]
    block_counts = [0, 0, 0, 0]
    block_totals = [0, 0, 0, 0]
    n = len(run.snapshots)
    for snapshot in run.snapshots:
        block = min(3, snapshot["index"] * 4 // n)
        value = sum(snapshot["sides"][side]["difference_R_N"] for side in ("EQH", "EQL"))
        block_counts[block] += value > 0
        block_totals[block] += value
    return {
        "candles": len(run.candles),
        "snapshots_with_R_N_difference": sum(value > 0 for value in differences),
        "max_R_N_difference": max(differences, default=0),
        "sum_R_N_difference": sum(differences),
        "block_divergent_snapshots": block_counts,
        "block_sum_R_N_difference": block_totals,
        "event_counts": dict(Counter(event["kind"] for event in run.events)),
        "published_versions": sum(event["kind"] == "publish" for event in run.events),
    }


def run_shadow(path: Path) -> dict:
    source = json.loads(path.read_text())
    candles = [Candle.model_validate(row) for row in source["candles"][:-1]]
    run = Replay()
    for candle in candles:
        run.feed(candle)
    return {"symbol": source["symbol"], "timeframe": source["timeframe"], **shadow_metrics(run)}


def run_panel(paths: list[Path]) -> dict:
    started = time.perf_counter()
    rows = [run_shadow(path) for path in paths]
    return {
        "schema": 1,
        "charts": len(rows),
        "elapsed_seconds": time.perf_counter() - started,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_panel(args.paths), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
