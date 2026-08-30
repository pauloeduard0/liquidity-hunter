# Domain entities

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Domain entities (`liquidity_hunter/core/domain`)

All domain entities subclass `DomainModel` (`core/domain/base.py`), a Pydantic
`BaseModel` configured as **immutable** (`frozen=True`), with `extra="forbid"`
and `validate_assignment=True`. New entities should follow this pattern.

- **`Candle`** — a single OHLCV price bar, including `taker_buy_volume`
  (taker buy base asset volume, the basis for `indicators.volume_delta`);
  validates high/low consistency against open/close and
  `taker_buy_volume <= volume` in `model_validator`s.
- **`LiquidityZone`** — a price region holding resting liquidity (equal
  highs/lows, order blocks, fair value gaps, etc.); validates
  `price_high >= price_low`.
- **`MarketStructure`** — a discrete structural observation (BOS/CHoCH/
  `CHOCH_FAILED`/`LIQUIDITY_SWEEP`/HH/HL/LH/LL) with a `MarketDirection` and
  `StructureScope`.
  Fields: `timestamp` (actual breaking candle, not the triggering pivot),
  `price_level` (triggering pivot's extreme), `reference_price_level` (the
  level that was broken — for `SWEEP`, `active_<side>`; for BOS (**both
  detectors**), the **formed low/high it broke** (the staircase floor), so it
  plots at the prior swing extreme; `validated_choch_<side>` for CHoCH, the
  broken CHoCH *origin* for `CHOCH_FAILED`), and `reference_timestamp` (for
  CHoCH: the timestamp of the LH/HL pivot promoted to `validated_choch_<side>`;
  for BOS: the candle that *formed* the broken level, so the line starts at the
  level's origin — both used to anchor the line's start in the frontend), and
  `reference_structural` (`bool | None`; `InternalStructureDetector` CHoCH only:
  whether the broken reference was a *structural* level — close-confirmed leg
  origin / continuation-promoted pullback / pending-BOS origin / blind-spot
  origin — or a *weak* one (re-anchor, wick-only-break promotion, cold-start
  fallback), the same classification the new-cycle persistence barrier uses;
  `None` for other events and the major detector — the frontend renders weak
  CHoCH dimmed/dotted with a `*` suffix), and `provisional` (`bool`, default
  `False`; `InternalStructureDetector` only): a *provisional* mark is a live-edge
  event whose confirming swing pivots have not formed yet (the swing-lookback lag
  at the right edge). A provisional **BOS** (`BREAK_OF_STRUCTURE`, under
  `emit_provisional_bos`) is a continuation whose staircase floor already
  *closed*-broke; a provisional **CHoCH** (`CHANGE_OF_CHARACTER`, under
  `emit_provisional_choch`) is a reversal whose *structural* CHoCH reference has
  been sustained-*closed*-broken (under `emit_provisional_choch_weak`, a *weak*
  reference also qualifies, at the weak-ref barrier persistence — rendered
  `CHoCH?* ▲`). That reference is resolved the way the *confirmed* CHoCH check
  resolves it — `validated → pending leg origin → blind-spot origin → re-arm`,
  stopping short of the trailing `active_<side>` fallback (a hair-trigger local
  pivot would put a `CHoCH?` on every pullback) — so a **re-armed** level
  (`choch_failed_rearm`) can re-fire at the live edge instead of waiting for a
  swing pivot a one-way move never forms; such a mark carries the failure's
  timestamp as its reference and renders `CHoCH? ↻ ▼`. Either appears only in the last few candles of a
  leg — superseded by the confirmed event once the pivots form, or it vanishes if
  the move fails first (an intentional live-edge repaint) — and the frontend
  renders it dimmed/dotted with a `?` suffix (`BOS? ▼` / `CHoCH? ▼`), like a weak
  CHoCH. A
  `CHOCH_FAILED` event marks a CHoCH
  that was invalidated before a confirming BOS (its `direction` is the failed
  CHoCH's direction); see the `InternalStructureDetector` notes. A `CHOCH_FAILED`
  may also be `provisional=True`: the additive **fast-fizzle marker**
  (`choch_fizzle_reclaim_candles`) that disregards a *standing* CHoCH whose
  reversal fizzled without flipping the state-machine trend — `provisional` here
  keeps it out of the `LiquidityHuntEngine`/`NarrativeEngine` replay while the
  frontend still terminates the stale line; see the `InternalStructureDetector`
  notes.
- **`POIZone`** — an institutional order/breaker/mitigation block zone,
  defined in `core/domain/poi_zone.py`. Anchored to a **market structure
  break (MSB)**. An `ORDER_BLOCK` is the *last opposite-direction candle
  before the impulse* that broke structure (for a bullish MSB, the last
  bearish candle of the down leg into the swing low the impulse launched
  from; bearish mirrors it); a `BREAKER_BLOCK`/`MITIGATION_BLOCK` is the last
  *same*-direction candle of the leg that formed the broken pivot (breaker
  when the impulse-origin extreme swept the prior one — bullish `l0 < l1`,
  bearish `h0 > h1` — mitigation otherwise). Both span the anchor candle's
  **full high-low range**, frozen at creation. Fields: `direction`, `kind`
  (`POIZoneKind`, default `ORDER_BLOCK`), `price_low`, `price_high`,
  `created_at` (the MSB confirmation candle), `ob_candle_timestamp` (the
  anchor candle — the box's left edge), `status` (`POIZoneStatus`:
  `ACTIVE`/`INVALIDATED`), `invalidated_at`. A single candle *close* beyond
  the far boundary (below `price_low` for bullish, above `price_high` for
  bearish) *breaks* the zone; price touching back inside does not. A broken
  zone does not retire itself, though — it retires the **oldest** zone of its
  queue (see `POIDetector`). Identical lifecycle for all kinds.
- **`ConsolidationRange`** — an observed lateral consolidation, defined in
  `core/domain/consolidation.py`: a stretch of candles with **no structure
  advance** where price oscillated inside a volatility-bounded box (at least
  N candles within K×mean-TR% height, touching both boundary zones
  alternately — see `liquidity/detectors/consolidation.py`). Where the
  structure detector is *correctly* silent (a range has no BOS/CHoCH), made
  explicit. Fields: `symbol`, `timeframe`, `start_timestamp`,
  `end_timestamp` (`None` while open), `price_low`/`price_high` (the box),
  `status` (`ConsolidationStatus`: `ACTIVE`/`RESOLVED`), `resolved_direction`
  (the breakout/advance direction when `RESOLVED`), `candle_count`. Resolution
  = sustained closes beyond a boundary, or a structure advance ending the
  segment; a wick/unsustained poke beyond the box is a boundary sweep and
  stays outside it.
- **`ManipulationCycle`** — an observed institutional manipulation cycle
  (accumulation → sweep → expansion), defined in
  `core/domain/manipulation_cycle.py`. Describes the three-phase Wyckoff/SMC
  pattern where price consolidates near a liquidity zone (accumulation),
  sweeps the zone to capture stops (manipulation), then moves impulsively in
  the opposite direction (expansion). `direction` is the expansion direction:
  a bullish cycle sweeps sell-side liquidity (lows) then expands upward.
  Fields: `direction`, `phase` (`ManipulationPhase`: `ACCUMULATION`/
  `MANIPULATION`/`EXPANSION`), `status` (`ManipulationCycleStatus`:
  `IN_PROGRESS`/`CONFIRMED`/`FAILED`), target zone info
  (`target_zone_price_low/high`, `target_zone_type`, `target_zone_side`),
  accumulation context (`accumulation_start/end`, `consolidation_candles`,
  `accumulation_avg_volume_delta`), sweep context (`sweep_timestamp`,
  `sweep_extreme`, `sweep_volume_delta`), and expansion context
  (`expansion_timestamp`, `expansion_price`, `expansion_volume_delta`).
- **`BehaviorDivergence`** — an observed divergence between price movement
  and volume delta, defined in `core/domain/behavior_divergence.py`. Detects
  when institutional flow opposes visible price direction. Fields: `timestamp`,
  `window_start` (first candle of the analysis window — the reading is a window
  observation, so the span is part of it; `None` if the producer didn't record
  it),
  `divergence_type` (`DivergenceType`: `DISTRIBUTION`/`ACCUMULATION`/
  `EXHAUSTION`/`ABSORPTION`), `direction` (apparent price direction),
  `price_level`, `volume_delta_avg`, `price_change_pct`, optional zone
  context (`nearest_zone_side`, `nearest_zone_price_low/high`),
  `confidence` (0-100), `description`.
- **`VolumeProfile`** / **`VolumeProfileBucket`** — volume-at-price over one
  window, defined in `core/domain/volume_profile.py`. Where the candle series
  says *when* price moved, the profile says *where* the market agreed.
  `VolumeProfile` fields: `symbol`, `timeframe`, `start_timestamp`/
  `end_timestamp`, `price_low`/`price_high`, `bucket_size`, `buckets`
  (low→high), `poc_price` (the heaviest band's midpoint), `value_area_low`/
  `value_area_high`/`value_area_pct`, `total_volume`, and `delta_estimated`
  (always `True` for a kline-sourced profile — the buy/sell split is inferred
  per candle, not observed per trade). Each `VolumeProfileBucket` carries
  `price_low`/`price_high`, `volume`, `buy_volume`/`sell_volume`, `node`
  (`VolumeNode`: `HIGH_VOLUME` shelf / `LOW_VOLUME` gap / `NORMAL`),
  `in_value_area`, `is_poc`, plus `delta` and `mid_price` properties.
  Descriptive: where participation concentrated, not a target.
- **`VWAPPoint`** / **`VWAPSeries`** — the volume-weighted average price paid
  since an anchor, defined in `core/domain/vwap.py`. Where a `VolumeProfile` is
  a static picture of *where* a window traded, a VWAP is a line that walks
  forward reporting *what the participants who entered since the anchor paid*
  — i.e. that population's break-even. `VWAPSeries` fields: `symbol`,
  `timeframe`, `anchor` (`VWAPAnchor`), `anchor_timestamp` (the accumulation
  still running at the live edge), `label`, `band_multipliers`, `points`, and
  `estimated` (always `True` for a kline-sourced VWAP — each candle contributes
  its typical price `hlc3` weighted by its whole volume, not every print at its
  own price; a second-order error, unlike kline-sourced delta-at-price). Each
  `VWAPPoint` carries `timestamp`, `anchor_timestamp` (constant across a run,
  so a consumer breaks the line into segments on it), `value`, and the
  volume-weighted standard-deviation bands `upper_1`/`lower_1`/`upper_2`/
  `lower_2` (`None` before the accumulation has any dispersion).
- **`RetailBias`** — a measurement of retail sentiment/positioning from a
  given `BiasSource`, with a bounded `sentiment_score` and `confidence`.
- **`OpenInterestPoint`** / **`FundingRate`** / **`LongShortRatio`** —
  perpetual-futures market-state samples, defined in `core/domain/futures.py`.
  The raw inputs the `LeverageLiquidationEstimator` uses to infer the
  over-leveraged side (open interest, funding rate, crowd long/short account
  ratio).
- **`LiquidationBand`** / **`LeverageLiquidationMap`** — a "gravitational map"
  of where leveraged retail positions would be force-liquidated, defined in
  `core/domain/liquidation.py`. `LeverageLiquidationMap` fields: `symbol`,
  `timeframe`, `current_price`, `dominant_leveraged_side` (`RetailPositioning`),
  `positioning_intensity` (0-1), `funding_rate`, `open_interest_change_pct`,
  `long_short_ratio`, `bands`. Each `LiquidationBand` has `price_low`,
  `price_high`, `leverage`, `side` (`LiquiditySide`), `source_entry_price`,
  `intensity` (0-100), and a time span: `start_time` (when the entry cluster
  formed) and `end_time` (when price first reached the liquidation level — the
  pool was consumed — or `None` if still live).
- **`OIRegimeReading`** / **`OIQualifiedEvent`** / **`OIAnalysis`** — joint
  price × open-interest observations, defined in `core/domain/oi_analysis.py`.
  `OIRegimeReading` classifies the most recent window into the classic futures
  matrix (`OIRegime`: `LONG_BUILDUP` price↑+OI↑, `SHORT_COVERING` price↑+OI↓,
  `SHORT_BUILDUP` price↓+OI↑, `LONG_LIQUIDATION` price↓+OI↓, `FLAT` below the
  significance floors), with `price_change_pct`, `oi_change_pct`,
  `window_candles`, `intensity` (0-100). `OIQualifiedEvent` attaches OI context
  to a structure event (`participation`, `OIParticipation`: `NEW_MONEY` OI
  rising into the break / `COVERING` OI falling / `FLUSH` sharp OI drop on a
  sweep / `FLAT`). `OIAnalysis` aggregates both plus the OI series' coverage
  span (`coverage_start`/`coverage_end`).
- **`LiquidityHuntState`** / **`LiquidityHuntTarget`** — a descriptive reading
  of *who is the resting liquidity* of the current move, defined in
  `core/domain/liquidity_hunt.py`. When the current timeframe's structure runs
  counter to the higher-timeframe trend, the counter-trend entrants become the
  fuel: `hunted_side` (`RetailPositioning`: SHORT during a bearish correction
  inside a bullish HTF, LONG mirrored), `phase` (`LiquidityHuntPhase`:
  `NONE`/`COUNTER_TREND`/`HUNT_IN_PROGRESS`/`CAPTURED`), `targets` (the nearby
  opposing pools — `LiquidityHuntTarget` with `kind` (`LiquidityHuntTargetKind`:
  `EQUAL_LEVEL`/`LIQUIDATION_BAND`), `label`, `price_level`, `captured`,
  `captured_at`; list capped at 8, counts in `targets_captured`/`targets_total`
  cover the full set), `correction_direction`, `counter_structure_timestamp`
  (the trend-flip event), `oi_unwinding`, `last_flush_timestamp`, `captured_at`,
  `description`. `CAPTURED` requires **all** mapped pools consumed (confirmed on
  closed candles; `oi_unwinding` is descriptive evidence, not a gate) —
  conservative by design (and never reached with zero mapped pools: absence of
  pools is not evidence of capture).
- **`MarketNarrative`** — synthesized institutional narrative for a
  symbol/timeframe snapshot, defined in `core/domain/narrative.py`. Fields:
  `symbol`, `timeframe`, `timestamp`, `phase` (`ManipulationPhase | None`),
  `timeline` (`list[NarrativeEvent]`), `anomalies` (`list[NarrativeAnomaly]`),
  `summary`, `confluence_count`, `confluence_total`.
- **`NarrativeEvent`** — a single event in the narrative timeline. Fields:
  `timestamp`, `event_type` (`NarrativeEventType`), `direction`, `description`,
  `source_layer`.
- **`NarrativeAnomaly`** — a pattern contradiction. Fields: `timestamp`,
  `expected`, `observed`, `description`, `severity` (`AnomalySeverity`).
- **`TimeframeOverview`** / **`MarketOverview`** — the multi-timeframe
  structural ladder, defined in `core/domain/overview.py` (built by
  `app.overview`, see below). `TimeframeOverview` is one timeframe's standing
  state: `timeframe`, `trend` (the internal detector's state-machine trend —
  exactly what the chart renders for that timeframe), `current_price`,
  `candle_timestamp`, the `_HIGHER_TIMEFRAME_MAP` anchor pair
  (`higher_timeframe`/`higher_timeframe_direction`, `None` at the top), the
  last non-provisional trend-relevant event (`last_event`,
  `last_event_direction`, `last_event_timestamp`, `last_event_candles_ago`),
  any provisional live-edge mark (`forming_event`/`forming_direction` — the
  dimmed `BOS?`/`CHoCH?`), the consolidation state (`in_consolidation` — price
  is inside a confirmed ACTIVE `ConsolidationRange`, so `trend` reads as the
  pre-range cycle — and `consolidation_candles`), and a hunt summary
  (`hunt_phase`, `hunted_side`,
  `hunt_targets_captured`/`_total`). `MarketOverview` is `symbol` + `entries`
  ordered fine → coarse. Descriptive state per timeframe, not signals.

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`,
`POIZoneStatus`, `POIZoneKind`, `ConsolidationStatus`, `ManipulationPhase`,
`ManipulationCycleStatus`,
`DivergenceType`, `LiquidityHuntPhase`, `LiquidityHuntTargetKind`,
`NarrativeEventType`, `AnomalySeverity`, `VolumeNode`, `VWAPAnchor`) live in
`core/domain/enums.py`. Extend behavior by adding enum members rather than
branching logic elsewhere (Open/Closed principle).

Full architecture rationale, including SOLID notes, is documented in
`liquidity_hunter/docs/architecture.md`.

