# Project status

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

## Project status

Core domain, data, indicators, liquidity detectors, scoring, psychology,
FastAPI API, and React frontend (main chart + sidebar) are all implemented.

**The full changelog of structure-detector design decisions and confirmed
behaviors lives in `liquidity_hunter/docs/structure_decisions.md`** (extracted
from this file to stay under its size limit). Read it before touching the
`InternalStructureDetector` / `SwingStructureDetector` pipeline — it documents
every production flag wired in `load_dashboard_data`, the measurements behind
each, the rejected alternatives, and the real-data regression fixtures. Current
state in brief:

- **Both structure detectors share one unified architecture**: trailing
  `active_high`/`active_low` references, the
  `candidate_choch_<side>` / `_baseline` / `validated_choch_<side>` two-step
  promotion gate, persistence-based CHoCH confirmation (`is_sustained_break`),
  and the LuxAlgo `bos_confluence` filter. No `volume_delta` in any
  confirmation. Defaults: major `swing_lookback=10`/`persistence_candles=10`,
  internal `swing_lookback=2`/`persistence_candles=5`. They diverge only in
  what an emitted BOS *reports* as its reference and in composition-level
  passes applied to the internal stream.
- **BOS**: state advances only on a *close* beyond the reference
  (`find_close_break_index`); a continuation must extend the BOS staircase
  (`last_bear_bos_low`/`last_bull_bos_high`), seeded at each CHoCH with the
  CHoCH level. Composition passes re-time each BOS to its first close beyond
  the formed level and drop wick-only continuations
  (`_reanchor_bos_close_break`, `_drop_pre_break_reference_bos`).
- **CHoCH**: persistence-confirmed against
  `validated_choch_<side> or choch_origin_<side> or active_<side>`; the
  validated reference is the leg origin of the most recent continuation BOS.
  `CHOCH_FAILED` reverts the trend when the origin is reclaimed before a
  confirming BOS.
- **Production flags** (all wired in `load_dashboard_data`, gated off by
  default in the detector; see the doc for each): staleness/chain re-anchor,
  impulse + wick-rejected + reversal-eaten BOS staging
  (`stage_reversal_eaten_bos`: the pending continuation a reversal's reclaim
  pivot discards without emitting is staged at its close-break, so the last
  fundo/topo before a CHoCH keeps its BOS) + superseded-continuation staging
  (`stage_superseded_continuation_bos`: the sibling case where the pending is
  overwritten by the *next same-direction advance* — an impulsive run of
  consecutive same-side pivots with no pullback pivot between — so each
  top/bottom that formed and broke keeps a mark instead of only the run's last
  one) + re-fire intermediate staging (`_STAGE_REFIRE_INTERMEDIATE_BOS`, a
  composition-level *post-pass* — a re-fired `CHoCH ↻` reads its whole
  excursion as one reversal, so the fundo/topo the original failed CHoCH
  formed, which the resumed leg closed straight through, prints no staircase
  step; staged at that level anchored on the `✕`. Deliberately outside the
  detector: an in-detector variant fed `_drop_failed_refire_cycles`'s
  `refire_worked` guard and rewrote settled structure), first-pending pullback seed at the CHoCH origin
  (`bos_pullback_seed_choch_origin`: the first pending BOS of a CHoCH-launched
  leg often snapshots a `None` pullback ref — the flip promoted an empty
  `pending_<side>` and the `None`-inheritance only covers continuations — so
  it can never confirm and the reverse-CHoCH reference family is never built;
  a persistent launch-pivot snapshot of the CHoCH origin seeds it, the ENAUSDT
  H4 2026-06 case where a −22% drop printed only sweeps under a stuck bullish
  trend), leg-origin CHoCH reference family, leg-bound clamp on it
  (`bos_leg_origin_leg_bound`: a `pullback_ref` snapshot that predates
  `trend_flip_index` — carried in by the impulsive-leg `None`-inheritance or the
  CHoCH-origin seed — is replaced by the leg's own last same-side pivot up to
  the BOS close-break (else its raw extreme measured from `flip + 1`, since the
  flip candle is the CHoCH's own impulse), so the reversal reference is the
  higher low / lower high the leg built instead of the previous, already-consumed
  cycle's level; the BTCUSDT H1 2026-07 case where the bullish leg's `CHoCH ▼`
  fired four days late against the 07-16 bearish cycle),
  volatility-normalized release gap, new-cycle weak-ref barrier,
  confirmed-trend barrier (`choch_confirmed_trend_persistence_candles`,
  hysteresis: a trend is *pending* until an emitted BOS confirms it — cheap
  reverse flips — then a counter-CHoCH must sustain the barrier persistence,
  so a single stop-hunt poke reports as a sweep),
  pending-CHoCH invalidation at the broken level
  (`choch_pending_fail_at_broken_level` + its own persistence: an unconfirmed
  CHoCH also dies on a sustained reclaim of the very level it broke, even
  structural — so an impulsive counter-move that never printed a BOS can't
  hold a stale trend through a full recovery),
  CHoCH-failure noise band (`choch_fail_level_buffer_atr` = 0.5: a level-armed
  failure's reclaim must *clear* the broken level by N×mean-TR%, not touch a
  hair past it — every failure check measured a bare price at base persistence
  2, so an ordinary retest negated the reversal; the BTC M15 2026-07-25 CHoCH
  killed by a 0.37-ATR dip that re-fired an hour later into a +1.9% leg. Origin
  reclaims keep the bare price — the escape valve is never hardened),
  failed-CHoCH re-activation (`choch_failed_rearm`: a `CHOCH_FAILED` arms the
  broken level as a re-arm reference — a later sustained break back beyond it
  re-fires the CHoCH, so a failure whose "reclaim" was the old trend's last
  gasp doesn't leave the resumed move printing as sweeps under the wrong
  trend — the MUUSDT H4 stuck-bullish crash; the re-arm pivot carries the
  *failure's* timestamp so the re-fired CHoCH's line starts at the `✕`, drawn
  by the frontend with a `↻` suffix; a re-fire that itself failed is collapsed
  with its failure by the composition pass `_drop_failed_refire_cycles` — a
  re-fire matched by the failure's timestamp at its `reference_timestamp` *or*
  by a prior same-direction failure at the exact same `reference_price_level`
  (a structural re-attempt of the same level, the ENAUSDT 4H 0.07463 cluster) —
  the ✕ → CHoCH → ✕ stack reads as the original ✕ alone, and a later surviving
  re-fire's `reference_timestamp` is remapped to the surviving ✕),
  persistent re-arm memory (`choch_failed_rearm_persistent`: every failure —
  re-fires included — re-arms the level, and opposite-trend confirmation
  *demotes* the memory instead of retiring it: a demoted re-arm vs a live
  fallback is arbitrated by whichever level price crosses first in the break
  direction, so a far armed level can't shadow a nearer live reference — the
  BTCUSDT D1 2025-10 sweep-shaped top, where the given-back rally re-fires
  the CHoCH at the proven level a month before the weak trailing reference),
  live-edge CHOCH_FAILED emission (`choch_fail_live_edge`: the pivot-gated
  failure check runs once more over final state at the end of `detect`, so a
  relentless one-way move that never forms a swing pivot cannot leave a
  long-since-invalidated CHoCH holding the wrong trend at the live edge),
  shallow-pullback promotion, close-confirmed structural floor, provisional
  live-edge BOS/CHoCH marks (including `emit_provisional_continuation_bos`: the
  standing-`pending_bos` route also covers a *continuation*, closing the hole
  where the advance had landed but the leg already had a confirmed BOS — the
  tail-scan route only fires before the advance, so a trending leg went unmarked
  for the whole swing-lookback lag, and `keep_provisional_bos_under_reversal`:
  a live-edge `CHoCH?` no longer erases the live-edge `BOS?` it reversed — the
  pair is the *sequence* of the ordinary reversal, the leg closing through its
  own floor before turning, and erasing it destroys the observation that dates
  the turn; safe because provisional marks never terminate another event's
  line, and the two staleness/noise guards on the live-edge `CHoCH?`:
  `provisional_choch_require_live` — a *sustained* reclaim of the bare
  reference retires every break that predates it, honouring the repaint the
  emission always promised, and `provisional_choch_break_buffer_atr` = 0.5 —
  the break must clear the reference by N×mean-TR%, mirroring
  `choch_fail_level_buffer_atr` on the failure side; at `persistence_candles=2`
  a bare level was cleared by two closes 0.17% beyond it, and the resulting mark
  then sat mid-chart for 25 candles under a +6% rally, the SOLUSDT H4 2026-08-15
  case), fast-fizzle marker (with the origin-buffer gate
  `choch_fizzle_reclaim_origin_buffer_atr` = 1.0: the fizzle reclaim must
  recover the leg *origin* ± N×mean-TR%, not merely retest the broken level —
  a routine pullback into the counter-zone no longer paints a `CHoCH✕`; the
  ZEC H1 / BTC M5 over-fire), failed-CHoCH whipsaw fixes,
  displacement release, weak-ref failure at the broken level, staircase
  rollback on a discarded phantom advance, displacement-success
  CHoCH-origin retirement (an impulsive reversal that emitted no BOS is not
  marked a false `CHOCH_FAILED` on its pullback; its ATR threshold is capped
  at `choch_success_displacement_max_pct` = 20% of price, where the ATR unit
  degenerates on volatile dailies — the AERO 1D −31% V-reversal), and the scoped
  consolidation cycle reset (`_CONSOLIDATION_RANGE_RESET_CYCLE`, a second
  `detect(range_resets=…)` pass re-seeding references onto the ACTIVE range's
  boundaries — active-only, measured 0/20 change). A `CHOCH_FAILED`'s reclaim
  scan is also bounded to *after* the CHoCH formed (`*_choch_arm_index`), so a
  failure can never be timestamped before the CHoCH it invalidates.
- **Consolidation (lateral range) observation + breakout staging** (phases
  1–2, 2026-07-14): a composition-level post-pass over the surviving event
  stream turns the detector's correct silence inside a range into explicit
  `ConsolidationRange`s (chart box + ladder chip + line truncation; trend
  untouched), and each sustained boundary breakout stages one additive event
  at the broken boundary — a real BOS with the segment trend, a
  `provisional=True` CHoCH against it (replay-skipped). Measured +7/−0 on
  the live matrix, `final_trend` unchanged. **Phase 3, scoped cycle reset**
  (flag `_CONSOLIDATION_RANGE_RESET_CYCLE`, default OFF): re-seeds the state
  machine's references onto the **ACTIVE** range's boundaries (a second
  `detect(range_resets=…)` pass fed the scanner's `RangeReset` directives),
  so while price sits in the box the references track the box instead of
  pre-range levels. Scoped to the one live range only — the blanket re-seed
  of all history was measured and rejected (20/20 churn, rewrote settled
  structure, flipped ETH 4H's July conclusion); active-only measures 0/20
  structural changes, 0 trend flips (just BTC 4H's spurious mid-box `BOS?`
  dropped). Conservative: suppresses mid-box provisional clutter + anchors
  the forming breakout mark at the boundary, but does not itself flip the
  trend at range exit (a range un-scopes on resolution). Full cycle-reset
  (re-seed persisting through resolution + `CHOCH_FAILED` preserved) is
  deferred. See `docs/structure_decisions.md`.

**Not yet implemented**:
- Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
  `invalidated_at` for the swept zone.
- Composite multi-timeframe confluence score (phase 2 of the score plan):
  per-TF signed sub-scores from OB/Sweep/EQL/VOL/RSI-div/Hunt with exposed
  components; requires porting RSI(14) + divergence detection (today
  frontend-only, `MainChart.tsx`) into `indicators/`.
- React frontend liquidity targets, retail trap, and market structure
  sidebar panels.
- **Order flow proper** (footprint / DOM). The project has order flow
  *aggregated per candle* (`volume_delta`, CVD, VSA, `MarketControlAnalyzer`,
  `OIRegimeAnalyzer`) and volume-at-price (`indicators.volume_profile`), but
  no trade-level tape and no order book: no true delta-at-price, no
  footprint chart, no DOM. Would need `aggTrades` (or a websocket) plus
  persistence — a new provider and data path, not a parameter of the
  profile. See `docs/volume_profile.md`.
