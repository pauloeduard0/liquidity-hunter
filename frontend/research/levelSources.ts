/**
 * The same defence levels, tagged with where each one came from.
 *
 * `buildDefenceLevels` collapses four very different sources into one flat
 * list of `{low, high, family, born, died}`, which is the right shape for the
 * rule and the wrong one for P6: "is the map of the market asymmetric between
 * the top and the bottom?" cannot be asked of a level that no longer remembers
 * whether it was an equal-high or an equal-low.
 *
 * So this walks the same sources in the same order and keeps the provenance.
 * It is a second copy of a traversal, which is exactly the kind of duplication
 * that drifts — so `research/levelSources.test.ts` requires the geometry it
 * produces to be identical, level for level and in order, to what
 * `buildDefenceLevels` returns on every fixture. If the production builder
 * changes and this does not, the test fails before any number is reported.
 */
import type { DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import {
  buildDefenceLevels,
  meanTrueRangePct,
  meanTrueRangePctSeries,
  type DefenceLevel,
  type LevelFamily,
} from '../src/utils/defendedLevels.ts'

/** Mirrors the `LEVEL_TOLERANCE_ATR` constant the rule applies. Not a knob. */
const LEVEL_TOLERANCE_ATR = 0.5

/** Where a level came from, finer than its family. */
export type LevelSource =
  | 'bos'
  | 'choch'
  | 'poi'
  | 'equal_highs'
  | 'equal_lows'
  | 'liquidation'
  | 'poc'
  | 'value_area'

/** Which side of the market the source speaks for, where it says so. A level
 *  is `above` when it maps supply/resistance and `below` when it maps
 *  demand/support; `neutral` when the source carries no side. */
export type LevelSide = 'above' | 'below' | 'neutral'

/**
 * What actually retires a level, which is **not** the same question across
 * sources and is the whole subject of P7:
 *
 *   - `wick`      the first candle whose wick reached through it;
 *   - `close`     the first candle whose close landed beyond it;
 *   - `structure` a later structure event superseded its reference line;
 *   - `never`     it has no death at all.
 */
export type DeathRule = 'wick' | 'close' | 'structure' | 'never'

export interface SourcedLevel extends DefenceLevel {
  source: LevelSource
  side: LevelSide
  deathRule: DeathRule
  /**
   * The *other* death this source records, where it records two. Only the
   * equal-level pools do: `died` is `invalidated_at` (the wick that grabbed
   * the resting orders) and this is `breached_at` (the close that spent the
   * level). `LiquidityZone`'s own docstring says the first leaves the level
   * "surviving as memory" and the second is when it "stopped being a pool" —
   * the rule retires it on the first. Recorded, not acted on.
   */
  altDied: string | null
}

function causalLookup(times: string[], series: number[]): (at: string) => number {
  return (at: string) => {
    if (times.length === 0) return 0
    if (at < times[0]) return series[0]
    let lo = 0
    let hi = times.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (times[mid] <= at) lo = mid
      else hi = mid - 1
    }
    return series[lo]
  }
}

/** `buildDefenceLevels`, with the source kept. Same order, same geometry. */
export function sourcedDefenceLevels(
  data: DashboardData,
  standingUntil: (event: MarketStructure) => string | null,
  options: { causalAtr?: boolean } = {},
): SourcedLevel[] {
  const causal = options.causalAtr
    ? causalLookup(
        data.candles.map((c) => c.timestamp),
        meanTrueRangePctSeries(data.candles),
      )
    : null
  const windowFrac = LEVEL_TOLERANCE_ATR * meanTrueRangePct(data.candles)
  const tolFracAt = (born: string) =>
    causal ? LEVEL_TOLERANCE_ATR * causal(born) : windowFrac

  const out: SourcedLevel[] = []
  const level = (
    price: number,
    family: LevelFamily,
    born: string,
    died: string | null,
    source: LevelSource,
    side: LevelSide,
    deathRule: DeathRule,
    altDied: string | null,
  ) => {
    const tol = price * tolFracAt(born)
    out.push({
      low: price - tol,
      high: price + tol,
      family,
      born,
      died,
      source,
      side,
      deathRule,
      altDied,
    })
  }

  for (const e of data.internal_structure_events) {
    if (e.provisional) continue
    if (e.event !== 'break_of_structure' && e.event !== 'change_of_character') continue
    if (e.reference_price_level == null) continue
    // A bullish break takes out a high, so its reference maps the level above;
    // a bearish one takes out a low.
    level(
      e.reference_price_level,
      'structural',
      e.timestamp,
      standingUntil(e),
      e.event === 'break_of_structure' ? 'bos' : 'choch',
      e.direction === 'bullish' ? 'above' : e.direction === 'bearish' ? 'below' : 'neutral',
      'structure',
      null,
    )
  }

  for (const z of data.poi_zones ?? []) {
    out.push({
      low: z.price_low,
      high: z.price_high,
      family: 'order',
      born: z.created_at,
      died: z.invalidated_at,
      source: 'poi',
      // A bullish order block is demand, sitting below price to be defended.
      side: z.direction === 'bullish' ? 'below' : z.direction === 'bearish' ? 'above' : 'neutral',
      // `POIZone`: "Price trading back inside the zone does not retire it" —
      // only a close beyond the far boundary does.
      deathRule: 'close',
      altDied: null,
    })
  }

  for (const z of data.liquidity_zones) {
    if (z.zone_type !== 'equal_highs' && z.zone_type !== 'equal_lows') continue
    const tol = z.price_low * tolFracAt(z.formed_at)
    out.push({
      low: z.price_low - tol,
      high: z.price_high + tol,
      family: 'resting',
      born: z.formed_at,
      died: z.invalidated_at,
      source: z.zone_type,
      side: z.zone_type === 'equal_highs' ? 'above' : 'below',
      deathRule: 'wick',
      altDied: z.breached_at ?? null,
    })
  }
  for (const b of data.liquidation_map?.bands ?? []) {
    out.push({
      low: b.price_low,
      high: b.price_high,
      family: 'resting',
      born: b.start_time,
      died: b.end_time,
      source: 'liquidation',
      // Sell-side liquidations are longs stopped out below price.
      side: b.side === 'sell_side' ? 'below' : b.side === 'buy_side' ? 'above' : 'neutral',
      // `_liquidation_hit_time`: the low/high piercing the level consumes the
      // pool. A touch is the whole event for a liquidation, so this one is not
      // an early death — it is the death.
      deathRule: 'wick',
      altDied: null,
    })
  }

  const vp = data.volume_profile
  if (vp) {
    for (const [price, source] of [
      [vp.poc_price, 'poc'],
      [vp.value_area_low, 'value_area'],
      [vp.value_area_high, 'value_area'],
    ] as [number | null, LevelSource][]) {
      // The profile is one snapshot over the whole window and never dies, so
      // it is both immortal and contaminated by hindsight. P7.11 keeps it out
      // of every primary conclusion.
      if (price) level(price, 'fair', vp.start_timestamp, null, source, 'neutral', 'never', null)
    }
  }
  return out
}

/** True when the sourced list is geometrically the production list. */
export function matchesProduction(
  data: DashboardData,
  standingUntil: (event: MarketStructure) => string | null,
  options: { causalAtr?: boolean } = {},
): { ok: boolean; detail: string } {
  const a = buildDefenceLevels(data, standingUntil, options)
  const b = sourcedDefenceLevels(data, standingUntil, options)
  if (a.length !== b.length) return { ok: false, detail: `${a.length} vs ${b.length} niveis` }
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i]
    const y = b[i]
    if (
      x.low !== y.low ||
      x.high !== y.high ||
      x.family !== y.family ||
      x.born !== y.born ||
      x.died !== y.died
    ) {
      return { ok: false, detail: `nivel ${i}: ${JSON.stringify(x)} vs ${JSON.stringify(y)}` }
    }
  }
  return { ok: true, detail: `${a.length} niveis` }
}
