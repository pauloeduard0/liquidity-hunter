/**
 * Tests for the derived leg state (`active` / `stale`) and its one visual
 * effect, opacity.
 *
 * Run on node's own runner, like `stallMarker.test.ts`:
 * `node --test src/utils/*.test.ts` (or `npm test`).
 *
 * The point of most of these is what must NOT change. The leg state is a
 * projection of `structural_stall`, so the structural readings around it --
 * the trend replay, the `CHOCH_FAILED` handling, the `○` mark, the payload
 * itself -- have to come out bit for bit identical whether a stall is present
 * or not. Anything else would mean the new axis leaked into the old one.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Candle, DashboardData, StructuralStall } from '../types/dashboard.ts'
import {
  STALE_ALPHA_SCALE,
  deriveLegState,
  legStateAlpha,
  legStateAt,
} from './legState.ts'
import { buildStallMarks } from './stallMarker.ts'
import { buildRibbon, structureTrendByCandle } from './tideRibbon.ts'

const COLORS = { bullish: '#2EE6B8', bearish: '#ce93d8' }

function candle(timestamp: string, price: number): Candle {
  return {
    symbol: 'BTCUSDT',
    timeframe: '1h',
    timestamp,
    open: price,
    high: price + 50,
    low: price - 50,
    close: price,
    volume: 10,
    taker_buy_volume: 6,
  } as Candle
}

const CANDLES = [
  candle('2026-09-03T15:00:00Z', 82282),
  candle('2026-09-05T09:00:00Z', 80500),
  candle('2026-09-07T09:00:00Z', 79300),
  candle('2026-09-09T12:00:00Z', 78900),
]

function stall(overrides: Partial<StructuralStall> = {}): StructuralStall {
  return {
    stale_since: '2026-09-07T09:00:00Z',
    direction: 'bullish',
    last_advance_timestamp: '2026-09-03T15:00:00Z',
    last_advance_price: 82282.8,
    bars_since_advance: 90,
    retracement_atr: 6.01,
    frozen_atr_pct: 0.0049,
    leg_extreme_price: 81722.9,
    leg_extreme_timestamp: '2026-09-03T19:00:00Z',
    ...overrides,
  }
}

/** The BTCUSDT H1 shape the audit ran on: a bullish BOS, a bearish CHoCH that
 *  failed, and the leg going quiet afterwards. */
function data(overrides: Partial<DashboardData> = {}): DashboardData {
  return {
    candles: CANDLES,
    timeframe: '1h',
    internal_structure_events: [
      {
        symbol: 'BTCUSDT',
        timeframe: '1h',
        timestamp: '2026-09-03T12:00:00Z',
        event: 'choch_failed',
        direction: 'bearish',
        price_level: 82282.8,
        provisional: false,
      },
      {
        symbol: 'BTCUSDT',
        timeframe: '1h',
        timestamp: '2026-09-03T15:00:00Z',
        event: 'break_of_structure',
        direction: 'bullish',
        price_level: 82282.8,
        provisional: false,
      },
    ],
    structural_stall: null,
    ...overrides,
  } as unknown as DashboardData
}

// --- the helper itself -------------------------------------------------------

test('no stall in the payload is an active leg', () => {
  assert.equal(deriveLegState(null), 'active')
  assert.equal(deriveLegState(undefined), 'active')
})

test('a stall in the payload is a stale leg', () => {
  assert.equal(deriveLegState(stall()), 'stale')
})

test('the state is active before stale_since and stale from it on', () => {
  const s = stall()
  assert.equal(legStateAt(s, '2026-09-03T15:00:00Z'), 'active')
  assert.equal(legStateAt(s, '2026-09-05T09:00:00Z'), 'active')
  // The stall's own candle already counts: it is where the leg went quiet.
  assert.equal(legStateAt(s, '2026-09-07T09:00:00Z'), 'stale')
  assert.equal(legStateAt(s, '2026-09-09T12:00:00Z'), 'stale')
})

test('without a stall every candle is active', () => {
  for (const c of CANDLES) {
    assert.equal(legStateAt(null, c.timestamp), 'active')
    assert.equal(legStateAt(undefined, c.timestamp), 'active')
  }
})

test('a bearish leg stalls exactly like a bullish one', () => {
  const s = stall({ direction: 'bearish' })
  assert.equal(deriveLegState(s), 'stale')
  assert.equal(legStateAt(s, '2026-09-05T09:00:00Z'), 'active')
  assert.equal(legStateAt(s, '2026-09-09T12:00:00Z'), 'stale')
})

// --- the visual channel ------------------------------------------------------

test('opacity is untouched while active and scaled once while stale', () => {
  assert.equal(legStateAlpha(0.18, 'active'), 0.18)
  assert.equal(legStateAlpha(0.75, 'active'), 0.75)
  assert.equal(legStateAlpha(0.18, 'stale'), 0.18 * STALE_ALPHA_SCALE)
  assert.equal(legStateAlpha(0.5, 'stale'), 0.5 * STALE_ALPHA_SCALE)
})

test('a stale segment is dimmer but still visible', () => {
  // The scale is a reduction, not an erasure: the state is information.
  assert.ok(STALE_ALPHA_SCALE > 0 && STALE_ALPHA_SCALE < 1)
  assert.ok(legStateAlpha(0.06, 'stale') > 0)
})

// --- what must not change ----------------------------------------------------

test('structureTrendByCandle is identical with and without a stall', () => {
  const without = structureTrendByCandle(data())
  const withStall = structureTrendByCandle(data({ structural_stall: stall() }))
  assert.deepEqual([...withStall.entries()], [...without.entries()])
})

test('a stale bullish leg still reads bullish, never neutral or bearish', () => {
  const trends = structureTrendByCandle(data({ structural_stall: stall() }))
  assert.equal(trends.get('2026-09-07T09:00:00Z'), 'bullish')
  assert.equal(trends.get('2026-09-09T12:00:00Z'), 'bullish')
})

test('a stale bearish leg still reads bearish', () => {
  const bearish = data({
    structural_stall: stall({ direction: 'bearish' }),
    internal_structure_events: [
      {
        symbol: 'BTCUSDT',
        timeframe: '1h',
        timestamp: '2026-09-03T15:00:00Z',
        event: 'break_of_structure',
        direction: 'bearish',
        price_level: 82282.8,
        provisional: false,
      },
    ],
  } as unknown as Partial<DashboardData>)
  const trends = structureTrendByCandle(bearish)
  assert.equal(trends.get('2026-09-09T12:00:00Z'), 'bearish')
})

test('the failed CHoCH keeps its own effect on the trend when a stall exists', () => {
  // The `✕` reverts the failed bearish CHoCH: structure resumes bullish. That
  // is the event stream's business and the stall does not touch it.
  const trends = structureTrendByCandle(data({ structural_stall: stall() }))
  assert.equal(trends.get('2026-09-03T15:00:00Z'), 'bullish')
})

test('the ribbon input is identical with and without a stall', () => {
  // `buildRibbon` never reads `structural_stall`: the leg state is applied at
  // the drawing step, on top of an unchanged hue/conviction reading.
  const without = buildRibbon(data())
  const withStall = buildRibbon(data({ structural_stall: stall() }))
  assert.deepEqual(withStall, without)
})

test('the ○ mark is unchanged and still anchored at stale_since', () => {
  const marks = buildStallMarks(stall(), CANDLES, COLORS)
  assert.equal(marks.length, 1)
  assert.equal(marks[0].glyph, 'circle')
  assert.equal(marks[0].side, 'below')
  assert.equal(marks[0].color, COLORS.bullish)
})

test('deriving the leg state mutates neither the payload nor the events', () => {
  const payload = data({ structural_stall: stall() })
  const snapshot = JSON.stringify(payload)
  deriveLegState(payload.structural_stall)
  for (const c of payload.candles) legStateAt(payload.structural_stall, c.timestamp)
  structureTrendByCandle(payload)
  buildRibbon(payload)
  assert.equal(JSON.stringify(payload), snapshot)
})

test('a new advance clears the stall and the leg is active again', () => {
  // The payload is the whole state: when the backend stops sending a stall the
  // reading goes back to active, with no memory of the old one.
  const resumed = data({ structural_stall: null })
  assert.equal(deriveLegState(resumed.structural_stall), 'active')
  for (const c of resumed.candles) {
    assert.equal(legStateAt(resumed.structural_stall, c.timestamp), 'active')
  }
})
