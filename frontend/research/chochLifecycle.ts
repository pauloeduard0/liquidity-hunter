/**
 * A price-derived death for a CHoCH reference level.
 *
 * P7 found the one lifecycle that does not measure like its control: after
 * `structureLineEndTime` retires a `change_of_character` reference, price
 * returns to that price and rejects 56.8% of the time against a matched 50.1%
 * (z 3.76, n=947), replicating across every timeframe, every temporal block and
 * 72% of symbols — while a BOS reference, same family and same death rule,
 * gives z=0.04.
 *
 * The suspicion is a category error rather than a bug: `structureLineEndTime`
 * answers "how long is this line drawn on the chart", and `defendedLevels` uses
 * it to answer "how long is this level still defended". Its own body says so —
 * it ends a CHoCH line at the first confirming same-direction BOS because "the
 * level stops governing anything and the staircase takes over", which is a
 * drawing decision about clutter.
 *
 * What invalidates a CHoCH reference, derived rather than guessed
 * --------------------------------------------------------------
 * `InternalStructureDetector` tracks the reference per side as
 * `validated_choch_high` — "the level a bullish CHoCH must break" — and
 * `validated_choch_low` for bearish, and the break requires a **close** beyond
 * it, not a wick ("a single candle that pokes through the reference and reverts
 * is a LIQUIDITY_SWEEP").
 *
 * So a **bullish** CHoCH fired because a close landed *above* a high; that high
 * is now support underneath price, and the flip is undone by the first close
 * back *below* it. A **bearish** CHoCH fired on a close *below* a low; that low
 * is now resistance, undone by the first close *above* it. Symmetric, and it is
 * the same shape `POIZone` already uses — the one source that measured exactly
 * at its control in P7.
 *
 * No grace period, no candle count, no new threshold: the comparison is against
 * `reference_price_level` itself, the quantity the detector broke.
 */
import type { Candle, MarketStructure } from '../src/types/dashboard.ts'

/** Which way a close has to go to undo the event that created the level. */
export function invalidatingSide(event: MarketStructure): 'below' | 'above' | null {
  if (event.direction === 'bullish') return 'below'
  if (event.direction === 'bearish') return 'above'
  return null
}

/**
 * First candle strictly after `event` whose **close** lands beyond the
 * reference on the invalidating side, or `null` if none does.
 *
 * Causal by construction: the scan only moves forward from the event and stops
 * at the first hit, so the answer for any prefix of `candles` is the same
 * answer or `null` — never a different timestamp. `research/chochLifecycle
 * .test.ts` asserts that on real fixtures rather than trusting the argument.
 */
export function chochDeathByClose(
  event: MarketStructure,
  candles: Candle[],
): string | null {
  const ref = event.reference_price_level
  const side = invalidatingSide(event)
  if (ref == null || side === null) return null
  for (const c of candles) {
    if (c.timestamp <= event.timestamp) continue
    if (side === 'below' ? c.close < ref : c.close > ref) return c.timestamp
  }
  return null
}
