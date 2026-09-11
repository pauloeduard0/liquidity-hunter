"""D6: is there any level family that holds after contact, beyond being near?

Research only; production, API and frontend are untouched. D1-D5 closed the
attempt to justify the current composite: the two `strength` measures are
incomparable, neither adds information once distance is controlled, and
"Dominant Liquidity" has no statistical definition. This round does not try to
rescue a score. It asks a prior question: does any *kind* of level -- equal
levels, swing pivots, or the volume profile -- earn the word "strong", where
strong means the price reacted after touching it, not that it was touched.

Pre-registration (fixed before any outcome of this round was read):

- Force is measured **after first contact only** (D6.0/D6.10). Reachability is
  reported beside it and never blended into it: the nearest level wins contact
  by geometry, which is not edge.
- Geometry is width-neutral for every family (D6.7). A level is a **point**
  (the midpoint of its band, or the profile price); contact is a future bar
  whose range contains that point; it is "through" when a close clears it by
  `CLEARANCE_ATR`. EQ pools are 0.3-0.5 ATR wide and swings are points, so any
  band-edge reading would compare widths instead of families.
- `CLEARANCE_ATR = 0.25` is the D4 clearance, reused verbatim, and it is also
  the confluence tolerance (D6.14). One pre-registered number, never swept.
- The volume profile is rebuilt **causally** at every observation, from the
  prefix only, with the production wiring (`_VOLUME_PROFILE_LOOKBACK` /
  `_VOLUME_PROFILE_BUCKETS`). The profile published for the chart today is
  recorded separately, as the hindsight arm (D6.2), and never scored.
- The placebo (D6.25 E) is a price drawn uniformly inside the observation's own
  same-side ATR bucket, seeded from (symbol, timeframe, end, bucket), measured
  by the identical contact and reaction code.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics as st
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.app.dashboard_data import (
    _VOLUME_PROFILE_BUCKETS,
    _VOLUME_PROFILE_LOOKBACK,
    _build_internal_detector,
)
from liquidity_hunter.core.domain import Candle, MarketDirection, TimeFrame, VolumeNode
from liquidity_hunter.indicators.volume_profile import volume_profile
from liquidity_hunter.liquidity.detectors import SwingHighDetector, SwingLowDetector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from research.dominant_liquidity_strength_audit import (
    ATR_BUCKETS,
    HORIZONS,
    MIN_FUTURE,
    START,
    STEP,
    TFS,
    atr14,
    family,
    midpoint,
    pct,
)
from research.eq_levels_e1 import FACTORIES

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "research/.replay_cache/dominant_liquidity_d6_baseline.json"

#: Width-neutral "the level gave way" threshold, in ATR past the level, and the
#: confluence tolerance (D6.7/D6.14). The D4 number, reused, never swept.
CLEARANCE_ATR = 0.25

#: Candles after first contact at which MFE/MAE are read (D6.8).
MFE_BARS = (1, 3, 5, 10, 20)

#: Bars after contact the hold/through verdict is read over (the longest MFE
#: window, so "held" and "MFE at 20" describe the same stretch of tape).
REACTION_WINDOW = 20

PROFILE_TYPES = ("POC", "VAH", "VAL", "HVN", "LVN")


# --------------------------------------------------------------------------
# Causal volume-profile levels
# --------------------------------------------------------------------------


def _runs(buckets, node: VolumeNode) -> list[tuple[float, float]]:
    """Contiguous bands of one node class, merged into shelves."""
    out: list[tuple[float, float]] = []
    start = None
    for i, bucket in enumerate(buckets):
        if bucket.node is node:
            if start is None:
                start = i
        elif start is not None:
            out.append((buckets[start].price_low, buckets[i - 1].price_high))
            start = None
    if start is not None:
        out.append((buckets[start].price_low, buckets[-1].price_high))
    return out


def profile_levels(profile) -> list[dict]:
    """Every level the existing `VolumeProfile` actually publishes.

    No name is invented: POC, the two value-area edges, and the shelves and
    gaps the domain model itself exposes as `high_volume_nodes` /
    `low_volume_nodes`, merged into contiguous runs so one shelf is one level.
    Adjacent HVN buckets are one shelf, not five candidates.
    """
    if profile is None:
        return []
    poc = profile.poc_price
    levels = [
        {"type": "POC", "level": poc, "lo": poc, "hi": poc},
        {
            "type": "VAH",
            "level": profile.value_area_high,
            "lo": profile.value_area_high,
            "hi": profile.value_area_high,
        },
        {
            "type": "VAL",
            "level": profile.value_area_low,
            "lo": profile.value_area_low,
            "hi": profile.value_area_low,
        },
    ]
    for node, name in ((VolumeNode.HIGH_VOLUME, "HVN"), (VolumeNode.LOW_VOLUME, "LVN")):
        for lo, hi in _runs(profile.buckets, node):
            if name == "HVN" and lo <= poc <= hi:
                continue  # the POC's own shelf is already reported as POC
            levels.append({"type": name, "level": (lo + hi) / 2, "lo": lo, "hi": hi})
    return levels


def causal_profile(prefix: Sequence[Candle], symbol: str, timeframe: TimeFrame):
    """The production profile wiring, over the prefix only."""
    return volume_profile(
        prefix[-_VOLUME_PROFILE_LOOKBACK:],
        symbol=symbol,
        timeframe=timeframe,
        bucket_count=_VOLUME_PROFILE_BUCKETS,
    )


# --------------------------------------------------------------------------
# Width-neutral contact and reaction
# --------------------------------------------------------------------------


def point_contact(level: float, future: Sequence[Candle]) -> int | None:
    """First 1-based future bar whose range contains the level."""
    return next((i for i, c in enumerate(future, 1) if c.low <= level <= c.high), None)


def separated_touches(level: float, future: Sequence[Candle]) -> list[int]:
    """Every *distinct* visit to the level, 1-based (D6.10).

    Consecutive bars straddling the level are one visit; a visit ends when a bar
    closes clear of it, so a retest is a genuine return rather than the same
    contact counted twice.
    """
    visits: list[int] = []
    touching = False
    for i, candle in enumerate(future, 1):
        hit = candle.low <= level <= candle.high
        if hit and not touching:
            visits.append(i)
        touching = hit
    return visits


def point_reaction(level, above, future, contact, atr):
    """What happened after contact, read identically for every family.

    `above` is the geometric role at the observation: a level above price is
    approached from below and tested as resistance, so a *favorable* reaction
    is price falling away from it and an *adverse* one is price closing above
    it by `CLEARANCE_ATR`.
    """
    if contact is None or atr <= 0 or len(future) < contact + 1:
        return None
    clearance = CLEARANCE_ATR * atr
    tail = future[contact:]
    mfe: dict[str, float | None] = {}
    mae: dict[str, float | None] = {}
    for bars in MFE_BARS:
        window = tail[:bars]
        if len(window) < bars:
            mfe[str(bars)] = mae[str(bars)] = None
            continue
        if above:
            mfe[str(bars)] = max(0.0, (level - min(c.low for c in window)) / atr)
            mae[str(bars)] = max(0.0, (max(c.high for c in window) - level) / atr)
        else:
            mfe[str(bars)] = max(0.0, (max(c.high for c in window) - level) / atr)
            mae[str(bars)] = max(0.0, (level - min(c.low for c in window)) / atr)
    window = tail[:REACTION_WINDOW]
    if len(window) < REACTION_WINDOW:
        return None
    if above:
        cleared = [c.close > level + clearance for c in window]
    else:
        cleared = [c.close < level - clearance for c in window]
    broke = next((i for i, hit in enumerate(cleared, 1) if hit), None)
    return {"through": broke is not None, "bars_to_break": broke, "mfe": mfe, "mae": mae}


# --------------------------------------------------------------------------
# Stage 1: causal replay
# --------------------------------------------------------------------------


def atr_bucket(d_atr: float) -> str:
    for edge in ATR_BUCKETS:
        if d_atr <= edge:
            return f"<={edge}"
    return f">{ATR_BUCKETS[-1]}"


def placebo_level(price, atr, above, bucket, seed) -> float:
    """A price drawn inside the same side and the same ATR bucket (D6.25 E)."""
    edges = (0.0, *ATR_BUCKETS, ATR_BUCKETS[-1] * 2)
    lo = hi = None
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        if atr_bucket((a + b) / 2) == bucket:
            lo, hi = a, b
            break
    if lo is None:
        lo, hi = ATR_BUCKETS[-1], ATR_BUCKETS[-1] * 2
    d = random.Random(seed).uniform(lo, hi) * atr
    return price + d if above else price - d


def structure_after(bars, end, horizon, timeframe):
    """Causal structure events in the future window, for the D6.21 outcome."""
    window = bars[: end + horizon]
    detector = _build_internal_detector(timeframe, confluence_filter=True)
    events = detector.detect(window)
    stamps = {c.timestamp: i for i, c in enumerate(window)}
    return [
        {
            "index": stamps.get(e.timestamp, -1) - end,
            "type": e.event.value,
            "direction": e.direction.value,
            "provisional": e.provisional,
        }
        for e in events
        if stamps.get(e.timestamp, -1) > end
    ]


def observe(bars, end, symbol, timeframe):  # noqa: C901 - one row, many fields
    prefix = bars[:end]
    price = prefix[-1].close
    atr = atr14(prefix)
    if atr <= 0:
        return None
    future = bars[end : end + max(HORIZONS)]
    zones = mark_swept_zones(
        [
            *SwingHighDetector().detect(prefix),
            *SwingLowDetector().detect(prefix),
            *FACTORIES["EQH"]().detect(prefix),
            *FACTORIES["EQL"]().detect(prefix),
        ],
        prefix,
    )
    detector = _build_internal_detector(timeframe, confluence_filter=True)
    detector.detect(prefix)
    trend = detector.final_trend

    raw: list[dict] = []
    for zone in zones:
        if zone.is_mitigated:
            continue
        raw.append(
            {
                "family": family(zone),
                "type": zone.zone_type.value,
                "level": midpoint(zone),
                "lo": zone.price_low,
                "hi": zone.price_high,
                "known_at": zone.formed_at.isoformat(),
            }
        )
    profile = causal_profile(prefix, symbol, timeframe)
    for level in profile_levels(profile):
        raw.append(
            {
                "family": "VP",
                **level,
                "known_at": prefix[-1].timestamp.isoformat(),
            }
        )

    candidates = []
    for item in raw:
        level = item["level"]
        if item["lo"] <= price <= item["hi"]:
            continue  # price is inside the band: it has no side to be tested from
        above = level > price
        d_atr = abs(level - price) / atr
        visits = separated_touches(level, future)
        contact = visits[0] if visits else None
        second = visits[1] if len(visits) > 1 else None
        candidates.append(
            {
                **item,
                "visits": len(visits),
                "reaction_2nd": point_reaction(level, above, future, second, atr),
                "above": above,
                "d_atr": d_atr,
                "bucket": atr_bucket(d_atr),
                "width_atr": (item["hi"] - item["lo"]) / atr,
                "contact": contact,
                "reaction": point_reaction(level, above, future, contact, atr),
            }
        )
    if not candidates:
        return None

    # Placebo: one per (side, bucket) actually occupied by a real candidate, so
    # the control population matches the treated one on geometry by design.
    for side_above in (True, False):
        for bucket in {c["bucket"] for c in candidates if c["above"] is side_above}:
            seed = f"{symbol}|{timeframe.value}|{end}|{side_above}|{bucket}"
            level = placebo_level(price, atr, side_above, bucket, seed)
            visits = separated_touches(level, future)
            contact = visits[0] if visits else None
            candidates.append(
                {
                    "family": "RAND",
                    "type": "RAND",
                    "level": level,
                    "lo": level,
                    "hi": level,
                    "known_at": prefix[-1].timestamp.isoformat(),
                    "above": side_above,
                    "d_atr": abs(level - price) / atr,
                    "bucket": bucket,
                    "width_atr": 0.0,
                    "visits": len(visits),
                    "reaction_2nd": point_reaction(
                        level, side_above, future, visits[1] if len(visits) > 1 else None, atr
                    ),
                    "contact": contact,
                    "reaction": point_reaction(level, side_above, future, contact, atr),
                }
            )

    return {
        "end": end,
        "block": (end - 1) * 4 // len(bars),
        "observed_at": prefix[-1].timestamp.isoformat(),
        "price": price,
        "atr": atr,
        "trend": trend.value if isinstance(trend, MarketDirection) else str(trend),
        "future_bars": len(future),
        "events": structure_after(bars, end, max(HORIZONS), timeframe),
        "candidates": candidates,
    }


def hindsight_probe(bars, symbol, timeframe, ends):
    """D6.2: the published profile vs the one available at the time.

    Truncation invariance is also read here: the causal profile for a prefix is
    recomputed from `bars[:end]` and from the full series truncated to the same
    `end`, which must agree bit for bit, and the *final* profile (what the chart
    shows today) is compared against it.
    """
    final = causal_profile(bars, symbol, timeframe)
    rows = []
    for end in ends:
        causal = causal_profile(bars[:end], symbol, timeframe)
        again = causal_profile(list(bars)[:end], symbol, timeframe)
        if causal is None or final is None:
            continue
        atr = atr14(bars[:end])
        rows.append(
            {
                "end": end,
                "truncation_identical": causal.model_dump_json() == again.model_dump_json(),
                "poc_moved_atr": abs(final.poc_price - causal.poc_price) / atr if atr else None,
                "vah_moved_atr": abs(final.value_area_high - causal.value_area_high) / atr
                if atr
                else None,
                "val_moved_atr": abs(final.value_area_low - causal.value_area_low) / atr
                if atr
                else None,
                "causal_hvn": len(_runs(causal.buckets, VolumeNode.HIGH_VOLUME)),
                "final_hvn": len(_runs(final.buckets, VolumeNode.HIGH_VOLUME)),
                "same_window": causal.start_timestamp == final.start_timestamp,
            }
        )
    return rows


def audit(path: Path, expected: dict) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"fixture changed: {path.name}")
    data = json.loads(raw)
    bars = [Candle.model_validate(c) for c in data["candles"][:-1]]
    timeframe = TimeFrame(data["timeframe"])
    ends = list(range(START, len(bars) - MIN_FUTURE + 1, STEP))
    rows = [r for r in (observe(bars, end, data["symbol"], timeframe) for end in ends) if r]
    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "sha256": expected["sha256"],
        "hindsight": hindsight_probe(bars, data["symbol"], timeframe, ends),
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
                "clearance_atr": CLEARANCE_ATR,
                "mfe_bars": list(MFE_BARS),
                "reaction_window": REACTION_WINDOW,
                "profile_lookback": _VOLUME_PROFILE_LOOKBACK,
                "profile_buckets": _VOLUME_PROFILE_BUCKETS,
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
# Stage 2: analysis. Reads the baseline; no fixture is touched again.
# --------------------------------------------------------------------------

CASE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
FAMILIES = ("VP", "EQ", "SW", "RAND")
TYPES = ("POC", "VAH", "VAL", "HVN", "LVN", "equal_highs", "equal_lows", "swing_high", "swing_low")
#: Minimum contacted candidates before a cell is read at all.
MIN_CELL = 30


def load():
    data = json.loads(BASELINE.read_text())
    observations = []
    for chart in data["charts"]:
        for row in chart["rows"]:
            obs = {**row, "symbol": chart["symbol"], "tf": chart["timeframe"]}
            for c in obs["candidates"]:
                c["obs"] = obs
            observations.append(obs)
    return data, observations


def cands(observations, **where):
    out = []
    for obs in observations:
        for c in obs["candidates"]:
            if all(c.get(k) == v for k, v in where.items()):
                out.append(c)
    return out


def share(hits, total):
    """`pct` of a hit count, with the empty cell printed rather than hidden."""
    return pct(hits / total) if total else "n/a"


def median(values):
    kept = [v for v in values if v is not None]
    return st.median(kept) if kept else float("nan")


def contacted(candidates, horizon):
    """Contact within `horizon`, only where the window was long enough."""
    hits = total = 0
    for c in candidates:
        if c["obs"]["future_bars"] < horizon:
            continue
        total += 1
        hits += c["contact"] is not None and c["contact"] <= horizon
    return hits, total


def reacted(candidates, field="reaction"):
    return [c for c in candidates if c[field]]


def hold_line(label, candidates, field="reaction"):
    rx = reacted(candidates, field)
    if len(rx) < MIN_CELL:
        return f"{label:<26} n={len(rx):5d} (below the {MIN_CELL} floor)"
    through = sum(r[field]["through"] for r in rx) / len(rx) * 100
    mfe = median([r[field]["mfe"]["10"] for r in rx])
    mae = median([r[field]["mae"]["10"] for r in rx])
    ratios = [
        r[field]["mfe"]["10"] / r[field]["mae"]["10"]
        for r in rx
        if r[field]["mae"]["10"] and r[field]["mae"]["10"] > 1e-9
    ]
    survived = [r[field]["bars_to_break"] for r in rx if r[field]["bars_to_break"]]
    return (
        f"{label:<26} n={len(rx):5d} through={through:5.1f}% rejected={100 - through:5.1f}% "
        f"MFE10={mfe:5.3f} MAE10={mae:5.3f} MFE/MAE={median(ratios):5.3f} "
        f"bars_to_break={median(survived):4.1f}"
    )


def stratum(candidate):
    obs = candidate["obs"]
    return (obs["symbol"], obs["tf"], candidate["above"], candidate["bucket"], obs["block"])


def matched_gap(candidates, group_a, group_b, outcome, key="family", min_side=2):
    """Difference in `outcome` between two groups, matched on geometry.

    Strata are symbol x timeframe x side x ATR bucket x temporal block, which is
    the discipline every earlier round used: a nearer level is contacted and
    held differently for reasons that are not the family it belongs to.
    """
    cells: dict[tuple, dict[str, list[float]]] = {}
    for c in candidates:
        value = outcome(c)
        if value is None:
            continue
        name = c[key]
        if name not in (group_a, group_b):
            continue
        cells.setdefault(stratum(c), {}).setdefault(name, []).append(value)
    gaps, weights, strata = [], [], 0
    for cell in cells.values():
        a, b = cell.get(group_a), cell.get(group_b)
        if not a or not b or len(a) < min_side or len(b) < min_side:
            continue
        strata += 1
        weight = min(len(a), len(b))
        gaps.append((st.fmean(a) - st.fmean(b)) * weight)
        weights.append(weight)
    if not weights:
        return None
    return {"gap": sum(gaps) / sum(weights), "strata": strata, "pairs": sum(weights)}


def through_rate(candidate, field="reaction"):
    r = candidate[field]
    return float(r["through"]) if r else None


def mfe_mae(candidate, bars="10"):
    r = candidate["reaction"]
    if not r or r["mae"][bars] is None or r["mfe"][bars] is None:
        return None
    return r["mfe"][bars] - r["mae"][bars]


def matched_line(label, candidates, a, b, key="family"):
    hold = matched_gap(candidates, a, b, through_rate, key=key)
    edge = matched_gap(candidates, a, b, mfe_mae, key=key)
    if not hold:
        return f"{label:<30} (no stratum holds both)"
    line = (
        f"{label:<30} through {a}-{b}={hold['gap'] * 100:+6.2f}pp "
        f"(strata={hold['strata']:4d} pairs={hold['pairs']:5d})"
    )
    if edge:
        line += f" MFE-MAE={edge['gap']:+6.3f} ATR"
    return line


def clusters(obs, tolerance=CLEARANCE_ATR):
    """Label each real candidate with the families it is in confluence with."""
    real = [c for c in obs["candidates"] if c["family"] != "RAND"]
    for c in real:
        near = {
            o["family"]
            for o in real
            if o is not c and abs(o["level"] - c["level"]) <= tolerance * obs["atr"]
        }
        c["with"] = near
        c["combo"] = "+".join(sorted({c["family"], *near}))
        others = [
            abs(o["level"] - c["level"]) / obs["atr"]
            for o in real
            if o is not c and o["family"] != c["family"]
        ]
        c["cluster_atr"] = min(others) if others else None
    return real


def report() -> None:  # noqa: C901, PLR0915 - a report is a sequence of tables
    data, observations = load()
    out = print
    every = [c for obs in observations for c in obs["candidates"]]
    out(
        f"# observations={len(observations)} candidates={len(every)} "
        f"clearance={data['clearance_atr']} ATR "
        f"profile={data['profile_lookback']}x{data['profile_buckets']}"
    )

    out("\n## D6.2 causality of the volume profile")
    rows = [h for chart in data["charts"] for h in chart["hindsight"]]
    same = sum(h["truncation_identical"] for h in rows)
    out(f"truncation invariant: {same}/{len(rows)} ({share(same, len(rows))})")
    aligned = sum(h["same_window"] for h in rows)
    out(f"causal window == published window: {share(aligned, len(rows))}")
    for name in ("poc", "vah", "val"):
        moved = [h[f"{name}_moved_atr"] for h in rows if h[f"{name}_moved_atr"] is not None]
        out(
            f"published {name.upper()} vs causal {name.upper()}: "
            f"p50={median(moved):6.2f} ATR  >1 ATR={share(sum(v > 1 for v in moved), len(moved))}"
        )

    out("\n## D6.1/D6.3 census and D6.12 typical distance")
    for t in TYPES:
        sel = [c for c in every if c["type"] == t]
        per = len(sel) / len(observations)
        distances = sorted(c["d_atr"] for c in sel)
        p25 = distances[len(distances) // 4] if distances else float("nan")
        out(
            f"{t:<14} n={len(sel):6d} ({per:5.2f}/observation) "
            f"d_atr p25={p25:5.2f} "
            f"p50={median([c['d_atr'] for c in sel]):5.2f} "
            f"<=1 ATR={share(sum(c['d_atr'] <= 1 for c in sel), len(sel))} "
            f"<=2 ATR={share(sum(c['d_atr'] <= 2 for c in sel), len(sel))} "
            f"width={median([c['width_atr'] for c in sel]):5.3f} ATR"
        )

    out("\n## D6.6 reachability (contact by horizon) -- not an edge, a geometry")
    for fam in FAMILIES:
        sel = [c for c in every if c["family"] == fam]
        line = f"{fam:<6} n={len(sel):6d} "
        for h in HORIZONS:
            hits, total = contacted(sel, h)
            line += f"h{h}={share(hits, total)} "
        out(line + f" d_atr p50={median([c['d_atr'] for c in sel]):5.2f}")
    out("nearest-level control: the same, restricted to candidates inside 1 ATR")
    for fam in FAMILIES:
        sel = [c for c in every if c["family"] == fam and c["d_atr"] <= 1]
        line = f"{fam:<6} n={len(sel):6d} "
        for h in HORIZONS:
            hits, total = contacted(sel, h)
            line += f"h{h}={share(hits, total)} "
        out(line)

    out("\n## D6.7/D6.8/D6.9 hold after FIRST contact, raw (distance NOT controlled)")
    for fam in FAMILIES:
        out(hold_line(fam, [c for c in every if c["family"] == fam]))
    out("")
    for t in TYPES:
        out(hold_line(t, [c for c in every if c["type"] == t]))

    out("\n## D6.5 hold with distance matched (symbol x TF x side x ATR bucket x block)")
    pairs = (("VP", "RAND"), ("EQ", "RAND"), ("SW", "RAND"),
             ("VP", "EQ"), ("VP", "SW"), ("EQ", "SW"))
    for a, b in pairs:
        out(matched_line(f"{a} vs {b}", every, a, b))

    out("\n## D6.11 profile types against each other, distance matched")
    vs_rand = (("POC", "RAND"), ("VAH", "RAND"), ("VAL", "RAND"),
               ("HVN", "RAND"), ("LVN", "RAND"))
    for a, b in vs_rand:
        out(matched_line(f"{a} vs {b}", every, a, b, key="type"))
    out(matched_line("HVN vs LVN", every, "HVN", "LVN", key="type"))
    out("\nD6.11b does any per-type gap replicate? (vs RAND, through, matched)")
    for t in PROFILE_TYPES:
        cells = []
        for tf in TFS:
            sel = [c for c in every if c["obs"]["tf"] == tf]
            g = matched_gap(sel, t, "RAND", through_rate, key="type")
            ok = g and g["pairs"] >= 20
            cells.append(f"{tf}={g['gap'] * 100:+6.2f}pp" if ok else f"{tf}=n/a")
        for block in range(4):
            sel = [c for c in every if c["obs"]["block"] == block]
            g = matched_gap(sel, t, "RAND", through_rate, key="type")
            ok = g and g["pairs"] >= 20
            cells.append(f"b{block}={g['gap'] * 100:+6.2f}pp" if ok else f"b{block}=n/a")
        out(f"{t:<6} " + " ".join(cells))

    out("\n## D6.13 the operational bucket: candidates inside 1 / 2 / 3 ATR")
    for limit in (1, 2, 3):
        out(f"-- d_atr <= {limit}")
        near = [c for c in every if c["d_atr"] <= limit]
        for fam in FAMILIES:
            out(hold_line(f"  {fam}", [c for c in near if c["family"] == fam]))

    out("\n## D6.14-D6.18 confluence (tolerance = clearance = 0.25 ATR, pre-registered)")
    for obs in observations:
        clusters(obs)
    real = [c for obs in observations for c in obs["candidates"] if c["family"] != "RAND"]
    combos = {}
    for c in real:
        combos.setdefault(c["combo"], []).append(c)
    for combo, sel in sorted(combos.items(), key=lambda kv: -len(kv[1])):
        out(hold_line(combo, sel) + f"  d_atr p50={median([c['d_atr'] for c in sel]):5.2f}")
    out("\nconfluence against each isolated component, distance matched (D6.30)")
    for combo, base in (("EQ+VP", "VP"), ("EQ+VP", "EQ"), ("SW+VP", "VP"), ("SW+VP", "SW"),
                        ("EQ+SW", "EQ"), ("EQ+SW", "SW"), ("EQ+SW+VP", "VP")):
        sel = [c for c in real if c["combo"] in (combo, base)]
        out(matched_line(f"{combo} vs {base}", sel, combo, base, key="combo"))
    out("\nD6.18 cluster distance vs hold (continuous, no threshold chosen after)")
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 99.0)):
        sel = [c for c in real if c["cluster_atr"] is not None and lo <= c["cluster_atr"] < hi]
        out(hold_line(f"  nearest other family {lo}-{hi}", sel))

    out("\n## D6.22 by timeframe")
    for tf in TFS:
        out(f"-- {tf}")
        sel = [c for c in every if c["obs"]["tf"] == tf]
        for fam in FAMILIES:
            out(hold_line(f"  {fam}", [c for c in sel if c["family"] == fam]))
        for a, b in (("VP", "RAND"), ("EQ", "RAND"), ("SW", "RAND"), ("EQ", "SW")):
            out("  " + matched_line(f"{a} vs {b}", sel, a, b))

    out("\n## D6.23 by temporal block")
    for block in range(4):
        sel = [c for c in every if c["obs"]["block"] == block]
        out(f"-- block {block}")
        for fam in FAMILIES:
            out(hold_line(f"  {fam}", [c for c in sel if c["family"] == fam]))

    out("\n## D6.19/D6.20 side and causal trend")
    for above in (True, False):
        sel = [c for c in every if c["above"] is above]
        out(f"-- {'above' if above else 'below'} price")
        for fam in FAMILIES:
            out(hold_line(f"  {fam}", [c for c in sel if c["family"] == fam]))
    for trend in ("bullish", "bearish", "neutral"):
        sel = [c for c in every if c["obs"]["trend"] == trend]
        if not sel:
            continue
        out(f"-- trend {trend}")
        for fam in FAMILIES:
            out(hold_line(f"  {fam}", [c for c in sel if c["family"] == fam]))
        for aligned, label in ((True, "level against trend"), (False, "level with trend")):
            want_above = (trend == "bullish") == aligned
            out(hold_line(f"  {label}", [c for c in sel if c["above"] is want_above]))

    out("\n## D6.24 robustness across symbols (VP vs RAND, through rate, matched)")
    symbols = sorted({obs["symbol"] for obs in observations})
    for a, b in (("VP", "RAND"), ("EQ", "RAND"), ("SW", "RAND")):
        gaps = []
        for symbol in symbols:
            sel = [c for c in every if c["obs"]["symbol"] == symbol]
            g = matched_gap(sel, a, b, through_rate)
            if g and g["pairs"] >= 20:
                gaps.append(g["gap"] * 100)
        if not gaps:
            out(f"{a} vs {b}: no symbol reaches the floor")
            continue
        out(
            f"{a} vs {b}: symbols={len(gaps):3d} better(through lower)="
            f"{share(sum(g < 0 for g in gaps), len(gaps))} "
            f"worse={share(sum(g > 0 for g in gaps), len(gaps))} "
            f"median={st.median(gaps):+6.2f}pp"
        )

    out("\n## D6.10 first touch vs retest")
    for fam in FAMILIES:
        sel = [c for c in every if c["family"] == fam]
        out(hold_line(f"{fam} 1st", sel))
        out(hold_line(f"{fam} 2nd", sel, field="reaction_2nd"))

    out("\n## D6.21 structure after the touch (secondary outcome)")
    for fam in ("VP", "EQ", "SW"):
        sel = reacted([c for c in every if c["family"] == fam])
        with_event = 0
        for c in sel:
            after = [e for e in c["obs"]["events"] if e["index"] > c["contact"]]
            with_event += bool(after)
        follows = share(with_event, len(sel))
        out(f"{fam:<6} contacted={len(sel):5d} a structure event follows={follows}")

    out("\n## D6.31 worked cases (one real observation per requested category)")
    pool = [
        c
        for obs in observations
        if obs["symbol"] in CASE_SYMBOLS
        for c in obs["candidates"]
        if c["family"] != "RAND" and c["reaction"] and c["d_atr"] <= 3
    ]
    wanted = (
        ("A profile near and held", lambda c: c["family"] == "VP" and not c["reaction"]["through"]),
        ("B profile touched, through", lambda c: c["family"] == "VP" and c["reaction"]["through"]),
        ("C equal level held", lambda c: c["family"] == "EQ" and not c["reaction"]["through"]),
        ("D swing held", lambda c: c["family"] == "SW" and not c["reaction"]["through"]),
        ("E profile + EQ", lambda c: c["combo"] == "EQ+VP"),
        ("F profile + swing", lambda c: c["combo"] == "SW+VP"),
        ("G triple confluence", lambda c: c["combo"] == "EQ+SW+VP"),
        ("H nearest candidate fails", lambda c: c["d_atr"] <= 0.5 and c["reaction"]["through"]),
    )
    for label, predicate in wanted:
        hit = next((c for c in pool if predicate(c)), None)
        if hit is None:
            out(f"{label:<26} no instance in the case symbols")
            continue
        obs, r = hit["obs"], hit["reaction"]
        verdict = (
            f"THROUGH in {r['bars_to_break']} bars" if r["through"] else "held 20 bars"
        )
        out(
            f"{label:<26} {obs['symbol']} {obs['tf']} {obs['observed_at'][:16]} "
            f"{hit['type']:<12} {hit['d_atr']:4.2f} ATR "
            f"{'above' if hit['above'] else 'below'} combo={hit['combo']:<9} "
            f"contact=+{hit['contact']:<3d} "
            f"{verdict} "
            f"MFE10={r['mfe']['10']:.2f} MAE10={r['mae']['10']:.2f} visits={hit['visits']}"
        )
