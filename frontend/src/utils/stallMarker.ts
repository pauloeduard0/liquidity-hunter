import type { GlyphMark } from '../charting/DivergenceMarksPrimitive'
import type { Candle, StructuralStall } from '../types/dashboard'
// Explicit `.ts` (the project sets `allowImportingTsExtensions`): it lets
// `stallMarker.test.ts` run under `node --test` with no test runner to install.
import { toChartTime } from './chartTime.ts'

/**
 * The structural-stall mark: one small hollow circle on the candle where the
 * standing leg stopped reading as active.
 *
 * A circle, and not `✕`/`▲`/`▼`, on purpose. Every other glyph on this pane is
 * already spoken for: `✕` is a failed CHoCH (an *invalidation*), the arrows are
 * direction. A stall is neither — the BOS still stands, the protected level
 * still stands, the trend is unchanged — so the mark had to be the one shape
 * that points nowhere and cancels nothing.
 *
 * Nothing else about the chart changes: no line is truncated or dimmed, no
 * label is added, no event is created. This is an *addition* of one glyph, and
 * it is the whole of the visual treatment.
 */

/** Two hex alpha bytes. Below the structure lines' own `99` dim, because the
 *  stall is a background state and must never compete with a break. */
export const STALL_MARK_ALPHA = '80'

/**
 * Where the mark hangs.
 *
 * A bullish leg's stall goes *below* the candle and a bearish one *above* —
 * the opposite of the divergence convention, and deliberately so. There the
 * side carries meaning (the mark leans the way the reading points); here it
 * carries none, and the only job is to stay off the price action the leg came
 * from. A bullish leg arrived from below, so its ink goes under the bar.
 */
export function stallMarkSide(direction: StructuralStall['direction']): 'above' | 'below' {
  return direction === 'bullish' ? 'below' : 'above'
}

/**
 * `StructuralStall` -> the glyph to draw, or `null` when there is nothing to
 * draw: no stall in the payload, or a `stale_since` that names no candle in
 * the visible window (a truncated window, or a payload from another series).
 *
 * Anchored strictly at `stale_since` — never at `last_advance_timestamp` (the
 * BOS, which has its own line and label) and never at `leg_extreme_timestamp`.
 * The question the mark answers is *when the leg stopped counting as active*.
 */
export function buildStallMark(
  stall: StructuralStall | null | undefined,
  candles: readonly Candle[],
  colors: Record<string, string | undefined>,
): GlyphMark | null {
  if (!stall?.stale_since || !stall.direction) return null
  const candle = candles.find((c) => c.timestamp === stall.stale_since)
  if (!candle) return null
  const color = colors[stall.direction]
  if (!color) return null
  const side = stallMarkSide(stall.direction)
  return {
    time: toChartTime(stall.stale_since),
    price: side === 'below' ? candle.low : candle.high,
    side,
    glyph: 'circle',
    color,
    alpha: STALL_MARK_ALPHA,
  }
}

/** The primitive takes a list; a stall is one mark or none. */
export function buildStallMarks(
  stall: StructuralStall | null | undefined,
  candles: readonly Candle[],
  colors: Record<string, string | undefined>,
): GlyphMark[] {
  const mark = buildStallMark(stall, candles, colors)
  return mark === null ? [] : [mark]
}
