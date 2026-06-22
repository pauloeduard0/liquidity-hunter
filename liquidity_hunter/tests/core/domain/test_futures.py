"""Tests for futures market-state domain entities."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from liquidity_hunter.core.domain import FundingRate, LongShortRatio, OpenInterestPoint

TS = datetime(2026, 6, 22, tzinfo=UTC)


def test_open_interest_point_valid() -> None:
    point = OpenInterestPoint(
        symbol="BTCUSDT", timestamp=TS, open_interest=1000.0, open_interest_value=5e7
    )
    assert point.open_interest == 1000.0
    assert point.open_interest_value == 5e7


def test_open_interest_value_defaults_zero() -> None:
    point = OpenInterestPoint(symbol="BTCUSDT", timestamp=TS, open_interest=1000.0)
    assert point.open_interest_value == 0.0


def test_open_interest_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        OpenInterestPoint(symbol="BTCUSDT", timestamp=TS, open_interest=-1.0)


def test_funding_rate_allows_negative() -> None:
    rate = FundingRate(symbol="BTCUSDT", timestamp=TS, funding_rate=-0.0003)
    assert rate.funding_rate == -0.0003


def test_long_short_ratio_valid() -> None:
    ls = LongShortRatio(
        symbol="BTCUSDT",
        timestamp=TS,
        long_account_pct=0.6,
        short_account_pct=0.4,
        ratio=1.5,
    )
    assert ls.ratio == 1.5


@pytest.mark.parametrize("pct", [-0.1, 1.1])
def test_long_short_ratio_rejects_out_of_range_pct(pct: float) -> None:
    with pytest.raises(ValidationError):
        LongShortRatio(
            symbol="BTCUSDT",
            timestamp=TS,
            long_account_pct=pct,
            short_account_pct=0.5,
            ratio=1.0,
        )


def test_long_short_ratio_rejects_non_positive_ratio() -> None:
    with pytest.raises(ValidationError):
        LongShortRatio(
            symbol="BTCUSDT",
            timestamp=TS,
            long_account_pct=0.5,
            short_account_pct=0.5,
            ratio=0.0,
        )
