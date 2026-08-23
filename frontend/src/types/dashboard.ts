/**
 * TypeScript mirror of `liquidity_hunter.api.schemas.DashboardDataResponse`
 * and the domain models it nests (`Candle`, `LiquidityZone`,
 * `MarketStructure`, `ScoredLiquidityZone`, `RetailBiasEstimate`).
 *
 * Enum values match the `str` enums in `liquidity_hunter.core.domain.enums`
 * (e.g. `TimeFrame.H1.value === "1h"`).
 */

export type TimeFrame = '1m' | '5m' | '15m' | '30m' | '1h' | '4h' | '1d' | '1w'

export type MarketDirection = 'bullish' | 'bearish' | 'neutral'

export type LiquiditySide = 'buy_side' | 'sell_side'

export type LiquidityZoneType =
  | 'equal_highs'
  | 'equal_lows'
  | 'swing_high'
  | 'swing_low'
  | 'order_block'
  | 'fair_value_gap'
  | 'liquidity_pool'

export type StructureEvent =
  | 'higher_high'
  | 'higher_low'
  | 'lower_high'
  | 'lower_low'
  | 'break_of_structure'
  | 'change_of_character'
  | 'choch_failed'
  | 'liquidity_sweep'

export type StructureScope = 'major' | 'internal'

export type RetailPositioning = 'long' | 'short' | 'neutral'

export type POIZoneStatus = 'active' | 'invalidated'
export type POIZoneKind = 'order_block' | 'breaker_block' | 'mitigation_block'

export type ManipulationPhase = 'accumulation' | 'manipulation' | 'expansion'

export type ManipulationCycleStatus = 'in_progress' | 'confirmed' | 'failed'

export type DivergenceType = 'distribution' | 'accumulation' | 'exhaustion' | 'absorption'

export type VSAPattern =
  | 'no_supply'
  | 'no_demand'
  | 'selling_climax'
  | 'buying_climax'
  | 'down_thrust'
  | 'up_thrust'

export type NarrativeEventType =
  | 'consolidation'
  | 'distribution'
  | 'accumulation'
  | 'sweep'
  | 'expansion'
  | 'exhaustion'
  | 'absorption'
  | 'structure_break'
  | 'zone_mitigation'

export type AnomalySeverity = 'low' | 'medium' | 'high'

export type OIRegime =
  | 'long_buildup'
  | 'short_covering'
  | 'short_buildup'
  | 'long_liquidation'
  | 'flat'

export type OIParticipation = 'new_money' | 'covering' | 'flush' | 'flat'

export type MarketControlSide = 'buyers' | 'sellers' | 'balanced'

/** One candle's control reading for the chart oscillator (mirror of
 *  `core.domain.MarketControlPoint`). `control_score` is signed [-100, 100]. */
export interface MarketControlPoint {
  timestamp: string
  control_score: number
  controller: MarketControlSide
  /** The full CVD×OI quadrant. Distinguishes buy aggression backed by fresh
   *  longs (`long_buildup`) from buy aggression that is only shorts covering
   *  (`short_covering`) — both read `balanced`/`buyers` ambiguously on
   *  `controller` alone. Drives the oscillator's solid-vs-hollow fill. */
  regime: OIRegime
}

/** Who is in control of the tape right now, from CVD aggression × open interest
 *  (mirror of `core.domain.MarketControlState`). `control_score` is the signed
 *  conviction oscillator in [-100, 100]: sign = aggressor side (positive =
 *  buyers), magnitude = conviction (amplified when OI confirms, attenuated when
 *  it diverges). Descriptive: the quadrant states what just happened on the
 *  tape, not what happens next — a `fade_warning` flag was removed from the
 *  model once the claim behind it was measured and did not hold. */
export interface MarketControlState {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  controller: MarketControlSide
  regime: OIRegime
  cvd_change: number
  cvd_change_ratio: number
  oi_change_pct: number
  conviction: number
  control_score: number
  window_candles: number
  description: string
  series: MarketControlPoint[]
}

export interface Candle {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  taker_buy_volume: number
}

export interface LiquidityZone {
  symbol: string
  timeframe: TimeFrame
  zone_type: LiquidityZoneType
  side: LiquiditySide
  price_high: number
  price_low: number
  formed_at: string
  /** First candle whose wick reached through the zone — the grab. */
  invalidated_at: string | null
  /** First candle whose close landed beyond it — the level spent. */
  breached_at: string | null
  /** Whether the sweeping candle closed back inside — the grab handed back. */
  sweep_rejected: boolean
  strength: number
  is_mitigated: boolean
}

export interface MarketStructure {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  event: StructureEvent
  direction: MarketDirection
  price_level: number
  reference_price_level: number | null
  reference_timestamp: string | null
  origin_price_level: number | null
  scope: StructureScope
  /** CHoCH only: broken reference was structural (conservative sequence) vs
   *  weak (re-anchor/fallback/wick-promoted, barrier-governed). Null elsewhere. */
  reference_structural?: boolean | null
  /** BOS only: a provisional live-edge continuation (floor closed-broken but its
   *  confirming swing pivots have not formed yet). Rendered dimmed with a `?`;
   *  superseded by the confirmed BOS once pivots form, or vanishes if the trend
   *  flips first. False/absent for confirmed BOS. */
  provisional?: boolean
}

export interface ScoredLiquidityZone {
  zone: LiquidityZone
  score: number
  distance_score: number
  touch_score: number
  timeframe_score: number
}

export interface RetailBiasEstimate {
  symbol: string
  generated_at: string
  dominant_side: RetailPositioning
  confidence: number
  explanation: string
}

export interface POIZone {
  symbol: string
  timeframe: TimeFrame
  direction: MarketDirection
  kind: POIZoneKind
  price_low: number
  price_high: number
  created_at: string
  ob_candle_timestamp: string
  status: POIZoneStatus
  invalidated_at: string | null
}

export interface ManipulationCycle {
  symbol: string
  timeframe: TimeFrame
  direction: MarketDirection
  phase: ManipulationPhase
  status: ManipulationCycleStatus
  target_zone_price_low: number
  target_zone_price_high: number
  target_zone_type: LiquidityZoneType
  target_zone_side: LiquiditySide
  accumulation_start: string
  accumulation_end: string
  consolidation_candles: number
  accumulation_avg_volume_delta: number
  sweep_timestamp: string | null
  sweep_extreme: number | null
  sweep_volume_delta: number | null
  expansion_timestamp: string | null
  expansion_price: number | null
  expansion_volume_delta: number | null
}

export interface BehaviorDivergence {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  window_start: string | null
  divergence_type: DivergenceType
  direction: MarketDirection
  price_level: number
  volume_delta_avg: number
  price_change_pct: number
  nearest_zone_side: LiquiditySide | null
  nearest_zone_price_low: number | null
  nearest_zone_price_high: number | null
  confidence: number
  description: string
}

export type ConfluenceFactor =
  | 'htf_alignment'
  | 'htf_order_block'
  | 'vsa_volume'
  | 'order_block'
  | 'oi_participation'
  | 'volume_delta'
  | 'liquidity_sweep'

export interface StructureConfluence {
  symbol: string
  timeframe: TimeFrame
  event_timestamp: string
  event_type: StructureEvent
  direction: MarketDirection
  price_level: number
  factors: ConfluenceFactor[]
  score: number
  description: string
  provisional: boolean
}

export interface VolumeSpreadSignal {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  pattern: VSAPattern
  direction: MarketDirection
  price_level: number
  spread_ratio: number
  close_position: number
  volume_ratio: number
  volume_delta: number
  confidence: number
  description: string
}

export interface HeatmapBucket {
  price_low: number
  price_high: number
  heat: number
  side: LiquiditySide
  heat_zones: number
  heat_poi: number
  heat_manipulation: number
}

export interface LiquidityHeatmap {
  symbol: string
  timeframe: TimeFrame
  current_price: number
  bucket_pct: number
  buckets: HeatmapBucket[]
}

export interface LiquidationBand {
  price_low: number
  price_high: number
  leverage: number
  side: LiquiditySide
  source_entry_price: number
  intensity: number
  start_time: string
  end_time: string | null
}

export interface LeverageLiquidationMap {
  symbol: string
  timeframe: TimeFrame
  current_price: number
  dominant_leveraged_side: RetailPositioning
  positioning_intensity: number
  funding_rate: number
  open_interest_change_pct: number
  long_short_ratio: number
  bands: LiquidationBand[]
}

export interface NarrativeEvent {
  timestamp: string
  event_type: NarrativeEventType
  direction: MarketDirection
  description: string
  source_layer: string
}

export interface NarrativeAnomaly {
  timestamp: string
  expected: string
  observed: string
  description: string
  severity: AnomalySeverity
}

export interface MarketNarrative {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  phase: ManipulationPhase | null
  timeline: NarrativeEvent[]
  anomalies: NarrativeAnomaly[]
  summary: string
  confluence_count: number
  confluence_total: number
}

export interface OIRegimeReading {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  regime: OIRegime
  price_change_pct: number
  oi_change_pct: number
  window_candles: number
  intensity: number
  description: string
}

export interface OIQualifiedEvent {
  symbol: string
  timeframe: TimeFrame
  event_timestamp: string
  event_type: StructureEvent
  direction: MarketDirection
  price_level: number
  oi_delta_pct: number
  participation: OIParticipation
  description: string
}

export type LiquidityHuntPhase = 'none' | 'counter_trend' | 'hunt_in_progress' | 'captured'

/** Quality of a hunt grab from CVD-aggression x OI: a genuine break has fresh
 *  money behind the capture direction, an exhaustion grab runs the stops on no
 *  new money (reversal-prone); unknown when no market-control reading exists. */
export type HuntCaptureQuality = 'unknown' | 'genuine_break' | 'exhaustion_grab'

export type LiquidityHuntTargetKind = 'equal_level' | 'liquidation_band'

export interface LiquidityHuntTarget {
  kind: LiquidityHuntTargetKind
  label: string
  price_level: number
  captured: boolean
  captured_at: string | null
}

/** Who is the resting liquidity of the current move (counter-trend hunt state).
 *  `hunted_side` is the positioning side whose stops/liquidations are the nearby
 *  fuel; `captured` requires the full mapped pool set consumed AND open interest
 *  no longer unwinding against that side. Purely observational. */
export interface LiquidityHuntState {
  symbol: string
  timeframe: TimeFrame
  phase: LiquidityHuntPhase
  hunted_side: RetailPositioning
  correction_direction: MarketDirection | null
  counter_structure_timestamp: string | null
  targets: LiquidityHuntTarget[]
  targets_captured: number
  targets_total: number
  oi_unwinding: boolean
  last_flush_timestamp: string | null
  captured_at: string | null
  capture_quality: HuntCaptureQuality
  description: string
}

/** A concluded counter-trend hunt from earlier in the window (history, not the
 *  live snapshot). The larger trend resumed at `end_timestamp`, consuming the
 *  counter-trend entrants that opened the leg at `start_timestamp`. */
export interface LiquidityHuntEpisode {
  hunted_side: RetailPositioning
  correction_direction: MarketDirection
  start_timestamp: string
  end_timestamp: string
  /** Weighted capture evidence that closed the hunt (sweep / VSA / OI flush /
   *  zone / delta). A hunt is recorded only at/above the capture threshold. */
  capture_score: number
  capture_sources: string[]
  /** Grab quality from CVD-aggression x OI at the grab candle: an exhaustion
   *  grab ran the stops on no new money (reversal-prone), a genuine break had
   *  fresh money behind the capture direction. */
  capture_quality: HuntCaptureQuality
  /** The grab was the extreme of a failed reversal (a capture-direction CHoCH
   *  later invalidated) — the leg's high-water mark, its principal hunt. */
  failed_reversal: boolean
  description: string
}

export type ConsolidationStatus = 'active' | 'resolved'

/** A confirmed lateral consolidation: a stretch with no structure advance where
 *  price oscillated inside a volatility-bounded box. Where the detector was
 *  *correctly* silent (a range has no BOS/CHoCH), made explicit. */
export type LiquidityPoolKind = 'equal_level' | 'order_block'

export type LiquidityGrabOutcome = 'rejected' | 'spent'

/**
 * What one `liquidity_sweep` actually swept.
 *
 * The event itself is a residual category of the structure detector -- a
 * counter-trend break that failed the CHoCH persistence check -- so it knows
 * a level was poked, not whether anything was resting there. Keyed to the
 * event by `event_timestamp`, the way `OIQualifiedEvent` is.
 */
export interface SweepContext {
  symbol: string
  timeframe: TimeFrame
  event_timestamp: string
  direction: MarketDirection
  swept_extreme: number
  /** The extreme landed in a facing, pre-existing order block. */
  in_order_block: boolean
  block_low: number | null
  block_high: number | null
  /** Depth past the broken reference, in mean-true-range units. */
  excursion_atr: number | null
}

/** One candle that took resting liquidity, whatever kind of pool held it. */
export interface LiquidityGrab {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  price_level: number
  side: LiquiditySide
  kinds: LiquidityPoolKind[]
  pool_count: number
  outcome: LiquidityGrabOutcome
  /** How far beyond the level the grabbing candle reached, in mean-true-range
   *  units of its own series. Null when the series has no volatility. */
  excursion_atr: number | null
  /** Whether the rejection survived its confirmation window (2 candles), a
   *  second look that leaves `outcome`'s local reading alone. Null for a
   *  spent grab and at the live edge, where the window has not elapsed.
   *  31% of the rejections drawn from `outcome` alone are closed through by
   *  the very next candle. */
  rejection_confirmed: boolean | null
  block_level: number | null
}

export interface ConsolidationRange {
  symbol: string
  timeframe: TimeFrame
  start_timestamp: string
  /** Resolution candle (first sustained close beyond a boundary); null while active. */
  end_timestamp: string | null
  price_low: number
  price_high: number
  status: ConsolidationStatus
  resolved_direction: MarketDirection | null
  candle_count: number
}

export interface OIAnalysis {
  symbol: string
  timeframe: TimeFrame
  current_regime: OIRegimeReading | null
  qualified_events: OIQualifiedEvent[]
  coverage_start: string | null
  coverage_end: string | null
}

/** One timeframe's standing structural state (mirror of `core.domain.TimeframeOverview`).
 *  `trend` is the internal detector's state-machine trend for the production run of
 *  this timeframe — exactly the trend the chart shows when it is opened. */
export interface TimeframeOverview {
  timeframe: TimeFrame
  trend: MarketDirection
  current_price: number
  candle_timestamp: string
  higher_timeframe: TimeFrame | null
  higher_timeframe_direction: MarketDirection | null
  last_event: StructureEvent | null
  last_event_direction: MarketDirection | null
  last_event_timestamp: string | null
  last_event_candles_ago: number | null
  forming_event: StructureEvent | null
  forming_direction: MarketDirection | null
  /** Whether price is currently inside a confirmed consolidation range (the
   *  timeframe's structure is lateral; `trend` reads as the pre-range cycle). */
  in_consolidation: boolean
  consolidation_candles: number | null
  hunt_phase: LiquidityHuntPhase
  hunted_side: RetailPositioning
  hunt_targets_captured: number
  hunt_targets_total: number
}

/** Per-timeframe structural readings for one symbol, ordered fine → coarse
 *  (mirror of `core.domain.MarketOverview`, from `GET /api/overview`). */
export interface MarketOverview {
  symbol: string
  entries: TimeframeOverview[]
}

/** One candle's Supertrend reading (ATR-banded trailing trend). */
export interface SupertrendPoint {
  timestamp: string
  /** The active band: the floor while bullish, the ceiling while bearish. */
  value: number
  direction: MarketDirection
  /** True on the candle where the trend flipped sides. */
  flip: boolean
  upper_band: number
  lower_band: number
}

export type SupertrendBreakQuality = 'unknown' | 'genuine' | 'stop_run'

/** A Supertrend flip qualified by who financed it. */
export interface SupertrendBreak {
  timestamp: string
  direction: MarketDirection
  /** The band that gave way — the active band of the preceding candle. */
  broken_level: number
  quality: SupertrendBreakQuality
  /** When price closed back inside the broken band (null while the break holds). */
  reclaim_timestamp: string | null
  reclaim_candles: number | null
  controller: MarketControlSide | null
  structure_confirmed: boolean
  evidence: string[]
  description: string
}

export type VolumeNode = 'high_volume' | 'low_volume' | 'normal'

export interface VolumeProfileBucket {
  price_low: number
  price_high: number
  volume: number
  /** Taker-buy share of `volume`. Estimated per candle, not observed per trade. */
  buy_volume: number
  sell_volume: number
  node: VolumeNode
  in_value_area: boolean
  is_poc: boolean
}

/** Volume-at-price over the visible window: where the market agreed, not when. */
export interface VolumeProfile {
  symbol: string
  timeframe: TimeFrame
  start_timestamp: string
  end_timestamp: string
  price_low: number
  price_high: number
  bucket_size: number
  buckets: VolumeProfileBucket[]
  poc_price: number
  value_area_low: number
  value_area_high: number
  value_area_pct: number
  total_volume: number
  /** Always true for a kline-sourced profile; the buy/sell split is inferred. */
  delta_estimated: boolean
}

export type VWAPAnchor = 'session' | 'week' | 'month' | 'rolling' | 'event'

export interface VWAPPoint {
  timestamp: string
  /** The candle the accumulation behind this reading started at. */
  anchor_timestamp: string
  value: number
  upper_1: number | null
  lower_1: number | null
  upper_2: number | null
  lower_2: number | null
}

/** The average price paid since an anchor, weighted by volume. */
export interface VWAPSeries {
  symbol: string
  timeframe: TimeFrame
  anchor: VWAPAnchor
  /** Start of the accumulation still running at the live edge. */
  anchor_timestamp: string
  /** Short name for what the line is anchored to ("Session", "CHoCH ▼"). */
  label: string
  band_multipliers: number[]
  points: VWAPPoint[]
  /** Built from per-candle typical prices, not per-trade prices. */
  estimated: boolean
}

/**
 * A candle that reclaimed the VWAP after price tested an order block.
 *
 * `r_atr` is the reading that matters: how far the reclaim sat from the tested
 * block in the series' own volatility. Small means the block and the VWAP are
 * one level holding two populations; large means two levels price happened to
 * visit in sequence. `vwap_candles` says how much the average had accumulated,
 * which the measured lift tracks. Descriptive -- no entry, size, or target.
 */
export interface BlockReclaim {
  symbol: string
  timeframe: TimeFrame
  timestamp: string
  direction: MarketDirection
  reclaim_price: number
  vwap_price: number
  block_price_low: number
  block_price_high: number
  block_timestamp: string
  test_start_timestamp: string
  first_test: boolean
  test_extreme: number
  reclaim_distance: number
  r_atr: number | null
  provisional: boolean
  /**
   * Which shared line the pinbar rejected: 'vwap', 'ema' (the fast EMA(9)) or
   * 'both'. Deliberately not drawn: the 'both' subset measures far better in
   * the search half and worse out of sample, so a glyph for it would invite
   * exactly the filtering the measurement rules out.
   */
  trigger_line: string
  /**
   * Which pinbar definitions the trigger candle met: 'legacy', 'l1' (the
   * golden two-thirds tail), 'l2' (body-heavy, capped nose), comma-joined.
   * The union is what the detector accepts, and it beats each subset out of
   * sample — so this is here to be read, not to be filtered on.
   */
  pinbar_grade: string
  vwap_candles: number
}

export interface DashboardData {
  symbol: string
  timeframe: TimeFrame
  candles: Candle[]
  current_price: number
  higher_timeframe_direction: MarketDirection
  /** The anchor timeframe the HTF direction was measured on (null for the top timeframe). */
  higher_timeframe: TimeFrame | null
  liquidity_zones: LiquidityZone[]
  ranked_zones: ScoredLiquidityZone[]
  market_structure_events: MarketStructure[]
  internal_structure_events: MarketStructure[]
  retail_bias: RetailBiasEstimate
  poi_zones: POIZone[]
  manipulation_cycles: ManipulationCycle[]
  behavior_divergences: BehaviorDivergence[]
  volume_spread_signals: VolumeSpreadSignal[]
  supertrend: SupertrendPoint[]
  supertrend_breaks: SupertrendBreak[]
  volume_profile: VolumeProfile | null
  vwap: VWAPSeries | null
  anchored_vwaps: VWAPSeries[]
  liquidity_heatmap: LiquidityHeatmap | null
  liquidation_map: LeverageLiquidationMap | null
  narrative: MarketNarrative | null
  oi_analysis: OIAnalysis | null
  market_control: MarketControlState | null
  liquidity_hunt: LiquidityHuntState | null
  liquidity_hunt_history: LiquidityHuntEpisode[]
  liquidity_continuation_history: LiquidityHuntEpisode[]
  consolidation_ranges: ConsolidationRange[]
  liquidity_grabs: LiquidityGrab[]
  block_reclaims: BlockReclaim[]
  sweep_contexts: SweepContext[]
  structure_confluence: StructureConfluence[]
}
