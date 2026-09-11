"""E2 quality extraction tests."""

from research.eq_levels_e2_quality import quality_holdout


def test_quality_uses_only_requested_final_block():
    event = {
        "arm": "R",
        "side": "EQH",
        "block": 3,
        "outcomes": {"10": {"won": 1}},
        "control": {"10": {"won": 0}},
    }
    baseline = {"charts": [{"symbol": "BTCUSDT", "tf": "1h", "sweeps": [event]}]}
    result = quality_holdout(baseline)
    assert result["groups"]["1h/EQH"]["R"] == {"n": 1, "delta_mean": 1}
    assert result["groups"]["1h/EQH"]["N"]["n"] == 0
