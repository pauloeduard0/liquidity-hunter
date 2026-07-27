"""Tests for `liquidity_hunter.indicators.volume_profile`."""

from collections.abc import Sequence
from typing import Any

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame, VolumeNode, VolumeProfile
from liquidity_hunter.indicators import infer_tick_size, volume_profile
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def profile_of(candles: Sequence[Candle], **kwargs: Any) -> VolumeProfile | None:
    """`volume_profile` with the symbol/timeframe every test shares."""
    return volume_profile(candles, symbol="BTCUSDT", timeframe=TimeFrame.H1, **kwargs)


def test_returns_none_for_empty_series() -> None:
    assert profile_of([]) is None


def test_returns_none_when_price_never_moved() -> None:
    candles = [make_candle(i, high=100.0, low=100.0) for i in range(5)]

    assert profile_of(candles) is None


def test_total_volume_is_conserved() -> None:
    candles = [
        make_candle(0, high=110.0, low=100.0, volume=10.0),
        make_candle(1, high=120.0, low=105.0, volume=30.0),
        make_candle(2, high=115.0, low=95.0, volume=20.0),
    ]

    profile = profile_of(candles)

    assert profile is not None
    assert profile.total_volume == pytest.approx(60.0)
    assert sum(b.volume for b in profile.buckets) == pytest.approx(60.0)


def test_poc_lands_where_candles_overlap_most() -> None:
    # Every candle covers 100-101; only one reaches up to 150. The narrow band
    # they share must hold the most volume.
    candles = [make_candle(i, high=101.0, low=100.0, volume=10.0) for i in range(9)]
    candles.append(make_candle(9, high=150.0, low=100.0, volume=10.0))

    profile = profile_of(candles)

    assert profile is not None
    assert 100.0 <= profile.poc_price <= 101.0


def test_value_area_holds_the_requested_share_of_volume() -> None:
    candles = [
        make_candle(0, high=110.0, low=100.0, volume=5.0),
        make_candle(1, high=105.0, low=102.0, volume=50.0),
        make_candle(2, high=130.0, low=104.0, volume=8.0),
    ]

    profile = profile_of(candles, value_area_pct=0.70)

    assert profile is not None
    inside = sum(b.volume for b in profile.buckets if b.in_value_area)
    assert inside >= 0.70 * profile.total_volume
    assert profile.value_area_low <= profile.poc_price <= profile.value_area_high


def test_value_area_bounds_are_inside_the_profile_range() -> None:
    candles = [make_candle(i, high=100.0 + i, low=90.0 + i, volume=3.0) for i in range(20)]

    profile = profile_of(candles)

    assert profile is not None
    assert profile.price_low <= profile.value_area_low
    assert profile.value_area_high <= profile.price_high


def test_full_value_area_pct_spans_every_traded_bucket() -> None:
    candles = [make_candle(i, high=100.0 + i, low=90.0 + i, volume=3.0) for i in range(10)]

    profile = profile_of(candles, value_area_pct=1.0)

    assert profile is not None
    assert all(b.in_value_area for b in profile.buckets)


def test_buy_sell_split_follows_taker_balance() -> None:
    candles = [
        make_candle(i, high=101.0, low=100.0, volume=10.0, taker_buy_volume=8.0)
        for i in range(4)
    ]

    profile = profile_of(candles)

    assert profile is not None
    traded = [b for b in profile.buckets if b.volume > 0]
    assert all(b.buy_volume > b.sell_volume for b in traded)
    assert sum(b.buy_volume for b in traded) == pytest.approx(32.0)
    assert all(b.delta > 0 for b in traded)


def test_buy_and_sell_volume_sum_to_bucket_volume() -> None:
    candles = [
        make_candle(0, high=120.0, low=100.0, volume=10.0, taker_buy_volume=7.0),
        make_candle(1, high=115.0, low=105.0, volume=20.0, taker_buy_volume=4.0),
    ]

    profile = profile_of(candles)

    assert profile is not None
    for bucket in profile.buckets:
        assert bucket.buy_volume + bucket.sell_volume == pytest.approx(bucket.volume)


def test_delta_is_flagged_estimated() -> None:
    candles = [make_candle(i, high=101.0, low=100.0) for i in range(3)]

    profile = profile_of(candles)

    assert profile is not None
    assert profile.delta_estimated is True


def test_volume_spreads_across_a_wide_candle_range() -> None:
    profile = profile_of([make_candle(0, high=200.0, low=100.0, volume=100.0)])

    assert profile is not None
    traded = [b for b in profile.buckets if b.volume > 0]
    assert len(traded) > 1
    # A single candle distributes uniformly, so every touched bucket is equal.
    assert max(b.volume for b in traded) == pytest.approx(min(b.volume for b in traded))


def test_nodes_classify_shelves_and_gaps() -> None:
    # A dense shelf at 100-101 plus one thin excursion far above it.
    candles = [make_candle(i, high=101.0, low=100.0, volume=100.0) for i in range(10)]
    candles.append(make_candle(10, high=200.0, low=100.0, volume=1.0))

    profile = profile_of(candles)

    assert profile is not None
    assert profile.high_volume_nodes
    assert profile.low_volume_nodes
    assert all(n.node is VolumeNode.HIGH_VOLUME for n in profile.high_volume_nodes)
    # The shelf is the high-volume end, the excursion the low-volume one.
    assert max(n.price_high for n in profile.high_volume_nodes) < max(
        n.price_low for n in profile.low_volume_nodes
    )


def test_buckets_are_contiguous_and_ordered_low_to_high() -> None:
    candles = [make_candle(i, high=100.0 + i, low=90.0 + i, volume=2.0) for i in range(12)]

    profile = profile_of(candles)

    assert profile is not None
    assert profile.buckets[0].price_low == pytest.approx(profile.price_low)
    assert profile.buckets[-1].price_high == pytest.approx(profile.price_high)
    for previous, current in zip(profile.buckets, profile.buckets[1:], strict=False):
        assert current.price_low == pytest.approx(previous.price_high)


def test_exactly_one_poc_bucket_and_it_is_the_heaviest() -> None:
    candles = [
        make_candle(0, high=110.0, low=100.0, volume=5.0),
        make_candle(1, high=104.0, low=103.0, volume=90.0),
    ]

    profile = profile_of(candles)

    assert profile is not None
    pocs = [b for b in profile.buckets if b.is_poc]
    assert len(pocs) == 1
    assert pocs[0].volume == max(b.volume for b in profile.buckets)


def test_bucket_width_is_floored_at_the_tick() -> None:
    # A 0.004-wide range over 100 buckets would put every bucket below the
    # 0.001 tick, turning the profile into a comb of unreachable bands.
    candles = [make_candle(i, high=1.804, low=1.800, volume=5.0) for i in range(6)]

    profile = profile_of(candles, tick_size=0.001)

    assert profile is not None
    assert profile.bucket_size >= 0.001
    assert len(profile.buckets) == 4


def test_explicit_tick_size_overrides_inference() -> None:
    candles = [make_candle(i, high=110.0, low=100.0, volume=5.0) for i in range(4)]

    coarse = profile_of(candles, tick_size=5.0)

    assert coarse is not None
    assert coarse.bucket_size == pytest.approx(5.0)
    assert len(coarse.buckets) == 2


def test_infer_tick_size_reads_the_finest_price_precision() -> None:
    # Whole-number OHLC (the factory's midpoint open/close included) → 1.0.
    assert infer_tick_size([make_candle(0, high=102.0, low=100.0)]) == pytest.approx(1.0)
    # The midpoint open/close of 101/100 is 100.5, so the series needs 0.1.
    assert infer_tick_size([make_candle(0, high=101.0, low=100.0)]) == pytest.approx(0.1)
    assert infer_tick_size([make_candle(0, high=101.25, low=100.55)]) == pytest.approx(0.01)
    # high/low/open/close = 1.8055 / 1.8005 / 1.803 / 1.801 → finest is 1e-4.
    assert infer_tick_size(
        [make_candle(0, high=1.8055, low=1.8005, close=1.801)]
    ) == pytest.approx(0.0001)


def test_rejects_invalid_parameters() -> None:
    candles = [make_candle(i, high=101.0, low=100.0) for i in range(3)]

    with pytest.raises(ValueError, match="bucket_count"):
        profile_of(candles, bucket_count=0)
    with pytest.raises(ValueError, match="value_area_pct"):
        profile_of(candles, value_area_pct=0.0)
    with pytest.raises(ValueError, match="value_area_pct"):
        profile_of(candles, value_area_pct=1.5)
    with pytest.raises(ValueError, match="tick_size"):
        profile_of(candles, tick_size=0.0)


def test_window_bounds_come_from_the_series() -> None:
    candles = [make_candle(i, high=101.0 + i, low=99.0, volume=4.0) for i in range(7)]

    profile = profile_of(candles)

    assert profile is not None
    assert profile.start_timestamp == candles[0].timestamp
    assert profile.end_timestamp == candles[-1].timestamp
    assert profile.price_low == pytest.approx(99.0)
    assert profile.price_high == pytest.approx(107.0)
