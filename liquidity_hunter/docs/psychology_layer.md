# Psychology layer

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Psychology layer (`liquidity_hunter/psychology`)

- **`psychology/analyzers/base.py`** — `RetailBiasEstimator`, the abstract
  port all retail bias estimators implement
  (`analyze(symbol, higher_timeframe_direction, market_structure_events,
  liquidity_zones, current_price) -> RetailBiasEstimate`). The plain-domain-type
  inputs double as a feature set, so a future ML-based estimator can
  implement the same interface as a drop-in replacement.
- **`psychology/analyzers/retail_trap.py`** — `RetailTrapAnalyzer`, a
  rule-based `RetailBiasEstimator`. Combines the higher timeframe trend,
  the most recent `MarketStructure` event, and nearby `LiquidityZone`s to
  estimate retail crowd psychology (e.g. "buying a perceived bottom against
  the higher timeframe trend").
- **`psychology/models.py`** — `RetailBiasEstimate`: `dominant_side`
  (`RetailPositioning`: LONG/SHORT/NEUTRAL), `confidence` (0-100), and a
  human-readable `explanation`. Distinct from `core.domain.RetailBias`,
  which represents a *measured* sentiment observation rather than an
  *inferred* one.

The full estimation logic (confidence formula and worked example) is
documented in `liquidity_hunter/docs/psychology.md`.
- **`psychology/analyzers/manipulation_cycle.py`** —
  `ManipulationCycleDetector`: connects existing observations (liquidity
  zones, `LIQUIDITY_SWEEP` events, BOS events, volume delta) into
  three-phase Wyckoff/SMC manipulation cycles. Works in two
  modes: **retrospective** (for each sweep event, looks backward for
  accumulation near a liquidity zone and forward for an expansion BOS) and
  **prospective** (scans active zones where price is currently consolidating,
  reporting `IN_PROGRESS` `ACCUMULATION` cycles). Constructor parameters:
  `proximity_pct` (default `0.015` = 1.5%), `min_accumulation_candles`
  (default `None` → resolved per timeframe from
  `_TIMEFRAME_MIN_ACCUMULATION`: M1=20, M5=15, M15=10, M30=7, H1=7, H4=3,
  D1=2, W1=2), `max_expansion_candles` (default `30`). Zone deduplication:
  nearby prospective zones are clustered per side within `proximity_pct`
  (keeping the strongest), and zones already targeted by a sweep-based cycle
  are excluded from prospective results via proximity matching.

- **`psychology/analyzers/behavior_divergence.py`** —
  `BehaviorDivergenceAnalyzer`: cross-references `volume_delta_series` with
  `LiquidityZone` proximity and `MarketStructure` events to detect when
  institutional flow opposes visible price direction. Produces
  `list[BehaviorDivergence]` with four divergence types:
  - **Distribution**: price rising + negative VD near a buy-side zone →
    institutional selling into retail buying.
  - **Accumulation**: *no longer emitted* (removed 2026-07-29). The falling-price
    mirror measured **against its own thesis** across 30 live combos — 22% hit
    rate at 20 candles (mean −0.65%) vs distribution's 83% (+3.39%) on the same
    sample: aggressive buying absorbed by a falling market is the falling-knife
    signature, not accumulation. The `DivergenceType` member survives for a
    producer that can measure it properly (trade-level flow).
  - **Exhaustion**: VD magnitude declining after a BOS while price continues
    trending → move losing momentum.
  - **Absorption**: high volume + small price movement near a zone → large
    orders being absorbed at a key level.
  Constructor parameters: `window_size` (default `None` → resolved per
  timeframe from `_TIMEFRAME_WINDOW`: M1=20, M5=15, M15=10, M30=7, H1=7,
  H4=5, D1=5, W1=3), `proximity_pct` (default `0.02` = 2%),
  `min_price_change_pct` (default `0.005` = 0.5%), `min_vd_ratio` (default
  **`0.05`** = 5% of average volume; at the previous `0.1` the distribution
  layer was effectively off — 3 events across 30 live combos. `proximity_pct`
  is *not* the binding gate: widening it 0.02 → 0.05 moved the count by one).
  Deduplication keeps only the
  highest-confidence event per type within a window-sized range.

- **`psychology/analyzers/leverage_liquidation.py`** —
  `LeverageLiquidationEstimator`: builds a `LeverageLiquidationMap` from
  perpetual-futures market state. `estimate(symbol, timeframe, current_price,
  liquidity_zones, open_interest, funding, long_short) -> LeverageLiquidationMap`.
  Infers the over-leveraged side from a signed positioning score (funding sign
  + long/short account ratio, each normalized to [-1, 1] and averaged; OI
  growth amplifies `positioning_intensity`): score > `_NEUTRAL_THRESHOLD`
  (0.1) → LONG (crowded), < -threshold → SHORT, else NEUTRAL. Then projects
  `LiquidationBand`s around unmitigated liquidity-zone entries (midpoint =
  entry) at leverage tiers `_LEVERAGE_DISTANCE_PCT` (10x=9.5%, 25x=3.6%,
  50x=1.6%, 100x=0.6%, from Binance tier-1 maintenance margin). **Both sides**
  are emitted (long-liquidation pool below entries, `SELL_SIDE`; short-liquidation
  pool above, `BUY_SIDE`); the non-dominant side's intensity is dampened by
  `_NON_DOMINANT_FACTOR` (0.45) so the over-leveraged side stays prominent. Band
  intensity (0-100, peak-normalized across both sides) = `side_scale ×
  entry.weight × _LEVERAGE_POPULATION_PRIOR[lev]` (10x most common → hottest).
  Entry anchors come from `_entry_anchors`: liquidity zones with `strength > 0`
  **including mitigated ones** (real past entry areas, downweighted by
  `_MITIGATED_ENTRY_FACTOR`=0.7) **and order blocks** (`poi_zones`, weight
  `_POI_ENTRY_WEIGHT`=1.0, invalidated dropped — order
  blocks concentrate real institutional volume), merged within
  `_ENTRY_CLUSTER_PCT` (0.4%,
  keep strongest), then at most `_MAX_ENTRY_CLUSTERS` (16) kept **spread evenly
  across price** via `_bucket_select` (strongest per equal-width price bucket) —
  so coverage isn't monopolized by the densest cluster and bands appear across
  the whole range, not just one region. NEUTRAL positioning or empty inputs →
  no bands.
  Each kept band is time-bounded via `candles`: `start_time = zone.formed_at`,
  `end_time = _liquidation_hit_time(...)` (first candle at/after start whose
  wick reaches the liquidation level, `None` if never — still live). The
  hit-scan runs only for the top-`_MAX_BANDS` bands.

- **`psychology/analyzers/oi_regime.py`** — `OIRegimeAnalyzer`: produces an
  `OIAnalysis` from candles + `OpenInterestPoint` history + structure events.
  `analyze(candles, open_interest, structure_events) -> OIAnalysis`. Two
  outputs: (1) **current regime** — the price × OI matrix over a rolling
  window (timeframe-adaptive `_TIMEFRAME_WINDOW`, same values as
  `BehaviorDivergenceAnalyzer`), `FLAT` unless both `min_price_change_pct`
  (default 0.2%) and `min_oi_change_pct` (default 0.3%) floors are met,
  intensity saturating at 4× each floor; (2) **event qualification** — for
  each BOS/CHoCH/`LIQUIDITY_SWEEP` (pivot labels and `CHOCH_FAILED` are
  skipped), the OI delta measured from `window` candles before the event
  through **one candle after** it (OI samples mark period ends, so the
  breaking candle's own OI change lands at the next sample — required to see
  a sweep's liquidation flush). A sweep with OI dropping ≥ `flush_oi_drop_pct`
  (default 0.5%) is a `FLUSH`; otherwise ±`min_oi_change_pct` splits
  `NEW_MONEY`/`COVERING`/`FLAT`. Events outside OI coverage are skipped, not
  guessed. OI alignment is by bisect (latest sample at/before each timestamp).

- **`psychology/analyzers/supertrend_break.py`** — `SupertrendBreakAnalyzer`:
  qualifies *who paid for* each Supertrend flip. The band is a public,
  mechanical level — everyone who entered on the previous flip keeps a stop
  there — so breaking it is a liquidity event as much as a trend event.
  `analyze(candles, points, structure_events=…, market_control=…,
  oi_analysis=…, volume_spread_signals=…) -> list[SupertrendBreak]` crosses
  each flip with four components: a **reclaim** (a *close* back inside the
  broken band within `reclaim_candles`, default 5 — a wick back inside is not
  a reclaim — where price also travelled at least `min_excursion_atr` = **1.0**
  mean-true-range beyond the band before returning), **fresh money** (the credited `controller` from the
  market-control series at the flip candle), **structural confirmation** (a
  non-provisional same-direction BOS/CHoCH within `confirm_candles`, default
  10), and an **exhaustion signature** (a same-direction OI `FLUSH` or a VSA
  climax/thrust on the raided side, within ±1 candle). Verdict
  (`SupertrendBreakQuality`): `GENUINE` = fresh money **and** structure agreed;
  `STOP_RUN` = a reclaim **plus** either no fresh money behind the flip or an
  exhaustion fingerprint; everything else `UNKNOWN`. Absence of fresh money
  alone never accuses a break — the reclaim is the positive evidence, the same
  discipline that keeps `HuntCaptureQuality` precise. A `STOP_RUN` is only
  knowable in retrospect, so a fresh flip whose reclaim window has not elapsed
  reads `UNKNOWN` until it resolves (the live-edge honesty of the detector's
  provisional marks). Every context input is optional: a spot symbol still
  gets its flips listed, unqualified. The excursion gate is what makes the
  label mean anything: without it the reading is dominated by the indicator's
  own whipsaw (a flip that reverses on the next bar without ever leaving the
  level attracted nobody into the break). Measured 2026-07-24 across
  BTC/ETH/SOL × 15m/1h/4h, 267 flips: no gate → 151 stop runs (57%), 0.5 ATR →
  113, **1.0 ATR → 58 (22%, ~6 per chart)**, 1.5 ATR → 29. `GENUINE` is rare by
  construction (4/267): `controller` credits a side only in the OI-rising
  quadrants, so demanding fresh money *and* structural confirmation is a strict
  bar — deliberately, since only `STOP_RUN` is drawn. `SupertrendBreak`
  (`core/domain/supertrend.py`) carries `timestamp`, `direction`,
  `broken_level`, `quality`, `reclaim_timestamp`/`reclaim_candles`,
  `controller`, `structure_confirmed`, `evidence` (the components that shaped
  the verdict) and `description`.
- **`psychology/analyzers/market_control.py`** — `MarketControlAnalyzer`:
  answers *who is in control of the tape right now?* by crossing **CVD
  aggression** (net taker delta over a per-TF window, normalized by window
  volume) with **open interest** — the classic futures matrix, but on the
  *aggression* axis instead of price, so a move that ticks up on no real buying
  isn't mistaken for buyer control. `analyze(candles, open_interest) ->
  MarketControlState | None` (`None` for spot/no-OI). It reuses `OIRegime` as
  the quadrant label (buy-agg+OI↑ = `LONG_BUILDUP`, sell-agg+OI↑ =
  `SHORT_BUILDUP`, buy-agg+OI↓ = `SHORT_COVERING`, sell-agg+OI↓ =
  `LONG_LIQUIDATION`). `controller` (`MarketControlSide`:
  `BUYERS`/`SELLERS`/`BALANCED`) credits a side **only in the OI-rising
  quadrants** (fresh money behind the aggression); covering/liquidation are
  position-closing → `BALANCED`. `control_score` is the signed conviction
  oscillator in `[-100, 100]` (sign = aggressor side, magnitude = conviction:
  amplified when OI confirms, attenuated when it diverges). A `fade_warning`
  field (`True` exactly when a side was credited — the "don't enter against the
  controller" flag) was **removed 2026-08-21**: the claim did not survive
  measurement (see the badge note under the KPI row), and it was pure redundancy
  over `controller` that no consumer read. Same `_TIMEFRAME_WINDOW` as `OIRegimeAnalyzer` so the
  two axes are measured over one horizon, and the window's OI is read at the
  last candle's **close** (`_oi_at_close`) — an OI sample carries the OI at its
  own timestamp, so the sample sharing a candle's timestamp is that candle's
  *open* and the displacement it produced only lands one period later. Reading
  the at-or-before sample inverted the verdict on exactly the candle that
  matters: a liquidation flush read `LONG_BUILDUP` (fresh money) at the moment
  of exhaustion. Not lookahead — the candle closes when that sample publishes —
  and the same one-period shift `OIRegimeAnalyzer` already applies to qualified
  events; at the live edge it falls back to at-or-before, as it does across an
  OI coverage gap wider than one period. Measured 2026-08-20 across
  BTC/ETH/SOL x 15m/1h/4h: 0.5-6% of candles change regime (mostly `FLAT`
  boundary crossings) and 0.5-2.4% change controller, while of 53 short-squeeze
  candles (close +0.8% or more on OI falling 0.5% or more) 5 changed and **all
  five moved from `LONG_BUILDUP` to `SHORT_COVERING`/`FLAT`** — none the other
  way; the 8 that still read `LONG_BUILDUP` are the trailing window's own OI
  ramp dominating, not the alignment. Besides the current-window snapshot
  it also emits a **rolling `series`** (`list[MarketControlPoint]`:
  `timestamp`, `control_score`, `controller`, `regime` per candle with OI
  coverage) for the chart oscillator. `regime` is on the point (not only the
  snapshot) because `controller` alone cannot distinguish buy aggression backed
  by fresh longs from buy aggression that is only shorts covering — both are
  real observations, but only the first is *control*, and collapsing the second
  into `BALANCED` made an exhaustion rally indistinguishable from a dead tape. `MarketControlState`/`MarketControlPoint` live in
  `core/domain/market_control.py`, the `MarketControlSide` enum in
  `core/domain/enums.py`.

All nine are re-exported from `liquidity_hunter.psychology`.

