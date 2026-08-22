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
  // **One hue for both sides of the pool layer.** EQH and EQL are the same
  // observation — resting stops — and which side they sit on is already told
  // by where they are relative to price, so the old pink/cyan pair spent two
  // saturated colours saying nothing. The sand tone puts them in the same
  // family as the Sweep (`STRUCTURE_EVENT_STYLES.liquidity_sweep`), which is
  // the same story one candle later, and stays clear of the neutral grey the
  // OB boxes and VSA marks now use.
  equal_highs: '#c9a86a',
  equal_lows: '#c9a86a',
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
  liquidity_sweep: { label: 'Sweep', color: '#e8ebf2' },  // soft white — a momentary stop-grab, not a reference level
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

/** POI order block box colors — border and fill.
 *  **Neutral for both directions**, the same choice already made for the
 *  consolidation box: the OB is the layer that occupies the most pixels on
 *  screen, so it is the one that gains most by reading as background. Its
 *  side is obvious from where it sits relative to price, and the old bearish
 *  red (`#ef5350`) was a hair from the down candle's own red (`#da4d4d`), so
 *  a tall supply box turned into a stain over the candles instead of framing
 *  them. */
export const POI_BOX_STYLES: Record<string, { border: string; fill: string }> = {
  bullish: { border: '#8a94a666', fill: '#8a94a610' },
  bearish: { border: '#8a94a666', fill: '#8a94a610' },
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

/**
 * Behavior divergence styles.
 *
 * **One hue for the layer**, the same discipline applied to VSA and the pools:
 * every divergence is one statement — *the flow contradicts the price* — and
 * the four members differ in which contradiction, which the label and the
 * geometry already carry (`distribution` draws a marker, `exhaustion` a dome
 * above the high, `absorption` a bowl below the low). Pink is the layer's own
 * accent: it is the rarest reading on the chart, so it keeps a real colour,
 * and the hue is free now that the pools went sand — it stays clear of the
 * whole cool half of the palette, which the VWAP family owns.
 *
 * It is a *light* rose rather than a deep one because the layer's geometry is
 * now a ~7px glyph: on this background a small mark is read by luminance far
 * more than by saturation, so lightness is what makes it glow. A darker,
 * more saturated pink measured as "washed out" at this size for exactly that
 * reason — the ink was strong, the light was not.
 */
export const DIVERGENCE_BASE_COLOR = '#ff8fc9'

export const DIVERGENCE_STYLES: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  distribution: { label: 'DIST', color: DIVERGENCE_BASE_COLOR, bg: `${DIVERGENCE_BASE_COLOR}15`, icon: '▼' },
  accumulation: { label: 'ACCUM', color: DIVERGENCE_BASE_COLOR, bg: `${DIVERGENCE_BASE_COLOR}15`, icon: '▲' },
  exhaustion: { label: 'EXHAUST', color: DIVERGENCE_BASE_COLOR, bg: `${DIVERGENCE_BASE_COLOR}15`, icon: '◇' },
  absorption: { label: 'ABSORB', color: DIVERGENCE_BASE_COLOR, bg: `${DIVERGENCE_BASE_COLOR}15`, icon: '◆' },
}

/**
 * Volume-Spread-Analysis (VSA) pattern styles — the tint of a candle's volume
 * bar (and the mark drawn above/below the candle) when the VSA analyzer flags
 * it.
 *
 * **One hue for the whole layer.** Climax vs thrust vs no-supply is a
 * difference of *degree* (how hard the candle rejected), not of kind, so it
 * rides the *weight* channel instead of the hue channel: climax opaque,
 * thrust ~70%, no-supply/no-demand ~45%. The direction is already carried by
 * `position` (above/below the bar) and the pattern name by `label`, so a
 * second and third saturated colour bought nothing and put VSA at the same
 * visual volume as the structure staircase — the only layer that should read
 * as primary.
 */
export const VSA_BASE_COLOR = '#9aa4b8'

export const VSA_STYLES: Record<
  string,
  { label: string; color: string; position: 'aboveBar' | 'belowBar' }
> = {
  selling_climax: { label: 'S.Climax', color: `${VSA_BASE_COLOR}ff`, position: 'belowBar' },
  buying_climax: { label: 'B.Climax', color: `${VSA_BASE_COLOR}ff`, position: 'aboveBar' },
  down_thrust: { label: 'D.Thrust', color: `${VSA_BASE_COLOR}b3`, position: 'belowBar' },
  up_thrust: { label: 'U.Thrust', color: `${VSA_BASE_COLOR}b3`, position: 'aboveBar' },
  no_supply: { label: 'NoSupply', color: `${VSA_BASE_COLOR}73`, position: 'belowBar' },
  no_demand: { label: 'NoDemand', color: `${VSA_BASE_COLOR}73`, position: 'aboveBar' },
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

// Supertrend (ATR-banded trailing trend): the band takes the colour of the
// trend it is trailing -- a floor under price while bullish, a ceiling above
// it while bearish. Flip markers reuse the same pair.
export const SUPERTREND_UP_COLOR = '#26a69a'
export const SUPERTREND_DOWN_COLOR = '#ef5350'
export const SUPERTREND_LINE_WIDTH = 2
// A Supertrend flip that took the band's stops and handed price back inside.
// Purple is the project's "trap" colour (see the exhaustion-grab hunt window),
// so a false break reads the same way wherever it appears.
export const SUPERTREND_STOP_RUN_COLOR = '#ab47bc'

// A level that was tested at the Tide envelope's edge and held. Gold, so it
// reads as the rare confluence it is against the purple stop-run marks.
export const DEFENDED_LEVEL_COLOR = '#ffca28'

// --- Volume profile (volume-at-price) ------------------------------------
/**
 * Length of the heaviest band, measured in **chart bars** rather than pixels,
 * so the profile grows and shrinks with horizontal zoom the way the reference
 * study does (its `scale_volume` works in bar units too).
 */
export const VP_MAX_LENGTH_BARS = 25
/**
 * Pixel bounds on that length. The dashboard's default window is far wider
 * than a typical TradingView view (1200 candles, ~1px per bar), so the raw
 * bar-unit length would collapse to a stub when zoomed out and swallow the
 * pane when zoomed in.
 */
export const VP_BAR_MIN_PX = 70
export const VP_BAR_MAX_PX = 300
/** Gap between the histogram's right anchor and the price scale, in px. */
export const VP_RIGHT_MARGIN = 12
/** Vertical gap between bands, in px — what gives the hatched line look. */
export const VP_LEVEL_GAP = 1
/** Adjacent bands merge until each renders at least this tall, in px. */
export const VP_MIN_BAND_PX = 2.5
/** Bands outside the value area. */
export const VP_LEVEL_LINE_COLOR = 'rgba(150, 158, 178, 0.45)'
/** Bands inside the value area. */
export const VP_VA_COLOR = '#2962ff'
/** Point of control: the band that traded most, and its line. */
export const VP_POC_COLOR = '#f23645'
export const VP_POC_LINE_WIDTH = 1
export const VP_VA_LINE_WIDTH = 1
/** Gap between a level line's end and the band it points at, in px. */
export const VP_VA_LINE_GAP = 10
/** Delta mode (modifier-click): bands coloured by the aggressor side. */
export const VP_DELTA_BUY_COLOR = 'rgba(38, 166, 154, 0.75)'
export const VP_DELTA_SELL_COLOR = 'rgba(239, 83, 80, 0.75)'

// --- VWAP (average price paid since an anchor) ----------------------------
/**
 * The session line: a desaturated ice blue. It used to be gold, which
 * collided with the sand the equal-level pools now use — and a *level someone
 * is defending* and *the average price a population paid* must never read as
 * the same family. The cool half of the palette is the free one (the OB boxes
 * went neutral grey, the divergences went pink), and it carries the right
 * tone for this layer: a reference the whole tape shares, not a directional
 * call like the Supertrend band.
 *
 * Low saturation is the point rather than a compromise: this line crosses the
 * entire width of the pane, so it is the layer with the most ink on screen
 * after the candles themselves, and it earns its place by receding behind
 * them. A saturated blue was tried first and read as a fourth thing shouting.
 */
export const VWAP_COLOR = '#8fb0c9'
/** A hairline, for the same reason the colour is desaturated: this is the
 *  widest-running overlay on the pane, so it reads as a reference rather than
 *  as a plotted series. */
export const VWAP_LINE_WIDTH = 1
/** ±1σ / ±2σ of the accumulation — how widely the session's volume paid. */
export const VWAP_BAND_1_COLOR = 'rgba(143, 176, 201, 0.40)'
export const VWAP_BAND_2_COLOR = 'rgba(143, 176, 201, 0.20)'
/**
 * Anchored VWAPs (a CHoCH, a sweep): cyan and violet — the same cool family
 * as the session line, since they are the same kind of reading, but distinct
 * within it because they answer a different question: not "what did today
 * pay" but "what does the crowd that entered on that event hold".
 */
export const VWAP_ANCHORED_COLORS = ['#4dd0e1', '#9575cd']
export const VWAP_ANCHORED_LINE_WIDTH = 2

// Block reclaim: a VWAP reclaim that followed a test of an order block. Takes
// the VWAP's own hue -- the reading is about that average -- lifted to full
// strength so it reads against the band it sits on.
export const BLOCK_RECLAIM_COLOR = '#d8a949'
