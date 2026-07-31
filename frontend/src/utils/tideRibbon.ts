/**
 * The "Tide" reading — the project's own composite, derived entirely on the
 * client from data the API already sends.
 *
 * The layers this dashboard carries are of three different natures, and that
 * is why averaging them into one number was the wrong idea: structure is a
 * *state* (it persists until an event flips it), control is a *measurement*
 * over a window, and VWAP is a *place*. Instead of summing them, each one gets
 * its own visual channel on a single geometry:
 *
 *   - the **envelope** (VWAP ±1σ) is the shape — where the current
 *     population's break-even sits and how dispersed it is;
 *   - the **hue** is the SMC structural trend — what the chart's own
 *     BOS/CHoCH staircase says;
 *   - the **saturation** is Market Control — a move with no fresh money
 *     behind it drains to grey rather than being labelled.
 *
 * A ribbon that is coloured but washed out is the interesting case: price is
 * trending structurally while nobody is paying for it. That disagreement is
 * the reading, and it is deliberately visible as texture instead of hidden
 * inside an average.
 *
 * Descriptive only. Nothing here says buy or sell — a measurement of this
 * project's own sweep/raid events across 16 symbols found no entry trigger
 * worth encoding (see `research/raid_reversal.py`).
 */
import type {
  DashboardData,
  MarketControlSide,
  MarketDirection,
  VWAPPoint,
} from '../types/dashboard'

export type TideTrend = MarketDirection | 'neutral'

export interface RibbonBand {
  timestamp: string
  upper: number
  lower: number
  mid: number
  trend: TideTrend
  controller: MarketControlSide
  /** Saturation channel, 0-1. See `convictionScale` for why this is the
   *  normalized score rather than the credited controller. */
  conviction: number
}

export interface PhasePoint {
  timestamp: string
  /** Position inside the envelope: 0 = VWAP, ±50 = ±1σ, clamped at ±100. */
  value: number
  trend: TideTrend
}

/** The events that move the structural state machine, mirroring the backend's
 *  own replay rule: provisional marks never mutate the standing trend, pivot
 *  labels and sweeps describe a wick rather than a state, and a failed CHoCH
 *  reverts. */
function trendAfter(event: string, direction: MarketDirection, current: TideTrend): TideTrend {
  if (event === 'break_of_structure' || event === 'change_of_character') return direction
  if (event === 'choch_failed') return direction === 'bullish' ? 'bearish' : 'bullish'
  return current
}

/**
 * The standing structural trend at every candle.
 *
 * Replays the internal structure stream forward and holds each state until the
 * next event — the same reading the chart's staircase draws, so the ribbon can
 * never disagree with the labels drawn over it.
 */
export function structureTrendByCandle(data: DashboardData): Map<string, TideTrend> {
  const events = [...data.internal_structure_events]
    .filter((e) => !e.provisional)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))

  const out = new Map<string, TideTrend>()
  let trend: TideTrend = 'neutral'
  let next = 0
  for (const candle of data.candles) {
    while (next < events.length && events[next].timestamp <= candle.timestamp) {
      const e = events[next]
      trend = trendAfter(e.event, e.direction, trend)
      next += 1
    }
    out.set(candle.timestamp, trend)
  }
  return out
}

/** Index the periodic VWAP by timestamp, keeping only readings that have a
 *  defined ±1σ band (the accumulation needs dispersion before an envelope
 *  exists — the first candles of every session have none). */
function vwapByTimestamp(points: VWAPPoint[]): Map<string, VWAPPoint> {
  const out = new Map<string, VWAPPoint>()
  for (const p of points) {
    if (p.upper_1 === null || p.lower_1 === null) continue
    out.set(p.timestamp, p)
  }
  return out
}

/**
 * The scale that turns `control_score` into a saturation channel.
 *
 * The first version keyed saturation off `controller !== 'balanced'` — whether
 * a side is *credited* with control. Measured across the visible window, that
 * fires on 13% of BTC 15m candles but only 1% of BTC 4h and 4% of SOL 1h: the
 * ribbon would have been grey essentially always on the higher timeframes, and
 * a channel that never varies carries no information. `MarketControlAnalyzer`
 * credits a side only in the OI-rising quadrants, which is right for a "don't
 * fade this" flag and far too strict for a texture.
 *
 * The signed score has usable range instead (median |score| 6-13, p90 21-39),
 * so saturation reads it directly, normalized against the *window's own* p90.
 * That makes the channel legible on every timeframe at the cost of being a
 * relative reading: saturation compares this candle to the rest of the visible
 * window, not to another symbol's chart.
 */
function convictionScale(scores: number[]): number {
  if (scores.length === 0) return 1
  const sorted = [...scores].map(Math.abs).sort((a, b) => a - b)
  const p90 = sorted[Math.floor(sorted.length * 0.9)] ?? 0
  // A window with no dispersion at all would divide by ~0 and paint everything
  // fully saturated; fall back to a flat scale so it reads as uniformly quiet.
  return p90 > 0 ? p90 : 1
}

/**
 * The ribbon: one band per candle, carrying the three channels.
 *
 * Bands are emitted only where a VWAP envelope exists, so the ribbon breaks
 * at each session rollover rather than drawing a jump nobody paid.
 */
export function buildRibbon(data: DashboardData): RibbonBand[] {
  const points = data.vwap?.points
  if (!points || points.length === 0) return []

  const vwap = vwapByTimestamp(points)
  const trends = structureTrendByCandle(data)
  const series = data.market_control?.series ?? []
  const control = new Map(series.map((p) => [p.timestamp, p]))
  const scale = convictionScale(series.map((p) => p.control_score))

  const out: RibbonBand[] = []
  for (const candle of data.candles) {
    const p = vwap.get(candle.timestamp)
    if (!p || p.upper_1 === null || p.lower_1 === null) continue
    // No OI (a spot symbol) means no control reading at all, which is not the
    // same as a reading of zero — but both drain the ribbon to grey, which is
    // the honest picture: nothing is known about who is paying.
    const c = control.get(candle.timestamp)
    out.push({
      timestamp: candle.timestamp,
      upper: p.upper_1,
      lower: p.lower_1,
      mid: p.value,
      trend: trends.get(candle.timestamp) ?? 'neutral',
      controller: c?.controller ?? 'balanced',
      conviction: c ? Math.min(1, Math.abs(c.control_score) / scale) : 0,
    })
  }
  return out
}

/**
 * The clamp. Measured across BTC 15m/4h and SOL 1h: |phase| exceeds 100 on
 * 9-13% of candles — pinning one bar in eight at the rail would throw away
 * resolution exactly where the move is interesting — while |phase| > 150 is
 * 0.9-2.6%, a genuine tail. So the rail sits at 150 (±3σ) and ±50/±100 stay
 * the meaningful gradations.
 */
const PHASE_CLAMP = 150

/**
 * Minimum envelope width, as a fraction of the VWAP itself.
 *
 * A fresh accumulation has near-zero dispersion, so the first candles after an
 * anchor divide by a σ of ~0 and produce readings in the millions (measured:
 * 4.3e6 on BTC 15m). One such point is enough to wreck the pane's autoscale
 * and flatten every real reading into a hairline. Below this width the
 * envelope is not yet a measurement and the candle is skipped.
 */
const MIN_SPAN_FRAC = 1e-4

/**
 * The phase oscillator: where price sits inside its own envelope.
 *
 * 0 is the VWAP itself (the population's break-even), ±50 the ±1σ edges, ±100
 * the clamp at ±2σ. Drawn as a line over the control histogram, so the *gap*
 * between the two is legible: price stretched to +80 while the control bars
 * are short and grey is an extension nobody is funding.
 */
export function buildPhase(data: DashboardData): PhasePoint[] {
  const points = data.vwap?.points
  if (!points || points.length === 0) return []

  const vwap = vwapByTimestamp(points)
  const trends = structureTrendByCandle(data)

  const out: PhasePoint[] = []
  for (const candle of data.candles) {
    const p = vwap.get(candle.timestamp)
    if (!p || p.upper_1 === null || p.lower_1 === null) continue
    // Distance is measured against the band on the side price actually sits,
    // because the two are not symmetric: a volume-weighted deviation computed
    // over a skewed accumulation puts the mean off-centre.
    const above = candle.close >= p.value
    const edge = above ? p.upper_1 : p.lower_1
    const span = Math.abs(edge - p.value)
    if (span <= Math.abs(p.value) * MIN_SPAN_FRAC) continue
    const raw = ((candle.close - p.value) / span) * 50
    out.push({
      timestamp: candle.timestamp,
      value: Math.max(-PHASE_CLAMP, Math.min(PHASE_CLAMP, raw)),
      trend: trends.get(candle.timestamp) ?? 'neutral',
    })
  }
  return out
}
