/**
 * How long a structure event's reference line stays drawn.
 *
 * Extracted verbatim from `MainChart` so it can run outside the browser. It is
 * pure over `MarketStructure[]` and carries no React or charting state, but it
 * was only ever reachable through the component, which meant the one rule that
 * decides how long a BOS/CHoCH reference counts as *standing* could not be
 * measured. `defendedLevels` depends on exactly that rule for its structural
 * family, so the piso harness in `frontend/research/` needs it importable.
 *
 * Nothing here changed in the move. `MainChart` imports these back and remains
 * the only caller in the app.
 */
import type { UTCTimestamp } from 'lightweight-charts'

import type { MarketStructure } from '../types/dashboard.ts'
import { toChartTime } from './chartTime.ts'

// If a provisional CHoCH is later invalidated, returns the timestamp of the
// `choch_failed` event that paired with it (a same-direction failure firing
// before any other same-direction CHoCH intervenes); otherwise `null`. A failed
// CHoCH never actually reversed structure — the prior trend resumed — so it must
// stay transparent to *other* lines' termination (it doesn't cut them), while
// its *own* line stops at this failure point. A *fizzle marker* (a provisional
// `choch_failed`) is different: the state-machine trend never flipped back, so
// the CHoCH still genuinely reversed structure and must keep cutting other
// lines — only its own line stops at the reclaim. Callers pass
// `includeFizzle: false` when deciding transparency.
export function failedChochTime(
  choch: MarketStructure,
  allEvents: MarketStructure[],
  { includeFizzle = true }: { includeFizzle?: boolean } = {},
): UTCTimestamp | null {
  if (choch.event !== 'change_of_character') return null
  const chochTime = toChartTime(choch.timestamp)
  const failedTimes = allEvents
    .filter(
      (e) =>
        e.scope === choch.scope &&
        e.event === 'choch_failed' &&
        (includeFizzle || !e.provisional) &&
        e.direction === choch.direction &&
        toChartTime(e.timestamp) > chochTime,
    )
    .map((e) => toChartTime(e.timestamp))
  if (failedTimes.length === 0) return null
  const firstFailed = Math.min(...failedTimes) as UTCTimestamp
  // Pair the failure with its CHoCH: ignore it if a later same-direction CHoCH
  // sits between them (that one owns the failure instead).
  const interveningChoch = allEvents.some(
    (e) =>
      e.scope === choch.scope &&
      e.event === 'change_of_character' &&
      e.direction === choch.direction &&
      toChartTime(e.timestamp) > chochTime &&
      toChartTime(e.timestamp) < firstFailed,
  )
  return interveningChoch ? null : firstFailed
}

export function isFailedChoch(choch: MarketStructure, allEvents: MarketStructure[]): boolean {
  return failedChochTime(choch, allEvents, { includeFizzle: false }) !== null
}

/** The price a structure event's reference line is drawn at. */
export function structureLinePrice(event: MarketStructure): number {
  return (event.event === 'change_of_character' ||
    event.event === 'choch_failed' ||
    event.event === 'break_of_structure') &&
    event.reference_price_level != null
    ? event.reference_price_level
    : event.price_level
}

// The real `choch_failed` this CHoCH died on, or `null`.
export function pairedFailure(
  choch: MarketStructure,
  allEvents: MarketStructure[],
): MarketStructure | null {
  const failedAt = failedChochTime(choch, allEvents, { includeFizzle: false })
  if (failedAt === null) return null
  return (
    allEvents.find(
      (e) =>
        e.event === 'choch_failed' &&
        e.provisional !== true &&
        e.scope === choch.scope &&
        e.direction === choch.direction &&
        toChartTime(e.timestamp) === failedAt,
    ) ?? null
  )
}

// Whether the `CHoCH ✕` fully stands in for the CHoCH it killed: it does only
// when both draw on the *same* level. A level-armed failure reclaims the very
// price the CHoCH broke, so the two lines coincide and drawing both plots one
// CHoCH twice. An *origin*-armed failure does not: it sits at the low/high the
// CHoCH's leg launched from, a different price entirely (BTCUSDT D1 — the
// bullish CHoCH broke 94760.3 on 01-13, and its ✕ sits at the 89242.0 origin),
// and hiding the CHoCH there leaves an orphan ✕ with nothing to explain what
// died or where it came from.
export function failureReplacesChoch(
  choch: MarketStructure,
  allEvents: MarketStructure[],
): boolean {
  const failure = pairedFailure(choch, allEvents)
  if (failure === null) return false
  const chochPrice = structureLinePrice(choch)
  const failurePrice = structureLinePrice(failure)
  if (chochPrice <= 0) return failurePrice === chochPrice
  return Math.abs(failurePrice - chochPrice) / chochPrice < 0.001
}

export function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
  bosTrailEnd?: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toChartTime(event.timestamp)

  if (event.event === 'change_of_character') {
    // A CHoCH line runs until the next real CHoCH supersedes it — of *either*
    // direction. An opposite-direction CHoCH is a reversal that clears the
    // stale reference; a *same*-direction CHoCH is simply a newer reference for
    // that side, so the older one stops there rather than both running to the
    // edge (the case where the internal trend briefly flipped and back without
    // surfacing a drawn opposite CHoCH, emitting two same-direction CHoCHs).
    // Failed/provisional CHoCHs don't count — one that never took hold or is
    // still forming isn't the active reference.
    const candidates = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'change_of_character' &&
          !other.provisional &&
          !isFailedChoch(other, allEvents) &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    // If this CHoCH itself failed, its line stops at the failure point.
    const ownFailure = failedChochTime(event, allEvents)
    if (ownFailure !== null) candidates.push(ownFailure)
    // A later same-direction BOS whose reference sits on the *wrong side* of
    // this CHoCH's level (below it for a bullish CHoCH, above for bearish)
    // means the trend collapsed through the level and rebuilt from the other
    // side — an excursion whose opposite CHoCH failed, so it is transparent
    // above, yet the old reversal reference is plainly stale (ENA 4H 2026-06:
    // a bullish CHoCH at 0.086 ran to the edge across a dive to 0.070 because
    // both superseding bearish CHoCHs failed). A normal leg's staircase only
    // moves away from the CHoCH level, so this never fires mid-trend.
    if (event.reference_price_level != null) {
      const rebasedAt = allEvents
        .filter(
          (other) =>
            other.scope === event.scope &&
            other.event === 'break_of_structure' &&
            !other.provisional &&
            other.direction === event.direction &&
            other.reference_price_level != null &&
            (event.direction === 'bullish'
              ? other.reference_price_level < event.reference_price_level!
              : other.reference_price_level > event.reference_price_level!) &&
            toChartTime(other.timestamp) > eventTime,
        )
        .map((other) => toChartTime(other.timestamp))
      candidates.push(...rebasedAt)
    }
    // An opposite-direction BOS also ends the line. A BOS is only emitted in
    // the direction of the standing trend, so a bearish BOS is proof the trend
    // *is* bearish — the bullish reversal reference is spent, whether or not
    // the CHoCH that opened that excursion later failed. Without this a failed
    // opposite CHoCH (excluded above as "never took hold") lets the old line
    // run straight through the whole counter-move: BTCUSDT H1 2026-07, where
    // the bullish CHoCH of 07-20 and its BOS both ran to the 07-31 re-fire,
    // across a bearish leg that had already printed real BOS on 07-24/07-28.
    const reversedAt = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'break_of_structure' &&
          !other.provisional &&
          other.direction !== event.direction &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    candidates.push(...reversedAt)
    // The first *confirming* same-direction BOS also ends the line. Until one
    // prints, the CHoCH is still provisional in substance — the level it broke
    // is what a `choch_failed` / re-arm measures, and watching price retest it
    // is half the flip read, so the line has to survive that window. Once the
    // BOS confirms the reversal, the level stops governing anything and the
    // staircase takes over; keeping the line to the *next CHoCH* (the old rule)
    // is what let it run across dozens of candles it no longer describes. This
    // subsumes the wrong-side rebase clause above, which stays as documentation
    // of the case that motivated it.
    const confirmedAt = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'break_of_structure' &&
          !other.provisional &&
          other.direction === event.direction &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    candidates.push(...confirmedAt)
    return candidates.length > 0 ? (Math.min(...candidates) as UTCTimestamp) : lastCandleTime
  }

  const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        !other.provisional &&
        toChartTime(other.timestamp) > eventTime &&
        ((other.direction === event.direction &&
          (other.event === 'break_of_structure' ||
            (other.event === 'change_of_character' && !isFailedChoch(other, allEvents)) ||
            // A real same-direction CHOCH_FAILED invalidates the leg this BOS
            // extended and reverts the trend, so the BOS reference is no longer
            // standing — the line ends at the ✕ instead of running to the edge
            // (a leg that ends via failure has no opposite CHoCH to end it).
            (event.event === 'break_of_structure' && other.event === 'choch_failed'))) ||
          (other.direction === oppositeDirection &&
            other.event === 'change_of_character' &&
            !isFailedChoch(other, allEvents))),
    )
    .map((other) => toChartTime(other.timestamp))

  // A confirmed BOS stops shortly after its own break candle (see
  // BOS_LINE_TRAIL_CANDLES); a superseding event can still cut it shorter.
  if (bosTrailEnd !== undefined) supersededAt.push(bosTrailEnd)

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}
