"""E2 fixed holdout panel tests."""

from research.eq_levels_e2_holdout import run_panel


def test_fixed_holdout_panel_has_all_nine_charts():
    result = run_panel()
    assert len(result["charts"]) == 9
    assert all(row["restart_equal"] for row in result["charts"])
    assert all(
        row["published_versions"] == row["stable_strength_versions"] for row in result["charts"]
    )
