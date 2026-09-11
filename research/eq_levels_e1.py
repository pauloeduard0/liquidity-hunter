"""E1 research only: pivot-clock normalization and versioned consumption memory.

R = production snapshots, N = confirmed-pivot clock + frozen membership strength,
L = N eligibility with sticky consumed connected lineage. No production imports
this module. Thresholds and numerical primitives come from production factories.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics as st
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from liquidity_hunter.app.dashboard_data import _equal_high_detector, _equal_low_detector
from liquidity_hunter.core.domain import Candle
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from research.eq_levels_audit import HORIZONS, ROOT, age_bucket, distance_bucket, forward, stats

FACTORIES = {"EQH": _equal_high_detector, "EQL": _equal_low_detector}


def membership(group):
    return tuple(sorted(s.formed_at.isoformat() for s in group))


def token(side, group):
    return hashlib.sha256((side + "|" + "|".join(group)).encode()).hexdigest()[:20]


def zone_key(z):
    return (z.formed_at, z.price_low, z.price_high)


def semantic(z):
    return (zone_key(z), z.invalidated_at, z.breached_at, z.is_mitigated, z.sweep_rejected)


class Replay:
    """Sequential, closed-bar-only research engine. Retains its origin history.

    Cached R is verified against native detect() at checkpoints. Swing detection,
    grouping, area strength and sweep/breach predicates remain production calls.
    N/L are experimental publication policies, explicitly NOT drop-in detectors.
    """

    def __init__(self):
        for factory in FACTORIES.values():
            det = factory()
            assert (det._min_touches, det._swing_detector._lookback, det._tolerance_atr) == (
                3,
                5,
                0.5,
            ), "production parameters changed"
        self.candles = []
        self._base_index = 0
        self._compact_mode = False
        self._seen_count = 0
        self.index = {}
        self.tr_sum = 0.0
        self.volume_sum = 0.0
        self.swings = {s: [] for s in FACTORIES}
        self.raw = {s: {} for s in FACTORIES}
        self.normal = {s: {} for s in FACTORIES}
        self.strength_coeff = {s: {} for s in FACTORIES}
        self.frozen_strength = {s: {} for s in FACTORIES}
        self.records = {}
        self.parents = {}
        self.consumed = {}
        self.events = []
        self.seen_lifecycle = set()
        self.sweeps = []
        self.snapshots = []
        self.diagnostics = Counter()

    def root(self, pivot):
        self.parents.setdefault(pivot, pivot)
        while self.parents[pivot] != pivot:
            pivot = self.parents[pivot]
        return pivot

    def roots_for(self, side, key):
        return {self.root((side, p)) for p in key}

    def consumed_at(self, side, key):
        values = [self.consumed[r] for r in self.roots_for(side, key) if r in self.consumed]
        return min(values) if values else None

    def union(self, side, key):
        roots = self.roots_for(side, key)
        chosen = min(roots)
        old = [self.consumed[r] for r in roots if r in self.consumed]
        for r in roots:
            self.parents[r] = chosen
        if old:
            self.consumed[chosen] = min(old)

    def log(self, kind, side, key, **details):
        if kind in {"sweep", "breach"}:
            event_key = (kind, side, key, details["market_at"])
            if event_key in self.seen_lifecycle:
                return
            self.seen_lifecycle.add(event_key)
        self.events.append(
            dict(
                observed_at=self.candles[-1].timestamp.isoformat(),
                kind=kind,
                side=side,
                version=token(side, key),
                **details,
            )
        )

    def apply_lifecycle(self, side, states, arm):
        """Evaluate previously published bands BEFORE accepting today's new pivots."""
        updated = {}
        inherited_before = {k: self.consumed_at(side, k) for k in states} if arm == "N" else {}
        for key, z in states.items():
            inherited = inherited_before.get(key)
            nz = mark_swept_zones([z], [self.candles[-1]])[0]
            updated[key] = nz
            if not z.is_mitigated and nz.is_mitigated:
                i = self._base_index + len(self.candles) - 1
                self.sweeps.append(
                    dict(
                        arm=arm,
                        side=side,
                        index=i,
                        low=z.price_low,
                        high=z.price_high,
                        version=token(side, key),
                        formed=self.index[z.formed_at],
                        touches=len(key),
                        eligible_L=inherited is None if arm == "N" else None,
                        rejected=nz.sweep_rejected,
                    )
                )
            if arm == "N":
                if not z.is_mitigated and nz.is_mitigated:
                    self.log("sweep", side, key, market_at=nz.invalidated_at.isoformat())
                    root = next(iter(self.roots_for(side, key)))
                    self.consumed[root] = min(i, self.consumed.get(root, i))
                if z.breached_at is None and nz.breached_at is not None:
                    self.log("breach", side, key, market_at=nz.breached_at.isoformat())
        return updated

    def feed(self, candle, *, closed=True, verify=False):
        if not closed:
            return None
        if self.candles:
            prev = self.candles[-1]
            if candle.timestamp <= prev.timestamp:
                raise ValueError("closed bars must be strictly increasing")
            if (candle.symbol, candle.timeframe) != (prev.symbol, prev.timeframe):
                raise ValueError("mixed symbol/timeframe stream")
            tr = max(
                candle.high - candle.low,
                abs(candle.high - prev.close),
                abs(candle.low - prev.close),
            )
        else:
            tr = candle.high - candle.low
        self.candles.append(candle)
        self._seen_count += 1
        i = self._base_index + len(self.candles) - 1
        self.index[candle.timestamp] = i
        self.tr_sum += tr / candle.close
        self.volume_sum += candle.volume
        mean_volume = self.volume_sum / self._seen_count
        atr = self.tr_sum / self._seen_count * candle.close
        snapshot = dict(index=i, timestamp=candle.timestamp.isoformat(), atr=atr, sides={})
        for side, factory in FACTORIES.items():
            det = factory()
            lookback = det._swing_detector._lookback
            self.raw[side] = self.apply_lifecycle(side, self.raw[side], "R")
            self.normal[side] = self.apply_lifecycle(side, self.normal[side], "N")
            new_pivot = []
            if len(self.candles) >= 2 * lookback + 1:
                # Exactly one potential center; native strict-fractal implementation.
                new_pivot = det._swing_detector.detect(self.candles[-(2 * lookback + 1) :])
                self.swings[side].extend(new_pivot)
            tolerance = det._tolerance_atr * self.tr_sum / len(self.candles)
            if tolerance <= 0:
                tolerance = det._tolerance_pct
            groups = [
                g
                for g in det._group_by_tolerance(self.swings[side], tolerance)
                if len(g) >= det._min_touches
            ]
            keys = [membership(g) for g in groups]
            raw_changed = set(keys) != set(self.raw[side])
            native = None
            if raw_changed or new_pivot or verify:
                if self._compact_mode:
                    native_map = {key: self.raw[side][key] for key in keys if key in self.raw[side]}
                    if len(native_map) != len(keys):
                        raise RuntimeError(
                            "compact checkpoint lacks area history for a new membership"
                        )
                    native = [native_map[key] for key in keys]
                else:
                    native = mark_swept_zones(det.detect(self.candles), self.candles)
                    assert len(native) == len(groups), "cached tolerance changed native memberships"
                    native_map = dict(zip(keys, native, strict=True))
                for key, g, z in zip(keys, groups, native, strict=True):
                    assert z.price_low == min(s.price_high for s in g)
                    assert z.price_high == max(s.price_high for s in g)
                    assert z.formed_at == max(s.formed_at for s in g)
                    if key not in self.strength_coeff[side]:
                        # Native area helper with an intentionally nonsaturating
                        # denominator recovers area_volume / 118 without a port.
                        den = max(self.volume_sum, 1.0)
                        value = det._area_strength(
                            self.candles,
                            min(s.formed_at for s in g),
                            z.formed_at,
                            z.price_low,
                            z.price_high,
                            den,
                        )
                        self.strength_coeff[side][key] = value * den
                if raw_changed:
                    if not new_pivot:
                        self.diagnostics[side + "_R_changes_without_pivot"] += 1
                    self.raw[side] = native_map
            for key, z in self.raw[side].items():
                strength = (
                    min(1.0, self.strength_coeff[side][key] / mean_volume)
                    if mean_volume > 0
                    else 0.0
                )
                self.raw[side][key] = z.model_copy(update={"strength": strength})
            if verify:
                assert native is not None
                assert len(native) == len(self.raw[side])
                for key, z in native_map.items():
                    actual = self.raw[side][key]
                    assert semantic(actual) == semantic(z)
                    assert abs(actual.strength - z.strength) < 1e-10
                self.diagnostics["native_parity_checks"] += 1
            if new_pivot:
                # N changes only on this side's confirmed pivot clock.
                assert native is not None
                old_keys = set(self.normal[side])
                new_keys = set(keys)
                for key in sorted(old_keys - new_keys):
                    self.log("withdraw", side, key)
                normal = {}
                for key, z in zip(keys, native, strict=True):
                    vid = token(side, key)
                    if vid not in self.records:
                        ancestors = sorted(
                            rid
                            for rid, r in self.records.items()
                            if r["side"] == side and set(key) & set(r["pivots"])
                        )
                        inherited = self.consumed_at(side, key)
                        self.records[vid] = dict(
                            version=vid,
                            side=side,
                            pivots=list(key),
                            known_at=i,
                            known_time=candle.timestamp.isoformat(),
                            formed_at=z.formed_at.isoformat(),
                            low=z.price_low,
                            high=z.price_high,
                            strength=z.strength,
                            tolerance=tolerance,
                            mean_volume=mean_volume,
                            parents=ancestors,
                            inherited_consumption=inherited,
                            raw_swept_at_birth=z.is_mitigated,
                            raw_invalidated_at=z.invalidated_at.isoformat()
                            if z.invalidated_at
                            else None,
                            raw_breached_at=z.breached_at.isoformat() if z.breached_at else None,
                        )
                        self.frozen_strength[side][key] = z.strength
                        self.log("publish", side, key, record=copy.deepcopy(self.records[vid]))
                        self.union(side, key)
                    if key not in old_keys and vid in self.records:
                        self.log("show", side, key)
                    nz = z.model_copy(update={"strength": self.frozen_strength[side][key]})
                    normal[key] = nz
                    # A reappearing version may reveal consumption during its
                    # absence. Record knowledge NOW, never backdate publication.
                    if nz.invalidated_at is not None:
                        self.log(
                            "sweep",
                            side,
                            key,
                            market_at=nz.invalidated_at.isoformat(),
                            discovered=nz.invalidated_at != candle.timestamp,
                        )
                    if nz.breached_at is not None:
                        self.log(
                            "breach",
                            side,
                            key,
                            market_at=nz.breached_at.isoformat(),
                            discovered=nz.breached_at != candle.timestamp,
                        )
                    if nz.is_mitigated:
                        root = next(iter(self.roots_for(side, key)))
                        self.consumed[root] = min(i, self.consumed.get(root, i))
                self.normal[side] = normal
            raw_live = [z for z in self.raw[side].values() if not z.is_mitigated]
            normal_live = [(k, z) for k, z in self.normal[side].items() if not z.is_mitigated]
            ledger_live = [z for k, z in normal_live if self.consumed_at(side, k) is None]
            # How much the conservative shared-pivot inheritance rejects.
            snapshot["sides"][side] = dict(
                R=len(raw_live),
                N=len(normal_live),
                L=len(ledger_live),
                normal_memberships=[token(side, k) for k in self.normal[side]],
                eligible_L=[
                    token(side, k) for k, z in normal_live if self.consumed_at(side, k) is None
                ],
                difference_R_N=len(set(self.raw[side]) ^ set(self.normal[side])),
                inherited_blocked=len(normal_live) - len(ledger_live),
                max_lineage_pivots=max(
                    Counter(self.root(p) for p in self.parents if p[0] == side).values(), default=0
                ),
            )
        self.snapshots.append(snapshot)
        return snapshot

    def checkpoint(self):
        # Explicit research checkpoint: retain raw origin history and replay it.
        # This proves correctness, NOT bounded-memory deployment readiness.
        return {"schema": 1, "candles": [c.model_dump(mode="json") for c in self.candles]}

    @classmethod
    def restore(cls, data):
        if data["schema"] != 1:
            raise ValueError("unsupported checkpoint")
        out = cls()
        for c in data["candles"]:
            out.feed(Candle.model_validate(c))
        return out


def outcome_controls(c, events, atr):
    """Baseline sweep-event controls; same symbol/TF/side/time + age/distance/vol."""
    n = len(c)
    for e in events:
        i = e["index"]
        unit = max(atr[i], 1e-12)
        e["block"] = min(3, i * 4 // n)
        e["age"] = i - e["formed"]
        level = e["high"] if e["side"] == "EQH" else e["low"]
        e["distance"] = abs(level - c[max(0, i - 1)].close) / max(atr[max(0, i - 1)], 1e-12)
        e["volatility"] = unit / c[i].close
        e["outcomes"] = {str(h): forward(c, i, h, e["side"], unit) for h in HORIZONS}
    base = [e for e in events if e["arm"] == "R"]
    for e in events:
        controls = [
            r
            for r in base
            if r["side"] == e["side"]
            and r["block"] == e["block"]
            and 40 < abs(r["index"] - e["index"]) <= 120
            and age_bucket(r["age"]) == age_bucket(e["age"])
            and distance_bucket(r["distance"]) == distance_bucket(e["distance"])
            and 0.8 <= r["volatility"] / e["volatility"] <= 1.25
        ]
        controls = sorted(controls, key=lambda r: (abs(r["index"] - e["index"]), r["version"]))[:3]
        e["control_n"] = len(controls)
        e["control"] = {
            str(h): {
                m: st.mean(vals)
                if (
                    vals := [
                        r["outcomes"][str(h)][m]
                        for r in controls
                        if r["outcomes"][str(h)] is not None
                    ]
                )
                else None
                for m in ["mfe", "mae", "won", "move"]
            }
            for h in HORIZONS
        }
        e["control_ids"] = [(r["version"], r["index"]) for r in controls]


def process_file(arg):
    path, baseline_hash, verify_every = arg
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    assert sha == baseline_hash, f"E0 fixture changed: {path.name}"
    data = json.loads(raw)
    c = [Candle.model_validate(x) for x in data["candles"][:-1]]
    run = Replay()
    for i, bar in enumerate(c):
        run.feed(bar, verify=(i % verify_every == 0 or i == len(c) - 1))
    outcome_controls(c, run.sweeps, [s["atr"] for s in run.snapshots])
    # Independent reruns from prefixes must reproduce every published record/event.
    checks = []
    for end in [len(c) // 4, len(c) // 2, 3 * len(c) // 4]:
        truncated = Replay()
        for bar in c[:end]:
            truncated.feed(bar)
        prefix_events = [
            e for e in run.events if e["observed_at"] <= c[end - 1].timestamp.isoformat()
        ]
        assert truncated.events == prefix_events
        assert truncated.snapshots == run.snapshots[:end]
        assert truncated.sweeps == [
            {
                k: v
                for k, v in e.items()
                if k
                in [
                    "arm",
                    "side",
                    "index",
                    "low",
                    "high",
                    "version",
                    "formed",
                    "touches",
                    "eligible_L",
                    "rejected",
                ]
            }
            for e in run.sweeps
            if e["index"] < end
        ]
        checks.append(end)
    # Same end, different left boundary: intentional cold-start sensitivity.
    left = len(c) // 4
    cold = Replay()
    for bar in c[left:]:
        cold.feed(bar)

    def final_ids(engine):
        return {s: set(engine.snapshots[-1]["sides"][s]["eligible_L"]) for s in FACTORIES}

    a, b = final_ids(run), final_ids(cold)
    result = dict(
        file=path.name,
        symbol=data["symbol"],
        tf=data["timeframe"],
        sha256=sha,
        n=len(c),
        start=c[0].timestamp.isoformat(),
        end=c[-1].timestamp.isoformat(),
        diagnostics=dict(run.diagnostics),
        prefix_checks=checks,
        cold_left_difference={s: len(a[s] ^ b[s]) for s in FACTORIES},
        records=list(run.records.values()),
        journal=run.events,
        snapshots=run.snapshots,
        sweeps=run.sweeps,
    )
    return result


def quality(es):
    out = {}
    for h in HORIZONS:
        valid = [e for e in es if e["outcomes"][str(h)] is not None]
        matched = [e for e in valid if e["control"][str(h)]["won"] is not None]
        per = defaultdict(list)
        for e in matched:
            per[e["symbol"]].append(e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"])
        sy = [dict(symbol=s, n=len(v), delta=st.mean(v)) for s, v in per.items()]
        deltas = [e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"] for e in matched]
        out[str(h)] = dict(
            n=len(valid),
            matched=len(matched),
            **{
                m: stats(e["outcomes"][str(h)][m] for e in valid)
                for m in ["mfe", "mae", "ratio", "won", "move"]
            },
            delta=stats(deltas),
            per_symbol=sorted(sy, key=lambda r: r["delta"]),
            positive_symbols=sum(r["delta"] > 0 for r in sy) / len(sy) if sy else None,
            median_symbol_delta=st.median(r["delta"] for r in sy) if sy else None,
            blocks={
                str(q): stats(
                    e["outcomes"][str(h)]["won"] - e["control"][str(h)]["won"]
                    for e in matched
                    if e["block"] == q
                )
                for q in range(4)
            },
        )
    return out


def summarize(charts):
    out = {}
    for tf in sorted({r["tf"] for r in charts}):
        cc = [r for r in charts if r["tf"] == tf]
        for side in FACTORIES:
            rows = [s["sides"][side] for c in cc for s in c["snapshots"]]
            sweeps = [
                dict(e, symbol=c["symbol"]) for c in cc for e in c["sweeps"] if e["side"] == side
            ]
            rset = {
                (c["symbol"], e["index"], e["low"], e["high"])
                for c in cc
                for e in c["sweeps"]
                if e["side"] == side and e["arm"] == "R"
            }
            nset = {
                (c["symbol"], e["index"], e["low"], e["high"])
                for c in cc
                for e in c["sweeps"]
                if e["side"] == side and e["arm"] == "N"
            }
            out[f"{tf}/{side}"] = dict(
                charts=len(cc),
                density={arm: stats(r[arm] for r in rows) for arm in ["R", "N", "L"]},
                inherited_blocked_exposures=sum(r["inherited_blocked"] for r in rows),
                changed_R_N_snapshots=sum(r["difference_R_N"] > 0 for r in rows),
                max_lineage_pivots=max(r["max_lineage_pivots"] for r in rows),
                sweep_overlap=dict(
                    common=len(rset & nset), R_only=len(rset - nset), N_only=len(nset - rset)
                ),
                quality={
                    label: quality([e for e in sweeps if predicate(e)])
                    for label, predicate in {
                        "R": lambda e: e["arm"] == "R",
                        "N": lambda e: e["arm"] == "N",
                        "L": lambda e: e["arm"] == "N" and e["eligible_L"],
                        "L_removed": lambda e: e["arm"] == "N" and not e["eligible_L"],
                    }.items()
                },
            )
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, default=ROOT / "research/eq_levels_baseline.json")
    ap.add_argument("--json", type=Path, default=ROOT / "research/eq_levels_e1_baseline.json")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--verify-every", type=int, default=100)
    args = ap.parse_args()
    if args.workers < 1 or args.verify_every < 1:
        ap.error("workers and verify-every must be positive")
    e0 = json.loads(args.baseline.read_text())
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert e0["commit"] == current, "Production commit differs from E0; audit before comparing"
    work = [
        (ROOT / "frontend/research/fixtures" / c["file"], c["sha256"], args.verify_every)
        for c in e0["charts"]
        if not args.symbols or c["symbol"] in args.symbols
    ]
    if not work:
        ap.error("no fixtures selected")
    charts = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(process_file, work):
            charts.append(result)
            print(
                f'{len(charts)}/{len(work)} {result["file"]}: {len(result["records"])} versions',
                flush=True,
            )
    report = dict(
        schema=1,
        production_commit=current,
        e0_sha256=hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        parameters=dict(min_touches=3, swing_lookback=5, tolerance_atr=0.5),
        closed_only=True,
        replay_step=1,
        warmup=0,
        verify_every=args.verify_every,
        arms={
            "R": "native-equivalent stateless snapshots",
            "N": "native proposals on confirmed-pivot clock; strength frozen by membership",
            "L": "N eligibility minus any historically consumed connected pivot lineage",
        },
        charts=charts,
        summary=summarize(charts),
    )
    args.json.write_text(json.dumps(report, allow_nan=False, separators=(",", ":")) + "\n")
    print(args.json)


if __name__ == "__main__":
    main()
