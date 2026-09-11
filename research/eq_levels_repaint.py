"""E0 provenance transitions and snapshot parity, using production calls only."""

import argparse
import json
from collections import Counter
from pathlib import Path

from research.eq_levels_audit import (
    ROOT,
    Candle,
    _equal_high_detector,
    _equal_low_detector,
    grouped,
    mark_swept_zones,
    members,
)


def run(path):
    report = json.loads(path.read_text())
    rows = []
    for chart in report["charts"]:
        d = json.loads((ROOT / "frontend/research/fixtures" / chart["file"]).read_text())
        full = [Candle.model_validate(c) for c in d["candles"]]
        c = full[:-1]
        for side, factory, kind in [
            ("EQH", _equal_high_detector, "equal_highs"),
            ("EQL", _equal_low_detector, "equal_lows"),
        ]:
            det = factory()
            real = mark_swept_zones(det.detect(full), full)
            stored = [z for z in d["liquidity_zones"] if z["zone_type"] == kind]
            closed = mark_swept_zones(det.detect(c), c)
            closed_keys = {(z.formed_at, z.price_low, z.price_high) for z in closed}
            full_keys = {(z.formed_at, z.price_low, z.price_high) for z in real}
            live_edge = dict(
                added=len(full_keys - closed_keys), removed=len(closed_keys - full_keys)
            )

            def encode(z):
                return (
                    z["formed_at"],
                    z["price_low"],
                    z["price_high"],
                    z["invalidated_at"],
                    z["breached_at"],
                    z["is_mitigated"],
                    z["sweep_rejected"],
                    round(z["strength"], 12),
                )

            parity = len(real) == len(stored) and set(
                encode(z.model_dump(mode="json")) for z in real
            ) == set(map(encode, stored))
            counts = Counter()
            previous = {}
            examples = []
            for end in sorted(set(range(40, len(c) + 1, report["replay_step"])) | {len(c)}):
                _, _, pairs = grouped(det, c[:end])
                now = {members(g): z for z, g in pairs}
                disappeared = previous.keys() - now.keys()
                appeared = now.keys() - previous.keys()
                for old in disappeared:
                    for new in appeared:
                        shared = len(set(old) & set(new))
                        if not shared:
                            continue
                        a, b = previous[old], now[new]
                        counts["shared_pivot_transitions"] += 1
                        same = a.price_low == b.price_low and a.price_high == b.price_high
                        counts["same_price_band_new_members"] += same
                        counts["formation_moves_forward"] += b.formed_at > a.formed_at
                        prior_state, state = mark_swept_zones([a, b], c[:end])
                        reset = prior_state.is_mitigated and not state.is_mitigated
                        counts["consumed_to_live_transitions"] += reset
                        if reset and len(examples) < 3:
                            examples.append(
                                dict(
                                    observed=c[end - 1].timestamp.isoformat(),
                                    shared_pivots=shared,
                                    previous=prior_state.model_dump(mode="json"),
                                    current=state.model_dump(mode="json"),
                                )
                            )
                previous = now
            rows.append(
                dict(
                    chart=chart["file"],
                    side=side,
                    snapshot_parity=parity,
                    last_bar_geometry_changes=live_edge,
                    transitions=dict(counts),
                    reset_examples=examples,
                )
            )
    report["provenance_replay"] = rows
    path.write_text(json.dumps(report, allow_nan=False, separators=(",", ":")) + "\n")
    print("snapshot parity", sum(r["snapshot_parity"] for r in rows), "/", len(rows))
    print(dict(sum((Counter(r["transitions"]) for r in rows), Counter())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_baseline.json")
    args = ap.parse_args()
    run(args.json)
