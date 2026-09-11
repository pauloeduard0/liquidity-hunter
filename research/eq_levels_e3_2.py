"""Apply the E3.1 operational decision to a real shadow baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.eq_levels_e3_contract import rollout_decision


def assess(report: dict, *, expected_charts: int, max_seconds: float = 300.0) -> dict:
    decision = rollout_decision(report, expected_charts=expected_charts, max_seconds=max_seconds)
    return {
        "charts": expected_charts,
        "elapsed_seconds": report["elapsed_seconds"],
        "max_seconds": max_seconds,
        "decision": decision,
        "divergent_charts": sum(
            any(row[field] > 0 for field in ("snapshots_with_R_N_difference",))
            for row in report["rows"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, default=Path("research/eq_levels_e3_panel_baseline.json")
    )
    args = parser.parse_args()
    report = assess(json.loads(args.baseline.read_text()), expected_charts=211)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
