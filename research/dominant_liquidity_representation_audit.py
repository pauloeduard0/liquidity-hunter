"""D5: which representation of the card is semantically honest and useful?

Research only. Nothing is implemented, no frontend is touched, and no new score
is invented — this round reads the D4 replay and asks what can be *said*
truthfully with the levels it already produces.

Three product models, and only three (D5.0):

- `A` SINGLE HONEST — one level, the nearest by causal ATR distance.
- `B` DUAL FAMILY — the nearest EQ and the nearest swing, side by side, with no
  cross-family comparison anywhere.
- `C` SIDE MAP — the nearest level above price and the nearest below,
  family-agnostic in the choice but always naming the family it came from.

All three use the same pre-registered D4.2 ordering (distance in ATR first;
strength only breaks an exact tie and is never displayed), so the models differ
in *what is shown*, never in how a level is chosen.
"""

from __future__ import annotations

import json
import statistics as st

from research.dominant_liquidity_product_audit import BASELINE, picks

#: How a family is named to a human. The card must never hide which one it is
#: (D5.8): D4 measured that the two behave differently after contact.
FAMILY_LABEL = {
    "equal_highs": "EQH",
    "equal_lows": "EQL",
    "swing_high": "Swing High",
    "swing_low": "Swing Low",
}

MODELS = ("A", "B", "C")
CASE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def load():
    data = json.loads(BASELINE.read_text())
    observations = []
    for chart in data["charts"]:
        for row in chart["rows"]:
            if row["candidates"]:
                observations.append({**row, "symbol": chart["symbol"], "tf": chart["timeframe"]})
    for obs in observations:
        obs["picks"] = picks(obs["candidates"])
    return observations


def model_levels(obs: dict, model: str) -> list[dict]:
    """The levels each model would put on the card, in display order."""
    chosen = obs["picks"]
    if model == "A":
        return [chosen["single"]]
    if model == "B":
        return [level for level in (chosen["best_eq"], chosen["best_swing"]) if level]
    if model == "C":
        return [level for level in (chosen["best_above"], chosen["best_below"]) if level]
    raise ValueError(model)


def describe(level: dict | None, *, side: bool = True) -> str:
    """One human-readable line for one level. No score, no strength (D5.10)."""
    if level is None:
        return "—"
    where = "above" if level["above"] else "below"
    label = FAMILY_LABEL[level["type"]]
    body = f"{label} {level['d_atr']:.1f} ATR"
    return f"{body} {where}" if side else body


def sentence(obs: dict, model: str) -> str:
    """The one-sentence reading of the card (D5.6)."""
    chosen = obs["picks"]
    if model == "A":
        level = chosen["single"]
        where = "above" if level["above"] else "below"
        name = FAMILY_LABEL[level["type"]].lower()
        return f"Nearest liquidity is a {name} {level['d_atr']:.1f} ATR {where}."
    if model == "B":
        return (
            f"Nearest EQ is {describe(chosen['best_eq'])}; "
            f"nearest swing is {describe(chosen['best_swing'])}."
        )
    return (
        f"Liquidity above: {describe(chosen['best_above'], side=False)}; "
        f"below: {describe(chosen['best_below'], side=False)}."
    )


def card_lines(obs: dict, model: str) -> list[str]:
    """The textual mock of the card body (D5.5). One string per rendered line."""
    chosen = obs["picks"]
    if model == "A":
        level = chosen["single"]
        return [describe(level)]
    if model == "B":
        return [f"EQ  {describe(chosen['best_eq'])}", f"SW  {describe(chosen['best_swing'])}"]
    return [
        f"↑  {describe(chosen['best_above'], side=False)}",
        f"↓  {describe(chosen['best_below'], side=False)}",
    ]


def is_top_of_family(level: dict, obs: dict) -> bool:
    """Would calling this level the 'strongest' be true? (D5.7)"""
    family_members = [c for c in obs["candidates"] if c["family"] == level["family"]]
    return level["strength"] >= max(c["strength"] for c in family_members) - 1e-12


def covers(levels: list[dict], reference: dict | None) -> bool:
    return reference is None or any(level is reference for level in levels)


def median(values):
    kept = [v for v in values if v is not None]
    return st.median(kept) if kept else float("nan")


def report() -> None:  # noqa: C901 - a report is a sequence of tables
    observations = load()
    out = print
    out(f"# observations={len(observations)} (D4 replay, no new measurement)")

    out("\n## D5.2 information matrix: how many levels each model puts on the card")
    for model in MODELS:
        counts = {0: 0, 1: 0, 2: 0}
        for obs in observations:
            counts[len(model_levels(obs, model))] += 1
        out(
            f"MODEL {model}: "
            + " ".join(
                f"{n} level(s)={counts[n] / len(observations) * 100:6.2f}%" for n in (0, 1, 2)
            )
        )

    out("\n-- coverage of the three reference levels (what the card does NOT drop)")
    for model in MODELS:
        shown = [model_levels(obs, model) for obs in observations]
        paired = list(zip(shown, observations, strict=True))
        nearest = sum(covers(levels, obs["picks"]["single"]) for levels, obs in paired)
        sides = sum(
            covers(levels, obs["picks"]["best_above"])
            and covers(levels, obs["picks"]["best_below"])
            for levels, obs in paired
        )
        families = sum(
            covers(levels, obs["picks"]["best_eq"])
            and covers(levels, obs["picks"]["best_swing"])
            for levels, obs in paired
        )
        out(
            f"MODEL {model}: nearest overall={nearest / len(observations) * 100:6.2f}% "
            f"| both sides={sides / len(observations) * 100:6.2f}% "
            f"| both families={families / len(observations) * 100:6.2f}%"
        )

    out("\n-- duplication: do the two shown levels say the same thing?")
    both_b = [obs for obs in observations if len(model_levels(obs, "B")) == 2]
    both_c = [obs for obs in observations if len(model_levels(obs, "C")) == 2]
    same_side_b = sum(
        obs["picks"]["best_eq"]["above"] == obs["picks"]["best_swing"]["above"] for obs in both_b
    )
    same_family_c = sum(
        obs["picks"]["best_above"]["family"] == obs["picks"]["best_below"]["family"]
        for obs in both_c
    )
    identical_pair = sum(
        {id(level) for level in model_levels(obs, "B")}
        == {id(level) for level in model_levels(obs, "C")}
        for obs in observations
    )
    out(
        f"MODEL B: the two levels share a side in {same_side_b / len(both_b) * 100:6.2f}% "
        f"(the card then says 'both that way')"
    )
    out(
        f"MODEL C: the two levels share a family in {same_family_c / len(both_c) * 100:6.2f}% "
        f"(usually two swings)"
    )
    out(f"B and C show the same pair of levels in {identical_pair / len(observations) * 100:6.2f}%")

    out("\n## D5.3 same side vs opposite sides, as each model would read it")
    both = [obs for obs in observations if obs["picks"]["best_eq"] and obs["picks"]["best_swing"]]
    same = [
        obs
        for obs in both
        if obs["picks"]["best_eq"]["above"] == obs["picks"]["best_swing"]["above"]
    ]
    opposite = [obs for obs in both if obs not in same]
    out(f"both families present={len(both)} same side={len(same)} opposite={len(opposite)}")
    for label, subset in (("same side", same), ("opposite", opposite)):
        # MODEL A always shows one family's best (the nearest overall is, by
        # construction, the nearest of its own family). The question is what it
        # costs to drop the other one.
        dropped = [
            obs["picks"]["best_swing"]
            if model_levels(obs, "A")[0] is obs["picks"]["best_eq"]
            else obs["picks"]["best_eq"]
            for obs in subset
        ]
        gaps = [
            level["d_atr"] - model_levels(obs, "A")[0]["d_atr"]
            for level, obs in zip(dropped, subset, strict=True)
        ]
        out(
            f"  {label:<10} n={len(subset):4d} MODEL A drops the other family's best, which sits "
            f"{median([level['d_atr'] for level in dropped]):.2f} ATR away "
            f"(median gap {median(gaps):+.2f} ATR); within 1 ATR of the shown one in "
            f"{sum(g <= 1 for g in gaps) / len(gaps) * 100:5.2f}%"
        )
    out(
        "  on opposite sides MODEL C is the only one whose two lines answer different questions "
        "(what is above / what is below) rather than competing"
    )

    out("\n## D5.5 visual density (textual mock)")
    for model in MODELS:
        widths = [max(len(line) for line in card_lines(obs, model)) for obs in observations]
        lines = [len(card_lines(obs, model)) for obs in observations]
        blanks = sum(
            sum(1 for line in card_lines(obs, model) if line.endswith("—")) for obs in observations
        )
        out(
            f"MODEL {model}: lines={median(lines):.0f} widest line p50={median(widths):.0f} "
            f"p90={sorted(widths)[int(len(widths) * 0.9)]} chars | em-dash placeholders="
            f"{blanks / (len(observations) * median(lines)) * 100:5.2f}% of lines"
        )

    out("\n## D5.6 one-sentence reading (first observation of each symbol)")
    seen = set()
    for obs in observations:
        if obs["symbol"] not in CASE_SYMBOLS or (obs["symbol"], obs["tf"]) in seen:
            continue
        seen.add((obs["symbol"], obs["tf"]))
        if len(seen) > 3:
            break
        out(f"{obs['symbol']} {obs['tf']} {obs['observed_at']}")
        for model in MODELS:
            out(f"  {model}: {sentence(obs, model)}")

    out("\n## D5.7 would 'dominant' / 'strongest' be a true word?")
    for model in MODELS:
        levels = [(level, obs) for obs in observations for level in model_levels(obs, model)]
        top = sum(is_top_of_family(level, obs) for level, obs in levels)
        out(
            f"MODEL {model}: the displayed level is the strongest of its own family in "
            f"{top / len(levels) * 100:6.2f}% of the lines"
        )
    legacy_top = sum(
        is_top_of_family(obs["picks"]["legacy"], obs) for obs in observations
    )
    out(
        f"LEGACY (today's card, labelled 'Dominant Liquidity'): "
        f"{legacy_top / len(observations) * 100:6.2f}%"
    )

    out("\n## D5.12 migration impact: how many cards change what they point at")
    for model in MODELS:
        changed = sum(
            not covers(model_levels(obs, model), obs["picks"]["legacy"]) for obs in observations
        )
        out(
            f"MODEL {model}: today's level disappears from the card in "
            f"{changed / len(observations) * 100:6.2f}% of observations"
        )

    out("\n## D5.11 real cases, three models side by side")
    scenarios = {
        "A same side": lambda o: o["picks"]["best_eq"]
        and o["picks"]["best_swing"]
        and o["picks"]["best_eq"]["above"] == o["picks"]["best_swing"]["above"],
        "B opposite": lambda o: o["picks"]["best_eq"]
        and o["picks"]["best_swing"]
        and o["picks"]["best_eq"]["above"] != o["picks"]["best_swing"]["above"],
        "C EQ far": lambda o: o["picks"]["best_eq"]
        and o["picks"]["best_swing"]
        and o["picks"]["best_eq"]["d_atr"] > 3
        and o["picks"]["best_swing"]["d_atr"] <= 1,
        "D swing far": lambda o: o["picks"]["best_eq"]
        and o["picks"]["best_swing"]
        and o["picks"]["best_swing"]["d_atr"] > 3
        and o["picks"]["best_eq"]["d_atr"] <= 1,
        "E both near": lambda o: o["picks"]["best_eq"]
        and o["picks"]["best_swing"]
        and max(o["picks"]["best_eq"]["d_atr"], o["picks"]["best_swing"]["d_atr"]) <= 1,
        "F h4 saturated": lambda o: o["tf"] == "4h"
        and all(c["legacy_distance_score"] == 0 for c in o["candidates"]),
    }
    for name, matches in scenarios.items():
        example = next(
            (o for o in observations if o["symbol"] in CASE_SYMBOLS and matches(o)), None
        )
        if example is None:  # rare shapes are shown from wherever they occur
            example = next((o for o in observations if matches(o)), None)
        if example is None:
            out(f"[{name}] no case in the sample")
            continue
        out(f"[{name}] {example['symbol']} {example['tf']} {example['observed_at']}")
        out(f"   LEGACY  Dominant Liquidity: {describe(example['picks']['legacy'])}")
        for model in MODELS:
            out(f"   MODEL {model} " + " | ".join(card_lines(example, model)))
