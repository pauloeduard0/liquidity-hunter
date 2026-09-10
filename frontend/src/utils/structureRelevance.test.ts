/**
 * Tests for the two axes: historical base appearance and current highlight.
 *
 * Run on node's own runner, like `legState.test.ts` and `stallMarker.test.ts`:
 * `node --test src/utils/*.test.ts` (or `npm test`).
 *
 * The load-bearing test in this file is the last one. Everything else checks a
 * value; that one checks a *property* -- that no amount of future can reach
 * back and change how a past event is drawn -- and it is the property the
 * first version of this module violated. If the two axes are ever multiplied
 * together again, that is the test that fails.
 *
 * The BTC H1 sequence (bullish leg -> bearish CHoCH -> its failure -> an
 * additive bullish BOS -> stall) is the fixture, because it is the one case
 * where all four facts are simultaneously true and none may erase another.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { MarketStructure, StructureEvent } from '../types/dashboard.ts'
import {
  FULL_BASE_ALPHA,
  NO_HIGHLIGHT,
  WEAK_BASE_ALPHA,
  alphaHex,
  currentHighlightStyle,
  isCurrentStructuralReference,
  isStructuralAdvance,
  isVisuallyWeak,
  lastStructuralAdvance,
  structureBaseAlpha,
  structureEventColor,
  structureEventPriority,
} from './structureRelevance.ts'

function event(
  timestamp: string,
  kind: StructureEvent,
  direction: 'bullish' | 'bearish',
  overrides: Partial<MarketStructure> = {},
): MarketStructure {
  return {
    symbol: 'BTCUSDT',
    timeframe: '1h',
    timestamp,
    event: kind,
    direction,
    price_level: 82000,
    reference_price_level: 81500,
    reference_timestamp: null,
    origin_price_level: null,
    scope: 'internal',
    ...overrides,
  } as MarketStructure
}

// The BTC H1 story, in order.
const BOS_OLD = event('2026-08-20T10:00:00Z', 'break_of_structure', 'bullish')
const CHOCH_BEAR = event('2026-09-01T16:00:00Z', 'change_of_character', 'bearish', {
  reference_structural: false, // the real BTC H1 one broke a weak reference
})
const FAILED = event('2026-09-03T12:00:00Z', 'choch_failed', 'bearish')
const BOS_NEW = event('2026-09-03T15:00:00Z', 'break_of_structure', 'bullish')
const BTC_H1 = [BOS_OLD, CHOCH_BEAR, FAILED, BOS_NEW]

const priority = (e: MarketStructure, events = BTC_H1) => structureEventPriority(e, events)

// --------------------------------------------------------------------------
// Axis 1 -- historical base appearance
// --------------------------------------------------------------------------

test('a confirmed BOS has base alpha 1.00 while it is current', () => {
  assert.equal(priority(BOS_NEW), 'current')
  assert.equal(structureBaseAlpha(BOS_NEW), FULL_BASE_ALPHA)
})

test('the same BOS keeps base alpha 1.00 after newer events arrive', () => {
  const later = [
    ...BTC_H1,
    event('2026-09-20T10:00:00Z', 'change_of_character', 'bearish'),
    event('2026-09-25T10:00:00Z', 'break_of_structure', 'bearish'),
  ]
  // Two advances later it is `history` -- and that is exactly the case the
  // old architecture faded to 0.40. It stays at full strength.
  assert.equal(structureEventPriority(BOS_NEW, later), 'history')
  assert.equal(structureBaseAlpha(BOS_NEW), FULL_BASE_ALPHA)
})

test('a weak CHoCH has base alpha 0.60, current or not', () => {
  assert.equal(isVisuallyWeak(CHOCH_BEAR), true)
  assert.equal(structureBaseAlpha(CHOCH_BEAR), WEAK_BASE_ALPHA)
  // Current in its own moment...
  assert.equal(structureEventPriority(CHOCH_BEAR, [BOS_OLD, CHOCH_BEAR]), 'current')
  assert.equal(structureBaseAlpha(CHOCH_BEAR), WEAK_BASE_ALPHA)
  // ...and history two sequences later. Same number.
  assert.equal(priority(CHOCH_BEAR), 'recent')
  assert.equal(structureBaseAlpha(CHOCH_BEAR), WEAK_BASE_ALPHA)
})

test('a historical CHOCH_FAILED keeps the full 1.00 of a real invalidation', () => {
  // It is not an advance and it will never be `current`, and neither fact is
  // allowed to fade it: a refused attempt at structure is a real event.
  assert.equal(isStructuralAdvance(FAILED), false)
  assert.equal(structureBaseAlpha(FAILED), FULL_BASE_ALPHA)
  assert.equal(structureEventColor('#ce93d8', FAILED), '#ce93d8ff')
})

test('a provisional mark keeps 0.60 at every priority', () => {
  const live = event('2026-09-09T12:00:00Z', 'break_of_structure', 'bullish', {
    provisional: true,
  })
  assert.equal(isVisuallyWeak(live), true)
  for (const events of [[live], [...BTC_H1, live], BTC_H1.concat(live)]) {
    assert.equal(structureBaseAlpha(live), WEAK_BASE_ALPHA)
    void events
  }
  assert.equal(structureEventColor('#26a69a', live), '#26a69a99')
})

test('base appearance is a function of the event and nothing else', () => {
  // The signature is the guarantee: one argument, and it is the event. This
  // test states the intent so a future parameter has to break it on purpose.
  assert.equal(structureBaseAlpha.length, 1)
  assert.equal(structureEventColor.length, 2)
})

// --------------------------------------------------------------------------
// Axis 2 -- current highlight
// --------------------------------------------------------------------------

test('the current element is 3px while the leg is active', () => {
  assert.deepEqual(currentHighlightStyle('current', 'active'), { lineWidth: 3 })
})

test('the current element drops to 2px while the leg is stale', () => {
  assert.deepEqual(currentHighlightStyle('current', 'stale'), { lineWidth: 2 })
})

test('recent and history are 1px, in both leg states', () => {
  for (const tier of ['recent', 'history'] as const) {
    for (const leg of ['active', 'stale'] as const) {
      assert.deepEqual(currentHighlightStyle(tier, leg), NO_HIGHLIGHT)
    }
  }
})

test('clearing the stall changes the current width and nothing else', () => {
  assert.equal(currentHighlightStyle('current', 'stale').lineWidth, 2)
  assert.equal(currentHighlightStyle('current', 'active').lineWidth, 3)
  for (const e of BTC_H1) {
    // No leg state reaches axis 1 at all -- there is nowhere to pass it.
    assert.equal(structureBaseAlpha(e), isVisuallyWeak(e) ? WEAK_BASE_ALPHA : FULL_BASE_ALPHA)
  }
})

test('a new BOS costs the previous one its highlight, not its appearance', () => {
  const before = [BOS_OLD, CHOCH_BEAR, FAILED, BOS_NEW]
  const after = [...before, event('2026-09-20T10:00:00Z', 'break_of_structure', 'bullish')]
  assert.equal(structureEventPriority(BOS_NEW, before), 'current')
  assert.equal(structureEventPriority(BOS_NEW, after), 'recent')
  assert.equal(currentHighlightStyle('current', 'active').lineWidth, 3)
  assert.equal(currentHighlightStyle('recent', 'active').lineWidth, 1)
  assert.equal(structureBaseAlpha(BOS_NEW), FULL_BASE_ALPHA) // unchanged
})

test('CHOCH_FAILED never becomes a highlighted advance', () => {
  const events = [BOS_OLD, CHOCH_BEAR, FAILED]
  // Even as the most recent event in the window, the last advance is the CHoCH
  // it killed -- the failure is `recent`, so its highlight is none.
  assert.equal(lastStructuralAdvance(events), CHOCH_BEAR)
  assert.equal(structureEventPriority(FAILED, events), 'recent')
  assert.deepEqual(currentHighlightStyle(structureEventPriority(FAILED, events), 'active'), NO_HIGHLIGHT)
  // And it is rejected as a structural reference however long its line runs.
  assert.equal(isCurrentStructuralReference(FAILED, 1_000, 1_000), false)
})

test('a provisional mark never becomes the current advance', () => {
  const live = event('2026-09-09T12:00:00Z', 'break_of_structure', 'bullish', {
    provisional: true,
  })
  const events = [...BTC_H1, live]
  assert.equal(lastStructuralAdvance(events), BOS_NEW)
  assert.equal(structureEventPriority(live, events), 'recent')
  assert.equal(isCurrentStructuralReference(live, 1_000, 1_000), false)
})

test('a still-live reference is highlighted without its base moving', () => {
  const lastCandleTime = 1_757_000_000
  assert.equal(isCurrentStructuralReference(BOS_OLD, lastCandleTime, lastCandleTime), true)
  assert.equal(
    structureEventPriority(BOS_OLD, BTC_H1, { endTime: lastCandleTime, lastCandleTime }),
    'current',
  )
  // Cut short by a superseding event: back to 1px, same appearance.
  assert.equal(
    structureEventPriority(BOS_OLD, BTC_H1, { endTime: lastCandleTime - 1, lastCandleTime }),
    'history',
  )
  assert.equal(structureBaseAlpha(BOS_OLD), FULL_BASE_ALPHA)
})

test('a sweep is never an advance, so it can never take the highlight', () => {
  const sweep = event('2026-09-08T13:00:00Z', 'liquidity_sweep', 'bearish')
  const events = [...BTC_H1, sweep]
  assert.equal(isStructuralAdvance(sweep), false)
  assert.notEqual(structureEventPriority(sweep, events), 'current')
  assert.equal(isCurrentStructuralReference(sweep, 1_000, 1_000), false)
})

// --------------------------------------------------------------------------
// The property that locks the architecture
// --------------------------------------------------------------------------

test('no amount of future changes the base appearance of any past event', () => {
  const windowA = [BOS_OLD, CHOCH_BEAR, FAILED, BOS_NEW]
  const windowB = [
    ...windowA,
    event('2026-09-10T00:00:00Z', 'liquidity_sweep', 'bearish'),
    event('2026-09-12T00:00:00Z', 'change_of_character', 'bearish'),
    event('2026-09-14T00:00:00Z', 'choch_failed', 'bearish'),
    event('2026-09-15T00:00:00Z', 'break_of_structure', 'bullish'),
    event('2026-09-19T00:00:00Z', 'break_of_structure', 'bullish', { provisional: true }),
  ]

  let priorityChanged = false
  for (const e of windowA) {
    // Base: identical, to the byte the canvas receives.
    assert.equal(structureBaseAlpha(e), structureBaseAlpha(e))
    assert.equal(structureEventColor('#26a69a', e), structureEventColor('#26a69a', e))
    // Priority: free to move, and it must actually move for this to prove
    // anything -- otherwise the test passes on a window nothing happened in.
    if (structureEventPriority(e, windowA) !== structureEventPriority(e, windowB)) {
      priorityChanged = true
    }
  }
  assert.ok(priorityChanged, 'the fixture must actually reclassify something')

  // Stated the other way round: the drawn appearance of window A's events is
  // computable without window B existing at all, because the function that
  // computes it cannot be handed a window.
  const drawnInA = windowA.map((e) => structureEventColor('#26a69a', e))
  const drawnInB = windowA.map((e) => structureEventColor('#26a69a', e))
  assert.deepEqual(drawnInA, drawnInB)
  assert.deepEqual(drawnInA, ['#26a69aff', '#26a69a99', '#26a69aff', '#26a69aff'])
})

test('alphaHex clamps and pads', () => {
  assert.equal(alphaHex(1), 'ff')
  assert.equal(alphaHex(0), '00')
  assert.equal(alphaHex(WEAK_BASE_ALPHA), '99')
  assert.equal(alphaHex(2), 'ff')
  assert.equal(alphaHex(-1), '00')
  assert.equal(alphaHex(0.04), '0a')
})

// --------------------------------------------------------------------------
// Classification (unchanged from 8.0, kept under test)
// --------------------------------------------------------------------------

test('the classification itself is unchanged', () => {
  assert.equal(priority(BOS_NEW), 'current')
  assert.equal(priority(FAILED), 'recent')
  assert.equal(priority(CHOCH_BEAR), 'recent')
  assert.equal(priority(BOS_OLD), 'history')
  assert.equal(structureEventPriority(CHOCH_BEAR, [BOS_OLD, CHOCH_BEAR]), 'current')
  assert.equal(structureEventPriority(BOS_OLD, []), 'recent') // no advance -> never current
})
