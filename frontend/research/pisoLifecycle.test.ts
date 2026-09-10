/**
 * P7.2 — the lifecycle states must not read the future.
 *
 * Every death this audit classifies comes from a timestamp the backend
 * computed over the whole window (`invalidated_at`, `breached_at`, `end_time`,
 * the structural line's end). Reading one of those at candle T is only causal
 * if a death dated *after* T changes nothing about the state at T. That is the
 * truncation invariant, and it is asserted here rather than assumed — the same
 * discipline the causal-ATR fix needed in P1.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import type { DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { sourcedDefenceLevels, type SourcedLevel } from './levelSources.ts'
import { levelStateAt } from './pisoLifecycleAudit.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')

function standingUntilFor(data: DashboardData): (event: MarketStructure) => string | null {
  const scopeEvents = data.internal_structure_events
  const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
  return (event: MarketStructure) => {
    const end = structureLineEndTime(event, scopeEvents, lastCandleTime)
    if (end >= lastCandleTime) return null
    return scopeEvents.find((other) => toChartTime(other.timestamp) === end)?.timestamp ?? null
  }
}

/** The same level as it would look to an observer who has only reached `ts`:
 *  any death still in the future has simply not happened yet. */
function truncate(l: SourcedLevel, ts: string): SourcedLevel {
  return {
    ...l,
    died: l.died !== null && l.died > ts ? null : l.died,
    altDied: l.altDied !== null && l.altDied > ts ? null : l.altDied,
  }
}

test('P7.2 — a death dated after T cannot change the state at T', () => {
  const base: SourcedLevel = {
    low: 99,
    high: 101,
    family: 'resting',
    born: '2026-01-01T00:00:00Z',
    died: '2026-01-05T00:00:00Z',
    source: 'equal_lows',
    side: 'below',
    deathRule: 'wick',
    altDied: '2026-01-09T00:00:00Z',
  }
  const at = '2026-01-03T00:00:00Z'
  assert.equal(levelStateAt(base, at), 'ALIVE')
  assert.equal(levelStateAt(truncate(base, at), at), 'ALIVE')
  // Between the two deaths it is the state production calls dead and the
  // domain model calls memory.
  const mid = '2026-01-07T00:00:00Z'
  assert.equal(levelStateAt(base, mid), 'DEAD_BY_WICK')
  assert.equal(levelStateAt(truncate(base, mid), mid), 'DEAD_BY_WICK')
  // Past the close, either reading agrees it is spent.
  const late = '2026-01-11T00:00:00Z'
  assert.equal(levelStateAt(base, late), 'DEAD_BY_CLOSE')
  // Before it exists at all.
  assert.equal(levelStateAt(base, '2025-12-31T00:00:00Z'), 'UNBORN')
})

test('P7.1 — each source only reaches the states it can reach', () => {
  const make = (over: Partial<SourcedLevel>): SourcedLevel => ({
    low: 99,
    high: 101,
    family: 'order',
    born: '2026-01-01T00:00:00Z',
    died: '2026-01-05T00:00:00Z',
    source: 'poi',
    side: 'below',
    deathRule: 'close',
    altDied: null,
    ...over,
  })
  const after = '2026-01-07T00:00:00Z'
  assert.equal(levelStateAt(make({}), after), 'DEAD_BY_CLOSE')
  assert.equal(
    levelStateAt(make({ source: 'liquidation', deathRule: 'wick', family: 'resting' }), after),
    'DEAD_BY_WICK',
  )
  assert.equal(
    levelStateAt(make({ source: 'bos', deathRule: 'structure', family: 'structural' }), after),
    'DEAD_BY_STRUCTURE',
  )
  // `fair` never dies, so it never leaves ALIVE.
  assert.equal(
    levelStateAt(
      make({ source: 'poc', deathRule: 'never', family: 'fair', died: null }),
      after,
    ),
    'ALIVE',
  )
  // TOUCHED_BUT_ALIVE is reachable only where a second, later death exists.
  assert.equal(
    levelStateAt(
      make({
        source: 'equal_highs',
        family: 'resting',
        deathRule: 'wick',
        died: '2026-01-09T00:00:00Z',
        altDied: '2026-01-03T00:00:00Z',
      }),
      '2026-01-05T00:00:00Z',
    ),
    'TOUCHED_BUT_ALIVE',
  )
})

const files = existsSync(FIXTURE_DIR)
  ? readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()
  : []

test('P7.2 — the invariant holds on every real level, at every candle it lived', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured')
    return
  }
  let checked = 0
  let probes = 0
  for (const f of files.slice(0, 40)) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    const levels = sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
    // Sample candles across the window rather than the full cross-product.
    const step = Math.max(1, Math.floor(data.candles.length / 40))
    for (const l of levels) {
      for (let i = 0; i < data.candles.length; i += step) {
        const ts = data.candles[i].timestamp
        assert.equal(
          levelStateAt(l, ts),
          levelStateAt(truncate(l, ts), ts),
          `${f}: ${l.source} em ${ts}`,
        )
        probes += 1
      }
    }
    checked += 1
  }
  assert.ok(checked > 20, `poucas fixtures: ${checked}`)
  console.log(`  truncation: ${checked} fixtures, ${probes} sondagens`)
})

test('P7.8 — the two deaths of a pool are ordered, as the domain requires', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured')
    return
  }
  let pools = 0
  let zombies = 0
  for (const f of files.slice(0, 60)) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    for (const l of sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })) {
      if (l.source !== 'equal_highs' && l.source !== 'equal_lows') continue
      pools += 1
      if (l.died === null || l.altDied === null) continue
      // `breached_at must be >= invalidated_at` is a model validator; if it
      // ever stopped holding, the zombie window would be nonsense.
      assert.ok(l.altDied >= l.died, `${f}: breached ${l.altDied} < invalidated ${l.died}`)
      if (l.altDied > l.died) zombies += 1
    }
  }
  assert.ok(pools > 100, `poucos pools: ${pools}`)
  console.log(`  pools: ${pools}, com janela zumbi nao vazia: ${zombies}`)
})
