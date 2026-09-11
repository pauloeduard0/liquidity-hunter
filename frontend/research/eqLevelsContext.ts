/** E0 snapshot-only context: imports the actual frontend structural state machine.
 * node --experimental-strip-types --experimental-specifier-resolution=node ...
 * Use the same loader invocation as the PISO research scripts on Node 24.
 */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { structureTrendByCandle } from '../src/utils/tideRibbon.ts'
import type { DashboardData } from '../src/types/dashboard.ts'
const dir = new URL('./fixtures/', import.meta.url)
const visual: object[] = []
const out: Record<string, Record<string, string>> = {}
for (const name of readdirSync(dir).filter(n => n.endsWith('.json')).sort()) {
  const data = JSON.parse(readFileSync(new URL(name, dir), 'utf8')) as DashboardData
  const trends = structureTrendByCandle(data)
  out[name] = Object.fromEntries(trends)
  // Audit traversal of MainChart selection; no renderer or production edit.
  const price = data.candles.at(-1)!.close
  const standing = data.ranked_zones.filter(s => ['equal_highs', 'equal_lows'].includes(s.zone.zone_type))
  const selected = ['equal_highs', 'equal_lows'].flatMap(side => standing
    .filter(s => s.zone.zone_type === side && (side === 'equal_highs' ? s.zone.price_low > price : s.zone.price_high < price))
    .sort((a, b) => side === 'equal_highs' ? a.zone.price_low-b.zone.price_low : b.zone.price_high-a.zone.price_high).slice(0, 2))
  const advances = data.internal_structure_events.filter(e => !e.provisional && ['break_of_structure','change_of_character'].includes(e.event))
  const lastAdvance = advances.map(e => e.timestamp).sort().at(-1) ?? data.candles[0].timestamp
  const trend = trends.get(data.candles.at(-1)!.timestamp)
  const grabs = data.liquidity_grabs.filter(g => ['bullish','bearish'].includes(trend ?? '') && g.side === (trend === 'bullish' ? 'buy_side' : 'sell_side') && g.timestamp >= lastAdvance)
    .sort((a,b) => b.timestamp.localeCompare(a.timestamp)).slice(0,3)
  const backing = grabs.map(g => data.liquidity_zones.filter(z => ['equal_highs','equal_lows'].includes(z.zone_type) && z.invalidated_at === g.timestamp && z.side === g.side).sort((a,b) => b.strength-a.strength)[0]).filter(Boolean)
  visual.push({ file:name, tf:data.timeframe, currentTrend:trend, standingCandidates:standing.length,
    selectedTargets:selected.length, selectedEQH:selected.filter(s => s.zone.zone_type==='equal_highs').length,
    selectedEQL:selected.filter(s => s.zone.zone_type==='equal_lows').length,
    selectedGrabs:grabs.length, memoryBands:backing.length, totalEQBands:selected.length+backing.length,
    note:'Default layer enabled; current snapshot including last bar. Pixel overlap not measured.' })
}
writeFileSync(process.argv[2] ?? '/tmp/eq_context_baseline.json', JSON.stringify(out))

writeFileSync(process.argv[3] ?? '/tmp/eq_visual_baseline.json', JSON.stringify(visual))
