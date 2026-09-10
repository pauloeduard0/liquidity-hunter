/**
 * P7.2 and P7.3 — the invalidation side, and that it cannot read the future.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import type { Candle, DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { chochDeathByClose, invalidatingSide } from './chochLifecycle.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')

const candle = (timestamp: string, close: number, high = close + 1, low = close - 1): Candle =>
  ({ timestamp, open: close, high, low, close, volume: 1 }) as Candle

const choch = (direction: string, ref: number): MarketStructure =>
  ({
    timestamp: '2026-01-01T00:00:00Z',
    event: 'change_of_character',
    direction,
    price_level: ref,
    reference_price_level: ref,
    provisional: false,
    scope: 'internal',
  }) as MarketStructure

test('P7.2 — a bullish CHoCH broke a high upward, so a close BELOW undoes it', () => {
  assert.equal(invalidatingSide(choch('bullish', 100)), 'below')
  const e = choch('bullish', 100)
  // A wick through is not enough — the detector itself calls that a sweep.
  assert.equal(
    chochDeathByClose(e, [
      candle('2026-01-01T01:00:00Z', 105),
      candle('2026-01-01T02:00:00Z', 101, 102, 95),
    ]),
    null,
  )
  assert.equal(
    chochDeathByClose(e, [
      candle('2026-01-01T01:00:00Z', 105),
      candle('2026-01-01T02:00:00Z', 99),
      candle('2026-01-01T03:00:00Z', 98),
    ]),
    '2026-01-01T02:00:00Z',
  )
  // Exactly at the level is not beyond it.
  assert.equal(chochDeathByClose(e, [candle('2026-01-01T01:00:00Z', 100)]), null)
})

test('P7.2 — a bearish CHoCH broke a low downward, so a close ABOVE undoes it', () => {
  assert.equal(invalidatingSide(choch('bearish', 100)), 'above')
  const e = choch('bearish', 100)
  assert.equal(
    chochDeathByClose(e, [
      candle('2026-01-01T01:00:00Z', 95),
      candle('2026-01-01T02:00:00Z', 99, 105, 98),
    ]),
    null,
  )
  assert.equal(
    chochDeathByClose(e, [
      candle('2026-01-01T01:00:00Z', 95),
      candle('2026-01-01T02:00:00Z', 101),
    ]),
    '2026-01-01T02:00:00Z',
  )
})

test('P7.3 — the event candle itself never kills its own level', () => {
  const e = choch('bullish', 100)
  assert.equal(chochDeathByClose(e, [candle('2026-01-01T00:00:00Z', 50)]), null)
})

const files = existsSync(FIXTURE_DIR)
  ? readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()
  : []

test('P7.3 — truncation: the answer on a prefix is the same answer or "not yet"', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured')
    return
  }
  let checked = 0
  let events = 0
  let probes = 0
  for (const f of files.slice(0, 40)) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    const chochs = data.internal_structure_events.filter(
      (e) => e.event === 'change_of_character' && !e.provisional && e.reference_price_level != null,
    )
    for (const e of chochs) {
      const full = chochDeathByClose(e, data.candles)
      events += 1
      const step = Math.max(1, Math.floor(data.candles.length / 25))
      for (let i = 0; i < data.candles.length; i += step) {
        const cut = data.candles.slice(0, i + 1)
        const partial = chochDeathByClose(e, cut)
        const ts = data.candles[i].timestamp
        // Either the death has already happened inside the prefix — and then it
        // must be exactly the death the full series reports — or it has not,
        // and the prefix says so.
        if (partial !== null) assert.equal(partial, full, `${f}: morte muda ao truncar`)
        else assert.ok(full === null || full > ts, `${f}: morte ${full} perdida no prefixo ate ${ts}`)
        probes += 1
      }
    }
    checked += 1
  }
  assert.ok(checked > 20, `poucas fixtures: ${checked}`)
  assert.ok(events > 100, `poucos CHoCH: ${events}`)
  console.log(`  truncation: ${checked} fixtures, ${events} CHoCH, ${probes} prefixos`)
})

test('P7.2 — on real data the death is on the side the event claims', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured')
    return
  }
  let verified = 0
  for (const f of files.slice(0, 30)) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    const byTs = new Map(data.candles.map((c) => [c.timestamp, c]))
    for (const e of data.internal_structure_events) {
      if (e.event !== 'change_of_character' || e.provisional) continue
      if (e.reference_price_level == null) continue
      const died = chochDeathByClose(e, data.candles)
      if (died === null) continue
      const c = byTs.get(died)!
      if (e.direction === 'bullish') assert.ok(c.close < e.reference_price_level)
      else assert.ok(c.close > e.reference_price_level)
      assert.ok(died > e.timestamp)
      verified += 1
    }
  }
  assert.ok(verified > 100, `poucas mortes verificadas: ${verified}`)
  console.log(`  mortes verificadas no lado correto: ${verified}`)
})
