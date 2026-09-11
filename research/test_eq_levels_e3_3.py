"""E3.3 attribution must expose broken pivot-clock invariants."""

import json
from pathlib import Path

import pytest
from research.eq_levels_e3_3 import audit_chart, identity_evidence, summarize


def test_between_pivots_attributes_both_directions():
    result = identity_evidence({"new"}, {"old"}, {"old"}, pivot=False)
    assert result == {"R_only": 1, "N_only": 1, "explained": True, "different": True}


@pytest.mark.parametrize(
    "raw,normal,anchor,pivot",
    [({"new"}, {"old"}, {"old"}, True), ({"old"}, {"old"}, {"other"}, False)],
)
def test_broken_clock_is_not_explained(raw, normal, anchor, pivot):
    assert not identity_evidence(raw, normal, anchor, pivot=pivot)["explained"]


def test_attribution_reconciles_real_shadow_and_checks_provenance():
    manifest = json.loads(Path("research/eq_levels_baseline.json").read_text())
    baseline = json.loads(Path("research/eq_levels_e3_panel_baseline.json").read_text())
    expected = next(r for r in manifest["charts"] if r["file"] == "BTCUSDT_1h.json")
    reference = next(
        r for r in baseline["rows"] if (r["symbol"], r["timeframe"]) == ("BTCUSDT", "1h")
    )
    path = Path("frontend/research/fixtures/BTCUSDT_1h.json")
    result = audit_chart(path, expected, reference)
    assert result["baseline_match"]
    assert result["native_parity_checks"] > 0
    assert (
        sum(r["divergent_snapshots"] for r in result["rows"])
        == reference["snapshots_with_R_N_difference"]
    )
    assert (
        sum(r["R_only_exposures"] + r["N_only_exposures"] for r in result["rows"])
        == reference["sum_R_N_difference"]
    )
    assert sum(r["unexplained_snapshots"] for r in result["rows"]) == 0
    assert result["examples"]
    with pytest.raises(ValueError, match="hash"):
        audit_chart(path, {**expected, "sha256": "wrong"}, reference)
    with pytest.raises(ValueError, match="baseline mismatch"):
        audit_chart(path, expected, {**reference, "sum_R_N_difference": -1})


def test_summary_keeps_maximum_instead_of_summing_wait_times():
    charts = [
        {
            "timeframe": "1h",
            "rows": [
                {"side": "EQH", "block": 0, "max_bars_since_pivot": value, "divergent_snapshots": 2}
            ],
        }
        for value in (3, 7)
    ]
    result = summarize(charts)[0]
    assert result["max_bars_since_pivot"] == 7
    assert result["divergent_snapshots"] == 4
