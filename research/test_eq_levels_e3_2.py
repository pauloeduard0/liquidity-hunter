"""E3.2 decision application tests."""

from research.eq_levels_e3_2 import assess


def test_realistic_divergence_is_not_promoted():
    report = {
        "schema": 1,
        "elapsed_seconds": 2.0,
        "rows": [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "snapshots_with_R_N_difference": 1,
                "max_R_N_difference": 1,
                "sum_R_N_difference": 1,
                "block_divergent_snapshots": [0, 0, 0, 1],
            }
        ],
    }
    result = assess(report, expected_charts=1)
    assert result["decision"] == "shadow-only"
    assert result["divergent_charts"] == 1
