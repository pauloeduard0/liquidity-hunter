"""E3 panel aggregation tests."""

from pathlib import Path

from research.eq_levels_e3_shadow import run_panel


def test_panel_reports_chart_count_and_runtime(tmp_path):
    source = Path("frontend/research/fixtures/BTCUSDT_1h.json")
    result = run_panel([source])
    assert result["charts"] == 1
    assert result["elapsed_seconds"] >= 0
