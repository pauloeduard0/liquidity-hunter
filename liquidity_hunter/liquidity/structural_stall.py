"""Structural stall: the standing leg stopped advancing and gave enough back.

A `BREAK_OF_STRUCTURE` leaves the leg it belongs to as the *standing*
structure until the next advance replaces it. When an expansion runs out of
buyers the machine is correctly silent -- a leg with no further break emits
nothing -- but the chart keeps presenting that last BOS as the structure in
force while price drifts back through it for weeks. `research/expansion_stall.py`
measured the shape (post-expansion windows last 78 candles at the median
against 40 for an ordinary BOS, and give back 10.9 ATR against 5.6) and
`research/structural_stall_validation.py` validated this candle-by-candle
condition over 1678 legs.

**A stall is not a reversal.** It says only: *this leg is no longer active*.
It carries no direction forecast, no new trend, and nothing about what price
does next -- at the validated setting the outcome after a stall is 44%
reversal / 38% still open / 19% resumption, which is exactly why the state
must not be read as a prediction. What it does carry is time: in the BTCUSDT
H1 2026-08-25 case it fires 97 candles before the counter-`CHoCH` and 144
before the leg resumes.

The condition (both must hold, evaluated on every candle of the standing leg):

- `bars_since_advance >= n` -- the leg has printed nothing for N candles;
- `retracement_atr >= k_atr` -- price has given back K volatility units of the
  extreme the leg reached.

`detect_structural_stall` reports the **first** candle where both hold, so the
state dates the moment the leg went quiet rather than the moment it was
observed. Returns `None` while the leg is still active.

Parameter choice (`research/structural_stall_validation.py`, 5 symbols x
M15/H1/H4, 90 windows): N=50 / K=6 sits in the middle of a wide plateau --
across N=30..80 and K=4..6 the outcome mix moves only from 13-25% resumption
and 31-48% reversal, and `quick_resume_20` is **0%** throughout, so no setting
in the region calls a leg dead just before it resumes. Only coverage varies
(62% down to 16% of post-expansion legs), which is the sensitivity dial. The
same N works on every timeframe measured -- the ATR normalization already
absorbs the scale difference -- and freezing the ATR versus recomputing it per
candle moved coverage by at most 2 points, so the frozen (stable) denominator
is kept.

Nothing here feeds the state machine. It is a read over the composed event
stream, in the spirit of `liquidity.detectors.consolidation`: purely
descriptive, and no detector, trend, or emitted event is affected by it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent
from liquidity_hunter.core.domain.market_structure import MarketStructure
from liquidity_hunter.core.domain.structural_stall import StructuralStall

#: Candles without a structure advance before the standing leg can be stale.
DEFAULT_STALL_BARS = 50
#: Retracement from the leg's extreme, in frozen-ATR units, for the same.
DEFAULT_STALL_RETRACEMENT_ATR = 6.0


#: Re-exported so callers can read the rule and its result from one place;
#: the entity itself lives in `core.domain` (it crosses the API).
__all__ = [
    "DEFAULT_MIN_EXPANSION_ATR",
    "DEFAULT_MIN_EXPANSION_BOS",
    "DEFAULT_STALL_BARS",
    "DEFAULT_STALL_RETRACEMENT_ATR",
    "StructuralStall",
    "detect_structural_stall",
    "frozen_atr_pct",
    "is_stall_eligible_leg",
]


def frozen_atr_pct(candles: Sequence[Candle], index: int) -> float:
    """Mean true-range as a fraction of price, over `candles[:index + 1]`.

    The detector's own formula (`InternalStructureDetector.detect`), as an
    *expanding* mean up to `index` -- its `mean_tr_trailing` reading, which is
    the causal one: a value at `index` never sees a later candle. Extracted
    here rather than imported because the detector computes it inline over the
    whole series; the detector is untouched.

    Returns `0.0` when there is no true range to measure (fewer than two
    candles, or an index at the series start).
    """
    if index <= 0 or len(candles) < 2:
        return 0.0
    total = 0.0
    count = 0
    for previous, current in zip(candles[:index], candles[1 : index + 1], strict=False):
        total += (
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            / current.close
        )
        count += 1
    return total / count if count else 0.0


def _last_advance(
    events: Sequence[MarketStructure], index_by_timestamp: dict[datetime, int]
) -> tuple[MarketStructure, int] | None:
    """The most recent structure advance, and the candle it landed on.

    Mirrors `app.dashboard_data._advance_boundaries` -- non-provisional
    `BREAK_OF_STRUCTURE` / `CHANGE_OF_CHARACTER` / `CHOCH_FAILED` -- which
    lives in the composition layer and cannot be imported from here (the
    dependency runs the other way). The two are pinned together by
    `research/test_structural_stall_equivalence.py`, which checks this against
    `_advance_boundaries` itself on real series.
    """
    latest: tuple[MarketStructure, int] | None = None
    for event in events:
        if event.provisional or event.event not in (
            StructureEvent.BREAK_OF_STRUCTURE,
            StructureEvent.CHANGE_OF_CHARACTER,
            StructureEvent.CHOCH_FAILED,
        ):
            continue
        index = index_by_timestamp.get(event.timestamp)
        if index is None:
            continue
        if latest is None or index >= latest[1]:
            latest = (event, index)
    return latest


def detect_structural_stall(
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    *,
    n: int = DEFAULT_STALL_BARS,
    k_atr: float = DEFAULT_STALL_RETRACEMENT_ATR,
    eligible: bool = True,
) -> StructuralStall | None:
    """Whether the standing structural leg has stalled, and since when.

    The standing leg is the one opened by the **last advance**, and only a
    `BREAK_OF_STRUCTURE` opens one: a leg that ended in a `CHoCH` or a
    `CHOCH_FAILED` was already closed by the machine, so there is nothing to
    call stale. Every later advance ends the previous leg and restarts the
    count, because only the last one is read.

    Retracement is measured on **closes**: the running extreme is the furthest
    close the leg reached, and the give-back is against the current close.
    A wick alone therefore never stalls a leg -- a stop-hunt spike is the one
    move that should not end it. (`research/structural_stall_validation.py`
    measures the wick reading too; it only ever fires earlier, never later.)

    `eligible=False` short-circuits to `None`. It is the seam for an external
    gate -- e.g. "only after a measured expansion", the residual risk the
    validation flagged, since an ordinary leg resumes after a stall 47% of the
    time against 19% for a post-expansion one. Whatever decides that lives
    outside this function.

    Returns `None` when: no advance, the last advance did not open a leg, the
    series is too short, the volatility unit is unmeasurable, or either
    condition is unmet. Strictly causal -- the result at the last candle never
    depends on a candle or event beyond it.
    """
    if n < 0 or k_atr < 0:
        raise ValueError("n and k_atr must be non-negative")
    if not eligible or len(candles) < 2:
        return None

    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    advance = _last_advance(events, index_by_timestamp)
    if advance is None:
        return None
    opener, advance_index = advance
    if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
        return None

    atr = frozen_atr_pct(candles, advance_index)
    if atr <= 0:
        return None

    bullish = opener.direction is MarketDirection.BULLISH
    # Normalized by the advance's own price level, the same denominator the
    # validation used -- so a retracement is read in ATR units of the leg that
    # produced it.
    entry = opener.price_level
    if entry <= 0:
        return None

    extreme = candles[advance_index].close
    extreme_timestamp = candles[advance_index].timestamp
    for index in range(advance_index, len(candles)):
        close = candles[index].close
        if bullish:
            if close > extreme:
                extreme, extreme_timestamp = close, candles[index].timestamp
            give_back = extreme - close
        else:
            if close < extreme:
                extreme, extreme_timestamp = close, candles[index].timestamp
            give_back = close - extreme
        bars = index - advance_index
        retracement = give_back / entry / atr
        if bars >= n and retracement >= k_atr:
            return StructuralStall(
                stale_since=candles[index].timestamp,
                direction=opener.direction,
                last_advance_timestamp=opener.timestamp,
                last_advance_price=entry,
                bars_since_advance=bars,
                retracement_atr=retracement,
                frozen_atr_pct=atr,
                leg_extreme_price=extreme,
                leg_extreme_timestamp=extreme_timestamp,
            )
    return None


#: Non-provisional same-direction BOS needed to call a run an expansion.
DEFAULT_MIN_EXPANSION_BOS = 3
#: Displacement of that run, in frozen-ATR units, for the same.
DEFAULT_MIN_EXPANSION_ATR = 15.0


def _expansion_run(
    events: Sequence[MarketStructure], opener: MarketStructure
) -> list[MarketStructure] | None:
    """The maximal same-direction BOS run that `opener` terminates.

    `research/expansion_stall.py._runs`, read backwards from the standing leg's
    opening BOS instead of forwards over the whole stream -- same rule, same
    outcome, but it only ever touches events at or before `opener`, which is
    what makes the gate causal.

    What breaks a run, and what does not (the research semantics, unchanged):

    - a **provisional** event of any kind is skipped -- it is not structure yet,
      so a provisional BOS neither counts nor breaks;
    - a non-provisional `CHANGE_OF_CHARACTER` **breaks** it: the machine itself
      declared the leg over;
    - a non-provisional BOS of the **opposite** direction breaks it (it opens
      the other run);
    - `CHOCH_FAILED` does **not** break it. That is the pipeline's own
      semantics and not a rule invented here: a failed CHoCH is the leg being
      *reaffirmed*, and `_runs` lets it pass for exactly that reason;
    - sweeps, LH/HL and every other event kind do not break it either -- only
      the three cases above are read.
    """
    run: list[MarketStructure] = []
    for event in events:
        if event.timestamp > opener.timestamp or event.provisional:
            continue
        if event.event is StructureEvent.CHANGE_OF_CHARACTER:
            run = []
            continue
        if event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            continue
        if run and run[-1].direction is not event.direction:
            run = []
        run.append(event)
    if not run or run[-1].timestamp != opener.timestamp:
        return None
    return run


def is_stall_eligible_leg(
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    *,
    min_bos: int = DEFAULT_MIN_EXPANSION_BOS,
    min_displacement_atr: float = DEFAULT_MIN_EXPANSION_ATR,
) -> bool:
    """Was the standing leg born of a measured expansion?

    The gate for `detect_structural_stall(..., eligible=...)`, kept outside it:
    the stall rule answers "did this leg go quiet", this one answers "is this
    the kind of leg worth asking about". `research/expansion_stall.py` measured
    the split -- a post-expansion window lasts 78 candles at the median against
    40, gives back 10.9 ATR against 5.6, and resumes after a stall 19% of the
    time against 47% for an ordinary leg.

    Expansion, reproducing Etapa 0.5 exactly:

    - a run of at least `min_bos` non-provisional BOS in the same direction
      (see `_expansion_run` for what breaks a run and what does not);
    - whose displacement, from the **first BOS's `price_level`** to the
      **extreme BOS `price_level` of the run** -- the broken levels, not the
      candle extremes -- normalized by that first price and by the volatility
      unit, is at least `min_displacement_atr`.

    One deliberate difference from research, and the only one: research divided
    by `mean_tr_pct` over the whole window, which is not causal (a later candle
    changes an earlier verdict). Here the unit is `frozen_atr_pct` at the run's
    last BOS -- the same expanding mean the stall itself freezes. The two are
    compared on real series in `research/test_expansion_gate_equivalence.py`.

    Eligibility belongs to the **standing leg**, never to the chart: it is
    computed from the run that the *last advance* terminates, so a leg opened
    after a `CHoCH` starts a fresh run and cannot inherit anything.
    """
    if min_bos < 0 or min_displacement_atr < 0:
        raise ValueError("min_bos and min_displacement_atr must be non-negative")
    if len(candles) < 2:
        return False
    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    advance = _last_advance(events, index_by_timestamp)
    if advance is None:
        return False
    opener, advance_index = advance
    if opener.event is not StructureEvent.BREAK_OF_STRUCTURE:
        return False

    run = _expansion_run(events, opener)
    if run is None or len(run) < min_bos:
        return False

    atr = frozen_atr_pct(candles, advance_index)
    if atr <= 0:
        return False
    prices = [event.price_level for event in run]
    start = run[0].price_level
    extreme = max(prices) if opener.direction is MarketDirection.BULLISH else min(prices)
    if start <= 0:
        return False
    displacement = abs(extreme - start) / start / atr
    return displacement >= min_displacement_atr
