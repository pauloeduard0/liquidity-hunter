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
  | 'liquidity_sweep'

export type StructureScope = 'major' | 'internal'

export type RetailPositioning = 'long' | 'short' | 'neutral'

export type POIZoneStatus = 'active' | 'mitigated' | 'invalidated'

export type ManipulationPhase = 'accumulation' | 'manipulation' | 'expansion'

export type ManipulationCycleStatus = 'in_progress' | 'confirmed' | 'failed'

export type DivergenceType = 'distribution' | 'accumulation' | 'exhaustion' | 'absorption'

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
  price_low: number
  price_high: number
  created_at: string
  origin_choch_timestamp: string
  origin_bos_timestamp: string
  extreme_candle_timestamp: string
  status: POIZoneStatus
  invalidated_at: string | null
  mitigated_at: string | null
}

export interface RTOSweepEvent {
  symbol: string
  timeframe: TimeFrame
  direction: MarketDirection
  timestamp: string
  zone_price_low: number
  zone_price_high: number
  sweep_extreme: number
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

export interface DashboardData {
  symbol: string
  timeframe: TimeFrame
  candles: Candle[]
  current_price: number
  higher_timeframe_direction: MarketDirection
  liquidity_zones: LiquidityZone[]
  ranked_zones: ScoredLiquidityZone[]
  market_structure_events: MarketStructure[]
  internal_structure_events: MarketStructure[]
  retail_bias: RetailBiasEstimate
  poi_zones: POIZone[]
  poi_sweep_events: RTOSweepEvent[]
  manipulation_cycles: ManipulationCycle[]
  behavior_divergences: BehaviorDivergence[]
}
