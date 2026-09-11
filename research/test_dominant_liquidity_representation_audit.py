"""D5: what each representation shows, and what it is allowed to say.

These double as the pre-defined test list of D5.14: EQ only, swing only, both,
same side, opposite sides, exact tie, per timeframe, causal ATR distance, and
the no-level fallback.
"""

import pytest
from research.dominant_liquidity_product_audit import picks
from research.dominant_liquidity_representation_audit import (
    FAMILY_LABEL,
    MODELS,
    card_lines,
    covers,
    describe,
    is_top_of_family,
    model_levels,
    sentence,
)


def candidate(kind="swing_high", d_atr=1.0, strength=0.1, above=True, tag="a"):
    return {
        "family": "EQ" if kind.startswith("equal") else "SW",
        "type": kind,
        "above": above,
        "d_atr": d_atr,
        "strength": strength,
        "strength_alt": strength,
        "legacy_distance_score": 50.0,
        "timeframe_score": 65.0,
        "legacy_score": 0.0,
        "fallback": (tag, 0.0, 0.0),
        "level": f"{kind}-{tag}",
    }


def observation(candidates, tf="1h"):
    obs = {"candidates": candidates, "tf": tf, "symbol": "BTCUSDT", "observed_at": "now"}
    obs["picks"] = picks(candidates)
    return obs


# --- D5.14: the shapes the card has to survive ------------------------------


def test_both_families_present():
    obs = observation(
        [
            candidate("equal_highs", d_atr=2.0, above=True, tag="eq"),
            candidate("swing_low", d_atr=0.5, above=False, tag="sw"),
        ]
    )
    assert len(model_levels(obs, "A")) == 1
    assert len(model_levels(obs, "B")) == 2
    assert len(model_levels(obs, "C")) == 2
    assert model_levels(obs, "A")[0]["type"] == "swing_low"


def test_swing_only_leaves_the_eq_line_empty_not_the_card():
    obs = observation([candidate("swing_high", d_atr=1.0)])
    assert model_levels(obs, "B") == [obs["picks"]["best_swing"]]
    assert card_lines(obs, "B") == ["EQ  —", "SW  Swing High 1.0 ATR above"]


def test_eq_only():
    obs = observation([candidate("equal_lows", d_atr=1.5, above=False)])
    assert model_levels(obs, "B") == [obs["picks"]["best_eq"]]
    assert "EQL" in card_lines(obs, "B")[0]


def test_one_side_only_leaves_the_other_arrow_empty():
    obs = observation([candidate("swing_high", d_atr=1.0, above=True)])
    assert card_lines(obs, "C") == ["↑  Swing High 1.0 ATR", "↓  —"]


def test_same_side_and_opposite_sides_are_both_representable():
    same = observation(
        [
            candidate("equal_highs", d_atr=3.0, above=True, tag="eq"),
            candidate("swing_high", d_atr=1.0, above=True, tag="sw"),
        ]
    )
    opposite = observation(
        [
            candidate("equal_lows", d_atr=3.0, above=False, tag="eq"),
            candidate("swing_high", d_atr=1.0, above=True, tag="sw"),
        ]
    )
    assert "above" in card_lines(same, "B")[0] and "above" in card_lines(same, "B")[1]
    assert "below" in card_lines(opposite, "B")[0] and "above" in card_lines(opposite, "B")[1]
    # MODEL C answers two different questions, so it never shows one side twice.
    up, down = card_lines(opposite, "C")
    assert up.startswith("↑") and down.startswith("↓")


def test_an_exact_tie_is_resolved_without_ever_saying_so():
    obs = observation(
        [
            candidate("swing_high", d_atr=2.0, strength=0.01, tag="weak"),
            candidate("swing_low", d_atr=2.0, strength=0.90, above=False, tag="strong"),
        ]
    )
    line = card_lines(obs, "A")[0]
    assert "0.90" not in line and "strength" not in line.lower()


def test_every_timeframe_renders_the_same_way():
    for tf in ("15m", "1h", "4h"):
        obs = observation([candidate("swing_high", d_atr=1.2)], tf=tf)
        assert card_lines(obs, "A") == ["Swing High 1.2 ATR above"]


def test_a_card_with_no_candidates_is_not_representable_and_must_be_handled_upstream():
    with pytest.raises(ValueError):
        model_levels(observation([candidate()]), "D")


# --- D5.8/D5.9/D5.10: what a line may and may not say -----------------------


def test_the_family_is_always_named():
    for kind, label in FAMILY_LABEL.items():
        obs = observation([candidate(kind, d_atr=1.0, above=not kind.endswith("low"))])
        assert label in card_lines(obs, "A")[0]


def test_distance_is_shown_in_atr_never_as_a_score_or_a_percent():
    obs = observation([candidate("swing_high", d_atr=1.234)])
    line = card_lines(obs, "A")[0]
    assert "1.2 ATR" in line
    assert "%" not in line and "score" not in line.lower()


def test_no_line_of_any_model_leaks_strength_or_score():
    obs = observation(
        [
            candidate("equal_highs", d_atr=2.0, strength=0.987, above=True, tag="eq"),
            candidate("swing_low", d_atr=0.5, strength=0.123, above=False, tag="sw"),
        ]
    )
    for model in MODELS:
        text = " ".join(card_lines(obs, model)) + sentence(obs, model)
        assert "0.987" not in text and "0.123" not in text
        assert "strength" not in text.lower() and "score" not in text.lower()


def test_sentences_read_as_english_and_name_direction():
    obs = observation(
        [
            candidate("equal_highs", d_atr=2.4, above=True, tag="eq"),
            candidate("swing_low", d_atr=0.7, above=False, tag="sw"),
        ]
    )
    assert sentence(obs, "A") == "Nearest liquidity is a swing low 0.7 ATR below."
    assert sentence(obs, "B") == (
        "Nearest EQ is EQH 2.4 ATR above; nearest swing is Swing Low 0.7 ATR below."
    )
    assert sentence(obs, "C") == "Liquidity above: EQH 2.4 ATR; below: Swing Low 0.7 ATR."


def test_describe_handles_the_missing_level():
    assert describe(None) == "—"


# --- helpers used by the report ---------------------------------------------


def test_is_top_of_family_compares_inside_the_family_only():
    strong_eq = candidate("equal_highs", strength=0.9, tag="eq1")
    weak_eq = candidate("equal_lows", strength=0.1, above=False, tag="eq2")
    swing = candidate("swing_high", strength=0.02, tag="sw")
    obs = observation([strong_eq, weak_eq, swing])
    assert is_top_of_family(strong_eq, obs)
    assert not is_top_of_family(weak_eq, obs)
    assert is_top_of_family(swing, obs)  # the only member of its family


def test_covers_is_identity_based_and_treats_a_missing_reference_as_covered():
    level = candidate()
    assert covers([level], level)
    assert not covers([], level)
    assert covers([], None)
