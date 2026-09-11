"""D3: family-relative calibration — definition, causality, window invariance."""

from collections import defaultdict

import pytest
from liquidity_hunter.tests.scoring._factories import make_zone
from research.dominant_liquidity_calibration_audit import (
    ARMS,
    CURVE,
    NEUTRAL,
    ROBUST_WARMUP,
    arm_key,
    distance_channel,
    family_ranks,
    midrank_percentile,
    observe,
    pick,
    rank_stability,
    robust_ranks,
    strength_channel,
)


def candidate(family="SW", strength=0.5, **overrides):
    base = {
        "family": family,
        "strength": strength,
        "touch_score": strength * 100,
        "distance_score": 50.0,
        "timeframe_score": 65.0,
        "d_atr": 1.0,
    }
    return {**base, **overrides}


# --- D3.1: what the percentile is ------------------------------------------


def test_midrank_spans_the_population_and_shares_ties():
    assert midrank_percentile(1.0, [1.0, 2.0, 3.0]) == 0.0
    assert midrank_percentile(3.0, [1.0, 2.0, 3.0]) == 100.0
    assert midrank_percentile(2.0, [1.0, 2.0, 3.0]) == 50.0
    assert midrank_percentile(1.0, [1.0, 1.0, 3.0]) == pytest.approx(25.0)


def test_a_lone_family_member_is_neutral_not_maximal():
    assert midrank_percentile(0.9, [0.9]) == NEUTRAL
    assert midrank_percentile(0.0, []) == NEUTRAL


def test_families_are_normalized_separately_never_together():
    cands = [
        candidate("EQ", 0.30),
        candidate("EQ", 0.90),
        candidate("SW", 0.001),
        candidate("SW", 0.009),
    ]
    family_ranks(cands)
    assert [c["rank_score"] for c in cands] == [0.0, 100.0, 0.0, 100.0]


def test_family_rank_is_invariant_to_a_shared_rescaling():
    """The swing defect exactly: every pivot divides by the same price_range."""
    cands = [candidate("SW", s) for s in (0.002, 0.005, 0.011)]
    family_ranks(cands)
    before = [c["rank_score"] for c in cands]
    rescaled = [candidate("SW", s / 3.7) for s in (0.002, 0.005, 0.011)]
    family_ranks(rescaled)
    assert [c["rank_score"] for c in rescaled] == before


# --- D3.2: causality --------------------------------------------------------


def test_robust_rank_is_neutral_until_the_warmup_is_met():
    history = defaultdict(list, {"EQ": [0.1] * (ROBUST_WARMUP - 1)})
    cands = [candidate("EQ", 0.9)]
    robust_ranks(cands, history)
    assert cands[0]["robust_score"] == NEUTRAL

    history["EQ"].append(0.1)
    robust_ranks(cands, history)
    assert cands[0]["robust_score"] == 100.0


def test_robust_rank_reads_history_without_writing_to_it():
    history = defaultdict(list, {"SW": [0.01] * ROBUST_WARMUP})
    robust_ranks([candidate("SW", 0.5)], history)
    assert history["SW"] == [0.01] * ROBUST_WARMUP


def test_observe_leaves_history_to_the_caller_so_it_stays_strictly_past():
    history = defaultdict(list)
    zones = [make_zone(110, strength=0.8), make_zone(105, strength=0.2)]
    observe(zones, 100.0, atr=1.0, future=[], history=history)
    assert not any(history.values())


def test_ranks_of_a_prefix_do_not_depend_on_later_observations():
    history = defaultdict(list)
    zones = [make_zone(110, strength=0.8), make_zone(105, strength=0.2)]
    first = observe(zones, 100.0, atr=1.0, future=[], history=history)
    scores = [(c["rank_score"], c["robust_score"]) for c in first]
    for candidate_row in first:  # a later observation arrives and extends history
        history[candidate_row["family"]].append(candidate_row["strength"])
    assert [(c["rank_score"], c["robust_score"]) for c in first] == scores


# --- D3.3/D3.4/D3.5: the arms ----------------------------------------------


def test_the_distance_curve_is_frozen_at_the_d2_survivor():
    assert CURVE == "LINEAR_5_ATR"
    assert distance_channel(candidate(d_atr=0.0), "atr") == 100.0
    assert distance_channel(candidate(d_atr=5.0), "atr") == 0.0
    assert distance_channel(candidate(distance_score=42.0), "pct") == 42.0


def test_each_arm_reads_its_own_channel():
    row = candidate("EQ", 0.25, rank_score=90.0, robust_score=10.0)
    assert strength_channel(row, "none") == 0.0
    assert strength_channel(row, "current") == 25.0
    assert strength_channel(row, "rank") == 90.0
    assert strength_channel(row, "robust") == 10.0


def test_weights_are_the_production_ones_only_the_content_changes():
    row = candidate("EQ", 1.0, rank_score=0.0, d_atr=0.0)
    assert arm_key(row, "atr", "current") == pytest.approx(100 * 0.4 + 100 * 0.4 + 65 * 0.2)
    assert arm_key(row, "atr", "rank") == pytest.approx(100 * 0.4 + 0.0 + 65 * 0.2)


def test_arm_list_matches_the_pre_registration():
    assert [name for name, _, _ in ARMS] == [
        "LEGACY",
        "L5_CURRENT",
        "L5_NO_STRENGTH",
        "L5_FAMILY_RANK",
        "L5_FAMILY_ROBUST",
    ]


def test_family_rank_can_lift_a_swing_over_a_far_strong_eq():
    """The D1/D2 pathology, in one observation."""
    cands = [
        candidate("EQ", 0.95, d_atr=6.0),
        candidate("EQ", 0.10, d_atr=7.0),
        candidate("SW", 0.004, d_atr=0.5),
        candidate("SW", 0.001, d_atr=4.0),
    ]
    family_ranks(cands)
    assert pick(cands, "atr", "current") == 0  # raw strength keeps the far EQ
    assert pick(cands, "atr", "rank") == 2  # the near swing, top of its own family


def test_tie_break_stays_entry_order_unless_asked():
    cands = [candidate("SW", 0.2, d_atr=9.0), candidate("SW", 0.2, d_atr=8.0)]
    family_ranks(cands)
    assert pick(cands, "atr", "rank") == 0
    assert pick(cands, "atr", "rank", by_proximity=True) == 1


# --- D3.13: the window probe ------------------------------------------------


def test_rank_stability_compares_the_same_population_only():
    previous = {("buy_side", "t1", 10.0): 0.40, ("buy_side", "t2", 12.0): 0.20}
    swings = [
        make_zone(10.0, strength=0.20),  # every strength halved: a shared denominator
        make_zone(12.0, strength=0.10),
        make_zone(14.0, strength=0.90),  # a newcomer, excluded from the probe
    ]
    keys = [("buy_side", z.formed_at.isoformat(), z.price_high) for z in swings]
    previous = {keys[0]: 0.40, keys[1]: 0.20}
    counts = rank_stability(previous, swings)["counts"]
    assert counts["shared"] == 2
    assert counts["raw_changed"] == 2
    assert counts["rank_changed"] == 0
    assert counts["same_membership"] is False
