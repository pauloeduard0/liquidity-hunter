import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { Candle, LiquidityZone, LiquidityZoneType, TimeFrame } from '../types/dashboard.ts'
import {
  atrOf,
  formatReference,
  liquidityReferences,
  nearestReference,
} from './liquidityReferences.ts'

/** A flat series whose true range is 1% of price, so one ATR is one unit at 100. */
function flatCandles(n: number, timeframe: TimeFrame = '1h'): Candle[] {
  return Array.from({ length: n }, (_, i) => ({
    symbol: 'TESTUSDT',
    timeframe,
    timestamp: new Date(Date.UTC(2026, 0, 1, i)).toISOString(),
    open: 100,
    high: 100.5,
    low: 99.5,
    close: 100,
    volume: 10,
    taker_buy_volume: 5,
  }))
}

function zone(
  zoneType: LiquidityZoneType,
  low: number,
  high: number,
  {
    strength = 0.5,
    formedAt = '2026-01-01T00:00:00Z',
    mitigated = false,
    timeframe = '1h' as TimeFrame,
  } = {},
): LiquidityZone {
  return {
    symbol: 'TESTUSDT',
    timeframe,
    zone_type: zoneType,
    side: zoneType === 'equal_highs' || zoneType === 'swing_high' ? 'buy_side' : 'sell_side',
    price_high: high,
    price_low: low,
    formed_at: formedAt,
    invalidated_at: null,
    breached_at: null,
    sweep_rejected: false,
    strength,
    is_mitigated: mitigated,
  }
}

const CANDLES = flatCandles(40)
const PRICE = 100
const ATR = atrOf(CANDLES, PRICE)

test('one ATR of the flat series is one price unit', () => {
  assert.ok(Math.abs(ATR - 1) < 1e-9)
})

test('only EQ available: the swing slot stays empty rather than inventing one', () => {
  const { eq, swing } = liquidityReferences([zone('equal_highs', 101.4, 101.4)], CANDLES, PRICE)
  assert.equal(swing, null)
  assert.equal(eq?.label, 'EQH')
  assert.ok(Math.abs(eq!.distanceAtr - 1.4) < 1e-9)
  assert.equal(eq!.above, true)
})

test('only swing available: the EQ slot stays empty', () => {
  const { eq, swing } = liquidityReferences([zone('swing_low', 99.2, 99.2)], CANDLES, PRICE)
  assert.equal(eq, null)
  assert.equal(swing?.label, 'Swing L')
  assert.equal(swing!.above, false)
})

test('both available: each family reports its own nearest, no winner between them', () => {
  const zones = [
    zone('equal_highs', 101.4, 101.4),
    zone('equal_lows', 97.0, 97.0),
    zone('swing_low', 99.2, 99.2),
    zone('swing_high', 105.0, 105.0),
  ]
  const { eq, swing } = liquidityReferences(zones, CANDLES, PRICE)
  // The swing is nearer (0.8 vs 1.4 ATR) and still does not displace the EQ.
  assert.equal(eq?.zoneType, 'equal_highs')
  assert.equal(swing?.zoneType, 'swing_low')
  assert.equal(formatReference(eq!), 'EQH 1.4 ATR ↑')
  assert.equal(formatReference(swing!), 'Swing L 0.8 ATR ↓')
})

test('EQ above and swing below are reported on their own sides', () => {
  const { eq, swing } = liquidityReferences(
    [zone('equal_highs', 102, 102), zone('swing_low', 98.5, 98.5)],
    CANDLES,
    PRICE,
  )
  assert.equal(eq!.above, true)
  assert.equal(swing!.above, false)
})

test('both families on the same side is reported as such, not arbitrated', () => {
  const { eq, swing } = liquidityReferences(
    [zone('equal_lows', 98, 98), zone('swing_low', 99, 99)],
    CANDLES,
    PRICE,
  )
  assert.equal(eq!.above, false)
  assert.equal(swing!.above, false)
  assert.ok(eq!.distanceAtr > swing!.distanceAtr)
})

test('the nearer level wins even when the far one is much stronger', () => {
  const zones = [
    zone('equal_highs', 105, 105, { strength: 1 }),
    zone('equal_lows', 99.5, 99.5, { strength: 0.01 }),
  ]
  const eq = nearestReference(zones, PRICE, ATR, ['equal_highs', 'equal_lows'], 'eq')
  assert.equal(eq?.zoneType, 'equal_lows')
})

test('strength breaks an exact distance tie, and only an exact one', () => {
  const zones = [
    zone('swing_high', 101, 101, { strength: 0.2 }),
    zone('swing_low', 99, 99, { strength: 0.9 }),
  ]
  assert.equal(
    nearestReference(zones, PRICE, ATR, ['swing_high', 'swing_low'], 'swing')?.zoneType,
    'swing_low',
  )
  // Nudge one of them: the tie is gone, so strength no longer has a say.
  const untied = [zone('swing_high', 100.9, 100.9, { strength: 0.2 }), zones[1]]
  assert.equal(
    nearestReference(untied, PRICE, ATR, ['swing_high', 'swing_low'], 'swing')?.zoneType,
    'swing_high',
  )
})

test('a total tie falls back on formation time, not on emission order', () => {
  const early = zone('swing_high', 101, 101, { formedAt: '2026-01-01T00:00:00Z' })
  const late = zone('swing_low', 99, 99, { formedAt: '2026-02-01T00:00:00Z' })
  const forward = nearestReference([early, late], PRICE, ATR, ['swing_high', 'swing_low'], 'swing')
  const reversed = nearestReference([late, early], PRICE, ATR, ['swing_high', 'swing_low'], 'swing')
  assert.equal(forward?.price, reversed?.price)
  assert.equal(forward?.zoneType, 'swing_high')
})

test('mitigated zones are not references', () => {
  const { eq } = liquidityReferences(
    [zone('equal_highs', 101, 101, { mitigated: true })],
    CANDLES,
    PRICE,
  )
  assert.equal(eq, null)
})

test('no levels at all: both slots are empty, the card falls back', () => {
  const { eq, swing } = liquidityReferences([], CANDLES, PRICE)
  assert.equal(eq, null)
  assert.equal(swing, null)
})

test('order blocks and other zone types are not references of either family', () => {
  const { eq, swing } = liquidityReferences(
    [zone('order_block', 100.5, 101), zone('fair_value_gap', 99, 99.5)],
    CANDLES,
    PRICE,
  )
  assert.equal(eq, null)
  assert.equal(swing, null)
})

test('a series with no range yields no reference rather than an infinite distance', () => {
  const flat = flatCandles(5).map((c) => ({ ...c, high: 100, low: 100 }))
  const { eq } = liquidityReferences([zone('equal_highs', 101, 101)], flat, PRICE)
  assert.equal(eq, null)
})

for (const timeframe of ['15m', '1h', '4h'] as TimeFrame[]) {
  test(`${timeframe}: the reading is the same geometry on every timeframe`, () => {
    const candles = flatCandles(40, timeframe)
    const { eq, swing } = liquidityReferences(
      [
        zone('equal_lows', 98.6, 98.6, { timeframe }),
        zone('swing_high', 102.2, 102.2, { timeframe }),
      ],
      candles,
      PRICE,
    )
    assert.equal(formatReference(eq!), 'EQL 1.4 ATR ↓')
    assert.equal(formatReference(swing!), 'Swing H 2.2 ATR ↑')
  })
}

test('the ATR distance is causal: no candle after the cut can reach it', () => {
  const prefix = flatCandles(30)
  const levels = [zone('equal_highs', 101.4, 101.4), zone('swing_low', 99.2, 99.2)]
  // A violent future: candles far wider than anything in the prefix.
  const future = Array.from({ length: 20 }, (_, i) => ({
    ...prefix[0],
    timestamp: new Date(Date.UTC(2026, 0, 2, i)).toISOString(),
    high: 140,
    low: 60,
  }))
  const whole = [...prefix, ...future]
  for (let cut = 5; cut <= prefix.length; cut += 5) {
    const onItsOwn = liquidityReferences(levels, prefix.slice(0, cut), PRICE)
    const truncated = liquidityReferences(levels, whole.slice(0, cut), PRICE)
    assert.equal(onItsOwn.eq!.distanceAtr, truncated.eq!.distanceAtr)
    assert.equal(onItsOwn.swing!.distanceAtr, truncated.swing!.distanceAtr)
  }
})

test('the ATR unit is the loaded window, and a wider window is a different unit', () => {
  // Declared, not hidden: `meanTrueRangePct` averages whatever is loaded, so a
  // more volatile stretch later changes the ATR the card divides by. The card
  // is a live reading at the last candle, so this only ever means "today's
  // volatility unit" -- but the number is not comparable across window sizes.
  const calm = flatCandles(30)
  const stormy = [...calm, { ...calm[0], high: 140, low: 60 }]
  assert.ok(atrOf(stormy, PRICE) > atrOf(calm, PRICE))
})

/** Comments name the composite to explain why it is gone; code must not read it. */
const withoutComments = (source: string) =>
  source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

test('nothing from the old composite reaches the card', async () => {
  const fs = await import('node:fs/promises')
  const files = ['./liquidityReferences.ts', '../components/KpiRow.tsx']
  for (const file of files) {
    const code = withoutComments(
      await fs.readFile(new URL(file, import.meta.url), 'utf8'),
    )
    for (const banned of ['ranked_zones', 'distance_score', 'touch_score', 'timeframe_score']) {
      assert.ok(!code.includes(banned), `${banned} must not be read in ${file}`)
    }
  }
})

test('the label "Dominant Liquidity" is gone from the KPI row', async () => {
  const source = await import('node:fs/promises').then((fs) =>
    fs.readFile(new URL('../components/KpiRow.tsx', import.meta.url), 'utf8'),
  )
  assert.ok(!source.includes('Dominant Liquidity'))
  assert.ok(!/dominantLiquidity/.test(source))
  assert.ok(source.includes('Liquidity References'))
})
