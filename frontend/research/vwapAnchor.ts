/**
 * The periodic VWAP envelope, rebuilt in the browser's own units.
 *
 * A faithful port of `liquidity_hunter/indicators/vwap.py::_accumulated_points`
 * and `_anchor_key`, and nothing else — it exists so P5 can ask what the PISO
 * would have read had the H4 envelope restarted on a different calendar
 * period. Production is untouched: the app still consumes `data.vwap` exactly
 * as the API sends it, and `research/pisoH4AnchorAudit.ts` swaps the points on
 * a copy.
 *
 * Fidelity is not assumed. `research/vwapAnchor.test.ts` rebuilds every fixture
 * with the anchor the backend actually used and requires the result to match
 * the shipped points; a port that drifts fails there before it can produce a
 * number.
 */
import type { Candle, VWAPPoint } from '../src/types/dashboard.ts'

/** The calendar periods `_VWAP_ANCHOR_PERIOD` can name. `rolling` and `event`
 *  are deliberately absent: neither draws the Tide envelope. */
export type AnchorName = 'session' | 'week' | 'month'

export const ANCHOR_LABEL: Record<AnchorName, string> = {
  session: 'SESSION (dia UTC)',
  week: 'WEEK (semana ISO)',
  month: 'MONTH (mes calendario)',
}

/** What each timeframe ships with — `_VWAP_ANCHOR_PERIOD` plus its default. */
export function productionAnchor(timeframe: string): AnchorName {
  if (timeframe === '4h') return 'week'
  if (timeframe === '1d' || timeframe === '1w') return 'month'
  return 'session'
}

/** `(high + low + close) / 3` — the price a candle's whole volume is paid at. */
export function typicalPrice(c: Candle): number {
  return (c.high + c.low + c.close) / 3
}

/**
 * The bucket a candle belongs to. A change here restarts the accumulation.
 *
 * Every boundary is UTC, because the timestamps are: crypto has no exchange
 * session, so `SESSION` is the 00:00 UTC day Binance's own daily candle uses.
 * `WEEK` is the ISO week (Monday-anchored), matching
 * `datetime.isocalendar()[:2]`.
 */
export function anchorKey(iso: string, anchor: AnchorName): string {
  const d = new Date(iso)
  if (anchor === 'session') return d.toISOString().slice(0, 10)
  if (anchor === 'month') return `${d.getUTCFullYear()}-${d.getUTCMonth()}`
  const [year, week] = isoWeek(d)
  return `${year}-W${week}`
}

/** ISO-8601 week number and its week-numbering year, in UTC. */
export function isoWeek(d: Date): [number, number] {
  // Thursday decides the year an ISO week belongs to.
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  const dow = (t.getUTCDay() + 6) % 7 // Monday = 0
  t.setUTCDate(t.getUTCDate() - dow + 3)
  const year = t.getUTCFullYear()
  const firstThursday = new Date(Date.UTC(year, 0, 4))
  const fdow = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - fdow + 3)
  const week = 1 + Math.round((t.getTime() - firstThursday.getTime()) / (7 * 86400000))
  return [year, week]
}

/**
 * Running VWAP with ±1σ/±2σ bands, restarting at each period boundary.
 *
 * The variance comes from the accumulated first and second moments, which is
 * what the Python does — the same floating-point path, so parity is exact
 * rather than approximate. A zero-volume prefix yields no point at all, and a
 * degenerate variance yields a point with null bands: both are the shipped
 * behaviour, and the PISO skips a candle whose ±1σ is null.
 */
export function rebuildVwapPoints(
  candles: Candle[],
  anchor: AnchorName,
  multipliers: readonly number[] = [1, 2],
): VWAPPoint[] {
  const points: VWAPPoint[] = []
  let sumWeight = 0
  let sumWeighted = 0
  let sumSquare = 0
  let currentKey: string | null = null
  let currentAnchor: string | null = null

  for (const c of candles) {
    const key = anchorKey(c.timestamp, anchor)
    if (currentAnchor === null || key !== currentKey) {
      currentKey = key
      currentAnchor = c.timestamp
      sumWeight = sumWeighted = sumSquare = 0
    }
    const price = typicalPrice(c)
    sumWeight += c.volume
    sumWeighted += c.volume * price
    sumSquare += c.volume * price * price
    if (sumWeight <= 0) continue

    const value = sumWeighted / sumWeight
    const variance = Math.max(sumSquare / sumWeight - value * value, 0)
    let upper1: number | null = null
    let lower1: number | null = null
    let upper2: number | null = null
    let lower2: number | null = null
    if (variance > 0) {
      const dev = Math.sqrt(variance)
      upper1 = value + multipliers[0] * dev
      lower1 = value - multipliers[0] * dev
      upper2 = value + multipliers[1] * dev
      lower2 = value - multipliers[1] * dev
    }
    points.push({
      timestamp: c.timestamp,
      anchor_timestamp: currentAnchor,
      value,
      upper_1: upper1,
      lower_1: lower1,
      upper_2: upper2,
      lower_2: lower2,
    })
  }
  return points
}
