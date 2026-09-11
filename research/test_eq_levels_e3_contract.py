"""E3.1 operational contract tests."""

import pytest
from research.eq_levels_e3_contract import ShadowContractError, rollout_decision


def report(elapsed_seconds=1.0, **row):
    return {
        "schema": 1,
        "elapsed_seconds": elapsed_seconds,
        "rows": [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "block_divergent_snapshots": [0, 0, 0, 0],
                **row,
            }
        ],
    }


def test_divergence_keeps_candidate_shadow_only():
    assert (
        rollout_decision(
            report(snapshots_with_R_N_difference=1, max_R_N_difference=1, sum_R_N_difference=1),
            expected_charts=1,
            max_seconds=2,
        )
        == "shadow-only"
    )


def test_latency_rolls_back_and_bad_panel_fails_closed():
    assert (
        rollout_decision(
            report(
                elapsed_seconds=3,
                snapshots_with_R_N_difference=0,
                max_R_N_difference=0,
                sum_R_N_difference=0,
            ),
            expected_charts=1,
            max_seconds=2,
        )
        == "rollback"
    )
    with pytest.raises(ShadowContractError, match="incomplete"):
        rollout_decision(
            report(snapshots_with_R_N_difference=0, max_R_N_difference=0, sum_R_N_difference=0),
            expected_charts=2,
            max_seconds=2,
        )


@pytest.mark.parametrize("runtime", [float("nan"), float("inf"), -1, True])
def test_invalid_runtime_fails_closed(runtime):
    with pytest.raises(ShadowContractError, match="runtime"):
        rollout_decision(report(elapsed_seconds=runtime), expected_charts=1, max_seconds=2)


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_budget_fails_closed(budget):
    with pytest.raises(ShadowContractError, match="budget"):
        rollout_decision(report(), expected_charts=1, max_seconds=budget)


@pytest.mark.parametrize("blocks", [[0, 0, 0, -1], [0, 0, 0, True], [0, 0, 0, "1"]])
def test_invalid_block_counts_fail_closed(blocks):
    with pytest.raises(ShadowContractError, match="block"):
        rollout_decision(
            report(
                snapshots_with_R_N_difference=0,
                max_R_N_difference=0,
                sum_R_N_difference=0,
                block_divergent_snapshots=blocks,
            ),
            expected_charts=1,
            max_seconds=2,
        )


@pytest.mark.parametrize("row", [None, [], {"symbol": [], "timeframe": "1h"}])
def test_malformed_rows_fail_with_contract_error(row):
    data = report()
    data["rows"] = [row]
    with pytest.raises(ShadowContractError):
        rollout_decision(data, expected_charts=1, max_seconds=2)


def test_valid_panel_is_only_eligible_for_review():
    assert (
        rollout_decision(
            report(snapshots_with_R_N_difference=0, max_R_N_difference=0, sum_R_N_difference=0),
            expected_charts=1,
            max_seconds=2,
        )
        == "eligible-for-review"
    )
