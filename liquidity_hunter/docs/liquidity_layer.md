# Liquidity layer (detectors)

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Liquidity layer (`liquidity_hunter/liquidity`)

- **`liquidity/detectors/base.py`** — `LiquidityZoneDetector`, the abstract
  port all detectors implement (`detect(candles) -> list[LiquidityZone]`).
- **`liquidity/detectors/swing_points.py`** — `SwingHighDetector` /
  `SwingLowDetector`: fractal-style local extrema (configurable `lookback`),
  returning point zones (`price_high == price_low`) with `strength` derived
  from prominence relative to the candle range.
- **`liquidity/detectors/equal_levels.py`** — `EqualHighDetector` /
  `EqualLowDetector`: group swing points into equal-level zones, requiring
  `min_touches` (constructor default 2; production wires **3**, measured — two
  pivots landing near each other is the coincidence the module docstring warns
  about). Grouping width is either a fixed relative
  `tolerance_pct` or, when `tolerance_atr` is given, N × the series' own mean
  true-range percent — production wires the latter
  (`_EQ_TOLERANCE_ATR` = 0.5, `_EQ_SWING_LOOKBACK` = 5, `_EQ_MIN_TOUCHES` = 3,
  shared by
  `load_dashboard_data` and `app.overview` through `_equal_high_detector` /
  `_equal_low_detector`), since "equal" is a statement about what a market
  treats as one level and that scale is volatility, not a constant. `strength`
  is the volume traded inside the band during the pool's construction window,
  normalized against the series — a channel that *varies* (touch count
  saturated at 1.0 with three touches), but measured **flat**: it does not
  rank pools, and nothing should read it as if it did. Calibration and the corrected measurement harness are in
  `docs/structure_decisions.md`.
- **`liquidity/detectors/base.py`** — also defines `MarketStructureDetector`,
  the abstract port for structure detectors
  (`detect(candles) -> list[MarketStructure]`).
- **`liquidity/detectors/market_structure.py`** — `SwingStructureDetector`:
  detects BOS/CHoCH and HH/HL/LH/LL on the major (swing) structure. As of
  2026-06-16 this detector **mirrors `InternalStructureDetector`'s
  architecture exactly**, differing only in defaults (`swing_lookback=10`,
  `persistence_candles=10`). It uses trailing `active_high`/`active_low`
  references and the same `candidate_choch_<side>` / `candidate_choch_<side>_baseline`
  / `validated_choch_<side>` two-step promotion gate as `InternalStructureDetector`.
  Volume-delta confirmation (`min_volume_delta_ratio`) has been removed entirely.

  **BOS**: The state machine (`active_<side>`, `pending_<side>`, `trend`)
  advances **only when a candle in the leg *closes* beyond the active
  reference** (`find_close_break_index`) — a wick-only overshoot does not
  advance state. On a wick-only break the reference is *frozen* (not trailed
  to the new pivot), so a later candle that closes beyond that same level
  activates the BOS then. A continuation BOS must also satisfy the **BOS
  staircase**: it must extend the leg beyond the previous BOS level
  (`last_bear_bos_low`/`last_bull_bos_high`) — breaking a higher trailing low
  (or lower trailing high) formed during a retrace, which does not beat the
  previous BOS extreme, is not a structural BOS. The staircase is **seeded at
  each CHoCH with the CHoCH level** (the broken reference), so the first BOS of
  the new leg must break beyond the CHoCH level — a BOS cannot form on the
  wrong side of the CHoCH. The
  `BREAK_OF_STRUCTURE` event is *emitted* once confirmed, and that close
  candle optionally passes the LuxAlgo-style confluence filter
  (`bos_confluence`, see `_common.py`). `confluence_filter` (constructor
  parameter, default `True`) enables this shadow-balance check: the breaking
  close candle must have a larger upper shadow than lower shadow (bullish) or
  vice versa (bearish). The emitted BOS `timestamp` is that closing candle's
  timestamp; `price_level` is the triggering pivot's extreme;
  `reference_price_level` is the **formed low/high it broke** (the staircase
  floor captured before it ratchets), mirroring `InternalStructureDetector`
  (`floor or active_<side>`), and re-anchored to the formed level's close-break
  in `load_dashboard_data`.

  **CHoCH**: A counter-trend break is confirmed via **persistence** (same as
  `InternalStructureDetector`): `is_sustained_break` must hold for
  `persistence_candles` consecutive candles beyond the break. The CHoCH
  reference is `validated_choch_<side> or choch_origin_<side> or
  active_<side>`, promoted from `candidate_choch_<side>` via the same
  two-step baseline gate described under `InternalStructureDetector` below.
  The `active_<side>` cold-start fallback ensures the detector can flip
  trend during the bootstrap phase (before any validated/origin reference has
  been built). `reference_price_level` is the reference that was broken;
  `reference_timestamp` is `validated_choch_<side>.timestamp` (when the
  validated reference was used).

  **SWEEP**: A counter-trend wick break that does not hold (`is_sustained_break`
  fails) is a `LIQUIDITY_SWEEP`; timestamp via `find_wick_break_index`.

  `pending_high`/`pending_low` accumulate the most extreme pivot seen since
  the opposite active level was last set, so a BOS/CHoCH reflects the true
  extreme of the prior leg. Every emitted `MarketStructure` has
  `scope = StructureScope.MAJOR`.
- **`liquidity/detectors/internal_structure.py`** — `InternalStructureDetector`:
  detects BOS/CHoCH/`LIQUIDITY_SWEEP`/HL/LH on finer-grained, internal/minor
  structure, with `scope = StructureScope.INTERNAL` stamped on every emitted
  `MarketStructure` (see `app.dashboard_data.load_dashboard_data`, which runs
  it on the same candle series as `market_structure_events`, with a smaller
  per-timeframe `swing_lookback`/`persistence_candles` to surface minor pivots
  within that series — see `_INTERNAL_STRUCTURE_PARAMS` under `load_dashboard_data`).
  A Portuguese walkthrough of the whole BOS/CHoCH pipeline lives in
  `liquidity_hunter/docs/estrutura_bos_choch.md`. Like
  `SwingStructureDetector`, it sources swing pivots from
  `SwingHighDetector`/`SwingLowDetector` (`swing_lookback`) via the shared
  `_common.collect_pivots`, and maintains `pending_high`/`pending_low`
  (the most extreme high/low pivot accumulated for a future promotion). But
  unlike `SwingStructureDetector`, `active_high`/`active_low` are *trailing*
  references — normally the most recently formed swing high/low pivot,
  updated after *every* pivot of that kind (adapted from LuxAlgo's "Smart
  Money Concepts" indicator) — rather than references held until the
  opposite side breaks. A pivot above `active_high` (below `active_low`), in
  the direction of `trend` (or the first such break while `trend` is
  `NEUTRAL`), is a `BREAK_OF_STRUCTURE` on price alone; against `trend` it is
  a `CHANGE_OF_CHARACTER` if confirmed (see below), else a `LIQUIDITY_SWEEP`.
  A pivot below `active_high` (above `active_low`) is a descriptive
  `LOWER_HIGH`/`HIGHER_LOW` label. A purely trailing reference has its own
  failure mode, though: comparing a CHoCH against the last pivot — possibly a
  minor retracement rather than the true extreme of the leg that just ended —
  can spuriously flag a continuation BOS right after the reversal. To avoid
  that, a confirmed BOS/CHoCH promotes the *opposite* side's `pending_<side>`
  to `active_<side>` (or to `None`, if nothing has accumulated there yet —
  the next pivot on that side then silently re-bootstraps with no label, the
  accepted cost of carrying forward "extreme of the prior leg" semantics). A
  `LIQUIDITY_SWEEP`, or a pivot that doesn't break the active reference (a
  HL/LH label), instead folds the *opposite* side's current `active_<side>`
  into its `pending_<side>` via `_extreme`, so that value isn't lost when
  `active_<side>` is later overwritten by its own next pivot. Bootstrapping a
  side (its `active_<side>` was `None`) also seeds `pending_<side>` with the
  same pivot, if the opposite side is already active. `SwingStructureDetector`'s
  freeze — an active reference that happens to equal the extreme of the
  entire remaining candle window can permanently freeze the *opposite* side,
  since it is only promoted once the opposite side breaks — is acceptable for
  `StructureScope.MAJOR`'s "significant level" semantics, but would leave
  `StructureScope.INTERNAL` unable to surface large moves as BOS/CHoCH for
  long stretches. `InternalStructureDetector` avoids this: both references
  keep tracking recent pivots (rather than freezing on either an old extreme
  or a stale promoted value).

  **BOS confirmation**: The state machine advances **only when a candle in the
  leg *closes* beyond the reference** (`find_close_break_index`) — a wick-only
  overshoot does not advance state; the reference is *frozen* (not trailed to
  the new pivot) until a close confirms. A continuation BOS must also satisfy
  the **BOS staircase**: it must extend the leg beyond the previous BOS level
  (`last_bear_bos_low`/`last_bull_bos_high`) — a break of a higher trailing low
  (or lower trailing high) formed during a retrace, which does not beat the
  previous BOS extreme, is not a structural BOS (it just trails the active
  reference). The staircase is **seeded at each CHoCH with the CHoCH level**
  (the broken reference), so the first BOS of the new leg must break beyond the
  CHoCH level — a BOS cannot form on the wrong side of the CHoCH. The
  `BREAK_OF_STRUCTURE` event is
  *emitted* once confirmed, and that close candle optionally passes the
  LuxAlgo-style confluence filter (`bos_confluence`): upper shadow > lower
  shadow for bullish, reverse for bearish. `confluence_filter` (constructor
  parameter, default `True`) enables this check; `load_dashboard_data` exposes
  it so tests can disable it. The BOS `timestamp` is the close-break candle's
  timestamp. A BOS is only *emitted* once a confirming opposite-direction
  pullback pivot forms beyond the pullback reference snapshot
  (`active_<opposite>` at the state-advance). In an **impulsive leg** of
  consecutive same-side pivots with no intervening opposite pivot, the first
  advance nulls `active_<opposite>` (promoting an empty `pending_<opposite>`),
  so a later advance would snapshot a `None` pullback ref and the BOS could
  never confirm — leaving a whole impulsive move with zero BOS. The leg keeps
  extending from the *same* opposite pivot, so a `None` snapshot inherits the
  prior pending BOS's pullback ref, and the continuation BOS still confirms at
  the next opposite pivot. Only the **last** pending of such a run emits, though
  — each advance overwrites the one before it, and the reported floor has
  ratcheted meanwhile, so the tops/bottoms broken in between would go unmarked;
  `stage_superseded_continuation_bos` stages the overwritten pendings
  additively (see `docs/structure_decisions.md`).

  **CHoCH confirmation** is **persistence**-based: a single candle that pokes
  through a level and immediately reverts is a "false break"; a break that
  *holds* beyond for a few candles is "real" — see `_common.is_sustained_break`.
  The constructor's `persistence_candles` (default `5`) is the count of
  consecutive candles (including the breaking one) that must close beyond the
  reference. This check is **not** anchored to the triggering pivot's own index:
  a sustained break is considered confirmed if *any* candle from just after the
  previous pivot of the same kind (exclusive) through the triggering pivot
  (inclusive) starts a window that holds for `persistence_candles` beyond it,
  even if that window extends past the pivot's own index. If no such window
  exists (or there aren't yet enough trailing candles), the pivot is reported
  as a `LIQUIDITY_SWEEP` instead. This replaces the previous
  `volume_delta`/volume-spike confirmation entirely.

  The reversal (`CHANGE_OF_CHARACTER`) reference is the **pullback (origin) of
  the most recent continuation-confirmed BOS**, tracked per side as
  `validated_choch_high` (the level a bullish CHoCH must break) and
  `validated_choch_low` (bearish CHoCH). The promotion pipeline for
  `validated_choch_high` (bearish leg, mirrored on the bullish side):

  1. **BOS emission**: when a bearish BOS is confirmed (pending BOS + LH
     pullback), the confirming LH pivot becomes `candidate_choch_high` —
     *provisional*, not yet the CHoCH reference. A continuation-dedup gate
     ensures each pullback stays below the previous pullback (LH staircase),
     preventing re-emission of the same structural break.

  2. **Continuation-gated promotion**: the next bearish state-advance (a new
     lower-low pivot) promotes `candidate_choch_high` to
     `validated_choch_high` **only if** the new low is below `bear_leg_low`
     (the running extreme of the current bearish leg). A pullback-BOS formed
     during a retrace that does not make a new leg low leaves the candidate
     provisional: that BOS never extended the leg, so its pullback must not
     ratchet the CHoCH reference down. `bear_leg_low` / `bull_leg_high` are
     seeded at each trend flip (CHoCH) and updated on every in-trend
     state-advance.

  2b. **Sweep re-anchor of the candidate**: while the leg unfolds, a
     counter-trend sweep that pokes beyond the current `candidate_choch_<side>`
     re-anchors that candidate to the swept extreme (the high a bearish leg's
     sweep grabbed / the low a bullish leg's sweep grabbed), but only to a
     *more extreme* level (higher for `candidate_choch_high`, lower for
     `candidate_choch_low`). Once price sweeps the prior pullback and then
     resumes the trend to a new leg extreme, the swept level — not the
     pre-sweep pullback — is where the eventual reversal launched from, so the
     CHoCH should break it (the SMC "sweep then expand" pattern). This only
     feeds step 2's continuation-gated promotion; a sweep with no follow-through
     never promotes, so the *validated* reference is untouched.

  3. **Validated reference is frozen**: once promoted, `validated_choch_high`
     stays until consumed by a CHoCH (reset to `None`) or replaced by the
     next genuine continuation promotion. Non-extending BOS do not overwrite
     it, and a sweep can only move the *candidate* (step 2b), never the
     validated level directly.

  A bullish CHoCH fires when, with `trend` BEARISH, a high pivot breaks
  (sustained, per the persistence rule above) above
  `validated_choch_high or choch_origin_high or active_high`; its
  `reference_price_level` is the reference it broke. The `active_high`
  cold-start fallback ensures the detector can flip trend during the
  bootstrap phase (before any validated/origin reference has been built),
  preventing the trend from getting stuck if the initial direction was wrong.
  A high pivot whose break does not hold is a `LIQUIDITY_SWEEP` (trend
  unchanged). A sweep never overwrites the *validated* CHoCH reference, but it
  re-anchors the pullback *candidate* to the swept extreme (step 2b above) so a
  later continuation can promote it.

  **Failed CHoCH (`CHOCH_FAILED`)**: a CHoCH is *provisional* until a
  same-direction BOS confirms it (that first BOS is beyond the CHoCH level by
  the staircase floor). While unconfirmed it carries an *origin*
  (`bull_choch_origin`/`bear_choch_origin` — the active low at a bullish CHoCH
  / active high at a bearish CHoCH, the swing the CHoCH move launched from). A
  sustained break back through that origin *before* a confirming BOS emits a
  `CHOCH_FAILED` (direction = the failed CHoCH's direction,
  `reference_price_level` = the broken origin) and flips the trend back. This
  supersedes the older `choch_origin` blind-spot recovery for the unconfirmed
  window at a tighter level. The origin is retired on the confirming BOS (the
  CHoCH can no longer fail) or at the next trend flip; a failed-CHoCH flip does
  not arm the opposite origin (one-shot, no ping-pong).

  **One-shot origin (blind-spot fallback)**: the moment a CHoCH fires, all
  validated/candidate state is reset. `choch_origin_<opposite>` is the
  extreme of the leg the CHoCH just reversed (set only by a *validated*-
  triggered CHoCH, one-shot). The CHoCH check uses `validated or origin`, so
  the origin serves as fallback until a validated reference is rebuilt. An
  origin-triggered CHoCH does NOT set origin on the opposite side (one-shot),
  breaking ping-pong chains.

  A `BREAK_OF_STRUCTURE`'s `reference_price_level` is the **formed low/high it
  broke** — the staircase floor (`last_bear_bos_low`/`last_bull_bos_high`) in
  effect at the state-advance, captured into `_PendingBOS.floor` before it
  ratchets to the breaking pivot — rather than the trailing `active_<side>` the
  state machine advanced on. So a continuation BOS reports (and plots at) the
  prior swing extreme it actually broke, forming a clean descending/ascending
  staircase of levels. The **first BOS of a leg** is seeded at the trend flip:
  `prev_bear_bos_extreme`/`prev_bull_bos_extreme` (the reported-floor tracker) is
  set at each CHoCH to the CHoCH's *confirming* extreme (`price` at the flip — the
  fundo/topo the reversal formed), so that first BOS references the level the leg
  actually launched from and, via the close-break re-anchor, confirms only on a
  close beyond it — rather than the trailing `active_<side>` that ratchets to a
  shallow retrace pivot during the pullback (the "reference climbs with trailing"
  bug: e.g. an M30 first bearish BOS reporting a 62,402 higher-low instead of the
  61,870 CHoCH fundo). This is distinct from the staircase *gate*
  (`last_bear_bos_low`/`last_bull_bos_high`), seeded at the CHoCH with the *broken*
  reference. The state machine, trailing references,
  and CHoCH promotion are unaffected — only the reported reference changes. A
  composition-level pass then re-times each BOS to the first *close* beyond that
  level and drops wick-only continuations (see `load_dashboard_data` below).

  The pivot loop above decides *which* event fires and *against which*
  reference level, but does not itself supply that event's `timestamp` for
  `BREAK_OF_STRUCTURE`, `LIQUIDITY_SWEEP`, and `CHANGE_OF_CHARACTER` — using
  the triggering pivot's own timestamp there would plot the marker at the
  extreme of the *new* leg (where the pivot forms) rather than the candle
  that actually broke the prior level, visually "lagging" the break. Instead,
  once a break is decided, a backward scan over the candles between the
  previous pivot of the same kind (exclusive) and the triggering pivot
  (inclusive) locates the actual breaking candle: `_common.find_wick_break_index`
  for `BREAK_OF_STRUCTURE`/`LIQUIDITY_SWEEP` (the first candle whose high/low
  wick crosses `active_<side>`, price-only), and `_common.find_sustained_break_index`
  for `CHANGE_OF_CHARACTER` (the first candle at which `is_sustained_break`
  against `validated_choch_<side>` holds). The emitted event's `timestamp` is
  that candle's timestamp; `price_level` remains the triggering pivot's own
  `price` — the true extreme of the move — and `reference_price_level` is
  unchanged either way (`active_<side>.price` or
  `validated_choch_<side>.price`). `LOWER_HIGH`/`HIGHER_LOW` labels are
  unaffected — they describe the pivot itself, not a break, so they keep the
  pivot's own timestamp/price.
- **`liquidity/detectors/_common.py`** — shared helpers used by both structure
  detectors:
  - `validate_candles`, `price_range`, `Pivot`, `collect_pivots` — unchanged
  - `is_sustained_break` — whether a break of `active_price` holds for
    `persistence_candles` consecutive closes
  - `find_wick_break_index` — first candle whose wick crosses a level (BOS/SWEEP
    timestamp attribution)
  - `find_close_break_index` — first candle whose **close** crosses a level;
    returns `None` if only a wick breach occurred (no confirming close)
  - `find_sustained_break_index` — first index at which `is_sustained_break`
    holds (CHoCH timestamp attribution)
  - `bos_confluence(candle, *, bullish)` — LuxAlgo-style shadow-balance check:
    `upper_shadow = high - max(close, open)`, `lower_shadow = min(close, open) - low`;
    bullish requires `upper_shadow > lower_shadow`, bearish the reverse. Mirrors
    LuxAlgo's "Confluence Filter" (`bullishBar`/`bearishBar` in Pine source).
- **`liquidity/detectors/poi.py`** — `POIDetector`: detects MSB-anchored order
  block zones, a **faithful batch port** of the "Market Structure Break &
  Order Block" TradingView indicator (EmreKb, MPL 2.0) — verified to
  reproduce the indicator's on-chart boxes exactly on real BTCUSDT 15m data
  (2026-07-11). **Self-contained**: `detect(candles) -> list[POIZone]` — it
  derives its own swing pivots rather than consuming structure events
  (deliberately a separate, simpler structure read than
  `InternalStructureDetector`). Constructor: `pivot_len` (default `9`, the
  indicator's "ZigZag Length") and `fib_factor` (default `0.33`).

  **Pivots (Pine `barssince` semantics)**: a rolling `pivot_len` window
  tracks the swing state — a candle whose high is the window max turns the
  swing up (`to_up`), one whose low is the window min turns it down. Each
  swing flip records the completed leg's extreme measured over a **local**
  window — the bars since the previous opposite *signal*
  (`ta.barssince(to_up[1])`, min 1 bar), **not** since the last opposite
  pivot. In choppy stretches these local windows are shorter than the full
  leg, renewing pivots faster and flipping the market state machine more
  often — porting this exactly is what makes the output match TradingView (a
  prior "leg extreme since the opposite pivot" variant produced fewer, later
  MSBs and missed real flips). The pivot index is the most recent bar whose
  own low/high equaled its running window extreme.

  **MSB**: with the market bullish, a new low pivot `l0 < l1 − fib_factor ×
  |h0 − l1|` confirms a bearish MSB (the bullish mirror breaks `h1` by
  `fib_factor × |h1 − l0|`). The market starts bullish. After a flip, both
  the high and low pivot **values** must change before another flip can fire
  (the indicator's `ta.valuewhen` guard, compared by value). The MSB
  confirms on the swing-flip candle that records the breaking pivot.

  **Zones**: each MSB emits up to two same-direction zones, anchored by
  **running scans** re-evaluated every bar exactly like the indicator,
  including its `[pivot_len]`-lagged window bound (the scan uses `l0i`/`h0i`
  as known `pivot_len` bars ago): `ORDER_BLOCK` = last opposite-direction
  candle in `h1i → l0i[pivot_len]` (bullish; bearish mirror in
  `l1i → h0i[pivot_len]`); `BREAKER_BLOCK`/`MITIGATION_BLOCK` = last
  *same*-direction candle in `l1i − pivot_len → h1i` (bullish; bearish
  mirror) — breaker when the impulse-origin extreme swept the prior one
  (bullish `l0 < l1`, bearish `h0 > h1`), else mitigation. Because the scans
  are running state, an anchor persists from earlier windows when the
  current window has no matching candle (faithful to the indicator). All
  zones span the anchor candle's full high-low range.

  **Lifecycle (all kinds)**: a **single close** beyond the far boundary
  *breaks* a zone (touches inside never do), but a broken zone does not retire
  *itself*. The indicator holds its boxes in four FIFO arrays (Bu-OB, Be-OB,
  Bu-BB, Be-BB) whose delete helper shifts the array's **front**, so each break
  retires the **oldest** box of that queue, and a box that stays broken keeps
  shifting the queue every later bar until it reaches the front itself — a
  running cull that leaves only the recent, unbroken shelves. Reading the rule
  per zone instead was the port's one divergence from the on-chart picture, and
  it was the cause of the accumulated-box clutter: measured on ZECUSDT H1
  (1500 candles, 2026-08-18), per-zone retirement leaves 10 active zones while
  the queue rule leaves 4 — three order blocks, exactly the three boxes the
  indicator draws on the same series. `retire_fifo=False` restores the per-zone
  rule. The indicator's five `na` array slots (which make its first five
  retirements per queue delete nothing) are deliberately *not* reproduced: they
  are consumed long before a production window's visible range (verified
  identical on ZECUSDT H1) and would suppress retirement entirely on a short
  series. There is no MITIGATED state and no RTO sweep events (removed with the
  old CHoCH→BOS detector).

- **`liquidity/detectors/consolidation.py`** — `detect_consolidation_ranges`
  and `stage_breakout_events`,
  **pure post-passes** (not a `MarketStructureDetector`): the first scans the
  quiet segments between structure advances for confirmed
  `ConsolidationRange`s.
  Inputs: the candle series plus `(candle index, established-trend direction)`
  advance boundaries; a range may never span an advance. Confirmation =
  `min_candles` candles inside a box no taller than `max_height_pct` (the
  caller resolves it as N × the series' mean true-range%) with alternating
  edge-zone touches (compressed top/bottom sequence ≥ 3 over the outer 25%
  zones, so a one-way drift inside the cap does not qualify). A confirmed box
  absorbs candles while total height stays within the cap; an unabsorbable
  poke either resolves the range (`is_sustained_break` beyond the boundary,
  `resolve_persistence` closes) or is a boundary sweep left outside the frozen
  box. Run at the composition level over the *surviving* internal event
  stream (see `load_dashboard_data`), **not** inside the detector — an
  in-detector variant was measured and reverted (a detector advance later
  dropped as wick-only split BTC H1's July 2026 box at an invisible point).
  `stage_breakout_events` (phase 2) stages one additive `MarketStructure`
  per range resolved by a sustained boundary break, at the breakout candle:
  a real BOS when the break continues the segment's standing trend (the
  direction of the advance that opened the segment), a `provisional=True`
  CHoCH when it reverses it (the additive contract — replay consumers skip
  it, the chart shows the dimmed `CHoCH?`), both referencing the broken
  boundary with `reference_timestamp` at its first forming candle. Nothing
  is staged for advance-resolved ranges, bootstrap segments, or when a real
  same-direction BOS/CHoCH sits within the dedup window of the breakout.
  Calibration + the motivating BTC/ETH H1 locks + the phase-2 measurement
  (+7/−0 on the live matrix, trend unchanged) are documented in
  `liquidity_hunter/docs/structure_decisions.md`.

All detectors are re-exported from `liquidity_hunter.liquidity`.

