"""E1.7 holdout extraction tests."""

from research.eq_levels_e1_7 import holdout_report


def baseline():
    quality = {
        arm: {
            "10": {
                "blocks": {"3": {"n": 2, "mean": 0.1, "p50": 0.0}},
                "median_symbol_delta": 0.0,
                "positive_symbols": 0.5,
            }
        }
        for arm in ("R", "N", "L")
    }
    density = {arm: {"mean": 1.0, "p90": 2.0} for arm in ("R", "N", "L")}
    return {
        "charts": [
            {"tf": "1h", "cold_left_difference": {"EQH": 1, "EQL": 2}, "prefix_checks": [1, 2]}
        ],
        "summary": {"1h/EQH": {"quality": quality, "density": density}},
    }


def test_holdout_is_fixed_to_final_block_and_keeps_all_arms():
    result = holdout_report(baseline())
    row = result["groups"]["1h/EQH"]
    assert result["holdout_block"] == "3"
    assert set(row) == {"R", "N", "L", "density", "restart"}
    assert row["N"]["delta_mean"] == 0.1
    assert row["restart"]["cold_start_differences"] == 1
