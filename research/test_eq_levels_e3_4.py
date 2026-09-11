"""E3.4 checkpoint roundtrip and detection of lost restart state."""

import json
from pathlib import Path

import pytest
from research.eq_levels_e1 import Replay
from research.eq_levels_e3_4 import audit_chart, verify_restarts
from research.test_eq_levels_e2 import candles


def test_three_disk_restarts_preserve_nonempty_state(tmp_path):
    reference, result = verify_restarts(candles(320), tmp_path)
    assert [r["closed_bars"] for r in result["checkpoints"]] == [80, 160, 240]
    assert result["final_state_equal"]
    assert result["events_compared"] > 0
    assert result["records_compared"] > 0
    assert result["snapshots_compared"] == 320
    assert len(list(tmp_path.glob("checkpoint-*.json"))) == 3
    assert len(reference.candles) == 320


def test_lost_state_is_rejected(monkeypatch, tmp_path):
    restore = Replay.restore

    def broken(data):
        result = restore(data)
        result.volume_sum = -1
        return result

    monkeypatch.setattr(Replay, "restore", broken)
    with pytest.raises(ValueError, match="restored state mismatch.*volume_sum"):
        verify_restarts(candles(40), tmp_path)


def test_short_stream_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="four"):
        verify_restarts(candles(3), tmp_path)


def test_real_chart_reconciles_with_shadow():
    manifest = json.loads(Path("research/eq_levels_baseline.json").read_text())
    baseline = json.loads(Path("research/eq_levels_e3_panel_baseline.json").read_text())
    expected = next(r for r in manifest["charts"] if r["file"] == "BTCUSDT_1h.json")
    observed = next(
        r for r in baseline["rows"] if (r["symbol"], r["timeframe"]) == ("BTCUSDT", "1h")
    )
    path = Path("frontend/research/fixtures/BTCUSDT_1h.json")
    result = audit_chart(path, expected, observed)
    assert result["baseline_match"] and result["final_state_equal"]
    assert len(result["checkpoints"]) == 3
    with pytest.raises(ValueError, match="hash"):
        audit_chart(path, {**expected, "sha256": "wrong"}, observed)
