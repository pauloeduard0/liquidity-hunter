"""E3 shadow metrics tests."""

from research.eq_levels_e1 import Replay
from research.eq_levels_e3_shadow import shadow_metrics
from research.test_eq_levels_audit import series


def test_shadow_metrics_keep_r_and_n_visible():
    run = Replay()
    for candle in series((102.0, 102.0, 102.0), count=120):
        run.feed(candle)
    result = shadow_metrics(run)
    assert result["candles"] == 120
    assert "event_counts" in result
    assert result["published_versions"] >= 0
