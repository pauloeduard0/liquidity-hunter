/**
 * The provenance list must be the production list, or P6 is measuring a fork.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

import type { DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { matchesProduction, sourcedDefenceLevels } from './levelSources.ts'

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

const files = existsSync(FIXTURE_DIR)
  ? readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()
  : []

test('P6.0 — sourced levels are the production levels, in order', (t) => {
  if (files.length === 0) {
    t.skip('no fixtures captured — run research/captureFixtures.sh')
    return
  }
  let checked = 0
  let levels = 0
  for (const f of files) {
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) continue
    } catch {
      continue
    }
    const standing = standingUntilFor(data)
    // Both ATR modes, because the tolerance path differs between them and the
    // audit runs in the causal one.
    for (const causalAtr of [true, false]) {
      const r = matchesProduction(data, standing, { causalAtr })
      assert.ok(r.ok, `${f} (causal=${causalAtr}): ${r.detail}`)
    }
    levels += sourcedDefenceLevels(data, standing, { causalAtr: true }).length
    checked += 1
  }
  assert.ok(checked > 50, `poucas fixtures comparadas: ${checked}`)
  console.log(`  paridade: ${checked} fixtures, ${levels} niveis`)
})

test('P6.0 — every level carries a source, and the sides are the ones claimed', (t) => {
  const f = files.find((x) => x.endsWith('_4h.json'))
  if (!f) {
    t.skip('no 4h fixture')
    return
  }
  const data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
  const levels = sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  assert.ok(levels.length > 0)
  for (const l of levels) {
    assert.ok(l.source, 'nivel sem fonte')
    assert.ok(['above', 'below', 'neutral'].includes(l.side))
    // A family and its sources may not disagree.
    if (l.source === 'bos' || l.source === 'choch') assert.equal(l.family, 'structural')
    if (l.source === 'poi') assert.equal(l.family, 'order')
    if (l.source === 'equal_highs') assert.equal(l.side, 'above')
    if (l.source === 'equal_lows') assert.equal(l.side, 'below')
    if (l.family === 'fair') assert.equal(l.side, 'neutral')
  }
})
