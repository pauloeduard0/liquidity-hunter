"""E2 causal candidate invariants."""

import json
from pathlib import Path

from liquidity_hunter.core.domain import Candle
from research.eq_levels_e2 import restart_equivalent, run_candidate, validate_candidate

FIXTURE = Path("frontend/research/fixtures/BTCUSDT_1h.json")


def candles(limit=500):
    raw = json.loads(FIXTURE.read_text())["candles"][:limit]
    return [Candle.model_validate(row) for row in raw]


def test_candidate_freezes_strength_per_published_identity():
    run = run_candidate(candles())
    result = validate_candidate(run)
    assert result["published_versions"] > 0
    assert result["stable_strength_versions"] == result["published_versions"]


def test_candidate_restart_preserves_events_and_snapshots():
    data = candles(320)
    assert restart_equivalent(data, 160)
