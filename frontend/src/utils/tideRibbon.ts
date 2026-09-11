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
 *   - the **saturation** is Market Control's magnitude — a move with no
 *     conviction behind it drains to grey rather than being labelled;
 *   - the **edges** are the credited controller, when there is one.
 *
 * A ribbon that is coloured but washed out is one interesting case: price is
 * trending structurally while nobody is paying for it. The edges exist because
 * saturation takes `|control_score|` and so cannot tell the other one apart —
 * a trend the *opposite* side is funding saturates exactly like a trend its
 * own side is funding. Putting the controller on the border leaves agreement
 * invisible (border and band share a hue) and makes the conflict jump out: a
 * bullish band edged in red. Both disagreements are deliberately visible as
 * texture instead of hidden inside an average.
 *
 * Descriptive only. Nothing here says buy or sell — a measurement of this
 * project's own sweep/raid events across 16 symbols found no entry trigger
 * worth encoding (see `research/raid_reversal.py`).
 */
import type {
  Candle,
  DashboardData,
  MarketControlSide,
  MarketDirection,
  TimeFrame,
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

/** `MarketControlAnalyzer._TIMEFRAME_WINDOW`, mirrored so the fallback below is
 *  measured over the same horizon as the reading it stands in for. */
const AGGRESSION_WINDOW: Partial<Record<TimeFrame, number>> = {
  '1m': 20,
  '5m': 15,
  '15m': 10,
  '30m': 7,
  '1h': 7,
  '4h': 5,
  '1d': 5,
  '1w': 3,
  '1M': 3,
}

/**
 * Net taker aggression over a trailing window, as a percentage of the volume
 * traded in it — the CVD half of Market Control, computed without open
 * interest.
 *
 * This exists because of a measurement, not a preference: `market_control`
 * covers 99% of a 15m window but only ~60% of 1h and **15% of 4h**, since
 * Binance retains roughly 30 days of open interest. The ribbon was therefore
 * grey over most of every higher-timeframe chart — reading as "nobody is paying
 * for this move" when the truth was "we cannot see who is". The delta needed to
 * answer that is already in every candle.
 */
function aggressionByCandle(candles: Candle[], window: number): Map<string, number> {
  const out = new Map<string, number>()
  if (window < 1 || candles.length < window) return out
  const deltas = candles.map((c) => 2 * c.taker_buy_volume - c.volume)
  let delta = 0
  let volume = 0
  for (let i = 0; i < candles.length; i += 1) {
    delta += deltas[i]
    volume += candles[i].volume
    if (i >= window) {
      delta -= deltas[i - window]
      volume -= candles[i - window].volume
    }
    if (i >= window - 1 && volume > 0) out.set(candles[i].timestamp, (100 * delta) / volume)
  }
  return out
}

/**
 * How much saturation an OI-less reading may reach.
 *
 * Aggression alone is the same axis as `control_score` — measured across ten
 * combos the two agree on sign 100% of the time, because the score's sign *is*
 * the aggressor side; what open interest adds is whether that aggression is
 * opening fresh positions or closing old ones. So the fallback is the same
 * reading with its confirmation missing, and it is held below full saturation
 * so a candle where OI actually confirmed can always out-colour one where it
 * merely could not be checked.
 */
const UNCONFIRMED_CONVICTION = 0.8

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

  const aggression = aggressionByCandle(
    data.candles,
    AGGRESSION_WINDOW[data.timeframe] ?? 10,
  )
  const aggressionScale = convictionScale([...aggression.values()])

  const out: RibbonBand[] = []
  for (const candle of data.candles) {
    const p = vwap.get(candle.timestamp)
    if (!p || p.upper_1 === null || p.lower_1 === null) continue
    // Where open interest reaches, the full CVD×OI reading drives saturation.
    // Where it does not — a spot symbol, or simply a window older than
    // Binance's ~30-day OI retention — the aggression alone stands in at
    // reduced weight rather than draining the band to grey. `controller` is
    // left uncredited there on purpose: crediting a side is a claim about
    // *fresh money*, and that is exactly the part that cannot be seen.
    const c = control.get(candle.timestamp)
    const agg = aggression.get(candle.timestamp)
    const conviction = c
      ? Math.min(1, Math.abs(c.control_score) / scale)
      : agg === undefined
        ? 0
        : UNCONFIRMED_CONVICTION * Math.min(1, Math.abs(agg) / aggressionScale)
    out.push({
      timestamp: candle.timestamp,
      upper: p.upper_1,
      lower: p.lower_1,
      mid: p.value,
      trend: trends.get(candle.timestamp) ?? 'neutral',
      controller: c?.controller ?? 'balanced',
      conviction,
    })
  }
  return out
}
