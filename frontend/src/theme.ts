/**
 * Color palette mirroring `liquidity_hunter.dashboard.charts`'s
 * institutional dark theme, so the React UI stays visually consistent with
 * the existing Streamlit dashboard.
 */

export const DARK_BG = '#0a0d14'
export const PANEL_BG = '#0f1319'
export const GRID_COLOR = '#1a1f2e'
export const FONT_COLOR = '#d1d4dc'
export const ACCENT_COLOR = '#2962ff'

export const CANDLE_UP_COLOR = '#26c6da'
export const CANDLE_DOWN_COLOR = '#b8b8b8'

export const ZONE_COLORS: Record<string, string> = {
  equal_highs: '#ef553b',
  equal_lows: '#636efa',
  swing_high: '#ffa15a',
  swing_low: '#19d3f3',
  order_block: '#ab63fa',
  fair_value_gap: '#00cc96',
  liquidity_pool: '#b6e880',
}
export const DEFAULT_ZONE_COLOR = '#888888'

/** Short labels for `LiquidityZoneType` values, used in chart line titles. */
export const ZONE_TYPE_LABELS: Record<string, string> = {
  equal_highs: 'EQH',
  equal_lows: 'EQL',
  swing_high: 'SH',
  swing_low: 'SL',
  order_block: 'OB',
  fair_value_gap: 'FVG',
  liquidity_pool: 'LP',
}

export const STRUCTURE_EVENT_STYLES: Record<string, { label: string; color: string }> = {
  break_of_structure: { label: 'BOS', color: '#26a69a' },
  change_of_character: { label: 'CHoCH', color: '#ffb74d' },
  liquidity_sweep: { label: 'Sweep', color: '#ef5350' },
}

export const TREND_ICONS: Record<string, string> = {
  bullish: '▲',
  bearish: '▼',
  neutral: '▬',
}

/** POI order block box colors — border and fill (TradingView style). */
export const POI_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  bullish: { border: '#2979ff', fill: '#2979ff2e' },   // vivid blue demand zone
  bearish: { border: '#ef5350', fill: '#ef53502e' },   // red supply zone
  mitigated: { border: '#88888866', fill: '#8888880a' },
}

/** RTO sweep signal label colors — slightly different shade from the OB box. */
export const RTO_COLORS: Record<string, string> = {
  bullish: '#2962ff',  // darker blue — distinct from the lighter OB box
  bearish: '#ff5252',
}

/** Manipulation cycle accumulation box colors by status. */
export const MANIPULATION_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  in_progress: { border: '#ffb74d', fill: '#ffb74d1a' },
  confirmed: { border: '#26a69a', fill: '#26a69a1a' },
  failed: { border: '#8a8f9c', fill: '#8a8f9c12' },
}

/** Behavior divergence type colors and marker shapes. */
export const DIVERGENCE_STYLES: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  distribution: { label: 'DIST', color: '#ef5350', bg: '#ef535015', icon: '▼' },
  accumulation: { label: 'ACCUM', color: '#26a69a', bg: '#26a69a15', icon: '▲' },
  exhaustion: { label: 'EXHAUST', color: '#ffb74d', bg: '#ffb74d15', icon: '◇' },
  absorption: { label: 'ABSORB', color: '#ab63fa', bg: '#ab63fa15', icon: '◆' },
}

/**
 * Liquidity heatmap gradient stops, cold -> hot, used by the lateral strip on
 * the main chart. Each entry maps a normalized heat threshold (0-1) to an RGB
 * triple; the strip interpolates between adjacent stops per bucket.
 */
export const HEATMAP_GRADIENT: ReadonlyArray<{ stop: number; rgb: [number, number, number] }> = [
  { stop: 0.0, rgb: [41, 98, 255] },   // cold — blue (low concentration)
  { stop: 0.45, rgb: [171, 99, 250] }, // purple
  { stop: 0.7, rgb: [255, 183, 77] },  // amber
  { stop: 1.0, rgb: [239, 83, 80] },   // hot — red (stop magnet)
]

/** Max alpha (0-1) applied to the hottest heatmap band; cold bands fade out. */
export const HEATMAP_MAX_ALPHA = 0.6

/**
 * Max horizontal projection (px) of a heatmap bar into the chart, reached by
 * the hottest bucket. Bar length scales with normalized heat, so hot levels
 * reach further left like a volume profile.
 */
export const HEATMAP_MAX_WIDTH = 104

/** Min bar length (px) for any non-zero bucket, so faint levels stay visible. */
export const HEATMAP_MIN_WIDTH = 6

/**
 * Leverage-liquidation band colors, warmer for higher leverage (more fragile
 * positions). The estimator emits only one side per snapshot (crowded longs
 * liquidate below price, shorts above), so the side is read from the band's
 * position relative to price and color is free to encode the leverage tier.
 */
export const LIQUIDATION_LEVERAGE_COLORS: Record<number, [number, number, number]> = {
  10: [255, 213, 79],  // amber — most common, lowest risk
  25: [255, 152, 0],   // orange
  50: [244, 81, 30],   // deep orange / red
  100: [198, 40, 40],  // crimson — hottest, most fragile
}
export const LIQUIDATION_DEFAULT_COLOR: [number, number, number] = [136, 136, 136]

/** Max alpha (0-1) applied to the most intense liquidation band. */
export const LIQUIDATION_MAX_ALPHA = 0.5

/** Min alpha (0-1) for any rendered liquidation band, so faint tiers stay visible. */
export const LIQUIDATION_MIN_ALPHA = 0.12

/** Volume delta histogram bar colors. */
export const VOLUME_DELTA_UP_COLOR = '#26a69a'
export const VOLUME_DELTA_DOWN_COLOR = '#ef5350'

/** RSI indicator colors. */
export const RSI_LINE_COLOR = '#ab63fa'
export const RSI_OVERBOUGHT_COLOR = '#26c6da66'
export const RSI_OVERSOLD_COLOR = '#b8b8b866'
export const RSI_DIV_BULLISH_COLOR = '#26a69a'
export const RSI_DIV_BEARISH_COLOR = '#ef5350'
