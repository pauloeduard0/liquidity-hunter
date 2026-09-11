/**
 * Liquidity references: the nearest resting level of each family, named.
 *
 * This replaces the "Dominant Liquidity" reading, which ranked every level by
 * a composite score and showed the top one. Six rounds of measurement
 * (`research/DOMINANT_LIQUIDITY_D0..D6`) found no basis for that ranking: the
 * EQ and swing `strength` values are not comparable quantities, neither adds
 * information once distance is controlled, and no family holds better than a
 * price drawn at random at the same distance. Whatever the composite's winner
 * was, it was not "dominant".
 *
 * What survived is geometry: a level is near, or it is not. So each family
 * reports its own nearest level, with the type spelled out, the distance in
 * ATR, and which side of price it sits on. There is no winner between the two
 * families, because nothing measured supports electing one.
 *
 * Deliberately absent: the composite score, `strength`, `touch_score` and
 * `timeframe_score`. The scoring engine still exists and still ships them --
 * this card just stopped reading them.
 */

import type { Candle, LiquidityZone, LiquidityZoneType } from '../types/dashboard.ts'
import { meanTrueRangePct } from './defendedLevels.ts'

export type ReferenceFamily = 'eq' | 'swing'

export interface LiquidityReference {
  family: ReferenceFamily
  /** Short label for the UI: EQH, EQL, Swing H, Swing L. */
  label: string
  zoneType: LiquidityZoneType
  /** Midpoint of the zone's band (a swing pivot's band is a point). */
  price: number
  /** Distance from the current price, in ATRs of the loaded series. */
  distanceAtr: number
  /** Spatial position only -- above or below price, never a direction call. */
  above: boolean
}

const EQ_TYPES: LiquidityZoneType[] = ['equal_highs', 'equal_lows']
const SWING_TYPES: LiquidityZoneType[] = ['swing_high', 'swing_low']

const LABELS: Partial<Record<LiquidityZoneType, string>> = {
  equal_highs: 'EQH',
  equal_lows: 'EQL',
  swing_high: 'Swing H',
  swing_low: 'Swing L',
}

const midpoint = (zone: LiquidityZone) => (zone.price_high + zone.price_low) / 2

/**
 * One ATR in price units, from the loaded candles only.
 *
 * `meanTrueRangePct` is the shipped unit (`utils/defendedLevels`), reused rather
 * than reimplemented so the card states distance in the same ATR the rest of the
 * frontend does. It is causal -- it reads no candle the loaded series does not
 * already contain, and truncating the series to any cut reproduces the value at
 * that cut exactly. It is *window-dependent*: it averages whatever is loaded, so
 * a more volatile stretch changes the unit. That is acceptable here and nowhere
 * near a silent detail -- the card is a live reading at the last candle, so the
 * unit is "the current window's volatility", never a historical claim.
 */
export function atrOf(candles: Candle[], price: number): number {
  return meanTrueRangePct(candles) * price
}

/**
 * The nearest zone of `types`, or null when the family has no active level.
 *
 * Ordering, in order of precedence:
 *   1. smaller distance in ATR;
 *   2. on an *exact* distance tie only, higher `strength` -- which is the only
 *      place strength is consulted at all, and never across families (D1: the
 *      two strengths are not the same quantity);
 *   3. a stable fallback on (formation time, band), so the answer never depends
 *      on the order the detectors emitted the zones in.
 *
 * Adding a third family later (the volume profile's POC is the candidate D6
 * found to be genuinely near) is one more call to this function with its own
 * types and label -- no arbitration between families has to be invented.
 */
export function nearestReference(
  zones: LiquidityZone[],
  price: number,
  atr: number,
  types: LiquidityZoneType[],
  family: ReferenceFamily,
): LiquidityReference | null {
  if (!(atr > 0) || !(price > 0)) return null
  const candidates = zones.filter((z) => types.includes(z.zone_type) && !z.is_mitigated)
  let best: LiquidityZone | null = null
  let bestDistance = Infinity
  for (const zone of candidates) {
    const distance = Math.abs(midpoint(zone) - price) / atr
    if (best === null || distance < bestDistance) {
      best = zone
      bestDistance = distance
      continue
    }
    if (distance !== bestDistance) continue
    if (zone.strength !== best.strength) {
      if (zone.strength > best.strength) best = zone
      continue
    }
    const key = (z: LiquidityZone) => `${z.formed_at}|${z.price_low}|${z.price_high}`
    if (key(zone) < key(best)) best = zone
  }
  if (best === null) return null
  return {
    family,
    label: LABELS[best.zone_type] ?? best.zone_type,
    zoneType: best.zone_type,
    price: midpoint(best),
    distanceAtr: bestDistance,
    above: midpoint(best) > price,
  }
}

/** Both families' nearest references. Either may be null; neither outranks the other. */
export function liquidityReferences(
  zones: LiquidityZone[],
  candles: Candle[],
  price: number,
): { eq: LiquidityReference | null; swing: LiquidityReference | null } {
  const atr = atrOf(candles, price)
  return {
    eq: nearestReference(zones, price, atr, EQ_TYPES, 'eq'),
    swing: nearestReference(zones, price, atr, SWING_TYPES, 'swing'),
  }
}

/** "EQH 1.4 ATR ↑" -- type, distance, and which side of price it sits on. */
export function formatReference(reference: LiquidityReference): string {
  return `${reference.label} ${reference.distanceAtr.toFixed(1)} ATR ${reference.above ? '↑' : '↓'}`
}
