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
}
