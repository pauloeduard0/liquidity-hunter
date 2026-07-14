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
  invalidated_at: string | null
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
  description: string
}

export type ConsolidationStatus = 'active' | 'resolved'

/** A confirmed lateral consolidation: a stretch with no structure advance where
 *  price oscillated inside a volatility-bounded box. Where the detector was
 *  *correctly* silent (a range has no BOS/CHoCH), made explicit. */
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
  liquidity_heatmap: LiquidityHeatmap | null
  liquidation_map: LeverageLiquidationMap | null
  narrative: MarketNarrative | null
  oi_analysis: OIAnalysis | null
  liquidity_hunt: LiquidityHuntState | null
  consolidation_ranges: ConsolidationRange[]
}
