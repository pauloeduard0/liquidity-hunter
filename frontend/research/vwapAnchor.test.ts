/**
 * The port is only worth as much as its parity with the backend.
 *
 * `research/vwapAnchor.ts` reimplements a Python indicator in TypeScript so
 * P5 can swap the H4 envelope's calendar period. Every number P5 reports rests
 * on that reimplementation being the same function, so the tests below rebuild
 * real fixtures with the anchor the backend already used and require the result
 * to match what the API sent, point for point.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import type { Candle, DashboardData } from '../src/types/dashboard.ts'
import { anchorKey, isoWeek, productionAnchor, rebuildVwapPoints } from './vwapAnchor.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')

test('P5.0 — the shipped anchor table, as `_VWAP_ANCHOR_PERIOD` states it', () => {
  assert.equal(productionAnchor('15m'), 'session')
  assert.equal(productionAnchor('1h'), 'session')
  assert.equal(productionAnchor('4h'), 'week')
  assert.equal(productionAnchor('1d'), 'month')
  assert.equal(productionAnchor('1w'), 'month')
})

test('P5.0 — every boundary is UTC, and the week is ISO', () => {
  // The session rolls at 00:00 UTC, not at a local midnight.
  assert.equal(anchorKey('2026-03-02T23:00:00Z', 'session'), '2026-03-02')
  assert.equal(anchorKey('2026-03-03T00:00:00Z', 'session'), '2026-03-03')
  // Monday starts the ISO week: Sunday the 1st belongs to the week before.
  assert.equal(anchorKey('2026-03-01T12:00:00Z', 'week'), anchorKey('2026-02-23T00:00:00Z', 'week'))
  assert.notEqual(
    anchorKey('2026-03-02T00:00:00Z', 'week'),
    anchorKey('2026-03-01T12:00:00Z', 'week'),
  )
  // The year-end case the naive "day of year / 7" gets wrong.
  assert.deepEqual(isoWeek(new Date('2027-01-01T00:00:00Z')), [2026, 53])
  assert.deepEqual(isoWeek(new Date('2026-01-01T00:00:00Z')), [2026, 1])
})

test('P5.0 — a fresh accumulation reports the candle itself, then spreads', () => {
  const candle = (timestamp: string, price: number, volume: number): Candle =>
    ({
      symbol: 'T',
      timeframe: '4h',
      timestamp,
      open: price,
      high: price,
      low: price,
      close: price,
      volume,
      taker_buy_volume: 0,
    }) as Candle
  // Two candles inside one session, then one that crosses into the next.
  const points = rebuildVwapPoints(
    [
      candle('2026-03-02T00:00:00Z', 100, 1),
      candle('2026-03-02T04:00:00Z', 110, 1),
      candle('2026-03-03T00:00:00Z', 200, 1),
    ],
    'session',
  )
  assert.equal(points.length, 3)
  // A single candle has no dispersion, so it has no bands at all.
  assert.equal(points[0].value, 100)
  assert.equal(points[0].upper_1, null)
  assert.equal(points[1].value, 105)
  assert.ok(points[1].upper_1 !== null && Math.abs(points[1].upper_1 - 110) < 1e-9)
  // The reset discards the first segment entirely rather than blending it.
  assert.equal(points[2].value, 200)
  assert.equal(points[2].anchor_timestamp, '2026-03-03T00:00:00Z')
})

test('P5.0 — zero volume contributes no point until something trades', () => {
  const flat = (timestamp: string, volume: number): Candle =>
    ({ timestamp, open: 1, high: 2, low: 1, close: 1.5, volume }) as Candle
  const points = rebuildVwapPoints(
    [flat('2026-03-02T00:00:00Z', 0), flat('2026-03-02T04:00:00Z', 5)],
    'session',
  )
  assert.equal(points.length, 1)
  assert.equal(points[0].timestamp, '2026-03-02T04:00:00Z')
})

const files = existsSync(FIXTURE_DIR)
  ? readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()
  : []

test('P5.0 — rebuilding a fixture with its own anchor reproduces the API', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured — run research/captureFixtures.sh')
    return
  }
  let checked = 0
  let comparedPoints = 0
  for (const f of files) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    const shipped = data.vwap?.points
    if (!shipped || shipped.length === 0) continue
    const anchor = productionAnchor(data.timeframe)
    // The fixture also states which anchor the backend used; if the table and
    // the payload ever disagree, this is where it surfaces.
    assert.equal(data.vwap!.anchor, anchor, `${f}: anchor da fixture`)

    const rebuilt = rebuildVwapPoints(data.candles, anchor)
    assert.equal(rebuilt.length, shipped.length, `${f}: numero de pontos`)
    for (let i = 0; i < shipped.length; i += 1) {
      const a = shipped[i]
      const b = rebuilt[i]
      assert.equal(b.timestamp, a.timestamp, `${f}[${i}]: timestamp`)
      assert.equal(b.anchor_timestamp, a.anchor_timestamp, `${f}[${i}]: reset`)
      // Relative, because a 65,000-dollar series and a 0.02-dollar one cannot
      // share an absolute tolerance. 1e-9 is float noise, not a different sum.
      const rel = Math.abs(b.value - a.value) / Math.max(1e-12, Math.abs(a.value))
      assert.ok(rel < 1e-9, `${f}[${i}]: value ${b.value} != ${a.value}`)
      for (const band of ['upper_1', 'lower_1'] as const) {
        if (a[band] === null) {
          assert.equal(b[band], null, `${f}[${i}]: ${band} deveria ser nulo`)
          continue
        }
        assert.ok(b[band] !== null, `${f}[${i}]: ${band} nulo a mais`)
        const r = Math.abs(b[band]! - a[band]!) / Math.max(1e-12, Math.abs(a[band]!))
        assert.ok(r < 1e-9, `${f}[${i}]: ${band} ${b[band]} != ${a[band]}`)
      }
      comparedPoints += 1
    }
    checked += 1
  }
  assert.ok(checked > 50, `poucas fixtures comparadas: ${checked}`)
  console.log(`  paridade: ${checked} fixtures, ${comparedPoints} pontos`)
})

test('P5.1 — a different anchor really is a different envelope', (t) => {
  const f = files.find((x) => x.endsWith('_4h.json'))
  if (!f) {
    t.skip('no 4h fixture')
    return
  }
  const data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
  const weekly = rebuildVwapPoints(data.candles, 'week')
  const session = rebuildVwapPoints(data.candles, 'session')
  assert.equal(weekly.length, session.length)
  const resets = (pts: typeof weekly) => new Set(pts.map((p) => p.anchor_timestamp)).size
  // A weekly envelope resets ~7x less often, and the H4 session segment holds
  // 6 candles — the degeneration `_VWAP_ANCHOR_PERIOD` was written to avoid.
  assert.ok(resets(session) > resets(weekly) * 4, 'session deveria resetar muito mais')
})
