"""E0 complementary controls and examples, applied to the completed audit baseline.

poetry run python -m research.eq_levels_followup
The comparison pool is a confirmed single fractal pivot, not a random timestamp.
Controls reuse the real swing detector and mitigation. Nothing is fitted.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from research.eq_levels_audit import (
    ROOT,
    Candle,
    _equal_high_detector,
    _equal_low_detector,
    age_bucket,
    distance_bucket,
    forward,
    mark_swept_zones,
    quality_summary,
    scale_series,
    stats,
)


def revisit(c, start, low, high, side):
    departed = False
    for j in range(start + 1, min(len(c), start + 41)):
        overlaps = c[j].high >= low and c[j].low <= high
        if not overlaps:
            departed = True
        if departed and overlaps:
            level = high if side == "EQH" else low
            beyond = c[j].close > level if side == "EQH" else c[j].close < level
            crossed = c[j].high > level if side == "EQH" else c[j].low < level
            return dict(
                index=j, wait=j - start, rejection=crossed and not beyond, close_through=beyond
            )
    return None


def run(path, fixtures, visual):
    b = json.loads(path.read_text())
    comparisons = []
    for chart in b["charts"]:
        name = chart["file"]
        d = json.loads((fixtures / name).read_text())
        c = [Candle.model_validate(x) for x in d["candles"][:-1]]
        ix = {x.timestamp: i for i, x in enumerate(c)}
        atr = scale_series(c)
        n = len(c)
        for side, factory in [("EQH", _equal_high_detector), ("EQL", _equal_low_detector)]:
            swings = mark_swept_zones(factory()._swing_detector.detect(c), c)
            cohorts = [r for r in b["frozen_cohorts"] if r["chart"] == name and r["side"] == side]
            for source, field in [("after_wick", "invalidated_at"), ("after_close", "breached_at")]:
                controls = []
                for z in swings:
                    t = getattr(z, field)
                    if t is None:
                        continue
                    born = ix[z.formed_at]
                    start = ix[t]
                    if start < born + 5 or start + 40 >= n:
                        continue
                    level = z.price_high if side == "EQH" else z.price_low
                    dist = abs(level - c[max(0, start - 1)].close) / atr[max(0, start - 1)]
                    controls.append((z, born, start, dist))
                for co in cohorts:
                    start = co["death" if source == "after_wick" else "breach"]
                    if start is None or start < co["observed"] or start + 40 >= n:
                        continue
                    born = co["born"]
                    level = co["high"] if side == "EQH" else co["low"]
                    dist = abs(level - c[max(0, start - 1)].close) / atr[max(0, start - 1)]
                    matched = [
                        (z, cb, cs)
                        for z, cb, cs, cd in controls
                        if cs != start
                        and abs(cs - start) <= 80
                        and cs * 4 // n == start * 4 // n
                        and age_bucket(cs - cb) == age_bucket(start - born)
                        and distance_bucket(cd) == distance_bucket(dist)
                        and 0.8 <= (atr[cs] / c[cs].close) / (atr[start] / c[start].close) <= 1.25
                    ]
                    # Geometrically identical comparator is not independent information.
                    matched = [m for m in matched if not co["low"] <= m[0].price_low <= co["high"]]
                    selected = sorted(
                        matched,
                        key=lambda m: (abs(m[2] - start), abs((m[2] - m[1]) - (start - born))),
                    )[:3]
                    r = revisit(c, start, co["low"], co["high"], side)
                    control_rows = []
                    for z, cb, cs in selected:
                        cr = revisit(c, cs, z.price_low, z.price_high, side)
                        control_rows.append(
                            dict(
                                pivot=cb,
                                start=cs,
                                revisited=cr is not None,
                                rejection=bool(cr and cr["rejection"]),
                                close_through=bool(cr and cr["close_through"]),
                                wait=cr["wait"] if cr else None,
                                reaction={
                                    str(h): forward(c, cr["index"], h, side, atr[cr["index"]])
                                    if cr
                                    else None
                                    for h in [5, 10, 20, 40]
                                },
                            )
                        )
                    comparisons.append(
                        dict(
                            chart=name,
                            symbol=chart["symbol"],
                            tf=chart["tf"],
                            side=side,
                            source=source,
                            start=start,
                            block=start * 4 // n,
                            matched=len(selected),
                            revisited=r is not None,
                            rejection=bool(r and r["rejection"]),
                            close_through=bool(r and r["close_through"]),
                            wait=r["wait"] if r else None,
                            reaction={
                                str(h): forward(c, r["index"], h, side, atr[r["index"]])
                                if r
                                else None
                                for h in [5, 10, 20, 40]
                            },
                            controls=control_rows,
                        )
                    )
    summary = {}
    for tf in sorted({r["tf"] for r in comparisons}):
        for side in ["EQH", "EQL"]:
            for source in ["after_wick", "after_close"]:
                rr = [
                    r
                    for r in comparisons
                    if (r["tf"], r["side"], r["source"]) == (tf, side, source)
                ]
                matched = [r for r in rr if r["matched"]]
                deltas = {
                    m: [
                        float(r[m]) - sum(float(c[m]) for c in r["controls"]) / len(r["controls"])
                        for r in matched
                    ]
                    for m in ["revisited", "rejection", "close_through"]
                }
                sy = defaultdict(list)
                for r, delta in zip(matched, deltas["revisited"], strict=False):
                    sy[r["symbol"]].append(delta)
                summary[f"{tf}/{side}/{source}"] = dict(
                    eligible=len(rr),
                    matched=len(matched),
                    raw={
                        m: stats(float(r[m]) for r in rr)
                        for m in ["revisited", "rejection", "close_through"]
                    },
                    delta={m: stats(v) for m, v in deltas.items()},
                    wait=stats(r["wait"] for r in rr),
                    blocks={
                        str(q): stats(
                            delta
                            for r, delta in zip(matched, deltas["revisited"], strict=False)
                            if r["block"] == q
                        )
                        for q in range(4)
                    },
                    per_symbol=[dict(symbol=s, delta=stats(v)) for s, v in sorted(sy.items())],
                )
    # Paired observational EQ controls for candidate axes: same symbol/TF/side,
    # quarter, source, volatility unavailable here => retain diagnostic label.
    axes = {}
    first = [e for e in b["events"] if e["source"] == "first_touch"]
    for tf in sorted({e["tf"] for e in first}):
        for side in ["EQH", "EQL"]:
            events = [e for e in first if e["tf"] == tf and e["side"] == side]
            axes[f"{tf}/{side}"] = {
                "4+": quality_summary([e for e in events if e["touches"] >= 4]),
                "5+": quality_summary([e for e in events if e["touches"] >= 5]),
                "rejection_A": quality_summary([e for e in events if e["category"] == "A"]),
                "close_through_BC": quality_summary(
                    [e for e in events if e["category"] in ["B", "C"]]
                ),
                "touch_D": quality_summary([e for e in events if e["category"] == "D"]),
            }
    # Deterministic descriptive cases. Selection is explicit and post hoc.
    examples = {}
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        rr = [r for r in b["retrospective_levels"] if r["symbol"] == symbol]
        cases = {}
        case_candles = {}

        def favorable_reaction(r, case_candles=case_candles):
            if r["death"] is None:
                return False
            if r["chart"] not in case_candles:
                snapshot = json.loads((fixtures / r["chart"]).read_text())
                bars = [Candle.model_validate(x) for x in snapshot["candles"][:-1]]
                case_candles[r["chart"]] = (bars, scale_series(bars))
            bars, units = case_candles[r["chart"]]
            outcome = forward(bars, r["death"], 10, r["side"], units[r["death"]])
            return outcome is not None and outcome["won"] == 1

        conditions = {
            "A_clean_rejection": lambda r: r["rejected"]
            and r["spread_atr"] < 0.25
            and favorable_reaction(r),
            "C_old": lambda r: r["age"] > 100,
            "D_sweep_rejection": lambda r: r["rejected"],
            "E_close_through": lambda r: r["breach"] is not None,
            "F_3plus": lambda r: r["touches"] >= 3,
            "G_widest_equal_candidate": lambda r: True,
        }
        for name, predicate in conditions.items():
            candidates = [r for r in rr if predicate(r)]
            if name == "C_old":
                candidates.sort(key=lambda r: r["age"], reverse=True)
            elif name == "G_widest_equal_candidate":
                candidates.sort(key=lambda r: r["spread_atr"], reverse=True)
            cases[name] = candidates[0] if candidates else None
        near = []
        for a_i, a in enumerate(rr):
            for other in rr[a_i + 1 :]:
                if a["chart"] != other["chart"] or a["side"] != other["side"]:
                    continue
                # Normalize gap by a's formation scale, recover from band spread.
                if not a["spread_atr"]:
                    continue
                unit = (a["high"] - a["low"]) / a["spread_atr"]
                gap = abs(a["midpoint"] - other["midpoint"]) / unit
                if gap <= 1:
                    near.append(
                        dict(
                            first=a,
                            second=other,
                            gap_atr=gap,
                            coexisted=max(a["confirmation"], other["confirmation"])
                            < min(a["death"] or 10**9, other["death"] or 10**9),
                        )
                    )
        cases["B_close_levels"] = (
            min(near, key=lambda r: (not r["coexisted"], r["gap_atr"])) if near else None
        )
        examples[symbol] = cases
    b["followup_controls"] = dict(
        definition=(
            "Confirmed single swing pools, first wick/close after confirmation; same symbol, "
            "TF, side, quarter, +/-80 bars, volatility ratio .8-1.25, age/distance bucket. "
            "Up to 3 nearest, with replacement. Overlapping outcome windows and shared "
            "market moves remain dependent. Not independent random pools."
        ),
        summary=summary,
        rows=comparisons,
    )
    b["extra_axes"] = axes
    b["examples"] = examples
    if visual.exists():
        b["visual_current_snapshot"] = json.loads(visual.read_text())
    path.write_text(json.dumps(b, allow_nan=False, separators=(",", ":")) + "\n")
    print(
        f'Added {len(comparisons)} eligible lifecycle observations, '
        f'{sum(bool(r["matched"]) for r in comparisons)} matched'
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_baseline.json")
    ap.add_argument("--fixtures", type=Path, default=ROOT / "frontend/research/fixtures")
    ap.add_argument("--visual", type=Path, default=Path("/tmp/eq_visual_baseline.json"))
    args = ap.parse_args()
    run(args.json, args.fixtures, args.visual)
