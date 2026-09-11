"""Characterization tests: passing means current behavior was reproduced, not fixed."""

from liquidity_hunter.app.dashboard_data import _equal_high_detector, _equal_low_detector
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle
from research.eq_levels_audit import forward, grouped, scale_series


def series(prices=(102.0, 102.15, 102.3), count=60):
    c = [make_candle(i, high=100.1, low=99.9, close=100.0, volume=1.0) for i in range(count)]
    for i, p in zip([10, 25, 40, 55], prices, strict=False):
        c[i] = make_candle(i, high=p, low=99.9, close=100.0, volume=1.0)
    return c


def test_production_configuration_is_not_class_defaults():
    for d in [_equal_high_detector(), _equal_low_detector()]:
        assert (d._min_touches, d._swing_detector._lookback, d._tolerance_atr) == (3, 5, 0.5)


def test_future_volatility_changes_old_clusters_without_new_high_pivots():
    d = _equal_high_detector()
    c = series()
    future = c + [
        make_candle(i, high=101.0, low=90.0, close=100.0, volume=1.0) for i in range(60, 120)
    ]
    assert d._swing_detector.detect(c) == d._swing_detector.detect(future) or [
        s.formed_at for s in d._swing_detector.detect(c)
    ] == [s.formed_at for s in d._swing_detector.detect(future)]
    assert d.detect(c) == []
    assert len(d.detect(future)) == 1
    assert d.detect(future)[0].formed_at == c[40].timestamp


def test_future_volume_changes_strength_with_identical_geometry():
    d = _equal_high_detector()
    c = series((102.0, 102.0, 102.0))
    future = c + [
        make_candle(i, high=100.1, low=99.9, close=100.0, volume=1000.0) for i in range(60, 80)
    ]
    a, b = d.detect(c)[0], d.detect(future)[0]
    assert (a.price_low, a.price_high, a.formed_at) == (b.price_low, b.price_high, b.formed_at)
    assert b.strength < a.strength


def test_anchor_grouping_is_not_transitive_and_partitions_pivots():
    d = _equal_high_detector()
    swings = d._swing_detector.detect(series((102.0, 103.0, 104.0)))
    groups = d._group_by_tolerance(swings, 0.01)
    assert [len(g) for g in groups] == [2, 1]
    assert len({s.formed_at for g in groups for s in g}) == 3


def test_five_right_bars_and_backdated_formed_at():
    d = _equal_high_detector()
    c = series((102.0, 102.0, 102.0))
    assert d.detect(c[:45]) == []
    z = d.detect(c[:46])[0]
    assert z.formed_at == c[40].timestamp
    assert not hasattr(z, "provisional")


def test_growth_moves_formation_and_reuses_consumed_origins():
    d = _equal_high_detector()
    c = series((102.0, 102.01, 102.02, 102.03), count=70)
    a = d.detect(c[:50])[0]
    b = d.detect(c)[0]
    assert a.formed_at == c[40].timestamp and b.formed_at == c[55].timestamp
    assert mark_swept_zones([a], c)[0].invalidated_at == c[55].timestamp
    assert mark_swept_zones([b], c)[0].invalidated_at is None


def test_wick_and_close_lifecycle_distinct_and_stable_for_frozen_zone():
    c = series((102.0, 102.0, 102.0))
    z = _equal_high_detector().detect(c)[0]
    c += [
        make_candle(60, high=103.0, low=99.0, close=101.0),
        make_candle(61, high=104.0, low=100.0, close=103.0),
    ]
    first = mark_swept_zones([z], c[:61])[0]
    later = mark_swept_zones([z], c)[0]
    assert first.is_mitigated and first.sweep_rejected and first.breached_at is None
    assert later.invalidated_at == first.invalidated_at == c[60].timestamp
    assert later.breached_at == c[61].timestamp


def test_causal_normalizer_and_forward_censoring():
    c = series()
    assert scale_series(c)[:45] == scale_series(c[:45])
    assert forward(c, 55, 5, "EQH", 1.0) is None
    assert forward(c, 54, 5, "EQH", 1.0) is not None


def test_harness_group_provenance_matches_real_detector():
    _, _, pairs = grouped(_equal_high_detector(), series((102.0, 102.0, 102.0)))
    assert len(pairs) == 1 and len(pairs[0][1]) == 3


def test_unclosed_confirmation_bar_can_remove_unflagged_pool():
    c = series((102.0, 102.0, 102.0))[:46]
    detector = _equal_high_detector()
    assert len(detector.detect(c)) == 1
    c[-1] = make_candle(45, high=103.0, low=99.9, close=100.0)
    assert detector.detect(c) == []


def test_unclosed_last_close_can_remove_breach_but_keep_wick():
    c = series((102.0, 102.0, 102.0))
    z = _equal_high_detector().detect(c)[0]
    spent = c + [make_candle(60, high=104.0, low=99.0, close=103.0)]
    recovered = c + [make_candle(60, high=104.0, low=99.0, close=101.0)]
    a = mark_swept_zones([z], spent)[0]
    b = mark_swept_zones([z], recovered)[0]
    assert a.breached_at is not None and b.breached_at is None
    assert a.invalidated_at == b.invalidated_at


def test_revisit_requires_departure_and_counts_only_reentry():
    from research.eq_levels_followup import revisit

    c = [
        make_candle(0, high=103.0, low=99.0, close=100.0),
        make_candle(1, high=103.0, low=101.0, close=102.0),
        make_candle(2, high=101.0, low=100.0, close=100.5),
        make_candle(3, high=103.0, low=101.0, close=101.5),
    ]
    assert revisit(c[:2], 0, 102.0, 102.0, "EQH") is None
    assert revisit(c, 0, 102.0, 102.0, "EQH") == {
        "index": 3,
        "wait": 3,
        "rejection": True,
        "close_through": False,
    }


def test_forward_excludes_event_wick_and_mirrors_sides():
    c = [
        make_candle(0, high=150.0, low=50.0, close=100.0),
        make_candle(1, high=102.0, low=99.0, close=101.0),
    ]
    down = forward(c, 0, 1, "EQH", 1.0)
    up = forward(c, 0, 1, "EQL", 1.0)
    assert (down["mfe"], down["mae"], down["move"]) == (1.0, 2.0, -1.0)
    assert (up["mfe"], up["mae"], up["move"]) == (2.0, 1.0, 1.0)
