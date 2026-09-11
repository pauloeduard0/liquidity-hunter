"""E0: read-only EQ audit, importing production detectors and lifecycle.

poetry run python research/eq_levels_audit.py --json research/eq_levels_baseline.json
No network, no production mutation. Frozen first-observed cohorts are a measurement
instrument, NOT a proposed detector. Final-window histories are explicitly retrospective.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics as st
import subprocess
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liquidity_hunter.app.dashboard_data import (  # noqa: E402
    _equal_high_detector,
    _equal_low_detector,
)
from liquidity_hunter.core.domain import Candle  # noqa: E402
from liquidity_hunter.indicators.supertrend import true_range_series  # noqa: E402
from liquidity_hunter.liquidity.mitigation import mark_swept_zones  # noqa: E402

HORIZONS = (5, 10, 20, 40)
RADII = (0.1, 0.25, 0.5, 1.0)


def stats(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return {"n": 0, "mean": None, "p50": None, "p90": None, "max": None}
    return dict(
        n=len(xs),
        mean=st.mean(xs),
        p50=st.median(xs),
        p90=xs[math.ceil(0.9 * len(xs)) - 1],
        max=xs[-1],
    )


def bucket(x, cuts, labels):
    return next((label for cut, label in zip(cuts, labels, strict=False) if x <= cut), labels[-1])


def age_bucket(x):
    return bucket(x, [10, 30, 100], ["0-10", "11-30", "31-100", "100+"])


def distance_bucket(x):
    return bucket(x, [0.5, 1, 2, 4], ["0-0.5", "0.5-1", "1-2", "2-4", "4+"])


def touch_bucket(n):
    return "5+" if n >= 5 else str(n)


def scale_series(c):
    # Same causal expanding TR/close normalization used by PISO; not Wilder ATR.
    out, total = [], 0.0
    for i, (bar, tr) in enumerate(zip(c, true_range_series(c), strict=False)):
        total += tr / bar.close
        out.append(max(1e-12, total / (i + 1) * bar.close))
    return out


def forward(c, i, h, side, atr):
    if i + h >= len(c):
        return None
    sign = -1 if side == "EQH" else 1
    base = c[i].close
    high = max(x.high for x in c[i + 1 : i + h + 1])
    low = min(x.low for x in c[i + 1 : i + h + 1])
    mfe, mae = (base - low, high - base) if sign == -1 else (high - base, base - low)
    mfe, mae = max(0.0, mfe / atr), max(0.0, mae / atr)
    return dict(
        mfe=mfe,
        mae=mae,
        ratio=mfe / mae if mae else None,
        won=float(mfe > mae),
        move=sign * (c[i + h].close - base) / atr,
    )


def category(c, i, low, high, side):
    b = c[i]
    through = b.high > high if side == "EQH" else b.low < low
    beyond = b.close > high if side == "EQH" else b.close < low
    prev_inside = i > 0 and (c[i - 1].close <= high if side == "EQH" else c[i - 1].close >= low)
    if through:
        return "A" if not beyond else ("C" if prev_inside else "B")
    if b.high >= low and b.low <= high:
        return "D"
    return None


def geometry(z):
    return (z.formed_at.isoformat(), z.price_low, z.price_high)


def grouped(det, c):
    swings = det._swing_detector.detect(c)
    groups = det._group_by_tolerance(swings, det._resolve_tolerance(c))
    zones = det.detect(c)
    eligible = [g for g in groups if len(g) >= det._min_touches]
    assert len(zones) == len(eligible)
    for z, g in zip(zones, eligible, strict=False):
        assert z.price_low == min(s.price_high for s in g)
        assert z.price_high == max(s.price_high for s in g)
        assert z.formed_at == max(s.formed_at for s in g)
    return swings, groups, list(zip(zones, eligible, strict=False))


def members(g):
    return tuple(sorted(s.formed_at.isoformat() for s in g))


def close_pairs(zs, atr):
    return {
        str(r): sum(
            abs((a.price_low + a.price_high - b.price_low - b.price_high) / 2) <= r * atr
            for a, b in combinations(zs, 2)
        )
        for r in RADII
    }


def overlaps_snapshot(d, z):
    # Snapshot geometry only. Never used as a causal quality feature.
    lo, hi = z.price_low, z.price_high

    def hit(a, b):
        return a is not None and b is not None and a <= hi and b >= lo

    events = d.get("internal_structure_events", [])
    result = {
        name: any(
            hit(e.get("reference_price_level"), e.get("reference_price_level"))
            for e in events
            if e["event"] == kind and not e.get("provisional")
        )
        for name, kind in [("bos", "break_of_structure"), ("choch", "change_of_character")]
    }
    result["poi"] = any(hit(p["price_low"], p["price_high"]) for p in d.get("poi_zones", []))
    result["liquidation"] = any(
        hit(p["price_low"], p["price_high"])
        for p in (d.get("liquidation_map") or {}).get("bands", [])
    )
    vp = d.get("volume_profile") or {}
    result["volume_profile"] = any(
        hit(vp.get(k), vp.get(k)) for k in ["poc_price", "value_area_low", "value_area_high"]
    )
    points = (d.get("vwap") or {}).get("points", [])
    p = points[-1] if points else {}
    result["vwap_1sigma"] = any(hit(p.get(k), p.get(k)) for k in ["upper_1", "lower_1"])
    return result


def audit_chart(path, step):
    raw = path.read_bytes()
    d = json.loads(raw)
    # Last bar may be open: exclude it from ALL measured detector runs.
    c = [Candle.model_validate(x) for x in d["candles"][:-1]]
    n = len(c)
    assert all(a.timestamp < b.timestamp for a, b in zip(c, c[1:], strict=False))
    ix = {x.timestamp.isoformat(): i for i, x in enumerate(c)}
    atr = scale_series(c)
    rows, cohorts, events, checkpoints = [], [], [], []
    result = dict(
        file=path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        symbol=d["symbol"],
        tf=d["timeframe"],
        n=n,
        start=c[0].timestamp.isoformat(),
        end=c[-1].timestamp.isoformat(),
        excluded_last_bar=d["candles"][-1]["timestamp"],
        sides={},
    )
    for side, factory in [("EQH", _equal_high_detector), ("EQL", _equal_low_detector)]:
        det = factory()
        swings, groups, pairs = grouped(det, c)
        zs = mark_swept_zones([z for z, _ in pairs], c)
        tol = det._resolve_tolerance(c)
        near = sum(
            abs(a.price_high - b.price_high) <= min(a.price_high, b.price_high) * tol
            for a, b in combinations(swings, 2)
        )
        active = [z for z in zs if not z.is_mitigated]
        pivot_ids = [s.formed_at for g in groups for s in g]
        sresult = dict(
            pivots=len(swings),
            near_pairs=near,
            groups=len(groups),
            group_sizes=dict(Counter(map(len, groups))),
            formed=len(zs),
            live=len(active),
            swept=sum(z.is_mitigated for z in zs),
            rejected=sum(z.was_rejected for z in zs),
            breached=sum(z.is_breached for z in zs),
            tolerance_fraction=tol,
            shared_pivots=len(pivot_ids) - len(set(pivot_ids)),
            simultaneous_final_pairs=close_pairs(active, atr[-1]),
            overlapping_final_bands=sum(
                a.price_low <= b.price_high and b.price_low <= a.price_high
                for a, b in combinations(zs, 2)
            ),
        )
        result["sides"][side] = sresult
        for z, (_, g) in zip(zs, pairs, strict=False):
            ids = sorted(ix[s.formed_at.isoformat()] for s in g)
            born = ids[-1]
            level = z.price_high if side == "EQH" else z.price_low
            death = ix.get(z.invalidated_at.isoformat()) if z.invalidated_at else None
            breach = ix.get(z.breached_at.isoformat()) if z.breached_at else None
            consumed = sum(
                any(
                    (b.high > s.price_high if side == "EQH" else b.low < s.price_low)
                    for b in c[i + 1 : born + 1]
                )
                for s, i in zip(sorted(g, key=lambda s: s.formed_at), ids, strict=False)
            )
            row = dict(
                chart=path.name,
                symbol=d["symbol"],
                tf=d["timeframe"],
                side=side,
                born=born,
                confirmation=born + det._swing_detector._lookback,
                formed_at=z.formed_at.isoformat(),
                invalidated_at=z.invalidated_at.isoformat() if z.invalidated_at else None,
                breached_at=z.breached_at.isoformat() if z.breached_at else None,
                death=death,
                breach=breach,
                rejected=z.was_rejected,
                touches=len(g),
                pivots=ids,
                prices=[s.price_high for s in sorted(g, key=lambda s: s.formed_at)],
                low=z.price_low,
                high=z.price_high,
                representative=level,
                midpoint=(z.price_low + z.price_high) / 2,
                pivot_deltas_atr=[
                    (level - s.price_high) / atr[born] for s in sorted(g, key=lambda s: s.formed_at)
                ],
                spread_atr=(z.price_high - z.price_low) / atr[born],
                gaps=[b - a for a, b in zip(ids, ids[1:], strict=False)],
                age=n - 1 - born,
                consumed_origin_pivots=consumed,
                strength=z.strength,
                distance_atr=abs(level - c[-1].close) / atr[-1],
                overlap_snapshot=overlaps_snapshot(d, z),
            )
            rows.append(row)
        # Every observation calls the real detector. Freeze a cohort at first
        # observation; do not count its growing/reshuffled pivot lineage twice.
        used = set()
        previous = {}
        counts = Counter()
        for end in sorted(set(range(40, n + 1, step)) | {n}):
            prefix = c[:end]
            _, _, pp = grouped(det, prefix)
            pzones = mark_swept_zones([z for z, _ in pp], prefix)
            live = [z for z in pzones if not z.is_mitigated]
            now = {members(g): z for z, g in pp}
            counts["observations"] += 1
            counts["previous_groups"] += len(previous)
            counts["disappeared_memberships"] += len(previous.keys() - now.keys())
            counts["unchanged_memberships"] += len(previous.keys() & now.keys())
            counts["strength_changed_same_members"] += sum(
                abs(previous[k].strength - now[k].strength) > 1e-12
                for k in previous.keys() & now.keys()
            )
            counts["future_vs_prefix_groups"] += len(now)
            final_old = [
                g for _, g in pairs if max(ix[s.formed_at.isoformat()] for s in g) + 5 < end
            ]
            counts["historical_symmetric_difference"] += len(
                set(now) ^ set(members(g) for g in final_old)
            )
            current_atr = atr[end - 1]
            distances = [
                abs((z.price_high if side == "EQH" else z.price_low) - prefix[-1].close)
                / current_atr
                for z in live
            ]
            ahead = [
                z
                for z in live
                if (
                    z.price_low > prefix[-1].close
                    if side == "EQH"
                    else z.price_high < prefix[-1].close
                )
            ]
            checkpoints.append(
                dict(
                    chart=path.name,
                    tf=d["timeframe"],
                    side=side,
                    index=end - 1,
                    live=len(live),
                    target_render=min(2, len(ahead)),
                    distances=dict(Counter(distance_bucket(x) for x in distances)),
                    ages=dict(
                        Counter(age_bucket(end - 1 - ix[z.formed_at.isoformat()]) for z in live)
                    ),
                    outside_4atr=sum(x > 4 for x in distances),
                    pairs=close_pairs(live, current_atr),
                )
            )
            for z, g in pp:
                key = members(g)
                if set(key) & used:
                    continue
                used.update(key)
                observed = end - 1
                z_now = mark_swept_zones([z], prefix)[0]
                frozen = mark_swept_zones([z], c)[0]
                death = ix.get(frozen.invalidated_at.isoformat()) if frozen.invalidated_at else None
                breach = ix.get(frozen.breached_at.isoformat()) if frozen.breached_at else None
                ci = len(cohorts)
                low, high = z.price_low, z.price_high
                level = high if side == "EQH" else low
                born = ix[z.formed_at.isoformat()]
                cohort = dict(
                    chart=path.name,
                    symbol=d["symbol"],
                    tf=d["timeframe"],
                    side=side,
                    observed=observed,
                    born=born,
                    low=low,
                    high=high,
                    touches=len(g),
                    spread_atr=(high - low) / atr[observed],
                    strength=z.strength,
                    distance_atr=abs(level - c[observed].close) / atr[observed],
                    already_swept=z_now.is_mitigated,
                    death=death,
                    breach=breach,
                    followup=n - 1 - observed,
                    pivots=list(key),
                )
                cohorts.append(cohort)
                # Fixed-level follow-up only after it was actually observable.
                starts = [("first_touch", observed)] if not z_now.is_mitigated else []
                if death is not None and death >= observed:
                    starts.append(("after_wick", death))
                if breach is not None and breach >= observed:
                    starts.append(("after_close", breach))
                for source, start in starts:
                    # A revisit requires departure from the band, then reentry;
                    # consecutive overlapping candles are one visit episode.
                    departed = source == "first_touch"
                    found = None
                    for j in range(start + 1, n):
                        overlaps = c[j].high >= low and c[j].low <= high
                        if not overlaps:
                            departed = True
                        if departed and overlaps:
                            found = j
                            break
                    cohort[source + "_eligible40"] = start + 40 < n
                    cohort[source + "_revisit40"] = found is not None and found - start <= 40
                    if found is None:
                        continue
                    cat = category(c, found, low, high, side)
                    e = dict(
                        chart=path.name,
                        symbol=d["symbol"],
                        tf=d["timeframe"],
                        side=side,
                        cohort=ci,
                        source=source,
                        index=found,
                        block=min(3, found * 4 // n),
                        timestamp=c[found].timestamp.isoformat(),
                        category=cat,
                        wait=found - start,
                        age=found - born,
                        touches=len(g),
                        spread_atr=cohort["spread_atr"],
                        strength=z.strength,
                        distance_atr=cohort["distance_atr"],
                        recent=found - born,
                        previous_sweep=source != "first_touch",
                        outcomes={str(h): forward(c, found, h, side, atr[found]) for h in HORIZONS},
                    )
                    # Controls: same symbol/TF/side, same quarter, within 80
                    # bars, volatility within 25%; exclude overlapping outcomes.
                    controls = [
                        j
                        for j in range(max(1, found - 80), min(n - 40, found + 81))
                        if abs(j - found) > 40
                        and j * 4 // n == found * 4 // n
                        and 0.8 <= (atr[j] / c[j].close) / (atr[found] / c[found].close) <= 1.25
                    ]
                    e["controls_n"] = len(controls)
                    e["control"] = {
                        str(h): {
                            metric: st.mean(
                                o[metric]
                                for j in controls
                                if (o := forward(c, j, h, side, atr[j])) is not None
                                and o[metric] is not None
                            )
                            if any(
                                (o := forward(c, j, h, side, atr[j])) is not None
                                and o[metric] is not None
                                for j in controls
                            )
                            else None
                            for metric in ["mfe", "mae", "won", "move"]
                        }
                        for h in HORIZONS
                    }
                    events.append(e)
                # All distinct sweep episodes on frozen geometry; E is an
                # orthogonal flag, since a repeated sweep is still A/B/C.
                sweep_indices = []
                last = False
                for j in range(observed + 1, n):
                    swept = c[j].high > high if side == "EQH" else c[j].low < low
                    if swept and not last:
                        sweep_indices.append(j)
                    last = swept
                cohort["sweep_episodes"] = len(sweep_indices)
                for j in sweep_indices:
                    # Separate stream, not pooled with first/revisit events.
                    events.append(
                        dict(
                            chart=path.name,
                            symbol=d["symbol"],
                            tf=d["timeframe"],
                            side=side,
                            cohort=ci,
                            source="sweep_episode",
                            index=j,
                            block=min(3, j * 4 // n),
                            timestamp=c[j].timestamp.isoformat(),
                            category=category(c, j, low, high, side),
                            multiple=len(sweep_indices) > 1,
                            wait=j - observed,
                            age=j - born,
                            touches=len(g),
                            spread_atr=cohort["spread_atr"],
                            strength=z.strength,
                            distance_atr=cohort["distance_atr"],
                            previous_sweep=j != sweep_indices[0],
                            outcomes={str(h): forward(c, j, h, side, atr[j]) for h in HORIZONS},
                            controls_n=0,
                            control={},
                        )
                    )
            previous = now
        # Isolate window effects using exactly the SAME confirmed pivots.
        isolated = []
        for end in sorted({n // 4, n // 2, 3 * n // 4}):
            pc = c[:end]
            ps = det._swing_detector.detect(pc)
            old = det._group_by_tolerance(ps, det._resolve_tolerance(pc))
            future = det._group_by_tolerance(ps, tol)
            a = {members(g) for g in old if len(g) >= det._min_touches}
            b = {members(g) for g in future if len(g) >= det._min_touches}
            isolated.append(
                dict(
                    end=end,
                    prefix_tolerance=det._resolve_tolerance(pc),
                    final_tolerance=tol,
                    groups_before=len(a),
                    groups_after=len(b),
                    membership_changes=len(a ^ b),
                )
            )
        sresult["replay"] = dict(counts)
        sresult["isolated_tolerance"] = isolated
        # Left window truncation: only compare memberships surviving boundary.
        left = n // 4
        _, _, shifted = grouped(det, c[left:])
        eligible_original = {
            members(g) for _, g in pairs if min(ix[s.formed_at.isoformat()] for s in g) >= left + 5
        }
        shifted_keys = {members(g) for _, g in shifted}
        sresult["left_window_changes"] = len(eligible_original ^ shifted_keys)
    return result, rows, cohorts, events, checkpoints


def quality_summary(events):
    out = {}
    for h in HORIZONS:
        valid = [e for e in events if e["outcomes"][str(h)] is not None]
        matched = [e for e in valid if e.get("control", {}).get(str(h), {}).get("won") is not None]
        symbols = defaultdict(list)
        for e in matched:
            symbols[e["symbol"]].append(e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"])
        per = [dict(symbol=s, n=len(v), delta=st.mean(v)) for s, v in symbols.items()]
        out[str(h)] = dict(
            n=len(valid),
            matched=len(matched),
            **{
                m: stats(e["outcomes"][str(h)][m] for e in valid)
                for m in ["mfe", "mae", "ratio", "won", "move"]
            },
            delta_won=stats(
                e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"] for e in matched
            ),
            blocks={
                str(b): stats(
                    e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"]
                    for e in matched
                    if e["block"] == b
                )
                for b in range(4)
            },
            symbols=len(per),
            positive_symbol_fraction=sum(p["delta"] > 0 for p in per) / len(per) if per else None,
            median_symbol_delta=st.median(p["delta"] for p in per) if per else None,
            concentration_top3=sum(sorted((p["n"] for p in per), reverse=True)[:3]) / len(matched)
            if matched
            else None,
            per_symbol=sorted(per, key=lambda p: p["delta"]),
        )
    return out


def summarize(charts, rows, cohorts, events, checkpoints):
    result = {}
    for tf in sorted({c["tf"] for c in charts}):
        for side in ["EQH", "EQL"]:
            key = f"{tf}/{side}"
            cc = [c["sides"][side] for c in charts if c["tf"] == tf]
            rr = [r for r in rows if r["tf"] == tf and r["side"] == side]
            co = [r for r in cohorts if r["tf"] == tf and r["side"] == side]
            ee = [e for e in events if e["tf"] == tf and e["side"] == side]
            pp = [p for p in checkpoints if p["tf"] == tf and p["side"] == side]
            result[key] = dict(
                charts=len(cc),
                funnel={
                    k: sum(c[k] for c in cc)
                    for k in [
                        "pivots",
                        "near_pairs",
                        "groups",
                        "formed",
                        "live",
                        "swept",
                        "rejected",
                        "breached",
                    ]
                },
                touches=dict(Counter(r["touches"] for r in rr)),
                spread_by_touches={
                    k: stats(r["spread_atr"] for r in rr if touch_bucket(r["touches"]) == k)
                    for k in ["2", "3", "4", "5+"]
                },
                age=stats(r["age"] for r in rr),
                gaps=stats(x for r in rr for x in r["gaps"]),
                consumed_origin_pivots=sum(r["consumed_origin_pivots"] for r in rr),
                density=stats(p["live"] for p in pp),
                rendered_targets=stats(p["target_render"] for p in pp),
                proximity_pair_observations={
                    str(rad): sum(p["pairs"][str(rad)] for p in pp) for rad in RADII
                },
                active_level_observations=sum(p["live"] for p in pp),
                outside_4atr=sum(p["outside_4atr"] for p in pp),
                distance_buckets=dict(sum((Counter(p["distances"]) for p in pp), Counter())),
                age_buckets=dict(sum((Counter(p["ages"]) for p in pp), Counter())),
                cohort_n=len(co),
                already_swept=sum(c["already_swept"] for c in co),
                multi_sweep_cohorts=sum(c["sweep_episodes"] > 1 for c in co),
                lifecycle={
                    source: dict(
                        eligible=sum(c.get(source + "_eligible40", False) for c in co),
                        revisited=sum(
                            c.get(source + "_eligible40", False)
                            and c.get(source + "_revisit40", False)
                            for c in co
                        ),
                    )
                    for source in ["first_touch", "after_wick", "after_close"]
                },
                overlap_snapshot={
                    k: sum(r["overlap_snapshot"][k] for r in rr)
                    for k in ["bos", "choch", "poi", "liquidation", "volume_profile", "vwap_1sigma"]
                },
                quality={
                    s: quality_summary([e for e in ee if e["source"] == s])
                    for s in ["first_touch", "after_wick", "after_close", "sweep_episode"]
                },
                sweep_categories={
                    cat: quality_summary(
                        [e for e in ee if e["source"] == "sweep_episode" and e["category"] == cat]
                    )
                    for cat in ["A", "B", "C", "D"]
                },
            )
            # E0 candidate axes, each separately. No score, no optimization.
            axes = {
                "touches": lambda e: touch_bucket(e["touches"]),
                "age": lambda e: age_bucket(e["age"]),
                "distance": lambda e: distance_bucket(e["distance_atr"]),
                "spread": lambda e: bucket(
                    e["spread_atr"], [0.1, 0.25, 0.5], ["0-.1", ".1-.25", ".25-.5", ".5+"]
                ),
                "structure_snapshot": lambda e: e.get("structure_snapshot", "unavailable"),
                "previous_sweep": lambda e: str(e["previous_sweep"]),
                "strength": lambda e: bucket(
                    e["strength"], [0.25, 0.5, 0.75], ["0-.25", ".25-.5", ".5-.75", ".75-1"]
                ),
            }
            primary = [e for e in ee if e["source"] == "first_touch"]
            result[key]["axes"] = {
                axis: {
                    b: quality_summary([e for e in primary if fn(e) == b])
                    for b in sorted({fn(e) for e in primary})
                }
                for axis, fn in axes.items()
            }
    totals = defaultdict(lambda: [0, 0])
    for p in checkpoints:
        k = (p["tf"], p["chart"], p["index"])
        totals[k][0] += p["live"]
        totals[k][1] += p["target_render"]
    result["total_density_by_tf"] = {
        tf: dict(
            detector=stats(v[0] for k, v in totals.items() if k[0] == tf),
            rendered_targets=stats(v[1] for k, v in totals.items() if k[0] == tf),
        )
        for tf in sorted({c["tf"] for c in charts})
    }
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", type=Path, default=ROOT / "frontend/research/fixtures")
    ap.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_baseline.json")
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument(
        "--context", type=Path, help="Snapshot structure exported with eqLevelsContext.ts"
    )
    args = ap.parse_args()
    if args.step < 1:
        ap.error("--step must be positive")
    charts, rows, cohorts, events, checkpoints = [], [], [], [], []
    files = sorted(args.fixtures.glob("*.json"))
    for p in files:
        if args.symbols and p.name.split("_")[0] not in args.symbols:
            continue
        d = json.loads(p.read_text())
        if d.get("timeframe") not in ["5m", "15m", "1h", "4h"] or len(d.get("candles", [])) < 100:
            continue
        r, rr, co, ee, pp = audit_chart(p, args.step)
        charts.append(r)
        rows.extend(rr)
        cohorts.extend(co)
        events.extend(ee)
        checkpoints.extend(pp)
        if len(charts) % 10 == 0:
            print(
                f"{len(charts)} charts; {len(rows)} final zones; {len(cohorts)} frozen cohorts",
                flush=True,
            )
    if not charts:
        raise SystemExit("No usable fixtures")
    context = json.loads(args.context.read_text()) if args.context else {}
    for e in events:
        # Timestamp formatting differs between Python isoformat and API UTC Z.
        states = context.get(e["chart"], {})
        e["structure_snapshot"] = states.get(e["timestamp"].replace("+00:00", "Z"), "unavailable")
    report = dict(
        schema=1,
        commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        replay_step=args.step,
        closed_bars_only=True,
        parameters={
            k: getattr(_equal_high_detector(), k)
            for k in ["_min_touches", "_tolerance_atr", "_tolerance_pct"]
        },
        charts=charts,
        summary=summarize(charts, rows, cohorts, events, checkpoints),
        retrospective_levels=rows,
        frozen_cohorts=cohorts,
        events=events,
        checkpoints=checkpoints,
        limitations=[
            "Final-window levels are retrospective; not evidence of historical availability.",
            "Frozen first-observed disjoint-pivot cohorts exclude subsequent reused-pivot "
            "clusters.",
            "Replay starts at 40 bars; later formation can be delayed by step-1 bars.",
            "Controls match candle/time/volatility, not a causal random-pool lifecycle.",
            "Overlap sources are final snapshot geometry, not historical causal confluence.",
            "OHLC cannot order intrabar wick and close; C is crossing from prior inside "
            "close, B remaining outside.",
            "No viewport pixel geometry; >4 ATR is a diagnostic proxy, not measured screen "
            "invisibility.",
            "Four within-chart blocks are not independent market regimes. No holdout or "
            "multiplicity correction.",
            "No conclusions about new thresholds or quality tiers are authorized by this "
            "descriptive audit.",
        ],
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, allow_nan=False, separators=(",", ":")) + "\n")
    print(
        f"Wrote {args.json}: {len(charts)} charts, {len(rows)} levels, {len(events)} events",
        flush=True,
    )


if __name__ == "__main__":
    main()
