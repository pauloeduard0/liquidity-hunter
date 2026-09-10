/**
 * P6 — where in the funnel does the H4 direction asymmetry come from?
 *
 * The H4 PISO fires 57 bearish marks to 11 bullish, and the bullish ones
 * measure 27% under all three anchors (P5). Five studies have now asked
 * quality questions about the H4 and got nothing; this one asks a counting
 * question instead, and asks it *before* any outcome is looked at: at which
 * gate do the two directions separate, and is it the geometry of the envelope,
 * the geometry of the candle, or the map of levels available on each side?
 *
 * What is instrumented
 * --------------------
 * The rule is untouched and runs exactly as shipped. What this adds is
 * visibility the production funnel does not expose:
 *
 *   - the two edges are scored **independently**. `evaluateCandidates` resolves
 *     a candle that cleared both edges as a top, which is right for a mark and
 *     wrong for a census — a bullish candidate hidden behind a bearish one
 *     would be invisible exactly where the asymmetry is being measured.
 *   - breach and reclaim are separated, so "price never went there" and "price
 *     went there and did not come back" stop being one number.
 *   - families are counted twice, once ignoring every level's lifespan and once
 *     honouring it, which is what turns "no second family" into either "nothing
 *     was ever mapped here" or "what was mapped had already died".
 *   - levels keep their provenance (`research/levelSources.ts`), so an
 *     equal-high and an equal-low can be told apart.
 *
 * Everything else — thresholds, anchor, lifecycle, ATR mode, family rules — is
 * production. No gate is added, no direction is excluded, nothing is optimised.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoH4BullishFunnel.ts --json research/piso_h4_bullish_funnel_baseline.json
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure, VWAPPoint } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import { meanTrueRangePctSeries, type LevelFamily } from '../src/utils/defendedLevels.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { sourcedDefenceLevels, type LevelSource } from './levelSources.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const TIMEFRAME = '4h'
const PRIMARY_H = 5
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250

/** The shipped thresholds, mirrored for the funnel. Not knobs — changing one
 *  here would only make this file disagree with the rule it is auditing. */
const MIN_EXCURSION_ATR = 1.0
const MIN_WICK_BODY = 2.0
const MIN_FAMILIES = 2

type Direction = 'bullish' | 'bearish'

// ---------------------------------------------------------------------------
// Production wiring.
// ---------------------------------------------------------------------------

function standingUntilFor(data: DashboardData): (event: MarketStructure) => string | null {
  const scopeEvents = data.internal_structure_events
  const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
  return (event: MarketStructure) => {
    const end = structureLineEndTime(event, scopeEvents, lastCandleTime)
    if (end >= lastCandleTime) return null
    return scopeEvents.find((other) => toChartTime(other.timestamp) === end)?.timestamp ?? null
  }
}

// ---------------------------------------------------------------------------
// The census.
// ---------------------------------------------------------------------------

interface Cand {
  key: string
  symbol: string
  timestamp: string
  index: number
  direction: Direction
  side: 'top' | 'bottom'
  /** Cleared the edge; `reclaim` additionally closed back inside. */
  reclaim: boolean
  excursionAtr: number
  wickBody: number
  rangeAtr: number
  bodyAtr: number
  /** Where the close sits in the candle's own range, 0 = low, 1 = high. */
  closePos: number
  sigmaAtr: number
  distVwapAtr: number
  atr: number
  frac: number
  ageSinceReset: number
  /** Families overlapping the swept range with lifespans ignored, and with
   *  lifespans honoured — the difference is what the lifecycle removed. */
  mapFamilies: LevelFamily[]
  liveFamilies: LevelFamily[]
  /** Why a mapped level did not count, per family. */
  bornAfter: Map<LevelFamily, number>
  diedBefore: Map<LevelFamily, number>
  sources: LevelSource[]
  passExc: boolean
  passWick: boolean
  passMap: boolean
  passLive: boolean
  isMark: boolean
  /** The structural direction standing before this candle, causally. */
  trend: Direction | 'none'
}

function censusFor(data: DashboardData): { cands: Cand[]; drift: Drift } {
  const levels = sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const atrSeries = meanTrueRangePctSeries(data.candles)
  const points = data.vwap?.points ?? []
  const byTs = new Map<string, VWAPPoint>()
  for (const p of points) byTs.set(p.timestamp, p)
  const resetAt = new Map<string, number>()
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  for (const p of points) {
    const i = idx.get(p.timestamp)
    if (i !== undefined && !resetAt.has(p.anchor_timestamp)) resetAt.set(p.anchor_timestamp, i)
  }

  // The causal trend: the last settled break before this candle, nothing more.
  const settled = data.internal_structure_events
    .filter(
      (e) =>
        !e.provisional &&
        (e.event === 'break_of_structure' || e.event === 'change_of_character'),
    )
    .sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1))
  let trendAt = 0
  let trend: Direction | 'none' = 'none'

  const drift: Drift = { candles: 0, above: 0, dist: 0, dist2: 0, dist3: 0 }
  const cands: Cand[] = []

  for (let i = 0; i < data.candles.length; i += 1) {
    const c = data.candles[i]
    while (trendAt < settled.length && settled[trendAt].timestamp <= c.timestamp) {
      const d = settled[trendAt].direction
      if (d === 'bullish' || d === 'bearish') trend = d
      trendAt += 1
    }
    const p = byTs.get(c.timestamp)
    if (!p || p.upper_1 === null || p.lower_1 === null) continue
    const atrPct = atrSeries[i]
    const atr = atrPct * c.close
    if (atr <= 0) continue

    // P6.3, on every candle rather than only the candidates.
    const z = (c.close - p.value) / atr
    drift.candles += 1
    if (c.close > p.value) drift.above += 1
    drift.dist += z
    drift.dist2 += z * z
    drift.dist3 += z * z * z

    const sigmaAtr = (p.upper_1 - p.value) / atr
    const age = i - (resetAt.get(p.anchor_timestamp) ?? i)
    const body = Math.abs(c.close - c.open) || 1e-9
    const range = c.high - c.low || 1e-9

    // Both edges, independently — the census the rule's tie-break hides.
    for (const side of ['top', 'bottom'] as const) {
      const edge = side === 'top' ? p.upper_1 : p.lower_1
      const breached = side === 'top' ? c.high > edge : c.low < edge
      if (!breached) continue
      const reclaim = side === 'top' ? c.close < edge : c.close > edge
      const price = side === 'top' ? c.high : c.low
      const wick =
        side === 'top' ? c.high - Math.max(c.open, c.close) : Math.min(c.open, c.close) - c.low
      const low = Math.min(edge, price)
      const high = Math.max(edge, price)

      const mapFamilies = new Set<LevelFamily>()
      const liveFamilies = new Set<LevelFamily>()
      const bornAfter = new Map<LevelFamily, number>()
      const diedBefore = new Map<LevelFamily, number>()
      const sources: LevelSource[] = []
      for (const l of levels) {
        if (l.high < low || l.low > high) continue
        mapFamilies.add(l.family)
        const notYet = l.born > c.timestamp
        const gone = l.died !== null && l.died < c.timestamp
        if (notYet) bornAfter.set(l.family, (bornAfter.get(l.family) ?? 0) + 1)
        else if (gone) diedBefore.set(l.family, (diedBefore.get(l.family) ?? 0) + 1)
        else {
          liveFamilies.add(l.family)
          sources.push(l.source)
        }
      }

      const excursionAtr = Math.abs(price - edge) / atr
      const wickBody = wick / body
      const passExc = excursionAtr >= MIN_EXCURSION_ATR
      const passWick = wickBody >= MIN_WICK_BODY
      const passMap = mapFamilies.size >= MIN_FAMILIES
      const passLive = liveFamilies.size >= MIN_FAMILIES
      cands.push({
        key: `${data.symbol}_${data.timeframe}.json`,
        symbol: data.symbol,
        timestamp: c.timestamp,
        index: i,
        // A defended top is a bearish reading, a defended bottom bullish.
        direction: side === 'top' ? 'bearish' : 'bullish',
        side,
        reclaim,
        excursionAtr,
        wickBody,
        rangeAtr: range / atr,
        bodyAtr: body / atr,
        closePos: (c.close - c.low) / range,
        sigmaAtr,
        distVwapAtr: Math.abs(z),
        atr,
        frac: i / data.candles.length,
        ageSinceReset: age,
        mapFamilies: [...mapFamilies].sort(),
        liveFamilies: [...liveFamilies].sort(),
        bornAfter,
        diedBefore,
        sources,
        passExc,
        passWick,
        passMap,
        passLive,
        isMark: reclaim && passExc && passWick && passLive,
        trend,
      })
    }
  }
  return { cands, drift }
}

interface Drift {
  candles: number
  above: number
  dist: number
  dist2: number
  dist3: number
}

// ---------------------------------------------------------------------------
// Cartography census, independent of any candidate.
// ---------------------------------------------------------------------------

interface MapCensus {
  count: Map<string, number>
  aliveCandles: Map<string, number>
}

function mapCensusFor(data: DashboardData, census: MapCensus): void {
  const levels = sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const n = data.candles.length
  for (const l of levels) {
    const k = `${l.source}|${l.side}`
    census.count.set(k, (census.count.get(k) ?? 0) + 1)
    const from = idx.get(l.born) ?? 0
    const to = l.died !== null ? (idx.get(l.died) ?? n) : n
    census.aliveCandles.set(k, (census.aliveCandles.get(k) ?? 0) + Math.max(0, to - from))
  }
}

// ---------------------------------------------------------------------------
// Forward reading (P6.12 only, and only after the funnel is explained).
// ---------------------------------------------------------------------------

interface Outcome {
  mfe: number
  mae: number
  won: boolean
  move: number
}

function forward(
  candles: Candle[],
  i: number,
  h: number,
  direction: Direction,
  atr: number,
): Outcome | null {
  if (i + h >= candles.length || atr <= 0) return null
  const entry = candles[i].close
  let mfe = 0
  let mae = 0
  for (let k = i + 1; k <= i + h; k += 1) {
    const up = (candles[k].high - entry) / atr
    const down = (entry - candles[k].low) / atr
    const fav = direction === 'bullish' ? up : down
    const adv = direction === 'bullish' ? down : up
    if (fav > mfe) mfe = fav
    if (adv > mae) mae = adv
  }
  return {
    mfe,
    mae,
    won: mfe > mae,
    move: ((candles[i + h].close - entry) / atr) * (direction === 'bullish' ? 1 : -1),
  }
}

function rng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 0x100000000
  }
}

interface Agg {
  n: number
  mfe: number
  mae: number
  move: number
  won: number
}
const emptyAgg = (): Agg => ({ n: 0, mfe: 0, mae: 0, move: 0, won: 0 })
function add(a: Agg, o: Outcome): void {
  a.n += 1
  a.mfe += o.mfe
  a.mae += o.mae
  a.move += o.move
  if (o.won) a.won += 1
}
function zProp(a: Agg, c: Agg): number {
  if (a.n === 0 || c.n === 0) return 0
  const p = (a.won + c.won) / (a.n + c.n)
  const se = Math.sqrt(p * (1 - p) * (1 / a.n + 1 / c.n))
  return se > 0 ? (a.won / a.n - c.won / c.n) / se : 0
}

// ---------------------------------------------------------------------------
// Small helpers.
// ---------------------------------------------------------------------------

function pct(values: number[], q: number): number {
  if (values.length === 0) return 0
  const s = [...values].sort((a, b) => a - b)
  const i = Math.min(s.length - 1, Math.max(0, Math.floor(q * (s.length - 1))))
  return s[i]
}
const excBucket = (v: number): string =>
  v < 0.5 ? 'exc<0.5' : v < 1.0 ? 'exc0.5-1.0' : v < 1.75 ? 'exc1.0-1.75' : 'exc1.75+'
const wickBucket = (v: number): string =>
  v < 1 ? 'wick<1' : v < 2 ? 'wick1-2' : v < 4 ? 'wick2-4' : 'wick4+'
const ageBucket = (v: number): string =>
  v <= 6 ? '0-6' : v <= 12 ? '7-12' : v <= 24 ? '13-24' : '25+'

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const atrCache = new Map<string, number[]>()
  const cands: Cand[] = []
  const drift: Drift = { candles: 0, above: 0, dist: 0, dist2: 0, dist3: 0 }
  const mapCensus: MapCensus = { count: new Map(), aliveCandles: new Map() }
  const skipped: string[] = []
  let evaluated = 0

  for (const f of files) {
    let d: DashboardData
    try {
      d = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(d.candles) || d.candles.length === 0) throw new Error('empty')
      if (!d.vwap?.points?.length) throw new Error('no vwap')
    } catch {
      skipped.push(f)
      continue
    }
    if (d.timeframe !== TIMEFRAME) continue
    fixtures.set(f, d)
    atrCache.set(f, meanTrueRangePctSeries(d.candles))
    const r = censusFor(d)
    cands.push(...r.cands)
    evaluated += r.drift.candles
    drift.candles += r.drift.candles
    drift.above += r.drift.above
    drift.dist += r.drift.dist
    drift.dist2 += r.drift.dist2
    drift.dist3 += r.drift.dist3
    mapCensusFor(d, mapCensus)
  }

  const random = rng(20260910)
  const report: Record<string, unknown> = { skipped }
  const bull = (c: Cand) => c.direction === 'bullish'
  const bear = (c: Cand) => c.direction === 'bearish'

  console.log('# PISO — P6: o funil do H4 por direcao\n')
  console.log(
    `painel: ${fixtures.size} fixtures H4, ${[...fixtures.values()].reduce((s, d) => s + d.candles.length, 0)} velas ` +
      `(${evaluated} com banda +-1sigma definida) — modo causal, regra inalterada\n`,
  )

  // --- P6.0 o funil ---------------------------------------------------------
  console.log('## P6.0 — o funil, lado a lado\n')
  console.log(
    '  as duas bordas sao pontuadas INDEPENDENTEMENTE: a regra resolve como topo\n' +
      '  a vela que limpou as duas, o que esconderia exatamente o candidato bullish\n' +
      '  que esta em questao. sobreposicao reportada no fim da secao.\n',
  )
  const stages: [string, (c: Cand) => boolean][] = [
    ['2. cruza +-1sigma', () => true],
    ['3. fecha de volta', (c) => c.reclaim],
    ['4. excursion >=1 ATR', (c) => c.reclaim && c.passExc],
    ['5. wick/body >=2', (c) => c.reclaim && c.passExc && c.passWick],
    ['6. cartografia >=2 fam', (c) => c.reclaim && c.passExc && c.passWick && c.passMap],
    ['7. vivas >=2 (lifecycle)', (c) => c.isMark],
    ['8. vira PISO', (c) => c.isMark],
  ]
  console.log(
    '  etapa                       bullish   passa%   marg%  |  bearish   passa%   marg%  |  bull/bear',
  )
  console.log(
    `  1. velas avaliadas        ${String(evaluated).padStart(9)}       —       —  |${String(evaluated).padStart(9)}       —       —  |      1.00`,
  )
  let prevB = 0
  let prevS = 0
  const funnel: Record<string, unknown>[] = []
  for (const [label, f] of stages) {
    const nb = cands.filter((c) => bull(c) && f(c)).length
    const ns = cands.filter((c) => bear(c) && f(c)).length
    const baseB = label.startsWith('2.') ? evaluated : prevB
    const baseS = label.startsWith('2.') ? evaluated : prevS
    const pb = baseB ? (100 * nb) / baseB : 0
    const ps = baseS ? (100 * ns) / baseS : 0
    console.log(
      `  ${label.padEnd(25)} ${String(nb).padStart(9)} ${pb.toFixed(1).padStart(7)} ` +
        `${(baseB - nb === 0 ? '0.0' : (100 - pb).toFixed(1)).padStart(7)}  |` +
        `${String(ns).padStart(9)} ${ps.toFixed(1).padStart(7)} ` +
        `${(baseS - ns === 0 ? '0.0' : (100 - ps).toFixed(1)).padStart(7)}  |` +
        `${(ns ? nb / ns : 0).toFixed(2).padStart(10)}`,
    )
    funnel.push({ stage: label, bullish: nb, bearish: ns })
    prevB = nb
    prevS = ns
  }
  report.funnel = funnel
  const both = new Set<string>()
  const seen = new Map<string, number>()
  for (const c of cands) {
    const k = `${c.symbol}|${c.timestamp}`
    seen.set(k, (seen.get(k) ?? 0) + 1)
    if ((seen.get(k) ?? 0) > 1) both.add(k)
  }
  console.log(`\n  velas que cruzaram as DUAS bordas: ${both.size}`)

  // --- P6.1 geometria -------------------------------------------------------
  console.log('\n## P6.1 — geometria do candidato (apos fechar de volta)\n')
  const rec = cands.filter((c) => c.reclaim)
  const axes: [string, (c: Cand) => number][] = [
    ['excursion (ATR)', (c) => c.excursionAtr],
    ['wick/body', (c) => c.wickBody],
    ['range/ATR', (c) => c.rangeAtr],
    ['body/ATR', (c) => c.bodyAtr],
    ['close no range', (c) => c.closePos],
    ['sigma/ATR', (c) => c.sigmaAtr],
    ['dist VWAP/ATR', (c) => c.distVwapAtr],
  ]
  console.log('  eixo                 dir        n     p25     p50     p75     p90')
  for (const [name, f] of axes) {
    for (const [dir, sel] of [
      ['bullish', bull],
      ['bearish', bear],
    ] as [string, (c: Cand) => boolean][]) {
      const v = rec.filter(sel).map(f)
      console.log(
        `  ${name.padEnd(20)} ${dir.padEnd(8)} ${String(v.length).padStart(6)} ` +
          [0.25, 0.5, 0.75, 0.9]
            .map((q) => pct(v, q).toFixed(2).padStart(7))
            .join(' '),
      )
    }
  }

  // --- P6.2 upper vs lower --------------------------------------------------
  console.log('\n## P6.2 — a borda superior contra a inferior\n')
  console.log('  metrica                        upper_1 (bearish)   lower_1 (bullish)')
  const up = cands.filter(bear)
  const dn = cands.filter(bull)
  const row = (label: string, a: number, b: number, dp = 2) =>
    console.log(`  ${label.padEnd(30)} ${a.toFixed(dp).padStart(17)} ${b.toFixed(dp).padStart(19)}`)
  row('breaches', up.length, dn.length, 0)
  row('breach % das velas', (100 * up.length) / evaluated, (100 * dn.length) / evaluated)
  row(
    'reclaim % dos breaches',
    (100 * up.filter((c) => c.reclaim).length) / Math.max(1, up.length),
    (100 * dn.filter((c) => c.reclaim).length) / Math.max(1, dn.length),
  )
  row(
    'excursion mediana',
    pct(up.map((c) => c.excursionAtr), 0.5),
    pct(dn.map((c) => c.excursionAtr), 0.5),
  )
  row('wick/body mediana', pct(up.map((c) => c.wickBody), 0.5), pct(dn.map((c) => c.wickBody), 0.5))
  row(
    'familias vivas medias',
    up.reduce((s, c) => s + c.liveFamilies.length, 0) / Math.max(1, up.length),
    dn.reduce((s, c) => s + c.liveFamilies.length, 0) / Math.max(1, dn.length),
  )
  row(
    'familias cartografadas',
    up.reduce((s, c) => s + c.mapFamilies.length, 0) / Math.max(1, up.length),
    dn.reduce((s, c) => s + c.mapFamilies.length, 0) / Math.max(1, dn.length),
  )

  // --- P6.3 drift -----------------------------------------------------------
  console.log('\n## P6.3 — deriva em torno da VWAP (descritivo)\n')
  const mean = drift.dist / Math.max(1, drift.candles)
  const varr = drift.dist2 / Math.max(1, drift.candles) - mean * mean
  const sd = Math.sqrt(Math.max(varr, 0))
  const skew =
    sd > 0
      ? (drift.dist3 / Math.max(1, drift.candles) -
          3 * mean * varr -
          mean * mean * mean) /
        (sd * sd * sd)
      : 0
  console.log(`  velas acima da VWAP:        ${((100 * drift.above) / drift.candles).toFixed(1)}%`)
  console.log(`  (close - VWAP)/ATR medio:   ${mean.toFixed(3)}`)
  console.log(`  desvio-padrao:              ${sd.toFixed(3)}`)
  console.log(`  skew:                       ${skew.toFixed(3)}`)

  // --- P6.4 lifecycle -------------------------------------------------------
  console.log('\n## P6.4 — por que a familia nao contou (candidatos com exc+pavio OK)\n')
  const gated = rec.filter((c) => c.passExc && c.passWick)
  console.log('  dir       n   1 fam   >=2 vivas   >=2 cartog   perdidas p/ lifecycle')
  for (const [dir, sel] of [
    ['bullish', bull],
    ['bearish', bear],
  ] as [string, (c: Cand) => boolean][]) {
    const g = gated.filter(sel)
    const one = g.filter((c) => c.liveFamilies.length === 1).length
    const live = g.filter((c) => c.passLive).length
    const map = g.filter((c) => c.passMap).length
    console.log(
      `  ${dir.padEnd(8)} ${String(g.length).padStart(4)} ${String(one).padStart(6)} ` +
        `${String(live).padStart(11)} ${String(map).padStart(12)} ${String(map - live).padStart(23)}`,
    )
  }
  console.log('\n  niveis descartados por familia (soma sobre os candidatos acima)')
  console.log('  dir      familia      nasceu depois   morreu antes   vivos')
  for (const [dir, sel] of [
    ['bullish', bull],
    ['bearish', bear],
  ] as [string, (c: Cand) => boolean][]) {
    const g = gated.filter(sel)
    for (const fam of ['structural', 'order', 'resting', 'fair'] as LevelFamily[]) {
      const after = g.reduce((s, c) => s + (c.bornAfter.get(fam) ?? 0), 0)
      const before = g.reduce((s, c) => s + (c.diedBefore.get(fam) ?? 0), 0)
      const live = g.filter((c) => c.liveFamilies.includes(fam)).length
      console.log(
        `  ${dir.padEnd(8)} ${fam.padEnd(12)} ${String(after).padStart(13)} ` +
          `${String(before).padStart(14)} ${String(live).padStart(7)}`,
      )
    }
  }

  // --- P6.5 familias --------------------------------------------------------
  console.log('\n## P6.5 — familias no gate (candidatos com exc+pavio OK)\n')
  console.log('  dir      familia      presente   %')
  for (const [dir, sel] of [
    ['bullish', bull],
    ['bearish', bear],
  ] as [string, (c: Cand) => boolean][]) {
    const g = gated.filter(sel)
    for (const fam of ['structural', 'order', 'resting', 'fair'] as LevelFamily[]) {
      const n = g.filter((c) => c.liveFamilies.includes(fam)).length
      console.log(
        `  ${dir.padEnd(8)} ${fam.padEnd(12)} ${String(n).padStart(8)} ${((100 * n) / Math.max(1, g.length)).toFixed(1).padStart(6)}`,
      )
    }
  }
  console.log('\n  combinacoes (n>=3)')
  for (const [dir, sel] of [
    ['bullish', bull],
    ['bearish', bear],
  ] as [string, (c: Cand) => boolean][]) {
    const counts = new Map<string, number>()
    for (const c of gated.filter(sel)) {
      const k = c.liveFamilies.join('+') || '(nenhuma)'
      counts.set(k, (counts.get(k) ?? 0) + 1)
    }
    const shown = [...counts.entries()]
      .filter(([, n]) => n >= 3)
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `${k}=${n}`)
      .join('  ')
    console.log(`  ${dir.padEnd(8)} ${shown || '—'}`)
  }

  // --- P6.6/7/8 cartografia -------------------------------------------------
  console.log('\n## P6.6/7/8 — a cartografia disponivel, por fonte e por lado\n')
  console.log('  fonte              lado      niveis   vela-vida media')
  const srcKeys = [...mapCensus.count.keys()].sort()
  for (const k of srcKeys) {
    const [source, side] = k.split('|')
    const n = mapCensus.count.get(k)!
    console.log(
      `  ${source.padEnd(18)} ${side.padEnd(9)} ${String(n).padStart(7)} ` +
        `${(mapCensus.aliveCandles.get(k)! / n).toFixed(0).padStart(17)}`,
    )
  }
  console.log('\n  interseccao pelo wick (candidatos com exc+pavio OK, so niveis VIVOS)')
  console.log('  dir      fonte                vezes')
  for (const [dir, sel] of [
    ['bullish', bull],
    ['bearish', bear],
  ] as [string, (c: Cand) => boolean][]) {
    const counts = new Map<string, number>()
    for (const c of gated.filter(sel)) {
      for (const s of new Set(c.sources)) counts.set(s, (counts.get(s) ?? 0) + 1)
    }
    for (const [s, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
      console.log(`  ${dir.padEnd(8)} ${s.padEnd(20)} ${String(n).padStart(5)}`)
    }
  }

  // --- P6.9 anchor age ------------------------------------------------------
  console.log('\n## P6.9 — o funil dentro de cada balde de idade da ancora\n')
  console.log('  idade    dir       breach  reclaim   exc+pavio   >=2 vivas   PISO')
  for (const b of ['0-6', '7-12', '13-24', '25+']) {
    for (const [dir, sel] of [
      ['bullish', bull],
      ['bearish', bear],
    ] as [string, (c: Cand) => boolean][]) {
      const g = cands.filter((c) => sel(c) && ageBucket(c.ageSinceReset) === b)
      console.log(
        `  ${b.padEnd(8)} ${dir.padEnd(8)} ${String(g.length).padStart(7)} ` +
          `${String(g.filter((c) => c.reclaim).length).padStart(8)} ` +
          `${String(g.filter((c) => c.reclaim && c.passExc && c.passWick).length).padStart(11)} ` +
          `${String(g.filter((c) => c.reclaim && c.passExc && c.passWick && c.passLive).length).padStart(11)} ` +
          `${String(g.filter((c) => c.isMark).length).padStart(6)}`,
      )
    }
  }

  // --- P6.10 regime ---------------------------------------------------------
  console.log('\n## P6.10 — contexto estrutural no instante (causal, sem virar filtro)\n')
  console.log('  tendencia   dir       exc+pavio   PISO    PISO% do gate')
  for (const t of ['bullish', 'bearish', 'none'] as const) {
    for (const [dir, sel] of [
      ['bullish', bull],
      ['bearish', bear],
    ] as [string, (c: Cand) => boolean][]) {
      const g = gated.filter((c) => sel(c) && c.trend === t)
      const m = g.filter((c) => c.isMark).length
      console.log(
        `  ${t.padEnd(11)} ${dir.padEnd(8)} ${String(g.length).padStart(11)} ${String(m).padStart(6)} ` +
          `${((100 * m) / Math.max(1, g.length)).toFixed(1).padStart(15)}`,
      )
    }
  }

  // --- P6.11 matching -------------------------------------------------------
  console.log('\n## P6.11 — matching geometrico\n')
  function matchedRate(
    label: string,
    withSymbol: boolean,
    withFamilies: boolean,
    pool: Cand[],
    hit: (c: Cand) => boolean,
  ): void {
    const key = (c: Cand) =>
      (withSymbol ? `${c.symbol}|` : '') +
      `${Math.floor(c.frac * 4)}|${excBucket(c.excursionAtr)}|${wickBucket(c.wickBody)}|${ageBucket(c.ageSinceReset)}` +
      (withFamilies ? `|${c.liveFamilies.length}` : '')
    const bucket = new Map<string, { b: Cand[]; s: Cand[] }>()
    for (const c of pool) {
      const k = key(c)
      if (!bucket.has(k)) bucket.set(k, { b: [], s: [] })
      ;(bull(c) ? bucket.get(k)!.b : bucket.get(k)!.s).push(c)
    }
    let w = 0
    let delta = 0
    let nb = 0
    let ns = 0
    let strata = 0
    let informative = 0
    for (const { b, s } of bucket.values()) {
      if (b.length === 0 || s.length === 0) continue
      const rb = b.filter(hit).length / b.length
      const rs = s.filter(hit).length / s.length
      const weight = (b.length * s.length) / (b.length + s.length)
      delta += weight * (rb - rs)
      w += weight
      nb += b.length
      ns += s.length
      strata += 1
      // A stratum where neither side ever fires carries no information about
      // the difference, only weight. Counting them is what keeps a diluted
      // estimator from being read as agreement.
      if (b.some(hit) || s.some(hit)) informative += 1
    }
    console.log(
      `  ${label.padEnd(38)} estratos ${String(strata).padStart(4)} (${String(informative).padStart(3)} com evento)  ` +
        `n(b)=${String(nb).padStart(5)} n(s)=${String(ns).padStart(5)}  ` +
        `delta = ${w > 0 ? ((100 * delta) / w).toFixed(1) : '—'}pp`,
    )
  }
  const rawB = rec.filter(bull)
  const rawS = rec.filter(bear)
  const rate = (rows: Cand[], hit: (c: Cand) => boolean) =>
    `${rows.filter(hit).length}/${rows.length} = ${((100 * rows.filter(hit).length) / Math.max(1, rows.length)).toFixed(1)}%`
  console.log('  taxas brutas, por etapa condicional:')
  console.log(
    `    reclaim -> PISO          bullish ${rate(rawB, (c) => c.isMark)}   bearish ${rate(rawS, (c) => c.isMark)}`,
  )
  console.log(
    `    reclaim -> exc+pavio     bullish ${rate(rawB, (c) => c.passExc && c.passWick)}   ` +
      `bearish ${rate(rawS, (c) => c.passExc && c.passWick)}`,
  )
  console.log(
    `    exc+pavio -> >=2 vivas   bullish ${rate(gated.filter(bull), (c) => c.passLive)}   ` +
      `bearish ${rate(gated.filter(bear), (c) => c.passLive)}`,
  )
  console.log('\n  o estimador estratificado, e quanto dele e informativo:')
  matchedRate('reclaim -> PISO, com simbolo', true, false, rec, (c) => c.isMark)
  matchedRate('reclaim -> PISO, sem simbolo', false, false, rec, (c) => c.isMark)
  matchedRate('reclaim -> PISO, +familias', false, true, rec, (c) => c.isMark)
  matchedRate(
    'reclaim -> exc+pavio, sem simbolo',
    false,
    false,
    rec,
    (c) => c.passExc && c.passWick,
  )
  matchedRate('exc+pavio -> >=2 vivas, sem simbolo', false, false, gated, (c) => c.passLive)

  // --- P6.12 outcome --------------------------------------------------------
  console.log('\n## P6.12 — outcome, so agora (h=5, controle casado)\n')
  function measure(rows: Cand[]): { ev: Agg; ctl: Agg } {
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const m of rows) {
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      if (o) add(ev, o)
      const series = atrCache.get(m.key)!
      const lo = Math.max(0, m.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - PRIMARY_H - 1, m.index + CONTROL_WINDOW)
      if (hi > lo) {
        for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
          const j = lo + Math.floor(random() * (hi - lo))
          const co = forward(d.candles, j, PRIMARY_H, m.direction, series[j] * d.candles[j].close)
          if (co) add(ctl, co)
        }
      }
    }
    return { ev, ctl }
  }
  console.log('  grupo                        n    MFE    MAE  MFE/MAE  MFE>MAE | controle      z')
  const marks = cands.filter((c) => c.isMark)
  for (const [label, rows] of [
    ['PISO bullish', marks.filter(bull)],
    ['PISO bearish', marks.filter(bear)],
    ['gate exc+pavio bullish', gated.filter(bull)],
    ['gate exc+pavio bearish', gated.filter(bear)],
  ] as [string, Cand[]][]) {
    const { ev, ctl } = measure(rows)
    const mfe = ev.n ? ev.mfe / ev.n : 0
    const mae = ev.n ? ev.mae / ev.n : 0
    console.log(
      `  ${label.padEnd(24)} ${String(ev.n).padStart(4)} ${mfe.toFixed(2).padStart(6)} ` +
        `${mae.toFixed(2).padStart(6)} ${(mae > 0 ? mfe / mae : 0).toFixed(2).padStart(8)} ` +
        `${((ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1) + '%').padStart(8)} | ` +
        `${((ctl.n ? (100 * ctl.won) / ctl.n : 0).toFixed(1) + '%').padStart(8)} ${zProp(ev, ctl).toFixed(2).padStart(6)}`,
    )
  }

  // --- P6.14 por simbolo ----------------------------------------------------
  console.log('\n## P6.14 — assimetria por simbolo\n')
  {
    const per = new Map<string, { b: number; s: number }>()
    for (const c of marks) {
      if (!per.has(c.symbol)) per.set(c.symbol, { b: 0, s: 0 })
      if (bull(c)) per.get(c.symbol)!.b += 1
      else per.get(c.symbol)!.s += 1
    }
    const rows = [...per.entries()].sort((a, b) => b[1].s - b[1].b - (a[1].s - a[1].b))
    const onlyBear = rows.filter((r) => r[1].b === 0 && r[1].s > 0).length
    const onlyBull = rows.filter((r) => r[1].s === 0 && r[1].b > 0).length
    console.log(
      `  simbolos com PISO H4: ${rows.length} — so bearish ${onlyBear}, so bullish ${onlyBull}, ` +
        `mistos ${rows.length - onlyBear - onlyBull}`,
    )
    console.log(
      `  mais bearish: ${rows.slice(0, 5).map(([s, v]) => `${s} ${v.b}/${v.s}`).join('  ')}`,
    )
    console.log(
      `  mais bullish: ${rows.slice(-5).map(([s, v]) => `${s} ${v.b}/${v.s}`).join('  ')}`,
    )
    // The same census at the gate, where the sample is large enough to mean
    // something — marks alone are 68 events across 44 symbols.
    const perGate = new Map<string, { b: number; s: number }>()
    for (const c of gated) {
      if (!perGate.has(c.symbol)) perGate.set(c.symbol, { b: 0, s: 0 })
      if (bull(c)) perGate.get(c.symbol)!.b += 1
      else perGate.get(c.symbol)!.s += 1
    }
    const ratios = [...perGate.values()]
      .filter((v) => v.b + v.s >= 3)
      .map((v) => v.b / (v.b + v.s))
    ratios.sort((a, b) => a - b)
    console.log(
      `  no gate exc+pavio, fracao bullish por simbolo (n>=3): mediana ${(100 * pct(ratios, 0.5)).toFixed(0)}%, ` +
        `p25 ${(100 * pct(ratios, 0.25)).toFixed(0)}%, p75 ${(100 * pct(ratios, 0.75)).toFixed(0)}%, ` +
        `simbolos ${ratios.length}`,
    )
    report.perSymbol = rows.map(([s, v]) => ({ symbol: s, bullish: v.b, bearish: v.s }))
  }

  // --- P6.15 temporal -------------------------------------------------------
  console.log('\n## P6.15 — a assimetria em quatro blocos temporais\n')
  console.log('  bloco   breach b/s     reclaim b/s    exc+pavio b/s    PISO b/s')
  for (let q = 0; q < 4; q += 1) {
    const g = cands.filter((c) => Math.floor(c.frac * 4) === q)
    const f = (sel: (c: Cand) => boolean) =>
      `${g.filter((c) => bull(c) && sel(c)).length}/${g.filter((c) => bear(c) && sel(c)).length}`
    console.log(
      `  Q${q + 1}     ${f(() => true).padEnd(14)} ${f((c) => c.reclaim).padEnd(15)} ` +
        `${f((c) => c.reclaim && c.passExc && c.passWick).padEnd(16)} ${f((c) => c.isMark)}`,
    )
  }

  // --- P6.13 casos ----------------------------------------------------------
  console.log('\n## P6.13 — casos reais (BTC / ETH / SOL, H4)\n')
  {
    const majors = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    const mine = cands.filter((c) => majors.includes(c.symbol))
    const show = (tag: string, c: Cand | undefined): void => {
      if (!c) {
        console.log(`  ${tag} —`)
        return
      }
      const d = fixtures.get(c.key)!
      const o = c.isMark ? forward(d.candles, c.index, PRIMARY_H, c.direction, c.atr) : null
      console.log(
        `  ${tag} ${c.symbol.padEnd(8)} ${c.timestamp}  ${c.direction.padEnd(7)} ` +
          `exc=${c.excursionAtr.toFixed(2)} pavio=${c.wickBody.toFixed(1)} sigma=${c.sigmaAtr.toFixed(2)} ` +
          `idade=${String(c.ageSinceReset).padStart(2)} viva=[${c.liveFamilies.join('+') || '—'}] ` +
          `cartog=[${c.mapFamilies.join('+') || '—'}]` +
          (o ? ` -> h5 MFE=${o.mfe.toFixed(2)} MAE=${o.mae.toFixed(2)} ${o.won ? 'OK' : 'falhou'}` : ''),
      )
    }
    show(
      'A) bullish morre em excursion ',
      mine.find((c) => bull(c) && c.reclaim && !c.passExc && c.passWick && c.passLive),
    )
    show(
      'B) bullish morre em families  ',
      mine.find((c) => bull(c) && c.reclaim && c.passExc && c.passWick && !c.passLive),
    )
    show('C) bearish equivalente passa  ', mine.find((c) => bear(c) && c.isMark))
    const failedBull = (c: Cand): boolean => {
      if (!bull(c) || !c.isMark) return false
      const d = fixtures.get(c.key)!
      const o = forward(d.candles, c.index, PRIMARY_H, c.direction, c.atr)
      return o !== null && !o.won
    }
    // The majors may hold no bullish H4 mark at all — which is itself the
    // finding, so it is stated rather than papered over with a silent dash.
    const localD = mine.find(failedBull)
    show('D) PISO bullish que falha     ', localD)
    if (!localD) {
      console.log('     (nenhum PISO bullish H4 em BTC/ETH/SOL — exemplo do painel inteiro:)')
      show('D\') idem, painel inteiro    ', cands.find(failedBull))
    }
    show(
      'E) PISO bearish que funciona  ',
      mine.find((c) => {
        if (!bear(c) || !c.isMark) return false
        const d = fixtures.get(c.key)!
        const o = forward(d.candles, c.index, PRIMARY_H, c.direction, c.atr)
        return o !== null && o.won
      }),
    )
  }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
}

main()
