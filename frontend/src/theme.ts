/**
 * Color palette mirroring `liquidity_hunter.dashboard.charts`'s
 * institutional dark theme, so the React UI stays visually consistent with
 * the existing Streamlit dashboard.
 */

export const DARK_BG = '#131722'
export const PANEL_BG = '#0f1319'
export const GRID_COLOR = '#1a1f2e'
export const FONT_COLOR = '#d1d4dc'
export const ACCENT_COLOR = '#2962ff'

export const CANDLE_UP_COLOR = '#9598a1'
export const CANDLE_DOWN_COLOR = '#da4d4d'

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
  choch_failed: { label: 'CHoCH ✕', color: '#9e9e9e' },
  liquidity_sweep: { label: 'Sweep', color: '#8d6fc4' },
}

/**
 * Direction colors for BOS/CHoCH lines and labels (TradingView-style): the
 * color carries the direction, so those labels drop the ▲/▼ arrow. Neutral
 * events (Sweep, CHoCH ✕) keep their own `STRUCTURE_EVENT_STYLES` color and
 * arrow — red/green stays reserved for direction.
 */
export const STRUCTURE_DIRECTION_COLORS: Record<string, string> = {
  bullish: '#2EE6B8',
  // Lilac rather than red: the candles' down color (#da4d4d) is red, so
  // bearish structure lines need a hue that doesn't blend into them.
  bearish: '#ce93d8',
}

export const TREND_ICONS: Record<string, string> = {
  bullish: '▲',
  bearish: '▼',
  neutral: '▬',
}

/** POI order block box colors — border and fill (TradingView style).
 *  Kept deliberately faint: the zone is context behind the candles, so the
 *  fill sits near ~7% alpha and the border is a translucent hairline. */
export const POI_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  bullish: { border: '#5b9cf699', fill: '#2979ff12' },  // soft blue demand zone
  bearish: { border: '#ef535099', fill: '#ef535012' },  // soft red supply zone
}

/** Manipulation cycle accumulation box colors by status. */
export const MANIPULATION_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  in_progress: { border: '#ffb74d', fill: '#ffb74d1a' },
  confirmed: { border: '#26a69a', fill: '#26a69a1a' },
  failed: { border: '#8a8f9c', fill: '#8a8f9c12' },
}

/** Consolidation (lateral range) box: neutral slate — a structural pause, not
 *  a directional zone. Live ranges render slightly stronger than resolved ones. */
export const CONSOLIDATION_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  active: { border: '#90a4ae', fill: '#90a4ae14' },
  resolved: { border: '#90a4ae66', fill: '#90a4ae0a' },
}

/** Behavior divergence type colors and marker shapes. */
export const DIVERGENCE_STYLES: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  distribution: { label: 'DIST', color: '#ef5350', bg: '#ef535015', icon: '▼' },
  accumulation: { label: 'ACCUM', color: '#26a69a', bg: '#26a69a15', icon: '▲' },
  exhaustion: { label: 'EXHAUST', color: '#ffb74d', bg: '#ffb74d15', icon: '◇' },
  absorption: { label: 'ABSORB', color: '#ab63fa', bg: '#ab63fa15', icon: '◆' },
}

/**
 * Volume-Spread-Analysis (VSA) pattern styles — the color a candle's volume
 * bar is tinted with (and the label drawn above/below the candle) when the
 * VSA analyzer flags it. Climax = high-energy alert (magenta), thrust =
 * rejection (amber), no-supply/no-demand = quiet/low-energy (muted grey).
 * `above`/`below` place the chart marker on the side the pattern reads from.
 */
export const VSA_STYLES: Record<
  string,
  { label: string; color: string; position: 'aboveBar' | 'belowBar' }
> = {
  selling_climax: { label: 'S.Climax', color: '#e040fb', position: 'belowBar' },
  buying_climax: { label: 'B.Climax', color: '#e040fb', position: 'aboveBar' },
  down_thrust: { label: 'D.Thrust', color: '#ffb74d', position: 'belowBar' },
  up_thrust: { label: 'U.Thrust', color: '#ffb74d', position: 'aboveBar' },
  no_supply: { label: 'NoSupply', color: '#8a94a6', position: 'belowBar' },
  no_demand: { label: 'NoDemand', color: '#8a94a6', position: 'aboveBar' },
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

/**
 * Volume overlay bar colors — the raw (futures) candle volume drawn as
 * translucent bars anchored to the base of the main candlestick pane, colored
 * by candle direction. Half-alpha keeps the bars readable without fully
 * obscuring the candles/structure lines behind them.
 */
export const VOLUME_UP_COLOR = CANDLE_UP_COLOR + '80'
export const VOLUME_DOWN_COLOR = CANDLE_DOWN_COLOR + '80'

/** RSI indicator colors. */
export const RSI_LINE_COLOR = '#ab63fa'
export const RSI_OVERBOUGHT_COLOR = '#26c6da66'
export const RSI_OVERSOLD_COLOR = '#b8b8b866'
export const RSI_DIV_BULLISH_COLOR = '#26a69a'
export const RSI_DIV_BEARISH_COLOR = '#ef5350'
