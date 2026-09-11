"""E4.2 payload tests."""

from pathlib import Path

import pytest
from research.eq_levels_e4_payload import check


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_candidate_payload_matches_domain(symbol, timeframe):
    result = check(Path(f"frontend/research/fixtures/{symbol}_{timeframe}.json"))
    assert result["symbol"] == symbol
    assert result["timeframe"] == timeframe
    assert result["zones"] >= 0
