/**
 * Tests for the structural-stall mark.
 *
 * No test runner is installed in `frontend/`, and this stage is not the place
 * to add one, so these run on node's own: `node --test src/utils/*.test.ts`
 * (node strips the types; the project already sets `allowImportingTsExtensions`
 * and `erasableSyntaxOnly`, so nothing had to change to make that work).
 *
 * The mark is a pure function precisely so it can be tested this way -- what is
 * left in `MainChart` is one `setMarks` call.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Candle, DashboardData, StructuralStall } from '../types/dashboard.ts'
import { buildStallMark, buildStallMarks, stallMarkSide } from './stallMarker.ts'
import { structureTrendByCandle } from './tideRibbon.ts'

const COLORS = { bullish: '#2EE6B8', bearish: '#ce93d8' }

function candle(timestamp: string, high: number, low: number): Candle {
  return {
    symbol: 'BTCUSDT',
    timeframe: '1h',
    timestamp,
    open: low,
    high,
    low,
    close: high,
    volume: 1,
    taker_buy_volume: 0.5,
  } as Candle
}

const CANDLES = [
  candle('2026-08-25T02:00:00Z', 81300, 81000),
  candle('2026-08-28T15:00:00Z', 80900, 80500),
  candle('2026-09-01T15:00:00Z', 80100, 79800),
]

function stall(overrides: Partial<StructuralStall> = {}): StructuralStall {
  return {
    stale_since: '2026-08-28T15:00:00Z',
    direction: 'bullish',
    last_advance_timestamp: '2026-08-25T02:00:00Z',
    last_advance_price: 81270.5,
    bars_since_advance: 85,
    retracement_atr: 6.3,
    frozen_atr_pct: 0.0046,
    leg_extreme_price: 80709.7,
    leg_extreme_timestamp: '2026-09-01T15:00:00Z',
    ...overrides,
  }
}

test('no stall in the payload draws nothing', () => {
  assert.equal(buildStallMark(null, CANDLES, COLORS), null)
  assert.equal(buildStallMark(undefined, CANDLES, COLORS), null)
  assert.deepEqual(buildStallMarks(null, CANDLES, COLORS), [])
})

test('a bullish stall marks the stale_since candle, below it', () => {
  const mark = buildStallMark(stall(), CANDLES, COLORS)
  assert.ok(mark)
  assert.equal(mark.side, 'below')
  assert.equal(mark.price, 80500) // the candle's low
  assert.equal(mark.color, COLORS.bullish)
  assert.equal(mark.glyph, 'circle')
})

test('a bearish stall marks the same candle, above it', () => {
  const mark = buildStallMark(stall({ direction: 'bearish' }), CANDLES, COLORS)
  assert.ok(mark)
  assert.equal(mark.side, 'above')
  assert.equal(mark.price, 80900) // the candle's high
  assert.equal(mark.color, COLORS.bearish)
})

test('the anchor is stale_since, never the advance or the leg extreme', () => {
  const bullish = buildStallMark(stall(), CANDLES, COLORS)!
  const advance = buildStallMark(
    stall({ stale_since: '2026-08-25T02:00:00Z' }),
    CANDLES,
    COLORS,
  )!
  const extreme = buildStallMark(
    stall({ stale_since: '2026-09-01T15:00:00Z' }),
    CANDLES,
    COLORS,
  )!
  // Each answer follows `stale_since` alone: three different stalls, three
  // different candles, while `last_advance_timestamp` never moved.
  assert.notEqual(bullish.time, advance.time)
  assert.notEqual(bullish.time, extreme.time)
  assert.equal(bullish.price, 80500)
  assert.equal(advance.price, 81000)
})

test('direction changes only placement and color, never the symbol', () => {
  const up = buildStallMark(stall(), CANDLES, COLORS)!
  const down = buildStallMark(stall({ direction: 'bearish' }), CANDLES, COLORS)!
  assert.equal(up.glyph, down.glyph)
  assert.equal(up.time, down.time)
  assert.notEqual(up.side, down.side)
  assert.notEqual(up.color, down.color)
})

test('the mark is quieter than the structure lines it sits under', () => {
  const mark = buildStallMark(stall(), CANDLES, COLORS)!
  // Structure lines dim to `99`; the stall sits below that and never carries
  // the `strong` (filled + badge) treatment, which belongs to divergences.
  assert.ok(Number.parseInt(mark.alpha!, 16) < 0x99)
  assert.equal(mark.strong, undefined)
})

test('an incomplete or foreign payload is dropped, not drawn', () => {
  // A `stale_since` outside the window (a shorter chart, or another series).
  assert.equal(
    buildStallMark(stall({ stale_since: '2020-01-01T00:00:00Z' }), CANDLES, COLORS),
    null,
  )
  // Missing fields, in the shape a hand-edited or older payload could arrive.
  assert.equal(buildStallMark({} as StructuralStall, CANDLES, COLORS), null)
  assert.equal(
    buildStallMark(stall({ stale_since: '' }), CANDLES, COLORS),
    null,
  )
  // A direction with no color configured.
  assert.equal(buildStallMark(stall(), CANDLES, {}), null)
  // No candles at all.
  assert.equal(buildStallMark(stall(), [], COLORS), null)
})

test('side is a pure function of direction', () => {
  assert.equal(stallMarkSide('bullish'), 'below')
  assert.equal(stallMarkSide('bearish'), 'above')
})

test('building the mark does not touch the candles it reads', () => {
  const snapshot = JSON.stringify(CANDLES)
  buildStallMarks(stall(), CANDLES, COLORS)
  assert.equal(JSON.stringify(CANDLES), snapshot)
})

// --- the invariant: the mark adds a glyph and changes nothing else ---------

test('the stall does not change the structure trend the ribbon draws', () => {
  const events = [
    {
      symbol: 'BTCUSDT',
      timeframe: '1h',
      timestamp: '2026-08-25T02:00:00Z',
      event: 'break_of_structure',
      direction: 'bullish',
      price_level: 81270.5,
      scope: 'internal',
      provisional: false,
    },
  ]
  const base = { candles: CANDLES, internal_structure_events: events }
  const withoutStall = structureTrendByCandle({
    ...base,
    structural_stall: null,
  } as unknown as DashboardData)
  const withStall = structureTrendByCandle({
    ...base,
    structural_stall: stall(),
  } as unknown as DashboardData)
  assert.deepEqual([...withStall], [...withoutStall])
  // ...and the leg is still bullish on the very candle the stall marks.
  assert.equal(withStall.get('2026-08-28T15:00:00Z'), 'bullish')
})
