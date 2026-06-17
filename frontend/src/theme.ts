/**
 * Color palette mirroring `liquidity_hunter.dashboard.charts`'s
 * institutional dark theme, so the React UI stays visually consistent with
 * the existing Streamlit dashboard.
 */

export const DARK_BG = '#0e1117'
export const PANEL_BG = '#161a25'
export const GRID_COLOR = '#1f2430'
export const FONT_COLOR = '#d1d4dc'
export const ACCENT_COLOR = '#2962ff'

export const CANDLE_UP_COLOR = '#26a69a'
export const CANDLE_DOWN_COLOR = '#ef5350'

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

/** POI order block zone colors by direction. */
export const POI_COLORS: Record<string, string> = {
  bullish: '#26a69a', // demand zone — teal
  bearish: '#ef5350', // supply zone — red
  mitigated: '#888888',
}
