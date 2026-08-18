/**
 * Defended levels ("piso") — where a level was tested and held.
 *
 * The third piece of the Tide reading, and the one that needed a distinction
 * the first level map got wrong: a **target** is virgin liquidity nobody has
 * defended yet (price is drawn there because the stops are there), while a
 * **floor** is a level that has already been tested and *held*, with a positive
 * signature. Only the second is marked here, because the question it answers —
 * "did this hold?" — is the one a confluence rule can actually address.
 *
 * The observation is a single candle in which all of this coincided:
 *
 *   1. price pushed beyond the Tide envelope's ±1σ edge (the population's
 *      break-even plus one deviation) by at least `MIN_EXCURSION_ATR`;
 *   2. the candle *closed back inside* the envelope — the excursion was given
 *      straight back;
 *   3. the wick on the raided side dominates the body (`MIN_WICK_BODY`), so it
 *      reads as rejection rather than as a candle that simply ran out of room;
 *   4. the swept range overlapped standing levels from at least
 *      `MIN_FAMILIES` different **families**.
 *
 * Families are the anti-collinearity rule: two order blocks stacked on the same
 * price are one piece of evidence, not two, or the same wick gets counted three
 * times. Measured 2026-07-31 across 10 symbol/timeframe combos, this matters
 * enormously — but far less than *time* does. A first probe that ignored when
 * each level existed found two families on 96% of candidate candles (998 marks,
 * ~100 per chart): with 1200 candles of accumulated zones, every price has
 * something near it. Once each level only counts while it is actually standing,
 * the same gates give 264, and at the shipped thresholds 27 — about three per
 * chart, which is the point. A mark that fires on every pullback would bury the
 * ribbon it is drawn over.
 *
 * Descriptive only. This says a level was defended, never that it will hold
 * again: the same family of events was measured across 16 symbols in
 * `research/raid_reversal.py` and produced no entry edge that survived a
 * direction-matched control.
 */
import type {
  Candle,
  DashboardData,
  MarketStructure,
  VWAPPoint,
} from '../types/dashboard'

/** The evidence families. Two members of one family count once. */
export type LevelFamily = 'structural' | 'order' | 'resting' | 'fair'

export const FAMILY_LABEL: Record<LevelFamily, string> = {
  structural: 'estrutura',
  order: 'ordem',
  resting: 'liquidez',
  fair: 'preço justo',
}

/** A price area that could be defended, and the span over which it stood. */
export interface DefenceLevel {
  low: number
  high: number
  family: LevelFamily
  /** ISO timestamps — the level counts only for candles inside this span. */
  born: string
  died: string | null
}

export interface DefendedMark {
  timestamp: string
  /** Which edge was raided: a top defence pushed above +1σ, a bottom below. */
  side: 'top' | 'bottom'
  /** The extreme the wick reached — where the mark is anchored. */
  price: number
  /** The envelope edge that was cleared and reclaimed. */
  edge: number
  families: LevelFamily[]
  /** How far beyond the edge the wick reached, in mean true ranges. */
  excursionAtr: number
  /** Levels that were consumed by this very candle — the pools it took. */
  consumed: number
}

/**
 * How far beyond the ±1σ edge the wick must reach.
 *
 * The same 1.0-ATR discipline `SupertrendBreakAnalyzer` needed: without an
 * excursion gate the reading is dominated by candles that brushed the edge and
 * never went anywhere. Measured over 10 combos: 0.25 ATR → 264 marks, 0.5 →
 * 88, **1.0 → 27**, and the reference case (BTC 1h, 2026-07-31 01:00, a 4-family
 * defence that took two liquidation pools) clears it at 1.61 ATR.
 */
const MIN_EXCURSION_ATR = 1.0

/** Rejection, not exhaustion of room: the raided wick must beat the body. */
const MIN_WICK_BODY = 2.0

/** "Does it hold?" is a question worth asking only where sources agree. */
const MIN_FAMILIES = 2

/** Half-width given to a *level* (a price, not a box) before it can be touched,
 *  as a fraction of the series' mean true range. */
const LEVEL_TOLERANCE_ATR = 0.5

/** Mean true range as a fraction of price — the series' own volatility unit,
 *  so every threshold above is stated in ATRs rather than in percent. */
export function meanTrueRangePct(candles: Candle[]): number {
  let sum = 0
  let n = 0
  let prev: Candle | null = null
  for (const c of candles) {
    const tr = prev
      ? Math.max(c.high - c.low, Math.abs(c.high - prev.close), Math.abs(c.low - prev.close))
      : c.high - c.low
    if (c.close > 0) {
      sum += tr / c.close
      n += 1
    }
    prev = c
  }
  return n > 0 ? sum / n : 0
}

/**
 * Every price area a defence could be anchored to, tagged by family and by the
 * span over which it stood.
 *
 * `standingUntil` supplies the structural levels' lifespan: a BOS/CHoCH
 * reference stands exactly as long as its line is drawn on the chart, so what
 * counts as evidence here is what the user can see. Without it, references from
 * six weeks back "defend" today's candle.
 */
export function buildDefenceLevels(
  data: DashboardData,
  standingUntil: (event: MarketStructure) => string | null,
): DefenceLevel[] {
  const tolFrac = LEVEL_TOLERANCE_ATR * meanTrueRangePct(data.candles)
  const out: DefenceLevel[] = []
  const level = (price: number, family: LevelFamily, born: string, died: string | null) => {
    const tol = price * tolFrac
    out.push({ low: price - tol, high: price + tol, family, born, died })
  }

  // 1. structural — the levels the staircase actually broke
  for (const e of data.internal_structure_events) {
    if (e.provisional) continue
    if (e.event !== 'break_of_structure' && e.event !== 'change_of_character') continue
    if (e.reference_price_level == null) continue
    level(e.reference_price_level, 'structural', e.timestamp, standingUntil(e))
  }

  // 2. order — POI blocks, live from creation to the close that broke them.
  //    Boxes already span a real range, so they need no tolerance.
  for (const z of data.poi_zones ?? []) {
    out.push({
      low: z.price_low,
      high: z.price_high,
      family: 'order',
      born: z.created_at,
      died: z.invalidated_at,
    })
  }

  // 3. resting liquidity — equal levels and liquidation pools, each retired
  //    when it is consumed
  for (const z of data.liquidity_zones) {
    if (z.zone_type !== 'equal_highs' && z.zone_type !== 'equal_lows') continue
    const tol = z.price_low * tolFrac
    out.push({
      low: z.price_low - tol,
      high: z.price_high + tol,
      family: 'resting',
      born: z.formed_at,
      died: z.invalidated_at,
    })
  }
  for (const b of data.liquidation_map?.bands ?? []) {
    out.push({
      low: b.price_low,
      high: b.price_high,
      family: 'resting',
      born: b.start_time,
      died: b.end_time,
    })
  }

  // 4. fair price — the profile's POC and value-area edges. The profile is one
  //    snapshot of a recent lookback, so it only speaks for its own window.
  const vp = data.volume_profile
  if (vp) {
    for (const price of [vp.poc_price, vp.value_area_low, vp.value_area_high]) {
      if (price) level(price, 'fair', vp.start_timestamp, null)
    }
  }

  return out
}

function vwapByTimestamp(points: VWAPPoint[]): Map<string, VWAPPoint> {
  const out = new Map<string, VWAPPoint>()
  for (const p of points) {
    if (p.upper_1 === null || p.lower_1 === null) continue
    out.set(p.timestamp, p)
  }
  return out
}

/** The candles where a level was tested at the envelope edge and held. */
export function buildDefendedMarks(data: DashboardData, levels: DefenceLevel[]): DefendedMark[] {
  const points = data.vwap?.points
  if (!points || points.length === 0) return []

  const vwap = vwapByTimestamp(points)
  const trPct = meanTrueRangePct(data.candles)
  if (trPct <= 0) return []

  const out: DefendedMark[] = []
  for (const c of data.candles) {
    const p = vwap.get(c.timestamp)
    if (!p || p.upper_1 === null || p.lower_1 === null) continue
    const atr = trPct * c.close
    if (atr <= 0) continue

    // Cleared the edge and closed back inside it. The two edges are checked
    // independently because the envelope is not symmetric — a volume-weighted
    // deviation over a skewed accumulation puts the mean off-centre.
    const top = c.high > p.upper_1 && c.close < p.upper_1
    const bottom = c.low < p.lower_1 && c.close > p.lower_1
    if (!top && !bottom) continue

    const edge = top ? p.upper_1 : p.lower_1
    const price = top ? c.high : c.low
    const excursion = Math.abs(price - edge)
    if (excursion < MIN_EXCURSION_ATR * atr) continue

    const body = Math.abs(c.close - c.open) || 1e-9
    const wick = top ? c.high - Math.max(c.open, c.close) : Math.min(c.open, c.close) - c.low
    if (wick < MIN_WICK_BODY * body) continue

    // The range the wick swept: from the edge it cleared to the extreme it
    // reached. A level defended here had to sit inside that.
    const low = Math.min(edge, price)
    const high = Math.max(edge, price)
    const families = new Set<LevelFamily>()
    let consumed = 0
    for (const l of levels) {
      if (l.high < low || l.low > high) continue
      if (l.born > c.timestamp) continue
      if (l.died !== null && l.died < c.timestamp) continue
      families.add(l.family)
      // A pool retired *by this candle* is one the wick took on its way through.
      if (l.died === c.timestamp) consumed += 1
    }
    if (families.size < MIN_FAMILIES) continue

    out.push({
      timestamp: c.timestamp,
      side: top ? 'top' : 'bottom',
      price,
      edge,
      families: [...families].sort(),
      excursionAtr: excursion / atr,
      consumed,
    })
  }
  return out
}
