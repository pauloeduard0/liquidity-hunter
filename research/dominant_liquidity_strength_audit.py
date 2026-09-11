"""D1: does `strength` add information beyond proximity, and is it comparable
between the EQ and swing families?

Research only. Nothing here is imported by production; every detector,
tolerance and score is a production call, replayed on causal prefixes.

Stage 1 (`main`) replays the 211 EQ fixtures and writes one row per *candidate*
(not only per winner, as D0 did) to `research/.replay_cache/` — the baseline.
Stage 2 (`report`) reads that baseline and prints the tables of
`DOMINANT_LIQUIDITY_D1_STRENGTH.md`. No weight search, no threshold fitting,
no production edit.
"""

from __future__ import annotations

import hashlib
import json
import statistics as st
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZoneType
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.scoring.engine import LiquidityScoringEngine
from research.eq_levels_e1 import FACTORIES

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research/.replay_cache/dominant_liquidity_d1_baseline.json"

START = 200
STEP = 100
MIN_FUTURE = 40
HORIZONS = (5, 10, 20, 40, 80)
REACTION_BARS = 10
#: Distance buckets in ATR14. Fixed before any outcome was read; they are the
#: D0 landmarks (1 ATR, 3 ATR) plus a split of the near field.
ATR_BUCKETS = (0.5, 1.0, 2.0, 3.0, 5.0)

_EQ_TYPES = {LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS}


def family(zone) -> str:
    return "EQ" if zone.zone_type in _EQ_TYPES else "SW"


def midpoint(zone) -> float:
    return (zone.price_low + zone.price_high) / 2


def true_ranges(candles):
    return [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles[:-1], candles[1:], strict=False)
    ]


def atr14(candles) -> float:
    values = true_ranges(candles)
    return sum(values[-14:]) / len(values[-14:])


def mean_tr_pct(candles) -> float:
    values = true_ranges(candles)
    return st.mean(tr / c.close for tr, c in zip(values, candles[1:], strict=False) if c.close > 0)


def bucket_of(distance_atr: float) -> int:
    """Index into ATR_BUCKETS + 1 for the overflow bucket."""
    for i, edge in enumerate(ATR_BUCKETS):
        if distance_atr < edge:
            return i
    return len(ATR_BUCKETS)


def first_contact(zone, future) -> int | None:
    """1-based index of the first future bar intersecting the *original* band.

    Contact is geometric intersection: not a sweep, not a rejection, not a
    trade, and it does not require the zone to still be published.
    """
    return next(
        (
            i
            for i, c in enumerate(future, 1)
            if c.high >= zone.price_low and c.low <= zone.price_high
        ),
        None,
    )


def reaction(zone, future, contact, atr):
    """Descriptive behaviour in the `REACTION_BARS` bars after first contact.

    `through` = a close beyond the far side of the band (the level gave way).
    `rejection_atr` = the excursion back to the origin side, in ATR.
    Both are None when the fixture does not carry the full window.
    """
    if contact is None or atr <= 0 or len(future) < contact + REACTION_BARS:
        return None, None
    window = future[contact : contact + REACTION_BARS]
    if zone.side == LiquiditySide.BUY_SIDE:
        through = any(c.close > zone.price_high for c in window)
        rejection = (zone.price_low - min(c.low for c in window)) / atr
    else:
        through = any(c.close < zone.price_low for c in window)
        rejection = (max(c.high for c in window) - zone.price_high) / atr
    return through, max(0.0, rejection)


def _rank_key(cand, strength_families: frozenset[str], distance_only: bool):
    """Counterfactual ranking key. Research only — never a production proposal."""
    if distance_only:
        return -cand["d_pct"]
    strength = cand["touch_score"] if cand["family"] in strength_families else 0.0
    return (
        cand["distance_score"] * 0.4 + strength * 0.4 + cand["timeframe_score"] * 0.2
    )


def _argmax(cands, key):
    """Stable argmax: ties keep detector entry order, exactly like `sorted`."""
    best = 0
    for i in range(1, len(cands)):
        if key(cands[i]) > key(cands[best]):
            best = i
    return best


def observe(zones, price, atr, future):
    ranked = LiquidityScoringEngine().score([z for z in zones if not z.is_mitigated], price)
    if not ranked:
        return None

    cands = []
    for scored in ranked:
        z = scored.zone
        delta = abs(midpoint(z) - price)
        contact = first_contact(z, future)
        through, rejection = reaction(z, future, contact, atr)
        cands.append(
            {
                "family": family(z),
                "type": z.zone_type.value,
                "side": z.side.value,
                "above": midpoint(z) > price,
                "d_pct": delta / price * 100,
                "d_atr": delta / atr if atr else None,
                "width_atr": (z.price_high - z.price_low) / atr if atr else None,
                "strength": z.strength,
                "touch_score": scored.touch_score,
                "distance_score": scored.distance_score,
                "timeframe_score": scored.timeframe_score,
                "score": scored.score,
                "contact": contact,
                "through": through,
                "rejection_atr": rejection,
                "future_bars": len(future),
            }
        )

    winner = cands[0]
    nearest = min(range(len(cands)), key=lambda i: cands[i]["d_pct"])
    counterfactuals = {
        "current": 0,
        "no_strength": _argmax(cands, lambda c: _rank_key(c, frozenset(), False)),
        "eq_only": _argmax(cands, lambda c: _rank_key(c, frozenset({"EQ"}), False)),
        "sw_only": _argmax(cands, lambda c: _rank_key(c, frozenset({"SW"}), False)),
        "distance_only": nearest,
    }

    top = [c for c in cands if abs(c["score"] - winner["score"]) < 1e-10]
    tie_break = min(
        (i for i, c in enumerate(cands) if abs(c["score"] - winner["score"]) < 1e-10),
        key=lambda i: cands[i]["d_pct"],
    )

    # Why did the winner win: better distance, better strength, both, or a tie.
    runner = cands[1] if len(cands) > 1 else None
    if len(top) > 1:
        reason = "D_tie"
    elif runner is None:
        reason = "E_sole"
    else:
        better_d = winner["distance_score"] > runner["distance_score"]
        better_s = winner["touch_score"] > runner["touch_score"]
        reason = (
            "C_both" if better_d and better_s else
            "A_distance" if better_d else
            "B_strength" if better_s else
            "E_other"
        )

    return {
        "n": len(cands),
        "candidates": cands,
        "winner_is_nearest": nearest == 0,
        "reason": reason,
        "ties": len(top),
        "tie_break_nearest": tie_break,
        "all_distance_scores_zero": all(c["distance_score"] == 0 for c in cands),
        "counterfactuals": counterfactuals,
    }


def audit(path: Path, expected: dict) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    rows = []
    for end in range(START, len(bars) - MIN_FUTURE + 1, STEP):
        prefix = bars[:end]
        zones = mark_swept_zones(
            [
                *SwingHighDetector().detect(prefix),
                *SwingLowDetector().detect(prefix),
                *FACTORIES["EQH"]().detect(prefix),
                *FACTORIES["EQL"]().detect(prefix),
            ],
            prefix,
        )
        price = prefix[-1].close
        atr = atr14(prefix)
        result = observe(zones, price, atr, bars[end : end + max(HORIZONS)])
        row = {
            "end": end,
            "block": (end - 1) * 4 // len(bars),
            "observed_at": prefix[-1].timestamp.isoformat(),
            "price": price,
            "atr": atr,
            "atr_pct": atr / price * 100,
            "mean_tr_pct": mean_tr_pct(prefix) * 100,
        }
        row.update(result or {"n": 0, "candidates": []})
        rows.append(row)
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "sha256": expected["sha256"],
        "rows": rows,
    }


def _job(spec):
    return audit(ROOT / "frontend/research/fixtures" / spec["file"], spec)


def main() -> None:
    manifest_path = ROOT / "research/eq_levels_baseline.json"
    manifest = json.loads(manifest_path.read_text())
    charts = []
    with ProcessPoolExecutor() as pool:
        for i, chart in enumerate(pool.map(_job, manifest["charts"]), 1):
            charts.append(chart)
            print(f"{i}/{len(manifest['charts'])} {chart['symbol']}", flush=True)
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(
        json.dumps(
            {
                "schema": 1,
                "start": START,
                "step": STEP,
                "min_future": MIN_FUTURE,
                "horizons": list(HORIZONS),
                "reaction_bars": REACTION_BARS,
                "atr_buckets": list(ATR_BUCKETS),
                "charts": charts,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            allow_nan=False,
        )
    )
    print(f"wrote {BASELINE} ({BASELINE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Stage 2: analysis. Reads the baseline; computes nothing new from fixtures.
# --------------------------------------------------------------------------

TFS = ("15m", "1h", "4h")
BUCKET_NAMES = ("0-0.5", "0.5-1", "1-2", "2-3", "3-5", ">5")


def load():
    """Flatten the baseline into (observations, candidates) with context attached."""
    data = json.loads(BASELINE.read_text())
    observations, candidates = [], []
    for chart in data["charts"]:
        for row in chart["rows"]:
            if not row["candidates"]:
                continue
            obs = {k: v for k, v in row.items() if k != "candidates"}
            obs["symbol"] = chart["symbol"]
            obs["tf"] = chart["timeframe"]
            obs["index"] = len(observations)
            obs["cand_ids"] = []
            for candidate in row["candidates"]:
                candidate["symbol"] = chart["symbol"]
                candidate["tf"] = chart["timeframe"]
                candidate["block"] = row["block"]
                candidate["obs"] = obs["index"]
                candidate["bucket"] = bucket_of(candidate["d_atr"])
                obs["cand_ids"].append(len(candidates))
                candidates.append(candidate)
            observations.append(obs)
    return data, observations, candidates


def quantiles(values):
    ordered = sorted(values)

    def q(p):
        if not ordered:
            return float("nan")
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

    return {
        "n": len(ordered),
        "min": ordered[0],
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "p95": q(0.95),
        "p99": q(0.99),
        "max": ordered[-1],
    }


def contacted(candidate, horizon):
    """True/False/None — None when the fixture lacks the full horizon."""
    if candidate["future_bars"] < horizon:
        return None
    return candidate["contact"] is not None and candidate["contact"] <= horizon


def rate(values):
    kept = [v for v in values if v is not None]
    return (sum(kept) / len(kept), len(kept)) if kept else (float("nan"), 0)


def spearman(pairs):
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    if len(pairs) < 3:
        return float("nan")
    xs, ys = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den = (
        sum((x - mx) ** 2 for x in xs) ** 0.5 * sum((y - my) ** 2 for y in ys) ** 0.5
    )
    return num / den if den else float("nan")


def stratified_split(candidates, outcome, keys, min_stratum=6):
    """Within-stratum median split on strength; report the high-minus-low gap.

    A stratum is a cell of `keys` (e.g. symbol x tf x side x distance bucket x
    block), so the comparison never puts a near level against a far one, a
    trending symbol against a quiet one, or one period against another. Strata
    with fewer than `min_stratum` usable candidates, or with no strength
    dispersion, are dropped rather than pooled.
    """
    strata = {}
    for candidate in candidates:
        value = outcome(candidate)
        if value is None:
            continue
        strata.setdefault(tuple(k(candidate) for k in keys), []).append((candidate, value))

    high_hits = high_n = low_hits = low_n = 0
    wins = losses = draws = 0
    for members in strata.values():
        if len(members) < min_stratum:
            continue
        median = st.median(c["strength"] for c, _ in members)
        high = [v for c, v in members if c["strength"] > median]
        low = [v for c, v in members if c["strength"] <= median]
        if not high or not low:
            continue
        high_hits += sum(high)
        high_n += len(high)
        low_hits += sum(low)
        low_n += len(low)
        gap = st.mean(high) - st.mean(low)
        wins += gap > 1e-12
        losses += gap < -1e-12
        draws += abs(gap) <= 1e-12
    if not high_n or not low_n:
        return None
    return {
        "high": high_hits / high_n,
        "low": low_hits / low_n,
        "gap": high_hits / high_n - low_hits / low_n,
        "n_high": high_n,
        "n_low": low_n,
        "strata": wins + losses + draws,
        "wins": wins,
        "losses": losses,
    }


def pct(value):
    return "n/a" if value != value else f"{value * 100:.2f}%"


def report() -> None:  # noqa: C901 - a report is a sequence of tables
    data, observations, candidates = load()
    out = print

    out(f"# observations={len(observations)} candidates={len(candidates)}")
    out(f"# horizons={data['horizons']} reaction_bars={data['reaction_bars']}")

    out("\n## 2/3. raw strength (= touch_score/100) by family and TF")
    for fam in ("EQ", "SW"):
        for tf in TFS:
            values = [c["strength"] for c in candidates if c["family"] == fam and c["tf"] == tf]
            q = quantiles(values)
            out(
                f"{fam} {tf:>3} n={q['n']:6d} "
                + " ".join(f"{k}={q[k]:.4f}" for k in ("min", "p10", "p50", "p90", "p99", "max"))
            )

    out("\n## 4. saturation: share of candidates per touch_score band")
    bands = ((0, 10), (10, 20), (20, 30), (30, 39.999), (39.999, 100))
    for fam in ("EQ", "SW"):
        for tf in TFS:
            sel = [c for c in candidates if c["family"] == fam and c["tf"] == tf]
            def share(lo, hi, sel=sel):
                return sum(lo <= c["touch_score"] < hi for c in sel) / len(sel) * 100

            row = " ".join(f"{lo:g}-{hi:g}:{share(lo, hi):5.1f}%" for lo, hi in bands)
            at_cap = sum(c["strength"] >= 1.0 - 1e-9 for c in sel) / len(sel) * 100
            out(f"{fam} {tf:>3} n={len(sel):6d} {row} cap@1.0={at_cap:5.1f}%")

    out("\n## 5. distance buckets: strength split (contact@20), all families")
    for i, name in enumerate(BUCKET_NAMES):
        sel = [c for c in candidates if c["bucket"] == i]
        coarse = [lambda c: (c["symbol"], c["tf"], c["side"], c["block"])]
        res = stratified_split(sel, lambda c: contacted(c, 20), coarse)
        contact = rate([contacted(c, 20) for c in sel])
        out(
            f"{name:>6} ATR n={len(sel):6d} contact@20={pct(contact[0])} "
            + (
                f"high={pct(res['high'])} low={pct(res['low'])} gap={res['gap'] * 100:+.2f}pp "
                f"strata={res['strata']} w/l={res['wins']}/{res['losses']}"
                if res
                else "no usable strata"
            )
        )

    out("\n## 6/7. same side + same bucket, per family (contact@20)")
    keys = [lambda c: (c["symbol"], c["tf"], c["side"], c["bucket"], c["block"])]
    for fam in ("EQ", "SW", None):
        for side in ("above", "below", None):
            sel = [
                c
                for c in candidates
                if (fam is None or c["family"] == fam)
                and (side is None or c["above"] == (side == "above"))
            ]
            res = stratified_split(sel, lambda c: contacted(c, 20), keys)
            label = f"{fam or 'ALL':>3} {side or 'both':>5}"
            out(
                f"{label} n={len(sel):6d} "
                + (
                    f"high={pct(res['high'])} low={pct(res['low'])} gap={res['gap'] * 100:+.2f}pp "
                    f"strata={res['strata']} w/l={res['wins']}/{res['losses']}"
                    if res
                    else "no usable strata"
                )
            )

    out("\n## 8/9/17. within-family strength split by TF and horizon (fully stratified)")
    for fam in ("EQ", "SW"):
        for tf in TFS:
            sel = [c for c in candidates if c["family"] == fam and c["tf"] == tf]
            parts = []
            for horizon in HORIZONS:
                res = stratified_split(sel, lambda c, h=horizon: contacted(c, h), keys)
                parts.append(f"h{horizon}={res['gap'] * 100:+.2f}pp" if res else f"h{horizon}=n/a")
            out(f"{fam} {tf:>3} n={len(sel):6d} " + " ".join(parts))

    out("\n## 20. temporal blocks (contact@20, stratified within block)")
    for fam in ("EQ", "SW"):
        parts = []
        for block in range(4):
            sel = [c for c in candidates if c["family"] == fam and c["block"] == block]
            res = stratified_split(sel, lambda c: contacted(c, 20), keys)
            parts.append(f"b{block}={res['gap'] * 100:+.2f}pp" if res else f"b{block}=n/a")
        out(f"{fam} " + " ".join(parts))

    out("\n## 18. reaction after contact (levels actually touched, h<=40)")
    for fam in ("EQ", "SW"):
        for i, name in enumerate(BUCKET_NAMES):
            sel = [
                c
                for c in candidates
                if c["family"] == fam and c["bucket"] == i and c["through"] is not None
            ]
            if len(sel) < 30:
                continue
            median = st.median(c["strength"] for c in sel)
            high = [c for c in sel if c["strength"] > median]
            low = [c for c in sel if c["strength"] <= median]
            out(
                f"{fam} {name:>6} ATR n={len(sel):5d} "
                f"through={sum(c['through'] for c in sel) / len(sel) * 100:5.1f}% "
                f"(hi {sum(c['through'] for c in high) / max(1, len(high)) * 100:5.1f}% / "
                f"lo {sum(c['through'] for c in low) / max(1, len(low)) * 100:5.1f}%) "
                f"rejection_atr med={st.median(c['rejection_atr'] for c in sel):.3f} "
                f"(hi {st.median([c['rejection_atr'] for c in high] or [0]):.3f} / "
                f"lo {st.median([c['rejection_atr'] for c in low] or [0]):.3f})"
            )

    out("\n## 10/11. cross-family, distance-matched (same obs, same side, same bucket)")
    matched = []
    for obs in observations:
        cells = {}
        for cid in obs["cand_ids"]:
            c = candidates[cid]
            cells.setdefault((c["above"], c["bucket"]), []).append(c)
        for members in cells.values():
            eq = [c for c in members if c["family"] == "EQ"]
            sw = [c for c in members if c["family"] == "SW"]
            if eq and sw:
                matched.append((obs, eq, sw))
    out(f"matched cells={len(matched)}")
    for tf in TFS:
        cells = [m for m in matched if m[0]["tf"] == tf]
        if not cells:
            continue
        eq_s = st.mean(st.mean(c["strength"] for c in e) for _, e, _ in cells)
        sw_s = st.mean(st.mean(c["strength"] for c in s) for _, _, s in cells)
        eq_c = rate([contacted(c, 20) for _, e, _ in cells for c in e])
        sw_c = rate([contacted(c, 20) for _, _, s in cells for c in s])
        eq_w = st.mean(c["width_atr"] for _, e, _ in cells for c in e)
        sw_w = st.mean(c["width_atr"] for _, _, w in cells for c in w)
        eq_t = [c["through"] for _, e, _ in cells for c in e if c["through"] is not None]
        sw_t = [c["through"] for _, _, s in cells for c in s if c["through"] is not None]
        out(
            f"{tf:>3} cells={len(cells):5d} strength EQ={eq_s:.4f} SW={sw_s:.4f} "
            f"-> score gap={(eq_s - sw_s) * 100 * 0.4:+.2f} composite pts | "
            f"contact@20 EQ={pct(eq_c[0])} SW={pct(sw_c[0])} | "
            f"through EQ={pct(st.mean(eq_t) if eq_t else float('nan'))} "
            f"SW={pct(st.mean(sw_t) if sw_t else float('nan'))} | "
            f"band width EQ={eq_w:.3f} SW={sw_w:.3f} ATR (confounds both outcomes)"
        )

    out("\n## 16. strength x distance correlation (spearman, per family/TF)")
    for fam in ("EQ", "SW"):
        for tf in TFS:
            sel = [c for c in candidates if c["family"] == fam and c["tf"] == tf]
            rho = spearman([(c["strength"], c["d_atr"]) for c in sel])
            out(f"{fam} {tf:>3} n={len(sel):6d} rho={rho:+.4f}")

    out("\n## 12. winner decomposition")
    reasons = {}
    for obs in observations:
        reasons[obs["reason"]] = reasons.get(obs["reason"], 0) + 1
    for reason, count in sorted(reasons.items()):
        out(f"{reason:>11} {count:5d} {count / len(observations) * 100:5.2f}%")

    far = [
        obs
        for obs in observations
        if candidates[obs["cand_ids"][0]]["d_atr"] > 3
        and any(candidates[i]["d_atr"] <= 1 for i in obs["cand_ids"])
    ]
    out(f"\nwinner>3ATR with a <=1ATR alternative: {len(far)}")
    sub = {}
    for obs in far:
        winner = candidates[obs["cand_ids"][0]]
        near = min((candidates[i] for i in obs["cand_ids"]), key=lambda c: c["d_atr"])
        key = (
            winner["family"],
            near["family"],
            "win_ds=0" if winner["distance_score"] == 0 else "win_ds>0",
            "near_ds=0" if near["distance_score"] == 0 else "near_ds>0",
        )
        sub[key] = sub.get(key, 0) + 1
    for key, count in sorted(sub.items(), key=lambda kv: -kv[1]):
        out(f"  winner={key[0]} nearest={key[1]} {key[2]} {key[3]}: {count}")
    def nearest_of(obs):
        return min((candidates[i] for i in obs["cand_ids"]), key=lambda c: c["d_atr"])

    gaps = [candidates[o["cand_ids"][0]]["strength"] - nearest_of(o)["strength"] for o in far]
    out(f"  strength gap (winner - nearest) median={st.median(gaps):.4f}")
    out(f"  all winners stronger than the nearest: {all(g > 0 for g in gaps)}")
    out("  tf split: " + " ".join(f"{tf}={sum(o['tf'] == tf for o in far)}" for tf in TFS))
    for horizon in (20, 40):
        w = rate([contacted(candidates[o["cand_ids"][0]], horizon) for o in far])
        n = rate([contacted(nearest_of(o), horizon) for o in far])
        out(f"  contact@{horizon}: winner={pct(w[0])} nearest={pct(n[0])} (geometry, not edge)")

    out("\n## 13/14/15. counterfactual rankings (research only)")
    for name in ("no_strength", "eq_only", "sw_only", "distance_only"):
        changed = [o for o in observations if o["counterfactuals"][name] != 0]
        picks = [candidates[o["cand_ids"][o["counterfactuals"][name]]] for o in observations]
        cur = [candidates[o["cand_ids"][0]] for o in observations]
        out(
            f"{name:>14} winner changed={len(changed) / len(observations) * 100:5.2f}% "
            f"median d_atr {st.median(c['d_atr'] for c in cur):.2f}->"
            f"{st.median(c['d_atr'] for c in picks):.2f} "
            f"EQ share {sum(c['family'] == 'EQ' for c in cur) / len(cur) * 100:5.1f}%->"
            f"{sum(c['family'] == 'EQ' for c in picks) / len(picks) * 100:5.1f}% "
            f"contact@20 {pct(rate([contacted(c, 20) for c in cur])[0])}->"
            f"{pct(rate([contacted(c, 20) for c in picks])[0])} "
            f"through {pct(rate([c['through'] for c in picks])[0])}"
        )

    out("\n## 22. per symbol: sign of the EQ/SW strength gap (contact@20, stratified)")
    for fam in ("EQ", "SW"):
        gaps = []
        for symbol in {c["symbol"] for c in candidates}:
            sel = [c for c in candidates if c["family"] == fam and c["symbol"] == symbol]
            res = stratified_split(sel, lambda c: contacted(c, 20), keys)
            if res and res["strata"] >= 3:
                gaps.append(res["gap"])
        if gaps:
            out(
                f"{fam} symbols={len(gaps)} positive={sum(g > 0 for g in gaps)} "
                f"negative={sum(g < 0 for g in gaps)} median={st.median(gaps) * 100:+.2f}pp "
                f"p10={sorted(gaps)[len(gaps) // 10] * 100:+.2f}pp "
                f"p90={sorted(gaps)[len(gaps) * 9 // 10] * 100:+.2f}pp"
            )

    out("\n## 23. ties at the top score")
    tied = [o for o in observations if o["ties"] > 1]
    out(f"tied observations={len(tied)} ({len(tied) / len(observations) * 100:.2f}%)")
    if tied:
        changed = [o for o in tied if o["tie_break_nearest"] != 0]
        families = " ".join(
            f"{fam}={sum(candidates[o['cand_ids'][0]]['family'] == fam for o in tied)}"
            for fam in ("EQ", "SW")
        )
        out(f"  winner family: {families}")
        out(f"  proximity tie-break moves the pick in {len(changed)}"
            f" ({len(changed) / len(tied) * 100:.1f}% of ties)")
        if changed:
            entry = st.median(candidates[o["cand_ids"][0]]["d_atr"] for o in changed)
            prox = st.median(
                candidates[o["cand_ids"][o["tie_break_nearest"]]]["d_atr"] for o in changed
            )
            out(f"  median d_atr entry-order={entry:.2f} vs proximity={prox:.2f}")
        zeroed = sum(o["all_distance_scores_zero"] for o in tied)
        out(f"  all_distance_scores_zero among ties: {zeroed}")
    out(
        f"observations where every distance_score==0: "
        f"{sum(o['all_distance_scores_zero'] for o in observations) / len(observations) * 100:.2f}%"
    )

    out("\n## 21/24. what 5% (the distance_score floor) is worth in ATR")
    for tf in TFS:
        values = [5.0 / o["atr_pct"] for o in observations if o["tf"] == tf]
        q = quantiles(values)
        cells = " ".join(f"{k}={q[k]:7.2f}" for k in ("p10", "p25", "p50", "p75", "p90"))
        out(f"{tf:>3} n={q['n']:5d} {cells} ATR")
    for tf in TFS:
        sel = [o for o in observations if o["tf"] == tf]
        winners = [candidates[o["cand_ids"][0]] for o in sel]
        far_share = sum(c["d_atr"] > 3 for c in winners) / len(sel) * 100
        zero_share = sum(o["all_distance_scores_zero"] for o in sel) / len(sel) * 100
        out(
            f"{tf:>3} winner d_atr median={st.median(c['d_atr'] for c in winners):.2f} "
            f">3ATR={far_share:.2f}% all_ds_zero={zero_share:.2f}%"
        )
