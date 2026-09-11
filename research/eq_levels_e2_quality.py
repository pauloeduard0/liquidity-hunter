"""Descriptive E2 quality readout on the fixed final-block holdout."""

from __future__ import annotations

import argparse
import json
import statistics as stats
from pathlib import Path

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def quality_holdout(baseline: dict, *, symbols=None, block="3", horizon="10") -> dict:
    if symbols is None:
        symbols = tuple(sorted({c["symbol"] for c in baseline["charts"]}))
    rows = {}
    charts = [c for c in baseline["charts"] if c["symbol"] in symbols]
    for timeframe in sorted({c["tf"] for c in charts}):
        for side in ("EQH", "EQL"):
            group = [
                e for c in charts if c["tf"] == timeframe for e in c["sweeps"] if e["side"] == side
            ]
            arms = {}
            for arm in ("R", "N"):
                selected = [
                    e
                    for e in group
                    if e["arm"] == arm
                    and str(e["block"]) == block
                    and e["outcomes"][horizon] is not None
                    and e["control"][horizon]["won"] is not None
                ]
                deltas = [
                    e["outcomes"][horizon]["won"] - e["control"][horizon]["won"] for e in selected
                ]
                arms[arm] = {"n": len(deltas), "delta_mean": stats.mean(deltas) if deltas else None}
            rows[f"{timeframe}/{side}"] = arms
    return {
        "schema": 1,
        "symbols": list(symbols),
        "block": block,
        "horizon": horizon,
        "groups": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("research/eq_levels_e1_baseline.json")
    )
    parser.add_argument(
        "--json", type=Path, default=Path("research/eq_levels_e2_quality_baseline.json")
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()
    result = quality_holdout(json.loads(args.baseline.read_text()), symbols=args.symbols)
    args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
