"""Specification for the D6 candidate audit.

These tests are the pre-registered definitions in executable form: what counts
as a contact, when a level is "through", which profile levels exist, and that
the placebo really is matched on side and ATR bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from liquidity_hunter.core.domain import Candle, TimeFrame, VolumeNode
from research.dominant_liquidity_candidate_audit import (
    CLEARANCE_ATR,
    _runs,
    atr_bucket,
    causal_profile,
    clusters,
    matched_gap,
    mfe_mae,
    placebo_level,
    point_contact,
    point_reaction,
    profile_levels,
    separated_touches,
    share,
    through_rate,
)

START_AT = datetime(2026, 1, 1, tzinfo=UTC)


def bar(i, low, high, close=None, volume=100.0):
    close = high if close is None else close
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=START_AT + timedelta(hours=i),
        open=(low + high) / 2,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume / 2,
    )


class _Bucket:
    def __init__(self, lo, hi, node):
        self.price_low, self.price_high, self.node = lo, hi, node


def test_runs_merge_contiguous_bands_into_one_shelf():
    buckets = [
        _Bucket(1, 2, VolumeNode.HIGH_VOLUME),
        _Bucket(2, 3, VolumeNode.HIGH_VOLUME),
        _Bucket(3, 4, VolumeNode.NORMAL),
        _Bucket(4, 5, VolumeNode.HIGH_VOLUME),
    ]
    assert _runs(buckets, VolumeNode.HIGH_VOLUME) == [(1, 3), (4, 5)]


def test_runs_closes_a_shelf_that_reaches_the_last_bucket():
    buckets = [_Bucket(1, 2, VolumeNode.NORMAL), _Bucket(2, 3, VolumeNode.LOW_VOLUME)]
    assert _runs(buckets, VolumeNode.LOW_VOLUME) == [(2, 3)]


def test_profile_levels_publishes_only_what_the_domain_model_exposes():
    candles = [bar(i, 100 + i % 5, 104 + i % 5, volume=50 + (i % 7) * 30) for i in range(60)]
    profile = causal_profile(candles, "TESTUSDT", TimeFrame.H1)
    levels = profile_levels(profile)
    assert {level["type"] for level in levels} <= {"POC", "VAH", "VAL", "HVN", "LVN"}
    assert sum(level["type"] == "POC" for level in levels) == 1
    poc = next(level for level in levels if level["type"] == "POC")
    assert poc["level"] == pytest.approx(profile.poc_price)
    # The POC's own shelf is never also reported as an HVN candidate.
    assert not any(
        level["type"] == "HVN" and level["lo"] <= profile.poc_price <= level["hi"]
        for level in levels
    )


def test_profile_levels_of_a_missing_profile_is_empty():
    assert profile_levels(None) == []


def test_causal_profile_reads_only_the_lookback_window():
    """Truncation invariance: older bars outside the window cannot move it."""
    tail = [bar(i, 100 + i % 5, 104 + i % 5, volume=50 + (i % 7) * 30) for i in range(300)]
    full = causal_profile(tail, "TESTUSDT", TimeFrame.H1)
    trimmed = causal_profile(tail[-200:], "TESTUSDT", TimeFrame.H1)
    assert full.model_dump_json() == trimmed.model_dump_json()


def test_point_contact_is_the_first_bar_containing_the_level():
    future = [bar(0, 10, 11), bar(1, 12, 14), bar(2, 9, 13)]
    assert point_contact(13.0, future) == 2
    assert point_contact(50.0, future) is None


def test_separated_touches_counts_a_return_not_a_stay():
    level = 10.0
    future = [bar(0, 9, 11), bar(1, 9, 11), bar(2, 20, 22), bar(3, 9, 11)]
    assert separated_touches(level, future) == [1, 4]


def test_point_reaction_above_treats_a_close_over_the_level_as_through():
    level, atr = 100.0, 1.0
    future = [bar(0, 99, 101, close=100.0)]
    future += [bar(i, 100, 102, close=100.0 + CLEARANCE_ATR + 0.01) for i in range(1, 25)]
    result = point_reaction(level, True, future, 1, atr)
    assert result["through"] is True
    assert result["bars_to_break"] == 1
    assert result["mae"]["10"] == pytest.approx(2.0)


def test_point_reaction_below_is_the_mirror_image():
    level, atr = 100.0, 1.0
    future = [bar(0, 99, 101, close=100.0)]
    future += [bar(i, 98, 100, close=100.0 - CLEARANCE_ATR - 0.01) for i in range(1, 25)]
    result = point_reaction(level, False, future, 1, atr)
    assert result["through"] is True
    assert result["mae"]["10"] == pytest.approx(2.0)


def test_point_reaction_needs_the_full_window_before_it_judges_a_hold():
    future = [bar(i, 99, 101, close=100.0) for i in range(5)]
    assert point_reaction(100.0, True, future, 1, 1.0) is None
    assert point_reaction(100.0, True, future, None, 1.0) is None


def test_atr_bucket_edges_are_inclusive_on_the_upper_side():
    assert atr_bucket(0.5) == "<=0.5"
    assert atr_bucket(0.51) == "<=1.0"
    assert atr_bucket(9.0) == ">5.0"


def test_placebo_lands_on_the_same_side_and_inside_its_own_bucket():
    for bucket in ("<=0.5", "<=1.0", "<=2.0", "<=3.0", "<=5.0"):
        above = placebo_level(100.0, 2.0, True, bucket, f"seed-{bucket}")
        below = placebo_level(100.0, 2.0, False, bucket, f"seed-{bucket}")
        assert above > 100.0 and below < 100.0
        assert atr_bucket(abs(above - 100.0) / 2.0) == bucket
        assert atr_bucket(abs(below - 100.0) / 2.0) == bucket


def test_placebo_is_reproducible_from_its_seed():
    first = placebo_level(100.0, 1.0, True, "<=2.0", "BTCUSDT|1h|400|True|<=2.0")
    second = placebo_level(100.0, 1.0, True, "<=2.0", "BTCUSDT|1h|400|True|<=2.0")
    other = placebo_level(100.0, 1.0, True, "<=2.0", "BTCUSDT|1h|500|True|<=2.0")
    assert first == second
    assert first != other


def _candidate(family, above, bucket, symbol, block, through):
    obs = {"symbol": symbol, "tf": "1h", "block": block, "atr": 1.0}
    return {
        "family": family,
        "above": above,
        "bucket": bucket,
        "obs": obs,
        "reaction": {
            "through": through,
            "bars_to_break": 1 if through else None,
            "mfe": {"10": 1.0},
            "mae": {"10": 2.0 if through else 0.5},
        },
    }


def test_matched_gap_ignores_strata_that_hold_only_one_group():
    lonely = [_candidate("VP", True, "<=1.0", "AAA", 0, True) for _ in range(4)]
    assert matched_gap(lonely, "VP", "RAND", through_rate) is None


def test_matched_gap_weights_each_stratum_by_its_smaller_side():
    candidates = [
        *[_candidate("VP", True, "<=1.0", "AAA", 0, False) for _ in range(2)],
        *[_candidate("RAND", True, "<=1.0", "AAA", 0, True) for _ in range(9)],
        *[_candidate("VP", True, "<=1.0", "BBB", 0, True) for _ in range(5)],
        *[_candidate("RAND", True, "<=1.0", "BBB", 0, True) for _ in range(5)],
    ]
    result = matched_gap(candidates, "VP", "RAND", through_rate)
    assert result["strata"] == 2
    # Weights are min(n_a, n_b) = 2 and 5, so the -100pp cell carries 2/7.
    assert result["gap"] == pytest.approx((-1.0 * 2 + 0.0 * 5) / 7)
    assert result["pairs"] == 7


def test_mfe_mae_is_none_when_the_window_never_completed():
    candidate = {"reaction": {"mfe": {"10": None}, "mae": {"10": None}, "through": True}}
    assert mfe_mae(candidate) is None
    assert through_rate({"reaction": None}) is None


def test_clusters_label_each_level_with_the_families_within_tolerance():
    obs = {
        "atr": 1.0,
        "candidates": [
            {"family": "VP", "level": 100.0},
            {"family": "EQ", "level": 100.1},
            {"family": "SW", "level": 105.0},
            {"family": "RAND", "level": 100.0},
        ],
    }
    clusters(obs)
    by_family = {c["family"]: c for c in obs["candidates"] if c["family"] != "RAND"}
    assert by_family["VP"]["combo"] == "EQ+VP"
    assert by_family["EQ"]["combo"] == "EQ+VP"
    assert by_family["SW"]["combo"] == "SW"
    # The placebo is never allowed to create or join a confluence.
    assert "with" not in obs["candidates"][3]
    assert by_family["SW"]["cluster_atr"] == pytest.approx(4.9)


def test_share_reports_an_empty_cell_rather_than_dividing_by_zero():
    assert share(1, 2) == "50.00%"
    assert share(0, 0) == "n/a"
