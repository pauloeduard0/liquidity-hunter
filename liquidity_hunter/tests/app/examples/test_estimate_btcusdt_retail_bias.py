"""Tests for the BTCUSDT retail bias estimation example script."""

from liquidity_hunter.app.examples.estimate_btcusdt_retail_bias import main
from liquidity_hunter.core.domain import RetailPositioning


def test_main_estimates_long_bias_against_higher_timeframe_trend(capsys) -> None:
    estimate = main()

    assert estimate.dominant_side == RetailPositioning.LONG
    assert estimate.confidence == 82.0
    assert "buy a perceived bottom against the higher timeframe trend" in estimate.explanation

    captured = capsys.readouterr()
    assert "Dominant side: long" in captured.out
    assert "Confidence: 82" in captured.out
