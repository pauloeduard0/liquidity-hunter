"""Internal (minor) market structure detector: trailing-reference BOS/HL/LH
with a *continuation-confirmed* CHoCH reference.

`SwingStructureDetector` deliberately holds an active reference until the
*opposite* side breaks, so the reference reflects the true extreme of the
prior leg rather than whichever pivot formed last -- the right behavior for
`StructureScope.MAJOR`. For `StructureScope.INTERNAL` that same design can
freeze a side for long stretches, so `InternalStructureDetector` keeps
`active_high`/`active_low` as *trailing* references (normally the most
recently formed swing high/low pivot, updated after every pivot of that
kind). These drive:

- `BREAK_OF_STRUCTURE`: a pivot beyond the trailing reference *in the
  direction of* `trend` (or the first break while `trend` is `NEUTRAL`).
  State (trend, promotions) advances **only when a candle in the leg
  *closes* beyond the reference** -- a wick-only overshoot does not count.
  On a wick-only break the state does not advance and the broken reference
  is *frozen* (not trailed to this pivot), so a later candle that closes
  beyond that same level activates the BOS then. Once the close confirms the
  break, the BOS event is still only *emitted* when a pullback pivot forms
  in the opposite direction (HL for bullish, LH for bearish) that is above/
  below the pullback reference snapshot (confirming direction). If the next
  opposite-direction pivot is not a valid pullback, the pending BOS is
  silently discarded (state already advanced). A
  continuation dedup gate ensures each pullback stays on the correct side
  of the previous pullback (LH staircase for bearish, HL staircase for
  bullish), preventing re-emission of the same structural break.

  The pullback reference snapshot is the `active_<opposite>` captured at the
  state-advance. An impulsive leg of *consecutive same-side lows (highs) with
  no intervening opposite pivot* is the exception: the first advance promotes
  `pending_<opposite>` (empty in a clean impulse) into `active_<opposite>`,
  nulling it, so the next advance would snapshot a `None` pullback ref and the
  BOS could never confirm -- a whole impulsive move would emit zero BOS. Since
  the leg keeps extending from the *same* opposite pivot, a `None` snapshot
  instead inherits the prior pending BOS's pullback ref (the high the bearish
  leg is dropping from / the low the bullish leg is rising from), so the
  continuation BOS still confirms at the next opposite pivot.

  **BOS staircase**: a continuation BOS must also *extend* the leg beyond
  the previous BOS level (`last_bear_bos_low`/`last_bull_bos_high`). While
  the trend is unchanged, a break of a higher trailing low (or lower trailing
  high) formed during a retrace -- which does not beat the previous BOS
  extreme -- is not a structural BOS; it merely trails the active reference.
  So bearish BOS lows keep making lower lows (and bullish BOS highs higher
  highs) until a CHoCH flips the trend. The staircase is *seeded at each
  CHoCH with the CHoCH level itself* (the reference the CHoCH broke): the
  first BOS of the new leg must already break beyond that level, so a BOS
  cannot form on the wrong side of the CHoCH (e.g. a bullish BOS below a
  bullish CHoCH after price fell back through it). Only the very first BOS
  out of the `NEUTRAL` bootstrap (no CHoCH yet) is unconstrained.

  **Emitted reference**: a continuation BOS's `reference_price_level` is the
  **formed low/high it broke** -- the staircase floor in effect at the
  state-advance (`_PendingBOS.floor`, captured before it ratchets to this
  pivot) -- rather than the trailing pivot the state machine advanced on. So a
  BOS reports the prior swing extreme it actually broke (and the chart plots it
  there). The unconstrained first BOS of a leg (`floor is None`) falls back to
  the trailing reference. The state machine, CHoCH promotion, and trailing
  references are unaffected -- only the reported reference changes. A separate
  composition-level pass (`app.dashboard_data._reanchor_bos_close_break`)
  re-times each BOS to the first *close* beyond that formed level and drops
  wick-only continuations; see its docstring.
- `LOWER_HIGH`/`HIGHER_LOW`: a pivot that does not break the trailing
  reference.
- `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing
  reference but is not a confirmed reversal (see below). A sweep never
  promotes or overwrites the *validated* CHoCH reference directly, but a
  sweep that takes out the current pullback *candidate* re-anchors that
  candidate to the swept level (see the CHoCH-reference section): the swept
  low/high is the structural origin a subsequent same-trend expansion rises/
  falls from. The re-anchored candidate only becomes validated if a
  continuation confirms it, so a lone sweep with no follow-through expansion
  remains noise.

`pending_high`/`pending_low` accumulate the most extreme high/low pivot for
their side, promoted to `active_<side>` when the opposite side breaks (the
leg that just ended is retired in favor of the extreme accumulated during
it). `_extreme` keeps the more extreme of the two.

The CHoCH reference (`CHANGE_OF_CHARACTER`)
==========================================

The CHoCH reference is the **pullback (origin) of the most recent
continuation-confirmed BOS**. A BOS's pullback starts as a *provisional*
candidate; it is promoted to the *validated* CHoCH reference only when a
subsequent move makes a new leg extreme (a genuine continuation), confirming
that the BOS was structural, not noise. If price reverses before that
continuation, the BOS is never confirmed and its pullback never anchors a
CHoCH.

The reference is tracked per side as `validated_choch_high` (the level a
bullish CHoCH must break) and `validated_choch_low` (bearish CHoCH). The
promotion pipeline for `validated_choch_high` (bearish leg, mirrored on the
bullish side):

1. **BOS emission**: when a bearish BOS is confirmed (pending BOS + LH
   pullback), the confirming LH pivot becomes `candidate_choch_high` --
   *provisional*, not yet the CHoCH reference.

1b. **Sweep re-anchor**: while the bearish leg is unfolding, a counter-trend
   sweep (a high pivot wicking above the trailing reference but not holding)
   that pokes *above* the current `candidate_choch_high` re-anchors the
   candidate UP to that swept high. Rationale: once price grabs liquidity above
   the prior LH and then resumes lower, the swept high -- not the pre-sweep LH
   -- is the level a subsequent bullish reversal launched from, so the eventual
   CHoCH should break it. The candidate only moves to a *more extreme* (higher)
   sweep, so progressively higher grabs keep the highest origin. This re-anchor
   feeds step 2's promotion; a sweep with no continuation never promotes, so it
   does not affect the validated reference. Mirrored on the bullish side
   (`candidate_choch_low` re-anchors DOWN to a swept low).

2. **Continuation-gated promotion**: the next bearish state-advance (a lower-
   low pivot) promotes `candidate_choch_high` to `validated_choch_high`
   **only if** the new low is below `bear_leg_low` (the running extreme of
   the current bearish leg). This ensures the leg actually extended -- a
   pullback-BOS formed during a retrace that does not make a new leg low
   leaves the candidate provisional and cannot ratchet the reference down
   to a less significant level.

3. **Validated reference is frozen**: once promoted, `validated_choch_high`
   stays at that level until it is consumed by a CHoCH firing (reset to
   `None`) or replaced by the next genuine promotion. Weaker, more recent
   BOS pullbacks that cannot produce a new leg low do not overwrite it.

`bear_leg_low` / `bull_leg_high` track the running extreme of each leg,
seeded at each trend flip (CHoCH) and updated on every in-trend state-
advance.

**CHoCH check**: with `trend` BEARISH, a high pivot that breaks (sustained,
see persistence below) above `validated_choch_high or choch_origin_high or
active_high` is a `CHANGE_OF_CHARACTER`; its `reference_price_level` is the
reference it broke. The `active_high` fallback ensures the detector can flip
trend during the cold-start phase (before any validated/origin reference has
been built), preventing the trend from getting stuck if the bootstrap picks
the wrong initial direction. A high pivot whose break does not hold for
`persistence_candles` is a `LIQUIDITY_SWEEP` (trend unchanged).

**One-shot origin (blind-spot fallback)**: the moment a CHoCH fires, all
validated/candidate state is reset. Rebuilding the *reverse* reference needs
a fresh BOS + continuation, during which a failed reversal would otherwise
leave the trend stuck. `choch_origin_<side>` is the extreme of the leg the
CHoCH just reversed (set only by a *validated*-triggered CHoCH, one-shot).
The CHoCH check uses `validated or origin`, so the origin serves as fallback
until a validated reference is rebuilt. An origin-triggered CHoCH does NOT
set origin on the opposite side (one-shot), breaking ping-pong chains.

Confirmation is *persistence*-based (see `_common.is_sustained_break`): the
breaking candle AND the `persistence_candles` candles immediately following
it must all close beyond the reference. A single candle that pokes through
the reference and reverts (a "false break") is a `LIQUIDITY_SWEEP`; a break
that holds is a `CHANGE_OF_CHARACTER`.

**Failed CHoCH (`CHOCH_FAILED`)**: a CHoCH is only *provisional* until a
same-direction BOS confirms the new trend (that first BOS is guaranteed to be
beyond the CHoCH level by the staircase floor above). While unconfirmed, the
CHoCH carries an *origin* -- the swing it launched from
(`bull_choch_origin`/`bear_choch_origin`, the active low at a bullish CHoCH /
active high at a bearish CHoCH). If price breaks back through that origin
(sustained, same persistence rule) *before* a confirming BOS, the reversal
failed: a `CHOCH_FAILED` event fires (its `direction` is the failed CHoCH's
direction, `reference_price_level` the broken origin) and the trend flips
back. This supersedes the older `choch_origin` blind-spot recovery for the
unconfirmed window, at a tighter level (the impulse base, not the prior leg's
extreme). The origin is retired once the confirming BOS fires (the CHoCH can
no longer fail) or when the trend flips again. A failed-CHoCH flip does NOT
arm the opposite origin (one-shot), so failures cannot ping-pong.

When a CHoCH fires it nulls the reversing trend's BOS staircase
(`last_bear_bos_low`/`last_bull_bos_high`) to seed the new leg, but a failed
CHoCH means that trend never actually ended -- it must resume from its
*genuine* last BOS extreme, not from the (often higher-low / lower-high) CHoCH
origin, or a non-extending BOS could print past the previous same-direction
BOS. So the reversing trend's staircase floor is *stashed*
(`pre_choch_bear_bos_low`/`pre_choch_bull_bos_high`) when the CHoCH fires and
*restored* on failure (taking the more extreme of it and the origin); a
confirming BOS discards the stash. Lifecycle is tied 1:1 to the matching
`*_choch_origin`.

Every emitted `MarketStructure` has `scope = StructureScope.INTERNAL`.
"""

from dataclasses import dataclass
from datetime import datetime
from statistics import fmean

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.liquidity.detectors._common import (
    Pivot,
    bos_confluence,
    collect_pivots,
    find_close_break_index,
    find_fvg,
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector

# Allowed values for `InternalStructureDetector.reanchor_mode`. See the
# constructor and the "online re-anchor" section of the class docstring.
_REANCHOR_MODES = frozenset({"off", "displacement", "chain"})

# Relative price tolerance for matching a staged impulse BOS to a real emitted
# BOS (both report the advance pivot's extreme, so a genuine duplicate matches
# almost exactly). See the staged-BOS merge in `detect`.
_STAGED_BOS_DEDUP_PCT = 0.002


@dataclass
class _EatenBreak:
    """A counter-trend staircase break recorded during a provisional-CHoCH window.

    While a CHoCH awaits its confirming BOS the trend has flipped, so a new
    extreme in the *resumed* direction (a lower low under a provisional bullish
    CHoCH / a higher high under a bearish one) is classified as a sweep rather
    than a continuation BOS. If the CHoCH later *fails*, that trend never
    ended -- these breaks were genuine continuations the wrong flip ate. Each
    records the pivot that made the new extreme, the staircase level it broke
    (`ref_level`, the previous recorded extreme or the pre-CHoCH reported
    floor), the break candle's timestamp, and whether a candle actually
    *closed* beyond `ref_level` (drives the reported-floor ratchet at the
    failure restore, mirroring `bos_floor_require_close_break`). See
    `stage_choch_failed_window_bos`.
    """

    pivot: Pivot
    ref_level: float
    timestamp: datetime
    ref_closed: bool


@dataclass
class _PendingBOS:
    """A BOS break that awaits pullback confirmation."""

    direction: MarketDirection
    breaking_pivot: Pivot
    ref_price: float
    close_break_timestamp: datetime
    pullback_ref: Pivot | None
    # The formed low/high the continuation BOS breaks (the staircase floor at
    # the state-advance), or `None` for the first BOS of a leg. The emitted
    # `reference_price_level` is this floor (the prior swing extreme actually
    # broken) rather than the trailing pivot, so the BOS plots at the level it
    # structurally broke; `None` falls back to `ref_price`.
    floor: float | None
    # Whether an *additive* mark has already been staged for this pending BOS
    # after a wick-only pullback (guards against double-staging when several
    # wicky pullbacks form before -- or instead of -- a real one). Only used
    # when `stage_wick_rejected_bos` is set. See the wick-reject staging below.
    staged: bool = False
    # Whether a candle actually *closed* beyond the staircase floor (not just
    # wicked past it) by the time the state advanced. When the continuation only
    # poked the prior BOS level with a wick, the break is unconfirmed by close --
    # so the leg origin it promotes is *not* a structural CHoCH reference (see
    # `bos_leg_origin_require_close_break`). `True` when `floor` is `None` (the
    # first BOS of a leg has no floor to close through) or the feature is off.
    floor_closed: bool = True


class InternalStructureDetector(MarketStructureDetector):
    """Detects internal BOS/CHoCH/HL/LH from trailing swing pivot references.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order. See the module
    docstring for the full model; in brief:

    - `active_high`/`active_low` are *trailing* references (the most recent
      pivot of each kind); `pending_high`/`pending_low` accumulate each side's
      extreme for promotion when the opposite side breaks.
    - A pivot beyond the trailing reference in the direction of `trend` is a
      `BREAK_OF_STRUCTURE`; one that does not break it is a `LOWER_HIGH`/
      `HIGHER_LOW` label.
    - The reversal (`CHANGE_OF_CHARACTER`) reference is `validated_choch_high`/
      `validated_choch_low`, promoted from `candidate_choch_high`/
      `candidate_choch_low` (the strongest LH/HL of its window) on the next BOS
      in that leg's direction whose pivot price also surpasses
      `candidate_choch_high_baseline`/`candidate_choch_low_baseline` (a
      snapshot of the opposite side's trailing reference taken when the
      candidate was set) -- a genuine `LL2 < LL1`/`HH2 > HH1` relative to the
      leg containing the candidate, not necessarily a new absolute extreme of
      the whole leg. A counter-trend break of the validated reference is a
      CHoCH if sustained for `persistence_candles`, else a `LIQUIDITY_SWEEP`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be a `CHANGE_OF_CHARACTER` rather than a `LIQUIDITY_SWEEP`.

    `confluence_filter` (default `True`) applies LuxAlgo's internal-structure
    confluence filter to in-trend BOS candles: the breaking candle (the first
    one whose close crosses the level) must also have a larger upper shadow
    than lower shadow for a bullish BOS (or larger lower shadow for a bearish
    BOS), confirming directional price expansion beyond the level. When
    `False`, the filter is skipped and only the close requirement is checked.

    `reanchor_mode` (default `"off"`) enables the **online re-anchor** (flavor
    B). On a strong impulsive leg with few/no opposite pullbacks, the opposite-
    side references (`active_high`/`validated_choch_high` in a bearish impulse,
    mirror for bullish) stay parked at the leg's origin, so the eventual reversal
    CHoCH fires late and at a stale level. When enabled, a *trigger* pulls those
    references to a *local* level mid-move WITHOUT flipping `trend` (so the
    reversal lands locally), via `reanchor_opposite`. Triggers:

    - `"displacement"`: a 3-candle fair-value gap (`_common.find_fvg`) in the
      trend direction re-anchors to the gap's reclaim edge.
    - `"chain"`: `reanchor_chain_threshold` (default `3`) BOS state-advances
      within the current leg (minor LH/HL pullbacks do not interrupt the count;
      only a trend change does) re-anchor to the most recent in-leg counter-
      extreme.

    `"off"` preserves the original behavior exactly. The re-anchor only ever
    *tightens* the reversal reference (never loosens it or lands on the wrong
    side of price), and leaves the staircase floor and continuation-BOS logic
    untouched. (Staging the skipped intermediate BOS of an impulse is a deferred
    follow-up; this re-anchors only the reversal references.)

    `reanchor_chain_establish_only` (default `False`) restricts the `"chain"`
    trigger to *establishing* a reversal reference that has gone blind (the
    opposite-side `validated_choch_<side>` is `None`, as in a clean impulse that
    nulled it), never *tightening* one that already exists. The chain trigger
    exists for the blind-impulse case; when a fresh `validated_choch_<side>` was
    just promoted from a real pullback, tightening it down to a shallower in-leg
    extreme degrades the CHoCH reference to a weak pullback (so a small reclaim
    fires a CHoCH that should have needed the genuine pullback). With this set,
    a present reference is left for the staleness re-anchor to tighten only once
    it is actually stale; the blind-impulse establish case (which the chain was
    added for) is unaffected.

    `reanchor_min_price_gap_pct` (default `None` = off) guards the *output* of
    every re-anchor trigger (chain, stale, displacement): `reanchor_opposite`
    refuses to set the reversal reference to a local extreme closer than this
    fraction to current price. A reference sitting almost on top of price is
    hair-trigger -- a trivial bounce confirms a CHoCH mid-range that then
    immediately fails -- so requiring a minimum gap makes breaking the
    re-anchored level a real reversal rather than noise.

    `bos_pullback_max_wick_pct` (default `None` = off) filters the pullback pivot
    that *confirms* a BOS. A BOS confirms when a pivot forms in the opposite
    direction (a high pivot for a bearish BOS, a low for a bullish BOS); with a
    small swing lookback that pivot can be a single-candle **wick** (the candle
    spikes to the extreme intrabar but its body closes far away), so the BOS is
    confirmed by a "pullback" that never really retraced. When set, the pullback
    pivot candle's pivot-side wick (`_pullback_quality_ok`) must be at most this
    fraction of its range; a wick-only spike does not confirm and the pending BOS
    is kept alive so a *later, real* pullback confirms it instead (or it never
    confirms if none forms). Because the confirming pullback also seeds the
    CHoCH `candidate_choch_<side>`, this makes the reversal reference anchor to a
    genuine pullback too -- the filter propagates correctly into CHoCH detection
    rather than being a cosmetic mark drop.

    `stage_wick_rejected_bos` (default `False`) is the *additive* complement to
    `bos_pullback_max_wick_pct`: when a continuation advance is confirmed only by a
    wick pullback (rejected above) and no *real* pullback ever confirms it before
    the trend flips, the state machine emits no BOS even though the leg closed
    beyond the staircase floor -- a visibly-missing mark. When set, that break gets
    an **additive** `BREAK_OF_STRUCTURE` (once per pending BOS, at the break's close
    referencing the floor), staged and deduped against the real BOS at the end like
    the impulse staging. It never touches the state machine or CHoCH promotion (it
    does not seed `candidate_choch_<side>`), so -- unlike relaxing the wick filter,
    which cascades trend state and can corrupt a later reversal CHoCH -- it cannot
    change the CHoCH sequence; it only fills a genuinely-missing mark. A pending BOS
    that later gets a real pullback still emits its normal BOS (deduping the staged
    mark). With the flag off the output is byte-for-byte identical.

    `bos_leg_origin_choch_ref` (default `False`) promotes the **leg origin** of a
    confirmed BOS -- the extreme the breaking leg launched from
    (`_PendingBOS.pullback_ref`: the low a bullish leg rose from / the high a
    bearish leg dropped from) -- directly to the opposite `validated_choch_<side>`
    at BOS *emission*, without waiting for the continuation gate: the close-break
    plus a confirming pullback is itself the continuation evidence.

    - **Every emitted BOS refreshes** the reference to its own leg origin,
      replacing the current one unconditionally -- even to a looser (more
      distant) level. Structure wins over a re-anchored local extreme, and the
      reference always reflects the *most recent* confirmed break's origin.
    - Re-anchors (`reanchor_opposite`, every trigger) refuse to overwrite a
      *structural* reference while it remains **reachable** -- within the
      release gap of current price (`None` = always immune). The gap is
      `bos_leg_origin_release_gap_pct` (a fixed fraction of price) or, when
      `bos_leg_origin_release_gap_atr` is set (taking precedence),
      N x the series' mean true-range% -- volatility-normalized, so "reachable"
      means the same number of typical candles on every asset/timeframe. A
      fixed percentage is worth ~8 ATR on a calm 30m chart but under 1 ATR on
      a volatile daily (measured 2026-07-03: 4% pinned three whipsaw
      CHoCH/`CHOCH_FAILED` pairs across the BTC 30m June drop that N=3
      resolves into one bearish CHoCH plus a BOS staircase, and never held on
      SOL D1; N in [2, 3] measured as a stable plateau, N=4 reverts to the
      fixed-pct behavior on fine timeframes).
      So a stale-window slide cannot ratchet the reversal reference
      away from the genuine leg origin (e.g. an H4 CHoCH firing at a sliding
      local low instead of the fundo the leg actually launched from). Once the
      leg has run away beyond the gap, the re-anchor regains authority: holding
      the CHoCH hostage to an unreachable level re-opens the stuck-trend
      pathology (an impulsive leg can emit no BOS for months, so the refresh
      alone cannot keep the reference local). (A stricter variant -- promote
      only over blind/re-anchored references and let a structural one keep the
      continuation gate, with no release -- was measured and rejected: the
      first structural reference pins for the whole leg and coarse timeframes
      lose entire reversal sequences.)
    - Under this flag a re-anchor writes its synthetic level ONLY into
      `validated_choch_<side>`; the trailing `active_<side>` and the pullback
      candidate keep their genuine swing pivots. Otherwise the re-anchor level
      would feed the next BOS's `pullback_ref` snapshot and be laundered into a
      "structural" leg origin at emission (measured: an M30 leg-origin ref of
      63650 -- a stale-window artifact -- instead of the genuine 65469 fundo).
    - The continuation-gated candidate promotion still runs on top (a genuine
      continuation tightens the reference to the newer post-BOS pullback until
      that BOS's own emission refreshes it).
    - A pending BOS **killed by an origin reclaim** also promotes: when the
      next opposite pivot is already beyond the pending BOS's `pullback_ref`
      (no valid pullback ever confirmed the close-break), the state machine
      had still treated the advance as real (staircase/leg extremes
      ratcheted), and the reclaim of the leg origin is precisely the
      conservative reversal being missed -- so the origin is promoted before
      the pending BOS is discarded and the CHoCH check on that same pivot
      evaluates against it. Without this, the reference stays pinned to a
      stale far-off level and the reversal degrades into sweeps (the ETHUSDT
      H1 2026-06-06 case: leg origin 1618.85 never promoted after its
      pullback was wick-rejected, reference stuck at 1793.66, the whole rally
      to 1721 labeled sweeps with no CHoCH).
    - A **still-pending BOS contributes its leg origin to the CHoCH reference
      chain** (`validated or pending.pullback_ref or choch_origin or
      active_<side>`): while every pullback attempt is wick-rejected the
      pending stays alive and neither emission nor the reclaim kill has
      promoted yet -- without this, a side blinded by a prior CHoCH (validated
      and origin both `None`, e.g. after a fallback-triggered CHoCH that armed
      no origin) falls back to the trailing `active_<side>` and a shallow
      reclaim fires a premature CHoCH (the ETHUSDT H1 2026-06-25 case: CHoCH
      at the wick-rejected 1629.15 LH while the pending BOS carried the
      genuine 1692 leg origin). `validated` still outranks the pending origin
      so the staleness re-anchor keeps its authority over a long-lived
      pending. A CHoCH triggered via the pending origin counts as
      validated-triggered (it arms the opposite blind-spot origin).
    - The **`active_<side>` cold-start fallback is suppressed while an
      unconfirmed CHoCH's origin is armed** (`bear_choch_origin` /
      `bull_choch_origin`): the fallback exists for the bootstrap phase only,
      and inside the provisional post-CHoCH window the designed reversal exit
      is `CHOCH_FAILED` at the origin -- letting the fallback fire a CHoCH at
      a shallow trailing LH/HL undercuts that at a far weaker level (the
      SOLUSDT H1 2026-06-23 case: a fully-blind side -- the prior bearish
      CHoCH was itself fallback-triggered after a `CHOCH_FAILED` reset, so
      nothing was armed or promoted -- fired a premature bullish CHoCH at the
      69.63 LH and it failed the next day, while the CHoCH origin sat at
      74.97). Structural references (validated/pending/blind-spot origin)
      still apply if present; once the origin retires (confirming BOS or
      trend flip) the fallback is available again.

    Provenance is tracked per side (`validated_choch_<side>_structural`), reset
    when a CHoCH/`CHOCH_FAILED` consumes the reference. With the flag off the
    output is byte-for-byte identical.

    `choch_weak_ref_persistence_candles` (default `None` = off) is the
    **new-cycle barrier**: a CHoCH about to fire against a *weak* reference
    uses this persistence instead of `persistence_candles`. A reference is
    weak when it is a synthetic re-anchor level (`validated_choch_<side>`
    present but not structural -- only `reanchor_opposite` writes those) or
    the trailing `active_<side>` cold-start fallback; it is *structural* (base
    persistence, no delay) when it is a leg origin, a continuation-promoted
    candidate, a live pending BOS's origin, or the blind-spot
    `choch_origin_<side>` -- levels a leg actually launched from. Weak
    references sit at local extremes, so a brief poke through one is often
    just a sweep; demanding a longer sustained hold keeps those pokes from
    flipping the trend and starting a dirty cycle (they are reported as
    `LIQUIDITY_SWEEP` instead, or the CHoCH simply confirms later once a
    window does hold). The `CHOCH_FAILED` check always keeps the base
    persistence: it is the escape valve that undoes a wrong cycle, and
    delaying it holds the wrong trend longer. A genuine reversal off a weak
    reference still fires -- a real move holds well past the barrier -- so the
    cost is bounded at a few candles of confirmation delay. With the value
    `None` the output is byte-for-byte identical.

    `bos_leg_origin_min_pullback_atr` (default `None` = off; requires
    `bos_leg_origin_choch_ref`) is the **shallow-pullback leg-origin
    promotion**. The leg origin a BOS promotes to the opposite CHoCH reference
    is normally the trailing pivot at the state-advance (`active_high` for a
    bearish BOS / `active_low` for a bullish one) -- the *immediate* pullback
    high/low. When that immediate pullback is shallow -- its height
    (`active_high - active_low`) is less than N x the series' mean true-range%%
    of price -- it is a minor secondary high/low well inside the correction, so
    the CHoCH line ends up at a small pivot rather than the correction's visible
    top/bottom. In that case the origin is promoted instead to the correction's
    *extreme* pivot (`pending_high`/`pending_low`, already the most extreme high/
    low accumulated for the leg), but only when that extreme is genuinely beyond
    the immediate pullback. The reference then sits at the visible leg top; and
    because it is higher/lower, a premature poke through the shallow level is a
    sweep and the reversal CHoCH fires once price reclaims the true extreme (the
    AAVEUSDT H1 2026-07-02 case: bullish CHoCH ref 86.59 -> 87.82, firing 07-03
    on the reclaim instead of 07-02 on the poke that fell straight back). Only
    the promoted origin changes; the state machine, trailing references, and
    continuation gate are untouched. With `None` the output is byte-for-byte
    identical.

    `stale_reanchor_candles` (default `None` = off) is a separate *staleness*
    re-anchor, independent of `reanchor_mode`: when the trend runs this many
    candles past its last BOS / trend flip (`last_advance_index`, set in `emit`)
    without a fresh one, the reversal reference is pulled to the most recent
    local swing extreme over a trailing window (the recent high a bearish leg
    must reclaim / the recent low a bullish leg must lose) so a CHoCH can confirm
    locally and a new cycle can begin -- WITHOUT flipping `trend`. Same tightening
    discipline as the triggers above; a confirming CHoCH/BOS resets the counter.
    Targets the long-stuck-cycle pathology on coarse timeframes (a leg whose
    reversal reference stays pinned at the origin while price ranges/recovers).

    `choch_failed_fallback_suppress_candles` (default `None` = off) is the
    **post-failure fallback suppression**. A failed-CHoCH flip arms no
    blind-spot origin (one-shot, anti-ping-pong), so the cold-start
    `active_<side>` fallback -- suppressed while the origin was armed --
    becomes live again the moment a `CHOCH_FAILED` confirms, and a brief
    bounce can flip the trend right back off a hair-trigger trailing level
    (the BTCUSDT H1 2026-06-25 case: a fallback bullish CHoCH at the 61256
    trailing LH one day after the previous bullish CHoCH failed, mid-crash,
    which turned the final flush to 58030 into a sweep instead of a bearish
    BOS). When set, the fallback stays suppressed for this many candles after
    a *same-direction* `CHOCH_FAILED`; structural/validated references are
    untouched, so a genuine reversal (which promotes a leg origin via BOS)
    still fires. With `None` the output is byte-for-byte identical.

    `stage_choch_failed_window_bos` (default `False`) is the **failed-CHoCH
    window retro-staging**. While a CHoCH awaits its confirming BOS the trend
    is flipped, so a new extreme in the *resumed* direction (a lower low under
    a provisional bullish CHoCH / higher high under a bearish one) prints as a
    `LIQUIDITY_SWEEP`; when the CHoCH then *fails*, that trend never ended and
    those staircase breaks were genuine continuations the wrong flip ate (the
    BTCUSDT H1 2026-06-18..25 crash: one bearish BOS then only sweeps). When
    set, each such break (recorded against the previous recorded extreme,
    seeded from the pre-CHoCH reported-floor stash) is staged at the
    `CHOCH_FAILED` as an additive `BREAK_OF_STRUCTURE` of the resumed trend --
    merged and deduped like the impulse staging, close-break re-anchored at the
    composition level (wick-only ones dropped) -- and the eaten extremes are
    folded into the restored staircase floors (the gate takes the most extreme
    pivot, the reported floor only a close-confirmed one) so the next real
    continuation references the true prior formed extreme instead of a level
    the staged marks already broke. Recorded breaks are discarded when the
    CHoCH is confirmed instead (they were retraces of a genuine reversal) or
    when a fresh CHoCH re-arms the window. With `False` the output is
    byte-for-byte identical.
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        persistence_candles: int = 12,
        confluence_filter: bool = False,
        reanchor_mode: str = "off",
        reanchor_chain_threshold: int = 3,
        reanchor_chain_establish_only: bool = False,
        reanchor_min_price_gap_pct: float | None = None,
        stale_reanchor_candles: int | None = None,
        impulse_bos_displacement_pct: float | None = None,
        bos_pullback_max_wick_pct: float | None = None,
        stage_wick_rejected_bos: bool = False,
        bos_leg_origin_choch_ref: bool = False,
        bos_leg_origin_release_gap_pct: float | None = None,
        bos_leg_origin_release_gap_atr: float | None = None,
        bos_leg_origin_min_pullback_atr: float | None = None,
        bos_leg_origin_require_close_break: bool = False,
        bos_floor_require_close_break: bool = False,
        choch_weak_ref_persistence_candles: int | None = None,
        emit_provisional_bos: bool = False,
        emit_provisional_choch: bool = False,
        choch_origin_leg_extreme: bool = False,
        choch_fizzle_reclaim_candles: int | None = None,
        choch_failed_fallback_suppress_candles: int | None = None,
        stage_choch_failed_window_bos: bool = False,
    ) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        if reanchor_mode not in _REANCHOR_MODES:
            raise ValueError(f"reanchor_mode must be one of {sorted(_REANCHOR_MODES)}")
        if reanchor_chain_threshold < 1:
            raise ValueError("reanchor_chain_threshold must be at least 1")
        if reanchor_min_price_gap_pct is not None and reanchor_min_price_gap_pct <= 0:
            raise ValueError("reanchor_min_price_gap_pct must be positive")
        if stale_reanchor_candles is not None and stale_reanchor_candles < 1:
            raise ValueError("stale_reanchor_candles must be at least 1")
        if choch_fizzle_reclaim_candles is not None and choch_fizzle_reclaim_candles < 1:
            raise ValueError("choch_fizzle_reclaim_candles must be at least 1")
        if (
            choch_failed_fallback_suppress_candles is not None
            and choch_failed_fallback_suppress_candles < 1
        ):
            raise ValueError("choch_failed_fallback_suppress_candles must be at least 1")
        if impulse_bos_displacement_pct is not None and impulse_bos_displacement_pct <= 0:
            raise ValueError("impulse_bos_displacement_pct must be positive")
        if bos_pullback_max_wick_pct is not None and not 0 < bos_pullback_max_wick_pct <= 1:
            raise ValueError("bos_pullback_max_wick_pct must be in (0, 1]")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles
        self._confluence_filter = confluence_filter
        self._reanchor_mode = reanchor_mode
        self._reanchor_chain_threshold = reanchor_chain_threshold
        self._reanchor_chain_establish_only = reanchor_chain_establish_only
        self._reanchor_min_price_gap_pct = reanchor_min_price_gap_pct
        self._stale_reanchor_candles = stale_reanchor_candles
        self._impulse_bos_displacement_pct = impulse_bos_displacement_pct
        self._bos_pullback_max_wick_pct = bos_pullback_max_wick_pct
        self._stage_wick_rejected_bos = stage_wick_rejected_bos
        if bos_leg_origin_release_gap_pct is not None and bos_leg_origin_release_gap_pct <= 0:
            raise ValueError("bos_leg_origin_release_gap_pct must be positive")
        if bos_leg_origin_release_gap_atr is not None and bos_leg_origin_release_gap_atr <= 0:
            raise ValueError("bos_leg_origin_release_gap_atr must be positive")
        if bos_leg_origin_min_pullback_atr is not None and bos_leg_origin_min_pullback_atr <= 0:
            raise ValueError("bos_leg_origin_min_pullback_atr must be positive")
        if (
            choch_weak_ref_persistence_candles is not None
            and choch_weak_ref_persistence_candles < 1
        ):
            raise ValueError("choch_weak_ref_persistence_candles must be at least 1")
        self._bos_leg_origin_choch_ref = bos_leg_origin_choch_ref
        self._bos_leg_origin_release_gap_pct = bos_leg_origin_release_gap_pct
        self._bos_leg_origin_release_gap_atr = bos_leg_origin_release_gap_atr
        self._bos_leg_origin_min_pullback_atr = bos_leg_origin_min_pullback_atr
        self._bos_leg_origin_require_close_break = bos_leg_origin_require_close_break
        self._bos_floor_require_close_break = bos_floor_require_close_break
        self._choch_weak_ref_persistence_candles = choch_weak_ref_persistence_candles
        self._emit_provisional_bos = emit_provisional_bos
        self._emit_provisional_choch = emit_provisional_choch
        self._choch_origin_leg_extreme = choch_origin_leg_extreme
        self._choch_fizzle_reclaim_candles = choch_fizzle_reclaim_candles
        self._choch_failed_fallback_suppress_candles = choch_failed_fallback_suppress_candles
        self._stage_choch_failed_window_bos = stage_choch_failed_window_bos
        # The state-machine trend after the most recent `detect()` call
        # (mirrors `SwingStructureDetector.final_trend`). The single source of
        # truth for "the standing trend": unlike the last emitted event's
        # `direction`, it is unaffected by descriptive HL/LH labels,
        # LIQUIDITY_SWEEPs (whose `direction` is the pivot/wick side, not the
        # trend) or provisional live-edge marks (emitted from final state,
        # never mutating it), and it resolves CHOCH_FAILED correctly (the
        # trend reverts on failure). NEUTRAL until `detect()` runs.
        self.final_trend: MarketDirection = MarketDirection.NEUTRAL

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        # Effective structural-reference release gap: when
        # `bos_leg_origin_release_gap_atr` is set, the gap is N x the series'
        # mean true-range% -- the same nominal N then means the same number of
        # "typical candles" of distance on every asset/timeframe, where a fixed
        # percentage is worth ~8 ATR on a calm 30m chart but under 1 ATR on a
        # volatile daily (measured 2026-07-03: the fixed 4% pinned whipsaw
        # CHoCH pairs on BTC 30m and never held on SOL D1). Falls back to the
        # fixed `bos_leg_origin_release_gap_pct` when unset (or the series is
        # too short to measure a range).
        # Mean true-range as a fraction of price, computed once when any
        # volatility-normalized feature needs it (the release gap and/or the
        # shallow-pullback leg-origin promotion). `None` when unused or the
        # series is too short to measure a range.
        mean_tr_pct: float | None = None
        if (
            self._bos_leg_origin_release_gap_atr is not None
            or self._bos_leg_origin_min_pullback_atr is not None
        ) and len(candles) > 1:
            mean_tr_pct = fmean(
                max(
                    curr.high - curr.low,
                    abs(curr.high - prev.close),
                    abs(curr.low - prev.close),
                )
                / curr.close
                for prev, curr in zip(candles, candles[1:], strict=False)
            )
        release_gap = self._bos_leg_origin_release_gap_pct
        if self._bos_leg_origin_release_gap_atr is not None and mean_tr_pct is not None:
            release_gap = self._bos_leg_origin_release_gap_atr * mean_tr_pct

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}

        def confirms_break(
            start_index: int,
            end_index: int,
            level_price: float,
            *,
            bullish: bool,
            persistence: int | None = None,
        ) -> bool:
            return any(
                is_sustained_break(
                    candles,
                    index,
                    level_price,
                    bullish=bullish,
                    persistence_candles=persistence or self._persistence_candles,
                )
                for index in range(start_index, end_index + 1)
            )

        events: list[MarketStructure] = []
        # Staged impulse BOS (only when `impulse_bos_displacement_pct` is set).
        # On a clean impulsive leg the state machine advances at each lower low /
        # higher high but emits at most ONE deferred BOS (the surviving pending
        # BOS, confirmed at the next opposite pullback), so a sharp multi-step
        # move prints no intermediate staircase. These staged BOS mark each
        # advance whose displacement beyond the prior BOS level clears the
        # threshold; they are merged at the end and DEDUPED against the real
        # emitted BOS, so they only ever *add* marks where the state machine
        # produced none (the impulsive gaps). The state machine itself is
        # untouched -- with the flag off this list stays empty.
        staged_bos: list[MarketStructure] = []
        # Trailing references (most recent pivot of each kind); drive BOS
        # detection and HL/LH labels.
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        # Most extreme pivot of each side, promoted to active_<side> when the
        # opposite side breaks.
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        # The CHoCH reference levels. validated_choch_high is the swing high a
        # bullish CHoCH must break: the pullback (origin) of the most recent
        # *continuation-confirmed* bearish BOS. Mirror for validated_choch_low.
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        # Provenance of validated_choch_<side>: True when it was set
        # *structurally* (a continuation-gated pullback promotion, or a BOS
        # leg-origin promotion under `bos_leg_origin_choch_ref`), False when it
        # was written by a re-anchor (stale/chain) or is unset. Only consulted
        # when `bos_leg_origin_choch_ref` is enabled: a structural reference is
        # authoritative -- re-anchors must not overwrite it, and a newer BOS's
        # leg origin replaces it only through the continuation gate.
        validated_choch_high_structural = False
        validated_choch_low_structural = False
        # The pullback (origin) of the most recent BOS in each direction, still
        # *provisional*: promoted to validated_choch_<side> only once a
        # continuation (the next BOS in that direction) confirms its BOS. If
        # price reverses before that continuation, the BOS is never confirmed
        # and its pullback never anchors a CHoCH.
        candidate_choch_high: Pivot | None = None
        candidate_choch_low: Pivot | None = None
        # One-shot blind-spot fallback. When a CHoCH fires it consumes the
        # validated reference (reset to None); rebuilding the *reverse*
        # reference needs a fresh BOS + continuation, during which a failed
        # reversal would otherwise leave the trend stuck. choch_origin_<side>
        # is the extreme of the leg the CHoCH just reversed (set only by a
        # *validated*-triggered CHoCH, one-shot, so the chain cannot ping-pong),
        # used as the CHoCH reference until a validated one is rebuilt.
        choch_origin_high: Pivot | None = None
        choch_origin_low: Pivot | None = None
        # Running extreme of the current leg, used to gate candidate -> validated
        # promotion. A bearish BOS's pullback is promoted only when a later low
        # makes a NEW LEG LOW (below bear_leg_low) -- not merely a lower-low
        # below that BOS's own pivot -- so a pullback-BOS formed during a
        # retrace (which never extends the leg) cannot ratchet the CHoCH
        # reference down to a less significant level. Seeded/reset at each trend
        # flip (CHoCH) and at the NEUTRAL bootstrap; mirror for bull_leg_high.
        bear_leg_low: float | None = None
        bull_leg_high: float | None = None
        # The price level of the previous confirmed BOS in the current trend
        # (the low established by the last bearish BOS / high by the last
        # bullish BOS). A new continuation BOS must *extend* the staircase --
        # break beyond this level -- so a break of a higher trailing low (lower
        # trailing high) formed during a retrace, which never beats the previous
        # BOS, is not a structural BOS. Reset to None at each trend flip (CHoCH);
        # the first BOS of a leg (None) is unconstrained.
        last_bear_bos_low: float | None = None
        last_bull_bos_high: float | None = None
        # The extreme of the *previous* BOS in the current leg, used as the
        # emitted `reference_price_level` (the formed level the continuation
        # broke). Seeded at a CHoCH with the CHoCH's *confirming* extreme (the
        # fundo/topo the reversal formed -- `price` at the trend flip), so the
        # FIRST BOS of the leg references that structural low/high and, via the
        # close-break re-anchor, confirms only on a close beyond it -- rather than
        # the trailing `active_<side>` that ratchets to a shallow higher-low /
        # lower-high during the pullback (the "reference climbs with trailing"
        # bug). Reset to `None` for the *opposite* (irrelevant) side at each flip.
        prev_bear_bos_extreme: float | None = None
        prev_bull_bos_extreme: float | None = None
        # The *origin* of an unconfirmed CHoCH: the swing the CHoCH move launched
        # from (the active low at a bullish CHoCH / active high at a bearish
        # CHoCH). While set, the CHoCH is provisional -- a break back through
        # this level (sustained) before a confirming BOS is a *failed* CHoCH
        # (CHOCH_FAILED): the reversal is invalidated and structure flips back.
        # Cleared once the first same-direction BOS confirms the CHoCH (it can no
        # longer fail), or when the trend flips again. Set only by a *normal*
        # CHoCH, never by a failed-CHoCH flip -- one-shot, so failures cannot
        # ping-pong.
        bull_choch_origin: Pivot | None = None
        bear_choch_origin: Pivot | None = None
        # Post-failure fallback suppression
        # (`choch_failed_fallback_suppress_candles`): the pivot-loop index at
        # which the most recent *real* CHOCH_FAILED of each direction was
        # emitted (the additive fizzle marker never sets these -- its trend
        # never flipped). A failed-CHoCH flip arms no blind-spot origin
        # (one-shot, anti-ping-pong), so the cold-start `active_<side>`
        # fallback -- suppressed while the origin was armed -- becomes live
        # again the moment the failure confirms, and a brief bounce can flip
        # the trend right back via a hair-trigger trailing level (the BTC H1
        # 06-25 case: a fallback CHoCH one day after the previous bullish
        # CHoCH failed, mid-crash, which ate the flush BOS). While within the
        # suppression window after a same-direction failure, the fallback
        # stays off; structural/validated references are never affected.
        bull_choch_failed_index = -1
        bear_choch_failed_index = -1
        # Counter-trend staircase breaks recorded while a CHoCH is provisional
        # (`stage_choch_failed_window_bos`): lows made under a provisional
        # bullish CHoCH / highs under a bearish one, each beyond the previous
        # recorded extreme (seeded from the pre-CHoCH reported-floor stash).
        # Consumed at CHOCH_FAILED -- staged as additive continuation BOS of
        # the resumed trend (the marks the wrong flip ate) and folded into the
        # restored staircase floors; discarded when the CHoCH is instead
        # confirmed (they were mere retraces of a genuine reversal) or when a
        # fresh CHoCH re-arms the window.
        bull_window_lows: list[_EatenBreak] = []
        bear_window_highs: list[_EatenBreak] = []
        # State for the *fast-fizzle* invalidation marker
        # (`choch_fizzle_reclaim_candles`, applied additively at the end of
        # `detect`): the *standing* CHoCH -- the most recent trend-defining
        # CHANGE_OF_CHARACTER whose line is still open -- as its own broken
        # reference level (the LH a bearish CHoCH broke / HL a bullish one broke,
        # a `Pivot` so its timestamp anchors the failed-CHoCH line), the candle
        # index it fired at, and its direction. Unlike the `*_choch_origin`
        # (retired once a confirming BOS makes the CHoCH no longer provisional),
        # this tracks the CHoCH while its *line* stands -- even after a
        # continuation BOS confirmed it (in the SOL M15 case that BOS was
        # wick-only and dropped from the chart, so the line looks unbroken). Set
        # at every CHoCH emission; cleared at every CHOCH_FAILED (the line ended).
        # If the standing CHoCH's reversal fizzles -- price reclaims its own
        # broken level (sustained close) within `choch_fizzle_reclaim_candles` --
        # an additive CHOCH_FAILED disregards the stale line *without* flipping
        # the state-machine trend (a real flip cascades the whole downstream CHoCH
        # sequence; a reclaim *after* the window is genuine follow-through).
        standing_choch_ref: Pivot | None = None
        standing_choch_index = -1
        standing_choch_dir: MarketDirection | None = None
        # The pre-CHoCH staircase floor of the trend that resumes if the current
        # provisional CHoCH *fails*. A CHoCH nulls the reversing trend's BOS
        # staircase (`last_bear_bos_low`/`last_bull_bos_high`) to seed the new
        # leg, but a failed CHoCH means that trend never actually ended -- it
        # must resume from its genuine last BOS extreme, not from the (often
        # higher-low / lower-high) CHoCH origin, or a non-extending BOS could
        # print above the previous same-direction BOS. Stashed when the CHoCH
        # fires, restored on failure, discarded once a confirming BOS makes the
        # reversal real. Lifecycle tied 1:1 to the matching `*_choch_origin`.
        pre_choch_bear_bos_low: float | None = None
        pre_choch_bull_bos_high: float | None = None
        # Companion stash for the *reported* staircase floor tracker
        # (`prev_<side>_bos_extreme`). The gate stash above holds the state
        # machine's staircase, which legitimately ratchets on wick-only breaks;
        # restoring the reported tracker *from the gate* on a failed CHoCH
        # would launder such a wick into the next continuation's reported
        # reference (the "second-top wick supersedes the real top" case). Only
        # read when `bos_floor_require_close_break` is set; lifecycle mirrors
        # the gate stash exactly.
        pre_choch_bear_bos_extreme: float | None = None
        pre_choch_bull_bos_extreme: float | None = None
        # Provenance of the current staircase floor (`last_<side>_bos_<extreme>`):
        # True when it is a genuine BOS extreme (a BOS advance, or a
        # failed-CHoCH restore of the resumed trend's real floor), False when it
        # was merely *seeded* at a fresh CHoCH with that CHoCH's own level. Only
        # read by the provisional-BOS emission (`emit_provisional_bos`): a
        # provisional marks a *continuation* beyond a real BOS step, never the
        # first break of a new leg -- which is the CHoCH itself, and drawing a
        # provisional BOS on the CHoCH-seed level just doubles the CHoCH line.
        bull_floor_from_bos = False
        bear_floor_from_bos = False
        pending_bos: _PendingBOS | None = None
        last_bullish_bos_price: float | None = None
        last_bullish_bos_origin: float | None = None
        last_bearish_bos_price: float | None = None
        last_bearish_bos_origin: float | None = None
        trend = MarketDirection.NEUTRAL
        # Candle index of the previous pivot of each kind, used to bound the
        # break-candle search below to the leg between consecutive pivots of
        # that kind. -1 (no previous pivot) is never read: every branch below
        # that performs a search is only reachable once active_<side>/
        # validated_choch_<side> is set, which happens no earlier than the
        # first pivot of that kind, i.e. once these are no longer -1.
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1
        # --- Online re-anchor state (flavor B; only used when reanchor_mode is
        # not "off"). On a strong impulsive leg with few/no opposite pullbacks,
        # the *opposite-side* references (`active_high`/`validated_choch_high`
        # for a bearish impulse, mirror for bullish) stay parked at the top/
        # bottom of the leg, so the eventual reversal CHoCH fires late and at a
        # stale level. A trigger (displacement FVG, or a pending-BOS chain) pulls
        # those references to a *local* level mid-move WITHOUT flipping `trend`,
        # so the reversal lands locally and structure resumes. The staircase
        # floor and continuation BOS logic are untouched.
        prev_any_pivot_index = -1
        # Count of BOS state-advances *within the current leg* (trigger
        # "chain"). `chain_dir` is the leg's advance direction. Minor pullback
        # pivots (LH/HL labels) within an impulse do NOT reset it -- only a
        # genuine trend change does, implicitly: the opposite leg's first advance
        # finds `chain_dir` mismatched and restarts the count at 1 (within a
        # single-direction leg only same-side advances occur, since a counter-
        # trend break is a sweep/CHoCH, never an opposite advance). At the
        # threshold the re-anchor level is the most recent in-leg counter-extreme
        # (the local high of a bearish advance / low of a bullish advance), and
        # the count resets so a long leg can re-anchor again as it extends.
        bos_chain = 0
        chain_dir = MarketDirection.NEUTRAL
        # Index of the candle that last *advanced* the cycle (a BOS) or *flipped*
        # it (a CHoCH / CHOCH_FAILED), set in `emit`. Drives the staleness
        # re-anchor (`stale_reanchor_candles`): a cycle that runs too long past
        # this index without a fresh advance/flip is stale (e.g. a bearish leg
        # whose bullish reversal reference stays pinned at the leg origin while
        # price grinds back up, so the eventual CHoCH only fires far overhead).
        last_advance_index = -1

        def reanchor_opposite(level: float, ts: datetime, *, current_price: float) -> bool:
            """Pull the stale *opposite-side* references to a local `level`
            (flavor B), without touching `trend` or the staircase.

            In a bearish trend the high-side references (`active_high`,
            `validated_choch_high`, `choch_origin_high`) are collapsed *down* to
            `level`; in a bullish trend the low-side references are collapsed
            *up*. It either *tightens* an existing reversal reference or
            *establishes* one when the impulse has nulled them all (the
            blind-spot the re-anchor exists to fix): it re-anchors when `level`
            is on the correct side of `current_price` (above it for bearish,
            below for bullish) and -- if any reference still exists -- does not
            loosen it (lower than the most stale high / higher than the most
            stale low). `candidate_choch_<side>` is cleared too, so a stale
            candidate cannot later promote back to the old extreme. Returns
            whether it moved anything.
            """
            nonlocal active_high, active_low
            nonlocal validated_choch_high, validated_choch_low
            nonlocal validated_choch_high_structural, validated_choch_low_structural
            nonlocal choch_origin_high, choch_origin_low
            nonlocal candidate_choch_high, candidate_choch_low
            new = Pivot(price=level, timestamp=ts)
            # Minimum-gap guard (applies to every trigger -- chain, stale,
            # displacement): a local extreme sitting almost on top of price makes
            # the reversal reference hair-trigger, so a trivial bounce confirms a
            # CHoCH mid-range that then immediately fails. Require the re-anchor
            # level to be at least `reanchor_min_price_gap_pct` away from current
            # price, so breaking it constitutes a real reversal.
            min_gap = self._reanchor_min_price_gap_pct
            if trend is MarketDirection.BEARISH:
                if level <= current_price:
                    return False
                if min_gap is not None and (level - current_price) / current_price < min_gap:
                    return False
                # A *structural* reference (a BOS leg origin or a promoted
                # pullback) is authoritative while it remains *reachable*:
                # re-anchors must not slide it (see `bos_leg_origin_choch_ref`).
                # But once the leg has run away -- the reference sits farther
                # than the release gap from current price --
                # holding the CHoCH hostage to that unreachable level re-opens
                # the stuck-trend pathology (e.g. an impulsive drop that emits
                # no BOS for months), so the re-anchor may act again.
                if (
                    self._bos_leg_origin_choch_ref
                    and validated_choch_high is not None
                    and validated_choch_high_structural
                ):
                    if (
                        release_gap is None
                        or (validated_choch_high.price - current_price) / current_price
                        <= release_gap
                    ):
                        return False
                # Only *tighten* the effective reversal reference (the one the
                # CHoCH actually uses, in priority order), never *loosen* it. Using
                # max(all refs) let a far-away trailing `active_high` mask a lower
                # `validated_choch_high`, so a staleness re-anchor could push the
                # reversal reference *further* from price (loosening it) during a
                # deep in-trend pullback -- the opposite of the un-stick intent --
                # and clear the candidate that would have validated the genuine
                # reversal level.
                effective_high = validated_choch_high or choch_origin_high or active_high
                if effective_high is not None and level >= effective_high.price:
                    return False
                # Under `bos_leg_origin_choch_ref`, a synthetic re-anchor level
                # lives ONLY in `validated_choch_<side>`: the trailing
                # `active_<side>` and the pullback candidate are genuine swing
                # pivots that feed the leg-origin snapshot (`pullback_ref`) of a
                # future BOS, and overwriting them here would launder the
                # re-anchor level into a "structural" leg origin at the next
                # emission.
                if not self._bos_leg_origin_choch_ref:
                    active_high = new
                    candidate_choch_high = None
                validated_choch_high = new
                validated_choch_high_structural = False
                choch_origin_high = None
                return True
            if trend is MarketDirection.BULLISH:
                if level >= current_price:
                    return False
                if min_gap is not None and (current_price - level) / current_price < min_gap:
                    return False
                # Mirror of the bearish branch: never slide a structural
                # reference while it remains reachable (see
                # `bos_leg_origin_choch_ref` and the release gap above).
                if (
                    self._bos_leg_origin_choch_ref
                    and validated_choch_low is not None
                    and validated_choch_low_structural
                ):
                    if (
                        release_gap is None
                        or (current_price - validated_choch_low.price) / current_price
                        <= release_gap
                    ):
                        return False
                effective_low = validated_choch_low or choch_origin_low or active_low
                if effective_low is not None and level <= effective_low.price:
                    return False
                # Mirror of the bearish branch: keep genuine pivots out of the
                # re-anchor's reach under `bos_leg_origin_choch_ref`.
                if not self._bos_leg_origin_choch_ref:
                    active_low = new
                    candidate_choch_low = None
                validated_choch_low = new
                validated_choch_low_structural = False
                choch_origin_low = None
                return True
            return False

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
            reference_timestamp: datetime | None = None,
            origin_price_level: float | None = None,
            reference_structural: bool | None = None,
        ) -> None:
            nonlocal last_advance_index
            if event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.CHOCH_FAILED,
            ):
                last_advance_index = index_by_timestamp[timestamp]
            events.append(
                MarketStructure(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    event=event,
                    direction=direction,
                    price_level=price_level,
                    reference_price_level=reference_price_level,
                    reference_timestamp=reference_timestamp,
                    origin_price_level=origin_price_level,
                    scope=StructureScope.INTERNAL,
                    reference_structural=reference_structural,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            # --- Trigger "displacement": a fair-value gap in the trend
            # direction, formed in the leg since the previous pivot, re-anchors
            # the stale opposite-side references to the gap's reclaim edge (the
            # last price before the imbalance). Runs before this pivot is
            # processed, so a reversal pivot here is already evaluated against
            # the local level. (BOS staging at the FVG is a deferred follow-up;
            # this only re-anchors the reversal references -- payoff "A".)
            if self._reanchor_mode == "displacement" and trend is not MarketDirection.NEUTRAL:
                fvg = find_fvg(
                    candles,
                    max(0, prev_any_pivot_index),
                    current_index,
                    bullish=trend is MarketDirection.BULLISH,
                )
                if fvg is not None:
                    fvg_c0_index, fvg_level = fvg
                    reanchor_opposite(
                        fvg_level,
                        candles[fvg_c0_index].timestamp,
                        current_price=candles[current_index].close,
                    )

            # --- Staleness re-anchor: the cycle has run `stale_reanchor_candles`
            # candles past its last BOS/CHoCH without a fresh one. Pull the stale
            # reversal reference to the most recent local swing extreme over a
            # trailing window (the recent high a bearish leg must reclaim / the
            # recent low a bullish leg must lose) so a CHoCH can confirm locally
            # instead of waiting for price to climb all the way back to the leg
            # origin. `reanchor_opposite` only tightens (and only to the correct
            # side of price), so this tracks the recent extreme as the range
            # unfolds; a confirming CHoCH/BOS resets the counter. Independent of
            # `reanchor_mode`.
            if (
                self._stale_reanchor_candles is not None
                and trend is not MarketDirection.NEUTRAL
                and last_advance_index >= 0
                and current_index - last_advance_index >= self._stale_reanchor_candles
            ):
                window_start = max(0, current_index - self._stale_reanchor_candles + 1)
                window = candles[window_start : current_index + 1]
                if trend is MarketDirection.BEARISH:
                    local = max(window, key=lambda c: c.high)
                    reanchor_opposite(
                        local.high, local.timestamp, current_price=candles[current_index].close
                    )
                else:
                    local = min(window, key=lambda c: c.low)
                    reanchor_opposite(
                        local.low, local.timestamp, current_price=candles[current_index].close
                    )

            if kind == "high":
                # Record a counter-trend staircase break while a bearish CHoCH
                # is provisional (see `stage_choch_failed_window_bos`): a high
                # pivot beyond the previous recorded extreme (seeded from the
                # pre-CHoCH reported floor) is a bullish continuation the flip
                # ate if the CHoCH later fails. Passive bookkeeping only -- the
                # pivot is still classified normally below.
                if (
                    self._stage_choch_failed_window_bos
                    and trend is MarketDirection.BEARISH
                    and bear_choch_origin is not None
                ):
                    prior_level = (
                        bear_window_highs[-1].pivot.price
                        if bear_window_highs
                        else (
                            pre_choch_bull_bos_extreme
                            if pre_choch_bull_bos_extreme is not None
                            else pre_choch_bull_bos_high
                        )
                    )
                    if prior_level is not None and price > prior_level:
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            prior_level,
                            bullish=True,
                        )
                        break_idx = (
                            close_idx
                            if close_idx is not None
                            else find_wick_break_index(
                                candles,
                                prev_high_pivot_index + 1,
                                current_index,
                                prior_level,
                                bullish=True,
                            )
                        )
                        bear_window_highs.append(
                            _EatenBreak(
                                pivot=pivot,
                                ref_level=prior_level,
                                timestamp=candles[break_idx].timestamp,
                                ref_closed=close_idx is not None,
                            )
                        )
                # A wick-only in-trend break (no candle closed beyond the
                # active reference) stays *pending*: the state must not advance
                # and the broken reference must stay frozen at its level (not
                # trail up to this pivot) so a later candle that *closes* beyond
                # it activates the BOS then.
                wick_only_break = False
                # --- Pending BEARISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BEARISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price < pb.price
                        and (last_bearish_bos_origin is None or price < last_bearish_bos_origin)
                    ):
                        if self._pullback_quality_ok(candles[current_index], high_pivot=True):
                            emit(
                                pending_bos.close_break_timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BEARISH,
                                pending_bos.breaking_pivot.price,
                                pending_bos.floor
                                if pending_bos.floor is not None
                                else pending_bos.ref_price,
                                origin_price_level=price,
                            )
                            last_bearish_bos_price = pending_bos.breaking_pivot.price
                            last_bearish_bos_origin = price
                            # This BOS's pullback (the confirming LH) is the
                            # *provisional* CHoCH reference; it is promoted to
                            # validated_choch_high only once a continuation (the
                            # next bearish BOS) confirms this BOS.
                            candidate_choch_high = pivot
                            # Leg-origin CHoCH promotion (see
                            # `bos_leg_origin_choch_ref`): the high this BOS's leg
                            # dropped from becomes the bullish-CHoCH reference at
                            # emission -- the close-break plus this confirming LH
                            # is itself the continuation evidence. Every emitted
                            # BOS *refreshes* the reference to its own leg origin
                            # (structure wins, even over a tighter level), so a
                            # structural reference never goes stale -- which is
                            # what makes barring re-anchors from sliding it safe.
                            if (
                                self._bos_leg_origin_choch_ref
                                and pending_bos.pullback_ref is not None
                            ):
                                validated_choch_high = pending_bos.pullback_ref
                                # Only a close-confirmed break makes the leg origin
                                # *structural*: a continuation that merely wicked the
                                # prior BOS level promotes it as a weak reference (so
                                # the new-cycle barrier governs the CHoCH and
                                # re-anchors may still slide it).
                                validated_choch_high_structural = pending_bos.floor_closed
                                choch_origin_high = None
                            # The bearish CHoCH is now confirmed by an *emitted*
                            # BOS (a state-advance alone leaves a still-pending BOS
                            # that may never emit, so the CHoCH could still fail):
                            # retire its origin and drop the stashed bullish ceiling.
                            bear_choch_origin = None
                            pre_choch_bull_bos_high = None
                            pre_choch_bull_bos_extreme = None
                            pending_bos = None
                        elif (
                            self._stage_wick_rejected_bos
                            and not pending_bos.staged
                            and pending_bos.floor is not None
                        ):
                            # Wick-only pullback: the filter (correctly) keeps this
                            # break out of the state machine / CHoCH promotion, so the
                            # reversal reference stays anchored to a genuine pullback
                            # and nothing cascades. But the continuation *did* happen
                            # -- the leg closed beyond the staircase floor -- so add an
                            # ADDITIVE mark for it (once), merged and deduped against
                            # the real BOS at the end like the impulse staging. Only
                            # *continuation* breaks (a real staircase `floor`, not the
                            # first-of-leg `ref_price` fallback which can be a stale
                            # far-off trailing level) are staged, so the mark always
                            # plots at the prior swing extreme it broke. The pending BOS
                            # stays alive so a later real pullback can still confirm it
                            # into the state machine (that emitted BOS then dedups this
                            # mark away).
                            pending_bos.staged = True
                            staged_bos.append(
                                MarketStructure(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    timestamp=pending_bos.close_break_timestamp,
                                    event=StructureEvent.BREAK_OF_STRUCTURE,
                                    direction=MarketDirection.BEARISH,
                                    price_level=pending_bos.breaking_pivot.price,
                                    reference_price_level=pending_bos.floor,
                                    scope=StructureScope.INTERNAL,
                                )
                            )
                        # else: wick-only pullback; keep the pending BOS alive so a
                        # later, real pullback can confirm it instead of this wick.
                    else:
                        # No valid pullback ever confirmed this close-break: this
                        # high pivot already reclaimed the leg origin itself
                        # (price > pullback_ref). The state machine treated the
                        # advance as real (staircase/leg extremes ratcheted), so
                        # under `bos_leg_origin_choch_ref` the reversal reference
                        # must follow: promote the leg origin the pending BOS
                        # carried before discarding it -- it is exactly the level
                        # this reclaim is a reversal *of*. Otherwise the CHoCH
                        # reference stays pinned to a stale far-off level and the
                        # reversal happening right now degrades into sweeps. The
                        # CHoCH check below runs on this same pivot against the
                        # promoted level.
                        if (
                            self._bos_leg_origin_choch_ref
                            and pb is not None
                            and price > pb.price
                        ):
                            validated_choch_high = pb
                            validated_choch_high_structural = True
                            choch_origin_high = None
                        pending_bos = None

                # Validated reference takes priority; choch_origin_high is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                # Under `bos_leg_origin_choch_ref`, a still-pending BOS (state
                # advanced on a close-break, awaiting its confirming pullback)
                # contributes its leg origin ahead of the blind-spot fallbacks:
                # while every pullback attempt is wick-rejected the pending
                # stays alive and neither emission nor the origin-reclaim kill
                # has promoted yet -- without this, a side blinded by a prior
                # CHoCH falls back to the trailing `active_high` and a shallow
                # reclaim fires a premature CHoCH (the ETHUSDT H1 2026-06-25
                # case: CHoCH at the wick-rejected 1629.15 LH while the pending
                # BOS carried the genuine 1692 leg origin). `validated` still
                # wins so the staleness re-anchor keeps its authority over a
                # long-lived pending.
                pending_leg_origin_high: Pivot | None = None
                if (
                    self._bos_leg_origin_choch_ref
                    and pending_bos is not None
                    and pending_bos.direction is MarketDirection.BEARISH
                ):
                    pending_leg_origin_high = pending_bos.pullback_ref
                via_validated = (
                    validated_choch_high is not None or pending_leg_origin_high is not None
                )
                # The trailing `active_high` cold-start fallback exists for the
                # bootstrap phase only (no structural reference built yet).
                # While an unconfirmed bearish CHoCH's origin is armed
                # (`bear_choch_origin`, awaiting its confirming BOS), the
                # bullish exit from that provisional structure is CHOCH_FAILED
                # at the origin -- letting the fallback fire a CHoCH at a
                # shallow trailing LH undercuts it at a far weaker level (the
                # SOLUSDT H1 2026-06-23 case: premature CHoCH at the 69.63 LH,
                # failed next day, while the CHoCH origin sat at 74.97). The
                # structural references above still apply if they exist.
                fallback_active_high = active_high
                if self._bos_leg_origin_choch_ref and bear_choch_origin is not None:
                    fallback_active_high = None
                # Post-failure suppression: a failed-CHoCH flip arms no origin
                # (one-shot, anti-ping-pong), so the suppression above lapses
                # the moment a bullish CHoCH fails -- and the hair-trigger
                # trailing fallback can flip the trend right back on a brief
                # bounce (the BTC H1 06-25 case: a fallback bullish CHoCH one
                # day after the previous one failed, mid-crash, which ate the
                # final flush's BOS). Keep the fallback off for a window after
                # a same-direction failure; structural/validated references
                # are untouched, so a genuine reversal still fires.
                if (
                    self._choch_failed_fallback_suppress_candles is not None
                    and bull_choch_failed_index >= 0
                    and current_index - bull_choch_failed_index
                    <= self._choch_failed_fallback_suppress_candles
                ):
                    fallback_active_high = None
                choch_high_ref = (
                    validated_choch_high
                    or pending_leg_origin_high
                    or choch_origin_high
                    or fallback_active_high
                )
                # New-cycle barrier (`choch_weak_ref_persistence_candles`): a
                # CHoCH about to fire against a *weak* reference -- a synthetic
                # re-anchor level (validated but not structural) or the trailing
                # `active_<side>` cold-start fallback -- must hold for more
                # candles than one breaking a genuine structural level (leg
                # origin, pending origin, candidate promotion, blind-spot
                # origin). Weak references sit at local extremes rather than at
                # the level a leg actually launched from, so a brief poke
                # through one is often just a sweep; demanding extra
                # persistence keeps those from starting a new cycle. The
                # CHOCH_FAILED check below is NOT hardened: it is the escape
                # valve that undoes a wrong cycle, and delaying it holds the
                # wrong trend longer.
                choch_high_weak_ref = (
                    validated_choch_high is not None
                    and not validated_choch_high_structural
                ) or (
                    validated_choch_high is None
                    and pending_leg_origin_high is None
                    and choch_origin_high is None
                )
                choch_high_persistence = (
                    self._choch_weak_ref_persistence_candles
                    if choch_high_weak_ref
                    and self._choch_weak_ref_persistence_candles is not None
                    else self._persistence_candles
                )
                if (
                    trend is MarketDirection.BEARISH
                    and bear_choch_origin is not None
                    and price > bear_choch_origin.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        bear_choch_origin.price,
                        bullish=True,
                    )
                ):
                    # Failed bearish CHoCH: price broke back above the origin the
                    # CHoCH drop launched from, before any confirming BOS. The
                    # reversal is invalidated; structure flips back to bullish.
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            bear_choch_origin.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHOCH_FAILED,
                        MarketDirection.BEARISH,
                        price,
                        bear_choch_origin.price,
                        reference_timestamp=bear_choch_origin.timestamp,
                    )
                    trend = MarketDirection.BULLISH
                    # Post-failure fallback suppression window starts here (a
                    # bearish CHoCH just failed; see the fallback chain above).
                    bear_choch_failed_index = current_index
                    active_low = pending_low
                    pending_low = None
                    validated_choch_high = None
                    validated_choch_low = None
                    validated_choch_high_structural = False
                    validated_choch_low_structural = False
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Bullish trend resumes: cap the staircase at its genuine
                    # last BOS high (preserved across the provisional CHoCH), not
                    # the lower CHoCH origin -- a non-extending BOS must not
                    # print below the previous bullish BOS.
                    bull_leg_high = price
                    last_bull_bos_high = (
                        bear_choch_origin.price
                        if pre_choch_bull_bos_high is None
                        else max(pre_choch_bull_bos_high, bear_choch_origin.price)
                    )
                    # The failed flip ate the highs made during its window: the
                    # bullish trend never ended, so each recorded staircase
                    # break was a genuine continuation. Stage an additive BOS
                    # per break (deduped/re-anchored like the other staged
                    # marks -- wick-only ones are dropped by the composition
                    # close-break pass) and fold the eaten extremes into the
                    # resumed staircase: the gate takes the highest pivot (it
                    # legitimately ratchets on wick breaks), the reported floor
                    # only a close-confirmed one. See
                    # `stage_choch_failed_window_bos`.
                    eaten_gate_high: float | None = None
                    eaten_reported_high: float | None = None
                    for eaten in bear_window_highs:
                        staged_bos.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=eaten.timestamp,
                                event=StructureEvent.BREAK_OF_STRUCTURE,
                                direction=MarketDirection.BULLISH,
                                price_level=eaten.pivot.price,
                                reference_price_level=eaten.ref_level,
                                scope=StructureScope.INTERNAL,
                            )
                        )
                        eaten_gate_high = (
                            eaten.pivot.price
                            if eaten_gate_high is None
                            else max(eaten_gate_high, eaten.pivot.price)
                        )
                        if eaten.ref_closed or not self._bos_floor_require_close_break:
                            eaten_reported_high = (
                                eaten.pivot.price
                                if eaten_reported_high is None
                                else max(eaten_reported_high, eaten.pivot.price)
                            )
                    bear_window_highs = []
                    bull_window_lows = []
                    if eaten_gate_high is not None:
                        last_bull_bos_high = max(last_bull_bos_high, eaten_gate_high)
                        bull_leg_high = max(bull_leg_high, eaten_gate_high)
                    last_bear_bos_low = None
                    # Restored from the resumed trend's real BOS floor (or the
                    # close-confirmed CHoCH origin), so a provisional continuation
                    # may reference it.
                    bull_floor_from_bos = True
                    # The bullish trend resumed -> its previous BOS extreme is the
                    # restored staircase floor (a genuine level, not a CHoCH seed).
                    # Under the close-break floor rule the reported tracker is
                    # restored from its OWN stash, not the gate: the gate may hold
                    # a wick-only ratchet the reported floor never accepted, and
                    # restoring from it would launder that wick into the next
                    # continuation's reported reference. The CHoCH origin joins
                    # via max(): the failure itself close-confirmed a break of it.
                    if (
                        self._bos_floor_require_close_break
                        and pre_choch_bull_bos_extreme is not None
                    ):
                        prev_bull_bos_extreme = max(
                            pre_choch_bull_bos_extreme, bear_choch_origin.price
                        )
                    else:
                        prev_bull_bos_extreme = last_bull_bos_high
                    # Fold the close-confirmed eaten extreme into the reported
                    # floor: the next continuation plots against the highest
                    # formed high of the resumed leg, not the pre-flip level
                    # the eaten (staged) marks already broke.
                    if eaten_reported_high is not None:
                        prev_bull_bos_extreme = max(prev_bull_bos_extreme, eaten_reported_high)
                    prev_bear_bos_extreme = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    pre_choch_bear_bos_extreme = None
                    pre_choch_bull_bos_extreme = None
                    # One-shot: a failed-CHoCH flip does NOT arm the opposite
                    # origin / blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_high = None
                    choch_origin_low = None
                    bear_choch_origin = None
                    bull_choch_origin = None
                    # The failed CHoCH's line ended here: no standing CHoCH to
                    # fizzle-mark (the resumed trend is not a fresh CHoCH).
                    standing_choch_ref = None
                    standing_choch_dir = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif (
                    trend is MarketDirection.BEARISH
                    and choch_high_ref is not None
                    and price > choch_high_ref.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        choch_high_ref.price,
                        bullish=True,
                        persistence=choch_high_persistence,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            choch_high_ref.price,
                            bullish=True,
                            persistence_candles=choch_high_persistence,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BULLISH,
                        price,
                        choch_high_ref.price,
                        reference_timestamp=choch_high_ref.timestamp,
                        # Surface the barrier's own classification so consumers
                        # (the chart) can tell a conservative-sequence CHoCH
                        # from one against a weak reference.
                        reference_structural=not choch_high_weak_ref,
                    )
                    trend = MarketDirection.BULLISH
                    # The low this rally launched from is the bullish CHoCH's
                    # origin: a sustained break back below it (before a confirming
                    # BOS) invalidates the CHoCH (CHOCH_FAILED). Under
                    # `choch_origin_leg_extreme` use the *deepest* low of the
                    # reversed bearish leg -- the more extreme of the trailing
                    # `active_low` and the accumulated `pending_low`. Neither alone
                    # is reliable: `active_low` ratchets UP through the higher-lows
                    # of the reversal rally (so at CHoCH confirm it can sit near the
                    # new high -- the NEAR M5 bug: origin 2.004 just below a 2.039
                    # top, arming an instant failure on the first pullback and
                    # ping-ponging the trend), while `pending_low` can retain a
                    # shallower early-leg low. The deeper of the two is the true
                    # fundo the reversal launched from. With the flag off the
                    # trailing `active_low` alone is used (byte-for-byte identical).
                    bull_choch_origin = (
                        self._extreme(active_low, pending_low, higher=False)
                        if self._choch_origin_leg_extreme
                        else active_low
                    )
                    # A fresh provisional window arms: discard any stale
                    # recorded breaks (see `stage_choch_failed_window_bos`).
                    bull_window_lows = []
                    bear_window_highs = []
                    # Fast-fizzle bookkeeping: this CHoCH is now the standing one
                    # (its broken level, index, direction; see
                    # `choch_fizzle_reclaim_candles`).
                    standing_choch_ref = choch_high_ref
                    standing_choch_index = current_index
                    standing_choch_dir = MarketDirection.BULLISH
                    bear_choch_origin = None
                    active_low = pending_low
                    pending_low = None
                    # CHoCH consumes the references; the next confirmed BOS
                    # chain rebuilds them from scratch (provisional -> validated).
                    validated_choch_high = None
                    validated_choch_low = None
                    validated_choch_high_structural = False
                    validated_choch_low_structural = False
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Arm the opposite-side origin (the bottom of the bearish leg
                    # just reversed) so a failed bullish reversal can still flip
                    # back to bearish before validated_choch_low is rebuilt --
                    # but only for a *validated* trigger (one-shot, no ping-pong).
                    choch_origin_high = None
                    choch_origin_low = active_low if via_validated else None
                    # New bullish leg begins; seed its running high extreme.
                    bull_leg_high = price
                    # New regime: the bullish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break ABOVE the
                    # level the CHoCH broke, never re-break a lower high formed
                    # after price fell back below the CHoCH (the active reference
                    # trails down during that decline). The bearish staircase is
                    # irrelevant in the new bullish leg.
                    # Stash the bearish floor in case this CHoCH later fails and
                    # the bearish trend has to resume from its genuine last BOS.
                    # The reported floor tracker is stashed alongside (see the
                    # bearish-CHoCH mirror): each is restored from its own stash.
                    pre_choch_bear_bos_low = last_bear_bos_low
                    pre_choch_bear_bos_extreme = prev_bear_bos_extreme
                    pre_choch_bull_bos_high = None
                    pre_choch_bull_bos_extreme = None
                    last_bull_bos_high = choch_high_ref.price
                    last_bear_bos_low = None
                    # Seeded with the CHoCH's own level, not a BOS extreme: the
                    # first break of this leg is the CHoCH itself, so no
                    # provisional continuation may reference it yet.
                    bull_floor_from_bos = False
                    # New leg: seed the reported staircase floor with the CHoCH's
                    # confirming high (the topo the reversal formed) so the FIRST
                    # BOS of the leg references that structural high -- and, via the
                    # close-break re-anchor, confirms only on a close above it --
                    # rather than the trailing `active_high` that ratchets down to a
                    # shallow lower-high during the pullback.
                    prev_bull_bos_extreme = price
                    prev_bear_bos_extreme = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                elif price > active_high.price:
                    if trend is MarketDirection.BEARISH:
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_high_pivot_index + 1,
                                current_index,
                                active_high.price,
                                bullish=True,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
                        # Mirror of the bearish case: a sweep that takes out the
                        # current bullish-CHoCH pullback candidate redefines the
                        # leg's pullback origin -- the swept high (not the
                        # pre-sweep LH) is the high a later new-low expansion
                        # falls from. Re-anchor the candidate up to it (more
                        # extreme only, so progressively higher sweeps keep the
                        # highest origin).
                        if (
                            candidate_choch_high is not None
                            and price > candidate_choch_high.price
                        ):
                            candidate_choch_high = pivot
                    elif last_bull_bos_high is not None and price <= last_bull_bos_high:
                        # BOS bullish staircase: a continuation BOS must *extend*
                        # the leg beyond the previous BOS high. A break of a lower
                        # trailing high formed during a retrace (price not above
                        # the last BOS high) is not a structural BOS -- it just
                        # trails active_high. The first BOS of the leg
                        # (last_bull_bos_high is None) is unconstrained.
                        pass
                    else:
                        # BOS bullish: the state advances ONLY when a candle in
                        # the leg *closes* beyond the reference. A wick-only
                        # overshoot stays pending (the reference is frozen below)
                        # so the BOS activates later, once a close confirms it.
                        ref_price = active_high.price
                        # The formed high the *previous* BOS made (the level this
                        # continuation broke). `None` for the first BOS of the leg
                        # -> the emit falls back to the trailing `ref_price`.
                        floor_at_advance = prev_bull_bos_extreme
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=True,
                        )
                        if close_idx is None:
                            wick_only_break = True
                        else:
                            # Did any candle actually *close* beyond the staircase
                            # floor (the prior BOS high), or did the leg only wick
                            # past it? Reused below to gate both the reported-floor
                            # ratchet and the leg-origin structural promotion.
                            floor_did_close = (
                                floor_at_advance is None
                                or find_close_break_index(
                                    candles,
                                    prev_high_pivot_index + 1,
                                    current_index,
                                    floor_at_advance,
                                    bullish=True,
                                )
                                is not None
                            )
                            # Promote the previous bullish BOS's pullback to the
                            # validated bearish-CHoCH reference *only* if this
                            # break makes a NEW LEG HIGH (above bull_leg_high,
                            # the bullish leg's running extreme) -- a genuine
                            # continuation. A higher-high that does not exceed
                            # the leg extreme (e.g. a pullback-BOS within a
                            # retrace) leaves the candidate provisional: that BOS
                            # never extended the leg, so its pullback must not
                            # ratchet the CHoCH reference down.
                            if (
                                candidate_choch_low is not None
                                and bull_leg_high is not None
                                and price > bull_leg_high
                            ):
                                validated_choch_low = candidate_choch_low
                                # The continuation that justifies this promotion is
                                # only close-confirmed if a candle closed beyond the
                                # staircase floor. A wick-only new leg high (the
                                # advance never closed past the prior BOS top --
                                # `_reanchor_bos_close_break` hides its mark) makes
                                # the promoted pullback a *weak* reference, so the
                                # new-cycle barrier governs a CHoCH against it
                                # instead of base persistence off an unconfirmed
                                # break (mirror of the emitted-BOS leg-origin rule).
                                validated_choch_low_structural = (
                                    floor_did_close
                                    or not self._bos_leg_origin_require_close_break
                                )
                                choch_origin_low = None
                            if bull_leg_high is None or price > bull_leg_high:
                                bull_leg_high = price
                            # Extend the BOS staircase: the next bullish
                            # continuation must break above this new high.
                            last_bull_bos_high = price
                            bull_floor_from_bos = True
                            # This BOS's extreme becomes the formed level the next
                            # bullish continuation will report as its reference --
                            # unless this advance only *wick-swept* the reported
                            # floor (pivot extreme beyond it, no close beyond it): a
                            # wick that merely swept the prior BOS high did not
                            # establish a new level, so the reported floor stays put
                            # and the next continuation references the last
                            # close-confirmed top, not this wick (the "second-top
                            # wick supersedes the real top" case). An advance whose
                            # pivot never even reached the floor (a trailing-level
                            # break far short of it, e.g. inside a post-crash range)
                            # still ratchets -- freezing there would leave later BOS
                            # reporting a level their leg never broke, and the
                            # close-break re-anchor would drop the whole staircase.
                            # State machine / gate `last_bull_bos_high` unaffected.
                            wick_swept_floor = (
                                floor_at_advance is not None
                                and price > floor_at_advance
                                and not floor_did_close
                            )
                            if not (
                                self._bos_floor_require_close_break and wick_swept_floor
                            ):
                                prev_bull_bos_extreme = price
                            # Stage an impulse BOS at this advance (mirror of the
                            # bearish case): displaces the prior BOS level upward by
                            # the threshold. Deduped against the real BOS later.
                            staged_pct = self._impulse_bos_displacement_pct
                            if (
                                staged_pct is not None
                                and floor_at_advance is not None
                                and price > floor_at_advance * (1 + staged_pct)
                            ):
                                staged_bos.append(
                                    MarketStructure(
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        timestamp=candles[close_idx].timestamp,
                                        event=StructureEvent.BREAK_OF_STRUCTURE,
                                        direction=MarketDirection.BULLISH,
                                        price_level=price,
                                        reference_price_level=floor_at_advance,
                                        scope=StructureScope.INTERNAL,
                                    )
                                )
                            pullback_ref_snapshot = active_low
                            # Shallow-pullback promotion (mirror of the bearish
                            # case): a minor retrace (active_high -> active_low)
                            # well above the correction's true bottom promotes the
                            # origin to the lowest low pivot of the correction
                            # (`pending_low`). See `bos_leg_origin_min_pullback_atr`.
                            if (
                                self._bos_leg_origin_choch_ref
                                and self._bos_leg_origin_min_pullback_atr is not None
                                and mean_tr_pct is not None
                                and active_high is not None
                                and active_low is not None
                                and pending_low is not None
                                and pending_low.price < active_low.price
                                and (active_high.price - active_low.price) / active_low.price
                                < self._bos_leg_origin_min_pullback_atr * mean_tr_pct
                            ):
                                pullback_ref_snapshot = pending_low
                            # Mirror of the bearish case: consecutive highs with
                            # no intervening low pivot reset active_low to None,
                            # so inherit the prior pending BOS's pullback ref --
                            # the leg keeps rising from the same low.
                            if (
                                pullback_ref_snapshot is None
                                and pending_bos is not None
                                and pending_bos.direction is MarketDirection.BULLISH
                            ):
                                pullback_ref_snapshot = pending_bos.pullback_ref
                            trend = MarketDirection.BULLISH
                            active_low = pending_low
                            pending_low = None
                            # Trigger "chain": count bullish BOS advances in this
                            # leg; at the threshold re-anchor the stale low-side
                            # references up to the most recent in-leg low.
                            if chain_dir is MarketDirection.BULLISH:
                                bos_chain += 1
                            else:
                                chain_dir = MarketDirection.BULLISH
                                bos_chain = 1
                            if (
                                self._reanchor_mode == "chain"
                                and bos_chain >= self._reanchor_chain_threshold
                                and (
                                    not self._reanchor_chain_establish_only
                                    or validated_choch_low is None
                                )
                            ):
                                # An outside-bar candle can register as both a high
                                # and low pivot at the same index; when that pivot
                                # was just processed, prev_any_pivot_index ==
                                # current_index, so clamp seg_start to current_index
                                # rather than let it exceed it (which would slice to
                                # empty and crash `min()` below).
                                seg_start = min(max(0, prev_any_pivot_index + 1), current_index)
                                # Re-anchor to the candle that actually formed the
                                # recent in-leg low (its timestamp anchors the CHoCH
                                # line's origin), not the advance pivot's timestamp.
                                low_candle = min(
                                    candles[seg_start : current_index + 1], key=lambda c: c.low
                                )
                                reanchor_opposite(
                                    low_candle.low,
                                    low_candle.timestamp,
                                    current_price=candles[current_index].close,
                                )
                                bos_chain = 0
                                chain_dir = MarketDirection.NEUTRAL
                            if (
                                last_bullish_bos_origin is not None
                                and last_bullish_bos_price is not None
                                and pullback_ref_snapshot is not None
                                and pullback_ref_snapshot.price < last_bullish_bos_origin
                                and price < last_bullish_bos_price
                            ):
                                last_bullish_bos_price = None
                                last_bullish_bos_origin = None
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=True
                            ):
                                # A wick-only poke of the prior BOS high is not a
                                # close-confirmed break, so the leg origin it carries
                                # is not a structural CHoCH reference (see the
                                # emission below). `floor_did_close` is the physical
                                # fact (computed above); the flag decides whether to
                                # act on it.
                                floor_closed = (
                                    not self._bos_leg_origin_require_close_break
                                    or floor_did_close
                                )
                                pending_bos = _PendingBOS(
                                    direction=MarketDirection.BULLISH,
                                    breaking_pivot=pivot,
                                    ref_price=ref_price,
                                    close_break_timestamp=candles[close_idx].timestamp,
                                    pullback_ref=pullback_ref_snapshot,
                                    floor=floor_at_advance,
                                    floor_closed=floor_closed,
                                )
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_high, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
                    active_high = pivot
                    prev_high_pivot_index = current_index
            else:
                # Mirror of the high-pivot bookkeeping: record a counter-trend
                # staircase break while a bullish CHoCH is provisional (a low
                # pivot beyond the previous recorded extreme is a bearish
                # continuation the flip ate if the CHoCH later fails). See
                # `stage_choch_failed_window_bos`.
                if (
                    self._stage_choch_failed_window_bos
                    and trend is MarketDirection.BULLISH
                    and bull_choch_origin is not None
                ):
                    prior_level = (
                        bull_window_lows[-1].pivot.price
                        if bull_window_lows
                        else (
                            pre_choch_bear_bos_extreme
                            if pre_choch_bear_bos_extreme is not None
                            else pre_choch_bear_bos_low
                        )
                    )
                    if prior_level is not None and price < prior_level:
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            prior_level,
                            bullish=False,
                        )
                        break_idx = (
                            close_idx
                            if close_idx is not None
                            else find_wick_break_index(
                                candles,
                                prev_low_pivot_index + 1,
                                current_index,
                                prior_level,
                                bullish=False,
                            )
                        )
                        bull_window_lows.append(
                            _EatenBreak(
                                pivot=pivot,
                                ref_level=prior_level,
                                timestamp=candles[break_idx].timestamp,
                                ref_closed=close_idx is not None,
                            )
                        )
                wick_only_break = False
                # --- Pending BULLISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BULLISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price > pb.price
                        and (last_bullish_bos_origin is None or price > last_bullish_bos_origin)
                    ):
                        if self._pullback_quality_ok(candles[current_index], high_pivot=False):
                            emit(
                                pending_bos.close_break_timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BULLISH,
                                pending_bos.breaking_pivot.price,
                                pending_bos.floor
                                if pending_bos.floor is not None
                                else pending_bos.ref_price,
                                origin_price_level=price,
                            )
                            last_bullish_bos_price = pending_bos.breaking_pivot.price
                            last_bullish_bos_origin = price
                            # Provisional CHoCH reference (see bearish mirror
                            # above): promoted only once a continuation (the next
                            # bullish BOS) confirms this BOS.
                            candidate_choch_low = pivot
                            # Leg-origin CHoCH promotion (mirror of the bearish
                            # case above): the low this BOS's leg rose from
                            # becomes the bearish-CHoCH reference at emission;
                            # every emitted BOS refreshes it to its own leg
                            # origin.
                            if (
                                self._bos_leg_origin_choch_ref
                                and pending_bos.pullback_ref is not None
                            ):
                                validated_choch_low = pending_bos.pullback_ref
                                # Mirror of the bearish case: only a close-confirmed
                                # break makes the leg origin structural; a wick-only
                                # continuation promotes it as a weak reference.
                                validated_choch_low_structural = pending_bos.floor_closed
                                choch_origin_low = None
                            # The bullish CHoCH is now confirmed by an *emitted*
                            # BOS (a state-advance alone leaves a still-pending BOS
                            # that may never emit, so the CHoCH could still fail):
                            # retire its origin and drop the stashed bearish floor.
                            bull_choch_origin = None
                            pre_choch_bear_bos_low = None
                            pre_choch_bear_bos_extreme = None
                            pending_bos = None
                        elif (
                            self._stage_wick_rejected_bos
                            and not pending_bos.staged
                            and pending_bos.floor is not None
                        ):
                            # Mirror of the bearish case: a wick-only pullback stays
                            # out of the state machine / CHoCH, but the continuation
                            # close beyond the floor gets an ADDITIVE mark (once),
                            # deduped against the real BOS at the end. Only continuation
                            # breaks with a real staircase `floor` are staged (never the
                            # first-of-leg `ref_price` fallback), so the reference is
                            # always the prior swing extreme it broke.
                            pending_bos.staged = True
                            staged_bos.append(
                                MarketStructure(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    timestamp=pending_bos.close_break_timestamp,
                                    event=StructureEvent.BREAK_OF_STRUCTURE,
                                    direction=MarketDirection.BULLISH,
                                    price_level=pending_bos.breaking_pivot.price,
                                    reference_price_level=pending_bos.floor,
                                    scope=StructureScope.INTERNAL,
                                )
                            )
                        # else: wick-only pullback; keep the pending BOS alive so a
                        # later, real pullback can confirm it instead of this wick.
                    else:
                        # Mirror of the bearish case above: this low pivot already
                        # lost the leg origin itself (price < pullback_ref), so no
                        # valid pullback ever confirmed the close-break. Promote
                        # the leg origin the pending BOS carried before discarding
                        # it -- the level this breakdown is a reversal *of* -- so
                        # the CHoCH check below evaluates against it instead of a
                        # stale far-off reference.
                        if (
                            self._bos_leg_origin_choch_ref
                            and pb is not None
                            and price < pb.price
                        ):
                            validated_choch_low = pb
                            validated_choch_low_structural = True
                            choch_origin_low = None
                        pending_bos = None

                # Validated reference takes priority; choch_origin_low is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                # Mirror of the high side: a still-pending bullish BOS
                # contributes its leg origin (the fundo the leg rose from)
                # ahead of the blind-spot fallbacks (see the bearish case for
                # rationale).
                pending_leg_origin_low: Pivot | None = None
                if (
                    self._bos_leg_origin_choch_ref
                    and pending_bos is not None
                    and pending_bos.direction is MarketDirection.BULLISH
                ):
                    pending_leg_origin_low = pending_bos.pullback_ref
                via_validated = (
                    validated_choch_low is not None or pending_leg_origin_low is not None
                )
                # Mirror of the high side: while an unconfirmed bullish
                # CHoCH's origin is armed (`bull_choch_origin`), the bearish
                # exit from the provisional structure is CHOCH_FAILED at that
                # origin, so the trailing `active_low` cold-start fallback is
                # suppressed.
                fallback_active_low = active_low
                if self._bos_leg_origin_choch_ref and bull_choch_origin is not None:
                    fallback_active_low = None
                # Mirror of the high side: keep the fallback suppressed for a
                # window after a same-direction (bearish) CHoCH failure -- the
                # failed flip armed no origin, so without this the trailing
                # fallback is live again immediately.
                if (
                    self._choch_failed_fallback_suppress_candles is not None
                    and bear_choch_failed_index >= 0
                    and current_index - bear_choch_failed_index
                    <= self._choch_failed_fallback_suppress_candles
                ):
                    fallback_active_low = None
                choch_low_ref = (
                    validated_choch_low
                    or pending_leg_origin_low
                    or choch_origin_low
                    or fallback_active_low
                )
                # Mirror of the high side: the new-cycle barrier applies when
                # the reference about to be broken is weak (a synthetic
                # re-anchor level or the trailing fallback), never to the
                # CHOCH_FAILED escape valve below.
                choch_low_weak_ref = (
                    validated_choch_low is not None
                    and not validated_choch_low_structural
                ) or (
                    validated_choch_low is None
                    and pending_leg_origin_low is None
                    and choch_origin_low is None
                )
                choch_low_persistence = (
                    self._choch_weak_ref_persistence_candles
                    if choch_low_weak_ref
                    and self._choch_weak_ref_persistence_candles is not None
                    else self._persistence_candles
                )
                if (
                    trend is MarketDirection.BULLISH
                    and bull_choch_origin is not None
                    and price < bull_choch_origin.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        bull_choch_origin.price,
                        bullish=False,
                    )
                ):
                    # Failed bullish CHoCH: price broke back below the origin the
                    # CHoCH rally launched from, before any confirming BOS. The
                    # reversal is invalidated; structure flips back to bearish.
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            bull_choch_origin.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHOCH_FAILED,
                        MarketDirection.BULLISH,
                        price,
                        bull_choch_origin.price,
                        reference_timestamp=bull_choch_origin.timestamp,
                    )
                    trend = MarketDirection.BEARISH
                    # Post-failure fallback suppression window starts here (a
                    # bullish CHoCH just failed; see the fallback chain above).
                    bull_choch_failed_index = current_index
                    active_high = pending_high
                    pending_high = None
                    validated_choch_low = None
                    validated_choch_high = None
                    validated_choch_low_structural = False
                    validated_choch_high_structural = False
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Bearish trend resumes: floor the staircase at its genuine
                    # last BOS low (preserved across the provisional CHoCH), not
                    # the higher CHoCH origin -- a non-extending BOS must not
                    # print above the previous bearish BOS.
                    bear_leg_low = price
                    last_bear_bos_low = (
                        bull_choch_origin.price
                        if pre_choch_bear_bos_low is None
                        else min(pre_choch_bear_bos_low, bull_choch_origin.price)
                    )
                    # Mirror of the bearish-CHoCH failure: the failed flip ate
                    # the lows made during its window -- stage an additive
                    # bearish BOS per recorded staircase break and fold the
                    # eaten extremes into the resumed staircase (gate: deepest
                    # pivot; reported floor: deepest close-confirmed one). See
                    # `stage_choch_failed_window_bos`.
                    eaten_gate_low: float | None = None
                    eaten_reported_low: float | None = None
                    for eaten in bull_window_lows:
                        staged_bos.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=eaten.timestamp,
                                event=StructureEvent.BREAK_OF_STRUCTURE,
                                direction=MarketDirection.BEARISH,
                                price_level=eaten.pivot.price,
                                reference_price_level=eaten.ref_level,
                                scope=StructureScope.INTERNAL,
                            )
                        )
                        eaten_gate_low = (
                            eaten.pivot.price
                            if eaten_gate_low is None
                            else min(eaten_gate_low, eaten.pivot.price)
                        )
                        if eaten.ref_closed or not self._bos_floor_require_close_break:
                            eaten_reported_low = (
                                eaten.pivot.price
                                if eaten_reported_low is None
                                else min(eaten_reported_low, eaten.pivot.price)
                            )
                    bull_window_lows = []
                    bear_window_highs = []
                    if eaten_gate_low is not None:
                        last_bear_bos_low = min(last_bear_bos_low, eaten_gate_low)
                        bear_leg_low = min(bear_leg_low, eaten_gate_low)
                    last_bull_bos_high = None
                    # Restored genuine floor (mirror of the bullish resume), so a
                    # provisional continuation may reference it.
                    bear_floor_from_bos = True
                    # The bearish trend resumed -> its previous BOS extreme is the
                    # restored staircase floor (a genuine level, not a CHoCH seed).
                    # Mirror of the bullish restore: under the close-break floor
                    # rule the reported tracker restores from its own stash (the
                    # gate may hold a wick-only ratchet), min()'d with the origin
                    # whose break the failure itself close-confirmed.
                    if (
                        self._bos_floor_require_close_break
                        and pre_choch_bear_bos_extreme is not None
                    ):
                        prev_bear_bos_extreme = min(
                            pre_choch_bear_bos_extreme, bull_choch_origin.price
                        )
                    else:
                        prev_bear_bos_extreme = last_bear_bos_low
                    # Fold the close-confirmed eaten extreme into the reported
                    # floor (mirror of the bearish-CHoCH failure restore).
                    if eaten_reported_low is not None:
                        prev_bear_bos_extreme = min(prev_bear_bos_extreme, eaten_reported_low)
                    prev_bull_bos_extreme = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    pre_choch_bear_bos_extreme = None
                    pre_choch_bull_bos_extreme = None
                    # One-shot: a failed-CHoCH flip does NOT arm the opposite
                    # origin / blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_low = None
                    choch_origin_high = None
                    bull_choch_origin = None
                    bear_choch_origin = None
                    # The failed CHoCH's line ended here: no standing CHoCH to
                    # fizzle-mark (the resumed trend is not a fresh CHoCH).
                    standing_choch_ref = None
                    standing_choch_dir = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif (
                    trend is MarketDirection.BULLISH
                    and choch_low_ref is not None
                    and price < choch_low_ref.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        choch_low_ref.price,
                        bullish=False,
                        persistence=choch_low_persistence,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            choch_low_ref.price,
                            bullish=False,
                            persistence_candles=choch_low_persistence,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BEARISH,
                        price,
                        choch_low_ref.price,
                        reference_timestamp=choch_low_ref.timestamp,
                        # Mirror of the bullish case: expose whether this CHoCH
                        # broke a structural or a weak (barrier-governed) level.
                        reference_structural=not choch_low_weak_ref,
                    )
                    trend = MarketDirection.BEARISH
                    # The high this drop launched from is the bearish CHoCH's
                    # origin (mirror of the bullish case): under
                    # `choch_origin_leg_extreme`, the *highest* high of the reversed
                    # bullish leg -- the more extreme of the trailing `active_high`
                    # (which ratchets DOWN through the lower-highs of the reversal
                    # drop) and the accumulated `pending_high`. Off -> `active_high`.
                    bear_choch_origin = (
                        self._extreme(active_high, pending_high, higher=True)
                        if self._choch_origin_leg_extreme
                        else active_high
                    )
                    # A fresh provisional window arms: discard any stale
                    # recorded breaks (see `stage_choch_failed_window_bos`).
                    bull_window_lows = []
                    bear_window_highs = []
                    # Fast-fizzle bookkeeping: this CHoCH is now the standing one
                    # (its broken level, index, direction; see
                    # `choch_fizzle_reclaim_candles`).
                    standing_choch_ref = choch_low_ref
                    standing_choch_index = current_index
                    standing_choch_dir = MarketDirection.BEARISH
                    bull_choch_origin = None
                    active_high = pending_high
                    pending_high = None
                    # CHoCH consumes the references; the next confirmed BOS
                    # chain rebuilds them from scratch (provisional -> validated).
                    validated_choch_low = None
                    validated_choch_high = None
                    validated_choch_low_structural = False
                    validated_choch_high_structural = False
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Arm the opposite-side origin (the top of the bullish leg
                    # just reversed) so a failed bearish reversal can still flip
                    # back to bullish before validated_choch_high is rebuilt --
                    # but only for a *validated* trigger (one-shot, no ping-pong).
                    choch_origin_low = None
                    choch_origin_high = active_high if via_validated else None
                    # New bearish leg begins; seed its running low extreme.
                    bear_leg_low = price
                    # New regime: the bearish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break BELOW the
                    # level the CHoCH broke, never re-break a higher low formed
                    # after price rose back above the CHoCH (the active reference
                    # trails up during that rise). The bullish staircase is
                    # irrelevant in the new bearish leg.
                    # Stash the bullish ceiling in case this CHoCH later fails
                    # and the bullish trend resumes from its genuine last BOS.
                    # The reported floor tracker is stashed alongside: it may sit
                    # below the gate (a wick-only break ratchets the gate but not
                    # the close-confirmed reported floor), and a failed CHoCH must
                    # restore each from its own stash.
                    pre_choch_bull_bos_high = last_bull_bos_high
                    pre_choch_bull_bos_extreme = prev_bull_bos_extreme
                    pre_choch_bear_bos_low = None
                    pre_choch_bear_bos_extreme = None
                    last_bear_bos_low = choch_low_ref.price
                    last_bull_bos_high = None
                    # Seeded with the CHoCH's own level (mirror of the bullish
                    # CHoCH): the first break of this leg is the CHoCH itself, so
                    # no provisional continuation may reference it yet.
                    bear_floor_from_bos = False
                    # New leg: seed the reported staircase floor with the CHoCH's
                    # confirming low (the fundo the reversal formed) so the FIRST
                    # BOS of the leg references that structural low -- and, via the
                    # close-break re-anchor, confirms only on a close below it --
                    # rather than the trailing `active_low` that ratchets up to a
                    # shallow higher-low during the pullback.
                    prev_bear_bos_extreme = price
                    prev_bull_bos_extreme = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bearish_bos_price = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    if trend is MarketDirection.BULLISH:
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_low_pivot_index + 1,
                                current_index,
                                active_low.price,
                                bullish=False,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
                        # A sweep that takes out the current bearish-CHoCH
                        # pullback candidate redefines the leg's pullback origin:
                        # the swept low (not the pre-sweep HL) is the low a later
                        # new-high expansion rises from, so a continuation must
                        # promote the swept low as the bearish-CHoCH reference.
                        # Re-anchor the candidate down to it (more extreme only,
                        # so progressively deeper sweeps keep the lowest origin).
                        if (
                            candidate_choch_low is not None
                            and price < candidate_choch_low.price
                        ):
                            candidate_choch_low = pivot
                    elif last_bear_bos_low is not None and price >= last_bear_bos_low:
                        # BOS bearish staircase: a continuation BOS must *extend*
                        # the leg beyond the previous BOS low. A break of a higher
                        # trailing low formed during a retrace (price not below
                        # the last BOS low) is not a structural BOS -- it just
                        # trails active_low. The first BOS of the leg
                        # (last_bear_bos_low is None) is unconstrained.
                        pass
                    else:
                        # BOS bearish: the state advances ONLY when a candle in
                        # the leg *closes* beyond the reference. A wick-only
                        # overshoot stays pending (the reference is frozen above)
                        # so the BOS activates later, once a close confirms it.
                        ref_price = active_low.price
                        # The formed low the *previous* BOS made (the level this
                        # continuation broke). `None` for the first BOS of the leg
                        # -> the emit falls back to the trailing `ref_price`.
                        floor_at_advance = prev_bear_bos_extreme
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=False,
                        )
                        if close_idx is None:
                            wick_only_break = True
                        else:
                            # Mirror of the bullish case: did any candle *close*
                            # beyond the staircase floor (the prior BOS low), or did
                            # the leg only wick past it? Reused for the reported-floor
                            # ratchet and the leg-origin structural promotion.
                            floor_did_close = (
                                floor_at_advance is None
                                or find_close_break_index(
                                    candles,
                                    prev_low_pivot_index + 1,
                                    current_index,
                                    floor_at_advance,
                                    bullish=False,
                                )
                                is not None
                            )
                            # Promote the previous bearish BOS's pullback to the
                            # validated bullish-CHoCH reference *only* if this
                            # break makes a NEW LEG LOW (below bear_leg_low, the
                            # bearish leg's running extreme) -- a genuine
                            # continuation. A lower-low that does not break the
                            # leg extreme (e.g. a pullback-BOS within a retrace)
                            # leaves the candidate provisional: that BOS never
                            # extended the leg, so its pullback must not ratchet
                            # the CHoCH reference down.
                            if (
                                candidate_choch_high is not None
                                and bear_leg_low is not None
                                and price < bear_leg_low
                            ):
                                validated_choch_high = candidate_choch_high
                                # Mirror of the bullish case: a wick-only new leg
                                # low (no candle closed past the prior BOS bottom)
                                # promotes the pullback as a *weak* reference, so
                                # the new-cycle barrier governs a CHoCH against it.
                                validated_choch_high_structural = (
                                    floor_did_close
                                    or not self._bos_leg_origin_require_close_break
                                )
                                choch_origin_high = None
                            if bear_leg_low is None or price < bear_leg_low:
                                bear_leg_low = price
                            # Extend the BOS staircase: the next bearish
                            # continuation must break below this new low.
                            last_bear_bos_low = price
                            bear_floor_from_bos = True
                            # This BOS's extreme becomes the formed level the next
                            # bearish continuation will report as its reference --
                            # unless this advance only *wick-swept* the reported
                            # floor (pivot extreme beyond it, no close beyond it):
                            # such a wick did not establish a new level, so the
                            # reported floor stays put and the next continuation
                            # references the last close-confirmed bottom, not the
                            # wick. An advance whose pivot never even reached the
                            # floor (a trailing-level break far short of it) still
                            # ratchets -- freezing there would leave later BOS
                            # reporting a level their leg never broke, and the
                            # close-break re-anchor would drop the whole staircase.
                            # State machine / gate `last_bear_bos_low` unaffected.
                            wick_swept_floor = (
                                floor_at_advance is not None
                                and price < floor_at_advance
                                and not floor_did_close
                            )
                            if not (
                                self._bos_floor_require_close_break and wick_swept_floor
                            ):
                                prev_bear_bos_extreme = price
                            # Stage an impulse BOS at this advance if it displaces
                            # the prior BOS level by the threshold. Recorded
                            # separately and deduped against the real BOS later, so
                            # it only surfaces in impulsive gaps the deferred
                            # pending BOS never fills.
                            staged_pct = self._impulse_bos_displacement_pct
                            if (
                                staged_pct is not None
                                and floor_at_advance is not None
                                and price < floor_at_advance * (1 - staged_pct)
                            ):
                                staged_bos.append(
                                    MarketStructure(
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        timestamp=candles[close_idx].timestamp,
                                        event=StructureEvent.BREAK_OF_STRUCTURE,
                                        direction=MarketDirection.BEARISH,
                                        price_level=price,
                                        reference_price_level=floor_at_advance,
                                        scope=StructureScope.INTERNAL,
                                    )
                                )
                            pullback_ref_snapshot = active_high
                            # Shallow-pullback promotion: the immediate pullback
                            # (active_low -> active_high) is a minor retrace well
                            # below the correction's true top; promote the origin
                            # to the highest high pivot of the correction
                            # (`pending_high`) so the CHoCH reference sits at the
                            # visible leg top, not a shallow secondary high. See
                            # `bos_leg_origin_min_pullback_atr`.
                            if (
                                self._bos_leg_origin_choch_ref
                                and self._bos_leg_origin_min_pullback_atr is not None
                                and mean_tr_pct is not None
                                and active_high is not None
                                and active_low is not None
                                and pending_high is not None
                                and pending_high.price > active_high.price
                                and (active_high.price - active_low.price) / active_high.price
                                < self._bos_leg_origin_min_pullback_atr * mean_tr_pct
                            ):
                                pullback_ref_snapshot = pending_high
                            # Consecutive lows with no intervening high pivot
                            # (an impulsive leg) reset active_high to None on the
                            # first advance, so a later advance would carry a
                            # null pullback ref and the BOS could never confirm.
                            # The leg keeps dropping from the *same* high, so
                            # inherit the prior pending BOS's pullback ref.
                            if (
                                pullback_ref_snapshot is None
                                and pending_bos is not None
                                and pending_bos.direction is MarketDirection.BEARISH
                            ):
                                pullback_ref_snapshot = pending_bos.pullback_ref
                            trend = MarketDirection.BEARISH
                            active_high = pending_high
                            pending_high = None
                            # Trigger "chain": count bearish BOS advances in this
                            # leg; at the threshold re-anchor the stale high-side
                            # references down to the most recent in-leg high.
                            if chain_dir is MarketDirection.BEARISH:
                                bos_chain += 1
                            else:
                                chain_dir = MarketDirection.BEARISH
                                bos_chain = 1
                            if (
                                self._reanchor_mode == "chain"
                                and bos_chain >= self._reanchor_chain_threshold
                                and (
                                    not self._reanchor_chain_establish_only
                                    or validated_choch_high is None
                                )
                            ):
                                # An outside-bar candle can register as both a high
                                # and low pivot at the same index; when that pivot
                                # was just processed, prev_any_pivot_index ==
                                # current_index, so clamp seg_start to current_index
                                # rather than let it exceed it (which would slice to
                                # empty and crash `max()` below).
                                seg_start = min(max(0, prev_any_pivot_index + 1), current_index)
                                # Re-anchor to the candle that actually formed the
                                # recent in-leg high (its timestamp anchors the CHoCH
                                # line's origin), not the advance pivot's timestamp.
                                high_candle = max(
                                    candles[seg_start : current_index + 1], key=lambda c: c.high
                                )
                                reanchor_opposite(
                                    high_candle.high,
                                    high_candle.timestamp,
                                    current_price=candles[current_index].close,
                                )
                                bos_chain = 0
                                chain_dir = MarketDirection.NEUTRAL
                            if (
                                last_bearish_bos_origin is not None
                                and last_bearish_bos_price is not None
                                and pullback_ref_snapshot is not None
                                and pullback_ref_snapshot.price > last_bearish_bos_origin
                                and price > last_bearish_bos_price
                            ):
                                last_bearish_bos_price = None
                                last_bearish_bos_origin = None
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=False
                            ):
                                # Mirror of the bullish case: a wick-only poke of
                                # the prior BOS low is not a close-confirmed break,
                                # so the leg origin it carries is not a structural
                                # CHoCH reference (see the emission below).
                                # `floor_did_close` is the physical fact (above).
                                floor_closed = (
                                    not self._bos_leg_origin_require_close_break
                                    or floor_did_close
                                )
                                pending_bos = _PendingBOS(
                                    direction=MarketDirection.BEARISH,
                                    breaking_pivot=pivot,
                                    ref_price=ref_price,
                                    close_break_timestamp=candles[close_idx].timestamp,
                                    pullback_ref=pullback_ref_snapshot,
                                    floor=floor_at_advance,
                                    floor_closed=floor_closed,
                                )
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_low, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
                    active_low = pivot
                    prev_low_pivot_index = current_index

            # Track the most recent pivot (of any kind) so the next iteration's
            # displacement scan and chain segment-extreme bound the leg to the
            # candles since this pivot. Updated even on a wick-only break (the
            # pivot still happened chronologically).
            prev_any_pivot_index = current_index

        # Provisional live-edge BOS (only under `emit_provisional_bos`). Since the
        # last confirmed advance (BOS/CHoCH/CHOCH_FAILED), if the trend has a
        # standing staircase floor and a later candle *closed* beyond it, a
        # continuation has broken by close but its confirming swing pivots have
        # not formed yet (the swing-lookback lag). Emit a single BOS flagged
        # `provisional=True` at that break so the chart shows the forming
        # continuation dimmed; once the pivots confirm, the normal BOS supersedes
        # it, and if the trend flips first it simply disappears (a live-edge
        # repaint, communicated by the dimmed style). Computed from authoritative
        # final state -- never re-derived outside the detector -- and left out of
        # the staged-BOS dedup (`real_bos` below), which must key off confirmed
        # marks only.
        prov_event: MarketStructure | None = None
        if self._emit_provisional_bos and trend is not MarketDirection.NEUTRAL:
            tail = candles[last_advance_index + 1 :] if last_advance_index >= 0 else []
            bearish = trend is MarketDirection.BEARISH
            floor = last_bear_bos_low if bearish else last_bull_bos_high
            floor_from_bos = bear_floor_from_bos if bearish else bull_floor_from_bos
            # Only a *continuation* (a break beyond a real BOS extreme) is
            # provisional; the first break of a leg is the CHoCH itself, whose
            # seed level would otherwise draw a redundant BOS on the CHoCH line.
            if floor is not None and floor > 0 and floor_from_bos:
                broke = [
                    c
                    for c in tail
                    if (c.close < floor if bearish else c.close > floor)
                ]
                if broke:
                    extreme = (
                        min(c.low for c in broke) if bearish else max(c.high for c in broke)
                    )
                    # Anchor the line's start at the floor's origin (the prior
                    # swing extreme at that price), like `_reanchor_bos_close_break`.
                    reference_timestamp: datetime | None = None
                    for candle in reversed(candles[: last_advance_index + 1]):
                        if (candle.low if bearish else candle.high) == floor:
                            reference_timestamp = candle.timestamp
                            break
                    prov_event = MarketStructure(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=broke[0].timestamp,
                        event=StructureEvent.BREAK_OF_STRUCTURE,
                        direction=trend,
                        price_level=extreme,
                        reference_price_level=floor,
                        reference_timestamp=reference_timestamp,
                        scope=StructureScope.INTERNAL,
                        provisional=True,
                    )

        # Provisional live-edge CHoCH (only under `emit_provisional_choch`). Mirror
        # of the provisional BOS for the *reversal*: since the last confirmed
        # advance, if a *structural* opposite-side CHoCH reference exists and a
        # sustained close-break of it has occurred -- `persistence_candles`
        # consecutive closes beyond, the same bar the confirmed CHoCH requires --
        # but its confirming swing pivot has not formed yet (the swing-lookback
        # lag), a reversal has broken by close but is not yet a confirmed CHoCH.
        # Emit a single CHANGE_OF_CHARACTER flagged `provisional=True` at that break
        # so the chart shows the forming reversal dimmed; the confirmed CHoCH
        # supersedes it once the pivot forms, and if price reclaims the level it
        # simply disappears (a live-edge repaint, honestly communicated by the
        # dimmed style). Only a *structural* reference qualifies (mirror of the BOS
        # `floor_from_bos` gate) -- a weak re-anchor / fallback level would repaint
        # as chop. A poke that closes below the level for fewer than
        # `persistence_candles` and reclaims is (correctly) just a sweep, so it
        # emits nothing. Computed from authoritative final state -- never
        # re-derived outside the detector.
        prov_choch_event: MarketStructure | None = None
        if self._emit_provisional_choch and trend is not MarketDirection.NEUTRAL:
            tail = candles[last_advance_index + 1 :] if last_advance_index >= 0 else []
            # A bearish CHoCH forms in a bullish trend (breaks validated_choch_low);
            # a bullish CHoCH in a bearish trend (breaks validated_choch_high).
            bearish_choch = trend is MarketDirection.BULLISH
            ref = validated_choch_low if bearish_choch else validated_choch_high
            ref_structural = (
                validated_choch_low_structural
                if bearish_choch
                else validated_choch_high_structural
            )
            need = self._persistence_candles
            if ref is not None and ref.price > 0 and ref_structural:
                # Only candles after the reference pivot *formed* can break it: a
                # freshly-promoted leg origin whose pivot sits inside the tail
                # would otherwise be "broken" by older closes that predate the
                # level entirely (a break candle earlier than the reference --
                # the floating mid-chart `CHoCH?` label).
                eligible = [c for c in tail if c.timestamp > ref.timestamp]
                # First candle that STARTS a run of `need` consecutive closes
                # beyond the reference (the sustained break the confirmed CHoCH
                # also demands; the pivot lag is all it has not yet cleared).
                break_i: int | None = None
                for i in range(len(eligible) - need + 1):
                    if all(
                        (c.close < ref.price if bearish_choch else c.close > ref.price)
                        for c in eligible[i : i + need]
                    ):
                        break_i = i
                        break
                if break_i is not None:
                    beyond = eligible[break_i:]
                    extreme = (
                        min(c.low for c in beyond)
                        if bearish_choch
                        else max(c.high for c in beyond)
                    )
                    prov_choch_event = MarketStructure(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=eligible[break_i].timestamp,
                        event=StructureEvent.CHANGE_OF_CHARACTER,
                        direction=(
                            MarketDirection.BEARISH
                            if bearish_choch
                            else MarketDirection.BULLISH
                        ),
                        price_level=extreme,
                        reference_price_level=ref.price,
                        reference_timestamp=ref.timestamp,
                        reference_structural=True,
                        scope=StructureScope.INTERNAL,
                        provisional=True,
                    )
                    # A live-edge reversal supersedes a live-edge continuation: the
                    # two references sit on opposite sides of price, so a rare
                    # same-tail double would draw a contradictory BOS?/CHoCH? pair.
                    prov_event = None

        # Fast-fizzle marker for the *standing* provisional CHoCH (additive; only
        # under `choch_fizzle_reclaim_candles`). If the standing CHoCH never
        # confirmed or failed -- its origin is still armed, so its trend still
        # holds -- yet its reversal fizzled (price reclaimed the very level it
        # broke, a sustained close `persistence_candles` beyond, within
        # `choch_fizzle_reclaim_candles` of the CHoCH), append a CHOCH_FAILED so
        # the chart disregards the stale line. It does NOT flip the state-machine
        # trend: a real flip cascades the whole downstream CHoCH sequence (the
        # additive-over-state-machine lesson), so this is a mark only, computed
        # from authoritative final state -- never re-derived outside the detector.
        # Same-direction and firing after the CHoCH, it pairs with the standing
        # CHoCH in the frontend's `failedChochTime`, terminating its line at the
        # reclaim. A reclaim *after* the window is genuine follow-through, left
        # alone (only the leg-origin exit governs it). At most one CHoCH is
        # standing (an armed origin fixes the trend), so the two sides are
        # exclusive.
        fizzle_event: MarketStructure | None = None
        if self._choch_fizzle_reclaim_candles is not None:
            need = self._persistence_candles
            k = self._choch_fizzle_reclaim_candles

            def first_sustained_reclaim(
                ref_price: float, at_index: int, *, above: bool
            ) -> int | None:
                # First candle in `(CHoCH, CHoCH + k]` that STARTS `need`
                # consecutive closes back beyond `ref_price` (the sustained
                # reclaim the confirmed CHoCH also demands). `need` may run past
                # the window -- only the *start* must fall within it.
                last_start = min(at_index + k, len(candles) - need)
                for start in range(at_index + 1, last_start + 1):
                    if all(
                        (candles[j].close > ref_price)
                        if above
                        else (candles[j].close < ref_price)
                        for j in range(start, start + need)
                    ):
                        return start
                return None

            if (
                standing_choch_ref is not None
                and standing_choch_dir is trend
            ):
                bearish = standing_choch_dir is MarketDirection.BEARISH
                # Bearish CHoCH fizzles on a sustained close back ABOVE the
                # lower-high it broke; bullish on a close back BELOW its higher-low.
                reclaim_i = first_sustained_reclaim(
                    standing_choch_ref.price, standing_choch_index, above=bearish
                )
                if reclaim_i is not None:
                    fizzle_event = MarketStructure(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candles[reclaim_i].timestamp,
                        event=StructureEvent.CHOCH_FAILED,
                        direction=standing_choch_dir,
                        price_level=(
                            max(c.high for c in candles[reclaim_i:])
                            if bearish
                            else min(c.low for c in candles[reclaim_i:])
                        ),
                        reference_price_level=standing_choch_ref.price,
                        reference_timestamp=standing_choch_ref.timestamp,
                        scope=StructureScope.INTERNAL,
                        # Flagged provisional so the *replay* consumers
                        # (`LiquidityHuntEngine`, `NarrativeEngine`) skip it -- the
                        # state-machine trend never flipped, so the hunt/narrative
                        # reading must not either (the additive contract). The
                        # frontend still pairs it to terminate the stale CHoCH line
                        # (`failedChochTime` keys off event+direction, not the flag)
                        # and renders it as a normal solid failure mark (the dimmed
                        # `?` style is keyed to BOS/CHoCH events, not CHOCH_FAILED).
                        provisional=True,
                    )

        self.final_trend = trend

        if not staged_bos:
            if prov_event is not None:
                events.append(prov_event)
            if prov_choch_event is not None:
                events.append(prov_choch_event)
            if fizzle_event is not None:
                events.append(fizzle_event)
            return events
        # Merge staged impulse BOS, dropping any that duplicate a real emitted BOS
        # of the same direction (same advance pivot, hence the same price level
        # within a small tolerance). What remains marks the impulsive gaps where
        # the deferred pending BOS never emitted.
        real_bos = [e for e in events if e.event is StructureEvent.BREAK_OF_STRUCTURE]

        def duplicates_real(staged: MarketStructure) -> bool:
            return any(
                real.direction is staged.direction
                and abs(real.price_level - staged.price_level)
                <= abs(staged.price_level) * _STAGED_BOS_DEDUP_PCT
                for real in real_bos
            )

        # Accept each staged BOS unless it duplicates a real emitted BOS or an
        # already-accepted staged one at the *same* close-break candle (the impulse
        # and wick-reject stagers can both fire for one advance -- same direction,
        # timestamp and price level -- so keep a single mark).
        accepted: list[MarketStructure] = []
        for staged in sorted(staged_bos, key=lambda e: e.timestamp):
            if duplicates_real(staged):
                continue
            if any(
                other.direction is staged.direction
                and other.timestamp == staged.timestamp
                and abs(other.price_level - staged.price_level)
                <= abs(staged.price_level) * _STAGED_BOS_DEDUP_PCT
                for other in accepted
            ):
                continue
            accepted.append(staged)

        merged = [*events, *accepted]
        if prov_event is not None:
            merged.append(prov_event)
        if prov_choch_event is not None:
            merged.append(prov_choch_event)
        if fizzle_event is not None:
            merged.append(fizzle_event)
        merged.sort(key=lambda e: e.timestamp)
        return merged

    def _pullback_quality_ok(self, candle: Candle, *, high_pivot: bool) -> bool:
        """Whether a confirming pullback pivot is a *real* bounce, not a wick.

        A BOS confirms when a pullback pivot forms in the opposite direction (a
        high pivot for a bearish BOS, a low pivot for a bullish BOS). With a small
        swing lookback that pivot can be a single-candle wick (the candle spikes
        to the extreme intrabar but its body closes far away), so the BOS is
        confirmed by a "pullback" that never really retraced. When
        `bos_pullback_max_wick_pct` is set, the pivot-side wick of that candle
        (the upper wick for a high pivot, the lower wick for a low pivot) must be
        at most this fraction of the candle's range; an emptier body (wick-only
        spike) fails. `None` (default) disables the check.
        """
        max_wick = self._bos_pullback_max_wick_pct
        if max_wick is None:
            return True
        rng = candle.high - candle.low
        if rng <= 0:
            return True
        if high_pivot:
            wick = candle.high - max(candle.open, candle.close)
        else:
            wick = min(candle.open, candle.close) - candle.low
        return wick / rng <= max_wick

    @staticmethod
    def _extreme(current: Pivot | None, candidate: Pivot | None, *, higher: bool) -> Pivot | None:
        """The more extreme of `current` and `candidate`, by price.

        Either may be `None`; returns whichever of the two is non-`None`, or
        `None` if both are. `higher=True` keeps the higher-priced pivot (for
        `pending_high`); `higher=False` keeps the lower-priced one (for
        `pending_low`).
        """
        if candidate is None:
            return current
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current
