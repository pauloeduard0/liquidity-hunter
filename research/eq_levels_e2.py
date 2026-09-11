"""E2 causal identity candidate, research-only.

This wraps the E1 N arm. It makes the proposed change explicit: confirmed
membership receives one immutable publication record and its strength is frozen
for later snapshots. Production thresholds and lifecycle calls are untouched.
"""

from __future__ import annotations

from collections import defaultdict

from research.eq_levels_e1 import Replay


def run_candidate(candles):
    run = Replay()
    for candle in candles:
        run.feed(candle)
    return run


def validate_candidate(run: Replay) -> dict:
    """Return invariant counts; raise if publication identity is not causal."""
    published = [event for event in run.events if event["kind"] == "publish"]
    by_version = defaultdict(list)
    for event in published:
        record = event["record"]
        assert event["version"] == record["version"]
        assert event["observed_at"] == record["known_time"]
        assert record["known_at"] >= 0
        by_version[event["version"]].append(record["strength"])
    stable = sum(len(set(values)) == 1 for values in by_version.values())
    assert stable == len(by_version)
    return {"published_versions": len(by_version), "stable_strength_versions": stable}


def restart_equivalent(candles, split: int) -> bool:
    """Check continuous and checkpoint/restart candidate outputs are identical."""
    continuous = run_candidate(candles)
    prefix = run_candidate(candles[:split])
    restarted = Replay.restore(prefix.checkpoint())
    for candle in candles[split:]:
        restarted.feed(candle)
    return continuous.events == restarted.events and continuous.snapshots == restarted.snapshots
