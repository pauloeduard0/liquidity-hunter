/**
 * PISO audit harness — the `⛨N` defended-level mark, measured.
 *
 * Runs the **shipped** rule, not a copy of it: `buildDefenceLevels`,
 * `diagnoseDefendedMarks` and `structureLineEndTime` are imported from the same
 * modules `MainChart` renders from, and the `standingUntil` closure below is
 * the same one the component passes. Nothing about the rule is restated here,
 * which is the whole point — the previous attempt to measure a frontend reading
 * would have meant porting it to Python and measuring the port.
 *
 * What it answers
 * ---------------
 * - the funnel: how many candles reach each gate and how many each one removes;
 * - the near-miss classes: candidates that failed exactly one gate, and by how
 *   much;
 * - a descriptive forward reading (MFE/MAE in ATR) for marks and for each
 *   near-miss class, against a control matched on symbol, timeframe, direction
 *   and time;
 * - how much of the reading rests on inputs that are not causal.
 *
 * What it is not
 * --------------
 * Not an entry study. The forward numbers describe what happened after a level
 * was defended; they are not a setup, and this family already measured negative
 * for entry edge under a direction-matched control in `research/raid_reversal.py`.
 * No threshold is chosen here and none is changed.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types research/pisoAudit.ts
 *   node --experimental-strip-types research/pisoAudit.ts --json baseline.json
 *
 * Fixtures come from `research/captureFixtures.sh` and are gitignored.
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import {
  buildDefenceLevels,
  diagnoseDefendedMarks,
  meanTrueRangePctSeries,
  type DefenceCandidate,
  type DefenceLevel,
} from '../src/utils/defendedLevels.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { buildPhase, buildRibbon } from '../src/utils/tideRibbon.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const

/** Controls drawn per event, and how far in time they may be drawn from. */
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250

// ---------------------------------------------------------------------------
// The production wiring, reproduced exactly once.
// ---------------------------------------------------------------------------

/**
 * `MainChart`'s own `standingUntil`, verbatim.
 *
 * A structural level stands exactly as long as its line is drawn, so what
 * counts as evidence is what the user can see. Reproducing the closure is
 * unavoidable — it is three lines of glue in a render body — but every rule it
 * calls is imported.
 */
function standingUntilFor(data: DashboardData): (event: MarketStructure) => string | null {
  const scopeEvents = data.internal_structure_events
  const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
  return (event: MarketStructure) => {
    const end = structureLineEndTime(event, scopeEvents, lastCandleTime)
    if (end >= lastCandleTime) return null
    return scopeEvents.find((other) => toChartTime(other.timestamp) === end)?.timestamp ?? null
  }
}

/** The same levels with every lifespan removed — used only to attribute a
 *  families failure to the level lifecycle rather than to the cartography. */
function timelessLevels(levels: DefenceLevel[]): DefenceLevel[] {
  return levels.map((l) => ({ ...l, born: '0000', died: null }))
}

// ---------------------------------------------------------------------------
// Forward reading.
// ---------------------------------------------------------------------------

/** A defence at the top edge argues down; at the bottom edge, up. */
type Direction = 'bullish' | 'bearish'

interface Outcome {
  mfeAtr: number
  maeAtr: number
  ratio: number
  won: boolean
  moveAtr: number
  barsToMfe: number
}

/**
 * MFE / MAE in ATR over `h` candles, read in the direction the defence argues
 * for, from the mark candle's close.
 *
 * Scale-free on purpose: both tails widening is volatility, and a mean return
 * cannot tell that apart from an edge.
 */
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
  let barsToMfe = 0
  for (let k = i + 1; k <= i + h; k += 1) {
    const up = (candles[k].high - entry) / atr
    const down = (entry - candles[k].low) / atr
    const fav = direction === 'bullish' ? up : down
    const adv = direction === 'bullish' ? down : up
    if (fav > mfe) {
      mfe = fav
      barsToMfe = k - i
    }
    if (adv > mae) mae = adv
  }
  const move = ((candles[i + h].close - entry) / atr) * (direction === 'bullish' ? 1 : -1)
  return {
    mfeAtr: mfe,
    maeAtr: mae,
    ratio: mae > 0 ? mfe / mae : mfe > 0 ? Infinity : 1,
    won: mfe > mae,
    moveAtr: move,
    barsToMfe,
  }
}

/** Deterministic RNG, so a rerun of the audit reproduces its own controls. */
function rng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 0x100000000
  }
}

// ---------------------------------------------------------------------------
// Aggregation.
// ---------------------------------------------------------------------------

interface Agg {
  n: number
  mfe: number[]
  mae: number[]
  move: number[]
  won: number
  bars: number[]
}

const emptyAgg = (): Agg => ({ n: 0, mfe: [], mae: [], move: [], won: 0, bars: [] })

function push(a: Agg, o: Outcome): void {
  a.n += 1
  a.mfe.push(o.mfeAtr)
  a.mae.push(o.maeAtr)
  a.move.push(o.moveAtr)
  a.bars.push(o.barsToMfe)
  if (o.won) a.won += 1
}

const mean = (xs: number[]): number => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : 0)

function summarize(a: Agg): Record<string, number> {
  return {
    n: a.n,
    mfe_atr: +mean(a.mfe).toFixed(3),
    mae_atr: +mean(a.mae).toFixed(3),
    mfe_over_mae: +(mean(a.mae) > 0 ? mean(a.mfe) / mean(a.mae) : 0).toFixed(3),
    win_rate: +(a.n ? (100 * a.won) / a.n : 0).toFixed(1),
    move_atr: +mean(a.move).toFixed(3),
    bars_to_mfe: +mean(a.bars).toFixed(1),
  }
}

// ---------------------------------------------------------------------------
// Per-fixture pass.
// ---------------------------------------------------------------------------

interface Row extends DefenceCandidate {
  symbol: string
  timeframe: string
  index: number
  direction: Direction
  /** Fails `families` only because level lifespans retired the evidence. */
  lifecycleKilled: boolean
  /** The mark needs a `fair` level to reach two families... */
  needsFair: boolean
  /** ...and the profile that produced it looked past this candle. */
  fairIsHindsight: boolean
  /** Sits on the last candle of the window — still forming when drawn. */
  onOpenCandle: boolean
  /** Within 10% of a gate boundary: a late tick could flip it. */
  fragile: boolean
  tidePhase: number | null
  tideTrend: string | null
  tideConviction: number | null
}

function auditFixture(name: string, data: DashboardData, causalAtr: boolean): Row[] {
  const standingUntil = standingUntilFor(data)
  const levels = buildDefenceLevels(data, standingUntil, { causalAtr })
  const cands = diagnoseDefendedMarks(data, levels, { causalAtr })

  const noLifecycle = diagnoseDefendedMarks(data, timelessLevels(levels), { causalAtr })
  const noLifecycleByTs = new Map(noLifecycle.map((c) => [c.timestamp, c]))

  // Families reachable without the `fair` sources, to see which marks lean on
  // the volume profile — the one input built with hindsight.
  const noFair = diagnoseDefendedMarks(
    data,
    levels.filter((l) => l.family !== 'fair'),
    { causalAtr },
  )
  const noFairByTs = new Map(noFair.map((c) => [c.timestamp, c]))

  const indexByTs = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const lastTs = data.candles[data.candles.length - 1].timestamp
  const vpEnd = data.volume_profile?.end_timestamp ?? null

  const phase = new Map(buildPhase(data).map((p) => [p.timestamp, p]))
  const ribbon = new Map(buildRibbon(data).map((b) => [b.timestamp, b]))

  return cands.map((c) => {
    const lifecycleKilled =
      c.failed.includes('families') && (noLifecycleByTs.get(c.timestamp)?.families.length ?? 0) >= 2
    const withoutFair = noFairByTs.get(c.timestamp)?.families.length ?? 0
    const needsFair = c.families.includes('fair') && withoutFair < 2
    const p = phase.get(c.timestamp)
    const r = ribbon.get(c.timestamp)
    return {
      ...c,
      symbol: data.symbol,
      timeframe: data.timeframe,
      index: indexByTs.get(c.timestamp) ?? -1,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      lifecycleKilled,
      needsFair,
      fairIsHindsight: needsFair && vpEnd !== null && vpEnd > c.timestamp,
      onOpenCandle: c.timestamp === lastTs,
      fragile:
        Math.abs(c.excursionAtr - 1.0) < 0.1 || Math.abs(c.wickBodyRatio - 2.0) < 0.2,
      tidePhase: p ? +p.value.toFixed(1) : null,
      tideTrend: r?.trend ?? null,
      tideConviction: r ? +r.conviction.toFixed(3) : null,
    }
  })
}

// ---------------------------------------------------------------------------
// Report.
// ---------------------------------------------------------------------------

function pct(a: number, b: number): string {
  return b > 0 ? `${((100 * a) / b).toFixed(1)}%` : '—'
}

function classOf(r: Row): string {
  if (r.failed.length === 0) return 'PISO'
  if (r.failed.length > 1) return 'E:multiplos'
  if (r.failed[0] === 'excursion') return 'A:so_excursion'
  if (r.failed[0] === 'wick_body') return 'B:so_wick_body'
  return r.lifecycleKilled ? 'D:so_lifecycle' : 'C:so_families'
}

function bucketExcursion(v: number): string {
  if (v < 0.25) return '<0.25'
  if (v < 0.5) return '0.25-0.49'
  if (v < 0.75) return '0.50-0.74'
  if (v < 1.0) return '0.75-0.99'
  if (v < 1.5) return '1.00-1.49'
  return '>=1.50'
}

function bucketWick(v: number): string {
  if (v < 1) return '<1'
  if (v < 1.5) return '1.0-1.49'
  if (v < 2) return '1.5-1.99'
  if (v <= 3) return '2.0-3.0'
  return '>3'
}

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()
  if (files.length === 0) {
    console.error(`no fixtures in ${FIXTURE_DIR} — run research/captureFixtures.sh first`)
    process.exit(1)
  }

  const report: Record<string, unknown> = {}
  const allRows: Row[] = []
  const shippedMarkTs = new Map<string, Set<string>>()
  const causalMarkTs = new Map<string, Set<string>>()
  const fixtures = new Map<string, DashboardData>()

  console.log('# PISO — auditoria P1\n')
  console.log(`fixtures: ${files.length} (${FIXTURE_DIR})\n`)

  // --- funil por fixture, nos dois modos -----------------------------------
  console.log('## Marcas: modo publicado vs ATR causal\n')
  console.log('fixture                velas  candidatos  PISO(pub)  PISO(causal)  mudaram')
  let totalShipped = 0
  let totalCausal = 0
  let totalChanged = 0
  let totalCandidates = 0
  let totalCandles = 0
  const skipped: string[] = []
  for (const f of files) {
    // A fixture can be a 500 body rather than a payload: two symbols in the
    // universe have a dead feed and the dashboard route fails on them. Skip
    // and say so, rather than measuring 209 symbols while reporting 211.
    let data: DashboardData
    try {
      data = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(data.candles) || data.candles.length === 0) throw new Error('no candles')
    } catch {
      skipped.push(f)
      continue
    }
    fixtures.set(f, data)
    const shipped = auditFixture(f, data, false)
    const causal = auditFixture(f, data, true)
    const s = new Set(shipped.filter((r) => r.failed.length === 0).map((r) => r.timestamp))
    const c = new Set(causal.filter((r) => r.failed.length === 0).map((r) => r.timestamp))
    shippedMarkTs.set(f, s)
    causalMarkTs.set(f, c)
    const changed = [...new Set([...s, ...c])].filter((t) => s.has(t) !== c.has(t)).length
    totalShipped += s.size
    totalCausal += c.size
    totalChanged += changed
    totalCandidates += causal.length
    totalCandles += data.candles.length
    allRows.push(...causal)
    console.log(
      `${f.replace('.json', '').padEnd(20)} ${String(data.candles.length).padStart(6)} ` +
        `${String(causal.length).padStart(11)} ${String(s.size).padStart(10)} ` +
        `${String(c.size).padStart(13)} ${String(changed).padStart(8)}`,
    )
  }
  console.log(
    `${'TOTAL'.padEnd(20)} ${String(totalCandles).padStart(6)} ${String(totalCandidates).padStart(11)} ` +
      `${String(totalShipped).padStart(10)} ${String(totalCausal).padStart(13)} ${String(totalChanged).padStart(8)}`,
  )
  if (skipped.length > 0) {
    console.log(`\nfixtures ignoradas (payload invalido): ${skipped.length} — ${skipped.join(', ')}`)
  }
  report.skipped = skipped
  report.marks = {
    shipped: totalShipped,
    causal: totalCausal,
    changed: totalChanged,
    candidates: totalCandidates,
    candles: totalCandles,
  }

  // --- P1.12 funil ---------------------------------------------------------
  console.log('\n## Funil (ATR causal, painel inteiro)\n')
  const reachedEdge = allRows.length
  const passExc = allRows.filter((r) => !r.failed.includes('excursion'))
  const passWick = passExc.filter((r) => !r.failed.includes('wick_body'))
  const passFam = passWick.filter((r) => !r.failed.includes('families'))
  const funnel = [
    ['velas totais', totalCandles, totalCandles],
    ['cruzam +-1s e fecham de volta', reachedEdge, totalCandles],
    ['+ excursao >= 1.0 ATR', passExc.length, reachedEdge],
    ['+ pavio/corpo >= 2.0', passWick.length, passExc.length],
    ['+ familias >= 2', passFam.length, passWick.length],
  ] as [string, number, number][]
  console.log('etapa                            n        % do anterior   removidos')
  for (const [label, n, prev] of funnel) {
    console.log(
      `${label.padEnd(32)} ${String(n).padStart(6)}   ${pct(n, prev).padStart(8)}      ${String(prev - n).padStart(6)}`,
    )
  }
  report.funnel = funnel.map(([label, n, prev]) => ({ label, n, prev }))

  // por timeframe e direcao
  console.log('\n### por timeframe / direcao\n')
  console.log('recorte          candidatos  passa_exc  passa_wick  PISO   PISO/1000 velas')
  const cuts = new Map<string, Row[]>()
  for (const r of allRows) {
    for (const key of [r.timeframe, `${r.timeframe} ${r.direction}`]) {
      if (!cuts.has(key)) cuts.set(key, [])
      cuts.get(key)!.push(r)
    }
  }
  const candlesByTf = new Map<string, number>()
  for (const [f, d] of fixtures) {
    void f
    candlesByTf.set(d.timeframe, (candlesByTf.get(d.timeframe) ?? 0) + d.candles.length)
  }
  for (const key of [...cuts.keys()].sort()) {
    const rs = cuts.get(key)!
    const marks = rs.filter((r) => r.failed.length === 0).length
    const tf = key.split(' ')[0]
    const per1000 = ((1000 * marks) / (candlesByTf.get(tf) ?? 1)).toFixed(2)
    console.log(
      `${key.padEnd(16)} ${String(rs.length).padStart(10)} ` +
        `${String(rs.filter((r) => !r.failed.includes('excursion')).length).padStart(10)} ` +
        `${String(rs.filter((r) => r.failed.every((g) => g === 'families')).length).padStart(11)} ` +
        `${String(marks).padStart(5)}   ${per1000.padStart(8)}`,
    )
  }

  // --- P1.13 near-miss -----------------------------------------------------
  console.log('\n## Classes de near-miss (ATR causal)\n')
  const byClass = new Map<string, Row[]>()
  for (const r of allRows) {
    const k = classOf(r)
    if (!byClass.has(k)) byClass.set(k, [])
    byClass.get(k)!.push(r)
  }
  console.log('classe             n       % candidatos   falta (mediana)')
  for (const k of [...byClass.keys()].sort()) {
    const rs = byClass.get(k)!
    const shortfalls = rs
      .map((r) =>
        r.failed[0] === 'excursion'
          ? r.excursionShortfall
          : r.failed[0] === 'wick_body'
            ? r.wickBodyShortfall
            : r.familiesShortfall,
      )
      .filter((x): x is number => x !== null)
      .sort((a, b) => a - b)
    const med = shortfalls.length ? shortfalls[Math.floor(shortfalls.length / 2)].toFixed(2) : '—'
    console.log(
      `${k.padEnd(18)} ${String(rs.length).padStart(5)}   ${pct(rs.length, allRows.length).padStart(10)}       ${med.padStart(6)}`,
    )
  }
  report.near_miss = Object.fromEntries([...byClass].map(([k, v]) => [k, v.length]))

  // --- P1.14 qualidade vs controle ----------------------------------------
  console.log('\n## Leitura para a frente (descritiva) — ATR causal\n')
  const random = rng(20260910)
  const causalAtrPct = new Map<string, number[]>()
  for (const [f, d] of fixtures) causalAtrPct.set(f, meanTrueRangePctSeries(d.candles))

  const groupAgg = new Map<string, Map<number, Agg>>()
  const controlAgg = new Map<string, Map<number, Agg>>()
  const bump = (
    store: Map<string, Map<number, Agg>>,
    key: string,
    h: number,
    o: Outcome,
  ): void => {
    if (!store.has(key)) store.set(key, new Map())
    const byH = store.get(key)!
    if (!byH.has(h)) byH.set(h, emptyAgg())
    push(byH.get(h)!, o)
  }

  for (const [f, d] of fixtures) {
    const atrSeries = causalAtrPct.get(f)!
    const rows = allRows.filter((r) => `${r.symbol}_${r.timeframe}.json` === f)
    for (const r of rows) {
      if (r.index < 0) continue
      const atr = atrSeries[r.index] * d.candles[r.index].close
      const key = classOf(r)
      for (const h of HORIZONS) {
        const o = forward(d.candles, r.index, h, r.direction, atr)
        if (o) bump(groupAgg, key, h, o)
        // Control: same symbol, same timeframe, same direction, drawn from the
        // same stretch of time — the direction match is what an earlier study
        // in this project learned it could not skip.
        for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
          const lo = Math.max(0, r.index - CONTROL_WINDOW)
          const hi = Math.min(d.candles.length - h - 1, r.index + CONTROL_WINDOW)
          if (hi <= lo) break
          const j = lo + Math.floor(random() * (hi - lo))
          const ca = atrSeries[j] * d.candles[j].close
          const co = forward(d.candles, j, h, r.direction, ca)
          if (co) bump(controlAgg, key, h, co)
        }
      }
    }
  }

  console.log('grupo             h    n     MFE    MAE   MFE/MAE  MFE>MAE  move   -> controle (MFE/MAE, MFE>MAE)')
  for (const key of [...groupAgg.keys()].sort()) {
    for (const h of HORIZONS) {
      const a = groupAgg.get(key)?.get(h)
      const c = controlAgg.get(key)?.get(h)
      if (!a || a.n === 0) continue
      const s = summarize(a)
      const cs = c ? summarize(c) : null
      console.log(
        `${key.padEnd(17)} ${String(h).padStart(2)} ${String(s.n).padStart(5)} ` +
          `${s.mfe_atr.toFixed(2).padStart(6)} ${s.mae_atr.toFixed(2).padStart(6)} ` +
          `${s.mfe_over_mae.toFixed(2).padStart(8)} ${(s.win_rate + '%').padStart(7)} ` +
          `${s.move_atr.toFixed(2).padStart(6)}   ` +
          (cs ? `${cs.mfe_over_mae.toFixed(2)}, ${cs.win_rate}% (n=${cs.n})` : '—'),
      )
    }
  }
  report.outcomes = Object.fromEntries(
    [...groupAgg].map(([k, byH]) => [
      k,
      Object.fromEntries(
        [...byH].map(([h, a]) => [
          h,
          { event: summarize(a), control: controlAgg.get(k)?.get(h) ? summarize(controlAgg.get(k)!.get(h)!) : null },
        ]),
      ),
    ]),
  )

  // --- P1.15/16/17/18 eixos descritivos ------------------------------------
  const axis = (
    label: string,
    bucketOf: (r: Row) => string,
    rows: Row[],
    h = 20,
  ): void => {
    console.log(`\n### ${label}\n`)
    const b = new Map<string, Agg>()
    for (const r of rows) {
      const d = fixtures.get(`${r.symbol}_${r.timeframe}.json`)
      if (!d || r.index < 0) continue
      const atr = causalAtrPct.get(`${r.symbol}_${r.timeframe}.json`)![r.index] * d.candles[r.index].close
      const o = forward(d.candles, r.index, h, r.direction, atr)
      if (!o) continue
      const k = bucketOf(r)
      if (!b.has(k)) b.set(k, emptyAgg())
      push(b.get(k)!, o)
    }
    console.log(`bucket           n      MFE    MAE   MFE/MAE  MFE>MAE  move  (h=${h})`)
    for (const k of [...b.keys()].sort()) {
      const s = summarize(b.get(k)!)
      console.log(
        `${k.padEnd(15)} ${String(s.n).padStart(5)} ${s.mfe_atr.toFixed(2).padStart(6)} ` +
          `${s.mae_atr.toFixed(2).padStart(6)} ${s.mfe_over_mae.toFixed(2).padStart(8)} ` +
          `${(s.win_rate + '%').padStart(7)} ${s.move_atr.toFixed(2).padStart(6)}`,
      )
    }
    report[`axis_${label.replace(/\W+/g, '_')}`] = Object.fromEntries(
      [...b].map(([k, a]) => [k, summarize(a)]),
    )
  }

  console.log('\n## Eixos descritivos (nada e alterado — so medido)')
  axis('P1.15 excursao (todos os candidatos)', (r) => bucketExcursion(r.excursionAtr), allRows)
  axis(
    'P1.16 pavio/corpo (so quem ja passou a excursao)',
    (r) => bucketWick(r.wickBodyRatio),
    passExc,
  )
  axis(
    'P1.17 familias (so quem ja passou excursao e pavio)',
    (r) => `${r.families.length} fam`,
    passWick,
  )
  axis(
    'P1.17b combinacao de familias (PISO apenas)',
    (r) => r.families.join('+') || '(nenhuma)',
    passFam,
  )
  axis(
    'P1.18 consumed (PISO apenas)',
    (r) => (r.consumed === 0 ? 'consumed=0' : r.consumed === 1 ? 'consumed=1' : 'consumed>=2'),
    passFam,
  )
  axis(
    'P1.19 fase da Tide no candle (PISO apenas)',
    (r) =>
      r.tidePhase === null
        ? 'sem fase'
        : Math.abs(r.tidePhase) < 50
          ? '|fase|<50'
          : Math.abs(r.tidePhase) < 100
            ? '|fase| 50-99'
            : '|fase|>=100',
    passFam,
  )
  axis(
    'P1.19b tendencia da Tide vs lado defendido (PISO apenas)',
    (r) => (r.tideTrend === null ? 'sem fita' : r.tideTrend === r.direction ? 'a favor' : r.tideTrend === 'neutral' ? 'neutra' : 'contra'),
    passFam,
  )

  // --- P1.7 / P1.8 causalidade residual -------------------------------------
  console.log('\n## Limitacoes causais que permanecem\n')
  const marks = passFam
  const leanFair = marks.filter((r) => r.needsFair).length
  const hindsight = marks.filter((r) => r.fairIsHindsight).length
  const open = marks.filter((r) => r.onOpenCandle).length
  const fragile = marks.filter((r) => r.fragile).length
  console.log(`PISO no painel ......................... ${marks.length}`)
  console.log(`  dependem de um nivel 'fair' p/ 2 fam . ${leanFair}  (${pct(leanFair, marks.length)})`)
  console.log(`  ... e esse perfil olhou depois do candle ${hindsight}  (${pct(hindsight, marks.length)})`)
  console.log(`  no candle ainda aberto (PROVISIONAL) . ${open}  (${pct(open, marks.length)})`)
  console.log(`  a <10% de um limiar (fragil a 1 tick)  ${fragile}  (${pct(fragile, marks.length)})`)
  report.causality = { marks: marks.length, leanFair, hindsight, open, fragile }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
}

main()
