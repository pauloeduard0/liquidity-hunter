"""E3.3: attribute R/N membership divergence without changing either replay arm."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e1 import FACTORIES, Replay, semantic, token
from research.eq_levels_e3_shadow import shadow_metrics

ROOT = Path(__file__).resolve().parents[1]


def identity_evidence(raw, normal, anchor, *, pivot):
    """Check N against the last pivot's R membership, including additions/removals."""
    r_only, n_only = raw - normal, normal - raw
    explained = normal == anchor and (not pivot or raw == normal)
    return {
        "R_only": len(r_only),
        "N_only": len(n_only),
        "explained": explained,
        "different": bool(r_only or n_only),
    }


def audit_chart(path: Path, expected: dict, baseline: dict, verify_every: int = 100) -> dict:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture hash changed: {path.name}")
    source = json.loads(content)
    if (source["symbol"], source["timeframe"]) != (expected["symbol"], expected["tf"]):
        raise ValueError(f"fixture identity changed: {path.name}")
    candles = [Candle.model_validate(row) for row in source["candles"][:-1]]
    run = Replay()
    anchors = {side: set() for side in FACTORIES}
    anchor_index = {side: None for side in FACTORIES}
    previous_raw = {side: set() for side in FACTORIES}
    previous_different = {side: False for side in FACTORIES}
    rows = {(side, block): Counter() for side in FACTORIES for block in range(4)}
    examples = []
    for i, candle in enumerate(candles):
        before = {side: len(run.swings[side]) for side in FACTORIES}
        run.feed(candle, verify=i % verify_every == 0 or i == len(candles) - 1)
        block = min(3, i * 4 // len(candles))
        for side in FACTORIES:
            raw, normal = set(run.raw[side]), set(run.normal[side])
            pivot = len(run.swings[side]) > before[side]
            if pivot:
                anchors[side] = raw.copy()
                anchor_index[side] = i
            evidence = identity_evidence(raw, normal, anchors[side], pivot=pivot)
            counts = rows[side, block]
            counts["snapshots"] += 1
            counts["pivot_confirmations"] += pivot
            counts["raw_changes_without_pivot"] += raw != previous_raw[side] and not pivot
            counts["divergent_snapshots"] += evidence["different"]
            counts["R_only_exposures"] += evidence["R_only"]
            counts["N_only_exposures"] += evidence["N_only"]
            counts["unexplained_snapshots"] += not evidence["explained"]
            counts["R_only_live_exposures"] += sum(
                not run.raw[side][key].is_mitigated for key in raw - normal
            )
            counts["N_only_live_exposures"] += sum(
                not run.normal[side][key].is_mitigated for key in normal - raw
            )
            for key in raw & normal:
                r, n = run.raw[side][key], run.normal[side][key]
                counts["shared_exposures"] += 1
                counts["shared_strength_differences"] += abs(r.strength - n.strength) > 1e-10
                counts["shared_semantic_differences"] += semantic(r) != semantic(n)
            if evidence["different"]:
                counts["max_bars_since_pivot"] = max(
                    counts["max_bars_since_pivot"], i - anchor_index[side]
                )
                if not previous_different[side]:
                    counts["divergence_episode_starts"] += 1
                    # One complete onset per side/block, retaining actual pivot memberships.
                    if not any(e["side"] == side and e["block"] == block for e in examples):
                        examples.append(
                            {
                                "side": side,
                                "block": block,
                                "index": i,
                                "timestamp": candle.timestamp.isoformat(),
                                "last_pivot_index": anchor_index[side],
                                "explained": evidence["explained"],
                                "R_only": [
                                    {"version": token(side, k), "pivots": list(k)}
                                    for k in sorted(raw - normal)
                                ],
                                "N_only": [
                                    {"version": token(side, k), "pivots": list(k)}
                                    for k in sorted(normal - raw)
                                ],
                            }
                        )
            previous_raw[side] = raw
            previous_different[side] = evidence["different"]
    metrics = shadow_metrics(run)
    for field, value in metrics.items():
        if baseline.get(field) != value:
            raise ValueError(f"shadow baseline mismatch: {path.name}/{field}")
    return {
        "symbol": source["symbol"],
        "timeframe": source["timeframe"],
        "sha256": expected["sha256"],
        "native_parity_checks": run.diagnostics["native_parity_checks"],
        "baseline_match": True,
        "rows": [
            {"side": side, "block": block, **counts} for (side, block), counts in rows.items()
        ],
        "examples": examples,
    }


def summarize(charts):
    groups = defaultdict(Counter)
    for chart in charts:
        for row in chart["rows"]:
            group = groups[chart["timeframe"], row["side"], row["block"]]
            for key, value in row.items():
                if key in {"side", "block"}:
                    continue
                if key.startswith("max_"):
                    group[key] = max(group[key], value)
                else:
                    group[key] += value
    return [
        {"timeframe": tf, "side": side, "block": block, **counts}
        for (tf, side, block), counts in sorted(groups.items())
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/.replay_cache/eq_e3_3.json")
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
    started = time.perf_counter()
    charts = []
    for identity, row in expected.items():
        charts.append(
            audit_chart(ROOT / "frontend/research/fixtures" / row["file"], row, observed[identity])
        )
        print(f"{len(charts)}/{len(expected)} {row['file']}", flush=True)
    report = {
        "schema": 1,
        "production_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "shadow_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "verify_every": 100,
        "elapsed_seconds": time.perf_counter() - started,
        "charts": charts,
        "summary": summarize(charts),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
