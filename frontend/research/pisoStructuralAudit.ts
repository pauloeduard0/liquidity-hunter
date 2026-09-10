/**
 * P3 — is the `structural` family a real qualifier, or a composition artefact?
 *
 * What the P2.0 audit left open. Holding the family count fixed at three, marks
 * carrying a structural level measured 63.4% MFE>MAE (n=41) against 44.4%
 * without one (n=9); with structural held fixed, going from two families to
 * three moved 54.1% to 63.6% (z=2.16, the only z above 2 in that whole run).
 * Neither number is large enough to act on, and both are exactly the shape a
 * small sample produces by accident. This script tries to break them.
 *
 * The design question it is built around
 * --------------------------------------
 * A mark with a structural level is not otherwise identical to one without. It
 * may sit on a bigger candle, in a different timeframe, in a different quarter
 * of the window. Comparing the two groups raw would credit `structural` with
 * whatever else differs. So the headline comparison is **stratified**: events
 * are matched to each other on timeframe, direction, family count, excursion
 * bucket, wick/body bucket and temporal block, and only strata containing both
 * groups contribute. That is the P3.14 requirement, and it is the difference
 * between measuring a qualifier and measuring candle magnitude.
 *
 * The random matched control (symbol / timeframe / direction / period) is still
 * reported alongside, because the stratified difference says whether structural
 * separates the two groups while the control says whether either group
 * separates from the market at all.
 *
 * Nothing is changed. `MIN_FAMILIES`, `MIN_EXCURSION_ATR`, `MIN_WICK_BODY`,
 * `LEVEL_TOLERANCE_ATR`, the level lifecycle, `volume_profile` and the Tide all
 * stay exactly as they ship, and the rule runs in the causal mode now in
 * production.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoStructuralAudit.ts --json research/piso_structural_baseline.json
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import {
  buildDefenceLevels,
  diagnoseDefendedMarks,
  meanTrueRangePctSeries,
  type DefenceLevel,
  type LevelFamily,
} from '../src/utils/defendedLevels.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { buildPhase } from '../src/utils/tideRibbon.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const
const PRIMARY_H = 5
const DISCOVERY_FRAC = 0.7
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250
/**
 * How far outside the shipped tolerance the near-miss probe looks, in ATR.
 *
 * Diagnostic only (P3.15): it asks whether candidates that fell one family
 * short had a structural reference sitting just beyond the 0.5 ATR half-width.
 * `LEVEL_TOLERANCE_ATR` is not touched.
 */
const NEAR_STRUCTURAL_ATR = 2.0

// ---------------------------------------------------------------------------
// Production wiring — identical to `MainChart` and to the other two harnesses.
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

/**
 * Attaches provenance to the structural levels the rule built.
 *
 * Deliberately does NOT restate the filter that decides which events become
 * levels — that is the rule's business and copying it here is how a harness
 * starts measuring itself. Instead each structural level is matched back to its
 * event by the two things that produced it: the `born` timestamp and the price
 * at the centre of the half-width.
 */
function structuralProvenance(
  data: DashboardData,
  levels: DefenceLevel[],
): Map<DefenceLevel, MarketStructure> {
  const out = new Map<DefenceLevel, MarketStructure>()
  const byTime = new Map<string, MarketStructure[]>()
  for (const e of data.internal_structure_events) {
    if (!byTime.has(e.timestamp)) byTime.set(e.timestamp, [])
    byTime.get(e.timestamp)!.push(e)
  }
  for (const l of levels) {
    if (l.family !== 'structural') continue
    const centre = (l.low + l.high) / 2
    const candidates = byTime.get(l.born) ?? []
    let best: MarketStructure | null = null
    let bestGap = Infinity
    for (const e of candidates) {
      if (e.reference_price_level == null) continue
      const gap = Math.abs(e.reference_price_level - centre)
      if (gap < bestGap) {
        bestGap = gap
        best = e
      }
    }
    if (best && bestGap <= Math.max(1e-9, Math.abs(centre) * 1e-9)) out.set(l, best)
  }
  return out
}

// ---------------------------------------------------------------------------
// Forward reading.
// ---------------------------------------------------------------------------

type Direction = 'bullish' | 'bearish'

interface Outcome {
  mfe: number
  mae: number
  won: boolean
  move: number
  bars: number
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
  let bars = 0
  for (let k = i + 1; k <= i + h; k += 1) {
    const up = (candles[k].high - entry) / atr
    const down = (entry - candles[k].low) / atr
    const fav = direction === 'bullish' ? up : down
    const adv = direction === 'bullish' ? down : up
    if (fav > mfe) {
      mfe = fav
      bars = k - i
    }
    if (adv > mae) mae = adv
  }
  return {
    mfe,
    mae,
    won: mfe > mae,
    move: ((candles[i + h].close - entry) / atr) * (direction === 'bullish' ? 1 : -1),
    bars,
  }
}

function rng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 0x100000000
  }
}

// ---------------------------------------------------------------------------
// Stats.
// ---------------------------------------------------------------------------

interface Agg {
  n: number
  mfe: number
  mae: number
  move: number
  bars: number
  won: number
}
const emptyAgg = (): Agg => ({ n: 0, mfe: 0, mae: 0, move: 0, bars: 0, won: 0 })
function add(a: Agg, o: Outcome): void {
  a.n += 1
  a.mfe += o.mfe
  a.mae += o.mae
  a.move += o.move
  a.bars += o.bars
  if (o.won) a.won += 1
}
interface Stat {
  n: number
  mfe: number
  mae: number
  ratio: number
  win: number
  move: number
  bars: number
}
function stat(a: Agg): Stat {
  const mfe = a.n ? a.mfe / a.n : 0
  const mae = a.n ? a.mae / a.n : 0
  return {
    n: a.n,
    mfe,
    mae,
    ratio: mae > 0 ? mfe / mae : 0,
    win: a.n ? (100 * a.won) / a.n : 0,
    move: a.n ? a.move / a.n : 0,
    bars: a.n ? a.bars / a.n : 0,
  }
}
function zProp(a: Agg, c: Agg): number {
  if (a.n === 0 || c.n === 0) return 0
  const p = (a.won + c.won) / (a.n + c.n)
  const se = Math.sqrt(p * (1 - p) * (1 / a.n + 1 / c.n))
  return se > 0 ? (a.won / a.n - c.won / c.n) / se : 0
}

// ---------------------------------------------------------------------------
// Marks.
// ---------------------------------------------------------------------------

interface Mark {
  symbol: string
  timeframe: string
  timestamp: string
  index: number
  direction: Direction
  families: LevelFamily[]
  nFam: number
  hasStructural: boolean
  hasFair: boolean
  excursionAtr: number
  wickBodyRatio: number
  frac: number
  atr: number
  /** Nearest structural level's distance to the wick extreme, in ATR. */
  structDistAtr: number | null
  /** `break_of_structure` / `change_of_character` of the nearest structural. */
  structEvent: string | null
  /** Candles between the structural event and this mark. */
  structAge: number | null
  /** The structural level's `died`: null means "line still drawn at window end". */
  structDiedOpen: boolean | null
}

const excBucket = (v: number): string =>
  v < 1.25 ? 'exc1.0-1.25' : v < 1.75 ? 'exc1.25-1.75' : 'exc1.75+'
const wickBucket = (v: number): string => (v < 3 ? 'wick2-3' : v < 5 ? 'wick3-5' : 'wick5+')
const distBucket = (v: number): string =>
  v < 0.25 ? '0-0.25' : v < 0.5 ? '0.25-0.5' : v < 1.0 ? '0.5-1.0' : '>1.0'
const ageBucket = (v: number): string =>
  v <= 10 ? '0-10' : v <= 30 ? '11-30' : v <= 100 ? '31-100' : '100+'

interface Panel {
  marks: Mark[]
  /** Candidates that cleared excursion + wick but hold exactly one family. */
  oneFamily: {
    symbol: string
    timeframe: string
    index: number
    direction: Direction
    family: LevelFamily
    /** Distance from the swept range to the nearest structural level, in ATR;
     *  0 would mean inside, but those became two families and are not here. */
    nearestStructAtr: number | null
    atr: number
    frac: number
  }[]
}

function panelFor(data: DashboardData): Panel {
  const levels = buildDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const cands = diagnoseDefendedMarks(data, levels, { causalAtr: true })
  const prov = structuralProvenance(data, levels)
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const atrSeries = meanTrueRangePctSeries(data.candles)
  const n = data.candles.length
  const structLevels = levels.filter((l) => l.family === 'structural')

  const marks: Mark[] = []
  const oneFamily: Panel['oneFamily'] = []

  for (const c of cands) {
    const i = idx.get(c.timestamp)
    if (i === undefined) continue
    const atr = atrSeries[i] * data.candles[i].close
    const low = Math.min(c.edge, c.price)
    const high = Math.max(c.edge, c.price)
    const alive = (l: DefenceLevel) =>
      l.born <= c.timestamp && (l.died === null || l.died >= c.timestamp)

    if (c.failed.length === 0) {
      // Nearest structural level actually inside the swept range.
      let best: DefenceLevel | null = null
      let bestGap = Infinity
      for (const l of structLevels) {
        if (l.high < low || l.low > high || !alive(l)) continue
        const centre = (l.low + l.high) / 2
        const gap = Math.abs(centre - c.price)
        if (gap < bestGap) {
          bestGap = gap
          best = l
        }
      }
      const e = best ? prov.get(best) : undefined
      marks.push({
        symbol: data.symbol,
        timeframe: data.timeframe,
        timestamp: c.timestamp,
        index: i,
        direction: c.side === 'top' ? 'bearish' : 'bullish',
        families: c.families,
        nFam: c.families.length,
        hasStructural: c.families.includes('structural'),
        hasFair: c.families.includes('fair'),
        excursionAtr: c.excursionAtr,
        wickBodyRatio: c.wickBodyRatio,
        frac: i / n,
        atr,
        structDistAtr: best && atr > 0 ? bestGap / atr : null,
        structEvent: e?.event ?? null,
        structAge: best ? i - (idx.get(best.born) ?? i) : null,
        structDiedOpen: best ? best.died === null : null,
      })
      continue
    }

    // P3.15: cleared excursion and wick, exactly one family standing.
    if (c.failed.length === 1 && c.failed[0] === 'families' && c.families.length === 1) {
      let nearest: number | null = null
      for (const l of structLevels) {
        if (!alive(l)) continue
        const gap = l.high < low ? low - l.high : l.low > high ? l.low - high : 0
        if (nearest === null || gap < nearest) nearest = gap
      }
      oneFamily.push({
        symbol: data.symbol,
        timeframe: data.timeframe,
        index: i,
        direction: c.side === 'top' ? 'bearish' : 'bullish',
        family: c.families[0],
        nearestStructAtr: nearest !== null && atr > 0 ? nearest / atr : null,
        atr,
        frac: i / n,
      })
    }
  }
  return { marks, oneFamily }
}

// ---------------------------------------------------------------------------
// Report.
// ---------------------------------------------------------------------------

function fmt(s: Stat): string {
  return (
    `${String(s.n).padStart(5)} ${s.mfe.toFixed(2).padStart(6)} ${s.mae.toFixed(2).padStart(6)} ` +
    `${s.ratio.toFixed(2).padStart(7)} ${(s.win.toFixed(1) + '%').padStart(7)} ` +
    `${s.move.toFixed(2).padStart(6)} ${s.bars.toFixed(1).padStart(5)}`
  )
}
const HEAD =
  'grupo                        n    MFE    MAE  MFE/MAE  MFE>MAE   move  barras | ctrl MFE/MAE MFE>MAE      z'

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const marks: Mark[] = []
  const oneFamily: Panel['oneFamily'] = []
  const phases = new Map<string, number>()
  const skipped: string[] = []

  for (const f of files) {
    let d: DashboardData
    try {
      d = JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as DashboardData
      if (!Array.isArray(d.candles) || d.candles.length === 0) throw new Error('empty')
    } catch {
      skipped.push(f)
      continue
    }
    fixtures.set(f, d)
    const p = panelFor(d)
    marks.push(...p.marks)
    oneFamily.push(...p.oneFamily)
    for (const pt of buildPhase(d)) phases.set(`${d.symbol}|${d.timeframe}|${pt.timestamp}`, pt.value)
  }

  const report: Record<string, unknown> = { skipped }
  const candles = [...fixtures.values()].reduce((s, d) => s + d.candles.length, 0)
  const atrCache = new Map<string, number[]>()
  for (const [f, d] of fixtures) atrCache.set(f, meanTrueRangePctSeries(d.candles))
  const random = rng(20260910)

  console.log('# PISO — P3: a familia `structural`\n')
  console.log(`painel: ${fixtures.size} fixtures, ${candles} velas — modo causal (producao)`)
  console.log(`marcas: ${marks.length}   candidatos com exatamente 1 familia: ${oneFamily.length}\n`)

  function measure(rows: Mark[], h: number): { ev: Agg; ct: Agg } {
    const ev = emptyAgg()
    const ct = emptyAgg()
    for (const m of rows) {
      const f = `${m.symbol}_${m.timeframe}.json`
      const d = fixtures.get(f)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (o) add(ev, o)
      const series = atrCache.get(f)!
      const lo = Math.max(0, m.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - h - 1, m.index + CONTROL_WINDOW)
      if (hi <= lo) continue
      for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
        const j = lo + Math.floor(random() * (hi - lo))
        const co = forward(d.candles, j, h, m.direction, series[j] * d.candles[j].close)
        if (co) add(ct, co)
      }
    }
    return { ev, ct }
  }

  function line(label: string, rows: Mark[], h = PRIMARY_H): void {
    if (rows.length === 0) {
      console.log(`${label.padEnd(26)} ${String(0).padStart(5)}  —`)
      return
    }
    const { ev, ct } = measure(rows, h)
    const s = stat(ev)
    const c = stat(ct)
    console.log(
      `${label.padEnd(26)} ${fmt(s)} | ${c.ratio.toFixed(2).padStart(11)} ` +
        `${(c.win.toFixed(1) + '%').padStart(7)} ${zProp(ev, ct).toFixed(2).padStart(6)}`,
    )
  }

  // --- P3.2 -----------------------------------------------------------------
  console.log('## P3.2 — com vs sem structural (bruto, h=5)\n')
  console.log(HEAD)
  const withS = marks.filter((m) => m.hasStructural)
  const noS = marks.filter((m) => !m.hasStructural)
  line('COM structural', withS)
  line('SEM structural', noS)
  console.log('\n### outros horizontes\n')
  console.log(HEAD)
  for (const h of HORIZONS) {
    line(`COM structural h=${h}`, withS, h)
    line(`SEM structural h=${h}`, noS, h)
  }

  // --- P3.3 -----------------------------------------------------------------
  console.log('\n## P3.3 — controlando o numero de familias\n')
  console.log(HEAD)
  for (const n of [2, 3, 4]) {
    const rows = marks.filter((m) => (n === 4 ? m.nFam >= 4 : m.nFam === n))
    line(`${n === 4 ? '4+' : n} fam COM structural`, rows.filter((m) => m.hasStructural))
    line(`${n === 4 ? '4+' : n} fam SEM structural`, rows.filter((m) => !m.hasStructural))
  }

  // --- P3.14 estratificado --------------------------------------------------
  console.log('\n## P3.14 — comparacao ESTRATIFICADA (TF x direcao x nFam x excursao x pavio x bloco)\n')
  const stratum = (m: Mark) =>
    `${m.timeframe}|${m.direction}|${m.nFam}|${excBucket(m.excursionAtr)}|${wickBucket(m.wickBodyRatio)}|Q${Math.min(3, Math.floor(m.frac * 4))}`
  const outcomeOf = (m: Mark, h: number): Outcome | null => {
    const d = fixtures.get(`${m.symbol}_${m.timeframe}.json`)
    return d ? forward(d.candles, m.index, h, m.direction, m.atr) : null
  }
  for (const h of HORIZONS) {
    const strata = new Map<string, { a: Agg; b: Agg }>()
    for (const m of marks) {
      const o = outcomeOf(m, h)
      if (!o) continue
      const k = stratum(m)
      if (!strata.has(k)) strata.set(k, { a: emptyAgg(), b: emptyAgg() })
      add(m.hasStructural ? strata.get(k)!.a : strata.get(k)!.b, o)
    }
    let usable = 0
    let nA = 0
    let nB = 0
    let weighted = 0
    let weight = 0
    for (const { a, b } of strata.values()) {
      if (a.n === 0 || b.n === 0) continue
      usable += 1
      nA += a.n
      nB += b.n
      const w = (a.n * b.n) / (a.n + b.n)
      weighted += w * (a.won / a.n - b.won / b.n)
      weight += w
    }
    console.log(
      `h=${String(h).padStart(2)}  estratos comparaveis ${String(usable).padStart(3)}  ` +
        `n(com)=${String(nA).padStart(3)} n(sem)=${String(nB).padStart(3)}  ` +
        `delta MFE>MAE ponderado = ${weight > 0 ? ((100 * weighted) / weight).toFixed(1) : '—'}pp`,
    )
  }
  console.log(
    '\n(um delta positivo quer dizer que, entre marcas iguais em TF, direcao, numero de\n' +
      ' familias, tamanho da excursao, forma do pavio e periodo, a que tem structural\n' +
      ' venceu mais vezes. E a unica comparacao aqui que nao pode ser explicada por\n' +
      ' magnitude do candle.)',
  )

  // --- P3.4 composicao ------------------------------------------------------
  console.log('\n## P3.4 — composicao (n>=10, h=5)\n')
  console.log(HEAD)
  const combos = new Map<string, Mark[]>()
  for (const m of marks) {
    const k = m.families.join('+')
    if (!combos.has(k)) combos.set(k, [])
    combos.get(k)!.push(m)
  }
  for (const [k, rows] of [...combos].sort((a, b) => b[1].length - a[1].length)) {
    if (rows.length >= 10) line(k, rows)
  }

  // --- P3.5/6/7/8 -----------------------------------------------------------
  const slice = (label: string, keyOf: (m: Mark) => string | null, rows: Mark[]): void => {
    console.log(`\n## ${label}\n`)
    console.log(HEAD)
    const groups = new Map<string, Mark[]>()
    for (const m of rows) {
      const k = keyOf(m)
      if (k === null) continue
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)!.push(m)
    }
    for (const k of [...groups.keys()].sort()) line(k, groups.get(k)!)
  }
  slice('P3.5 — distancia do nivel structural ao extremo (ATR)', (m) =>
    m.structDistAtr === null ? null : distBucket(m.structDistAtr), withS)
  slice('P3.6 — tipo do evento structural', (m) => m.structEvent, withS)
  slice('P3.7 — idade do nivel structural (velas)', (m) =>
    m.structAge === null ? null : ageBucket(m.structAge), withS)
  slice('P3.8 — vida util do nivel structural', (m) =>
    m.structDiedOpen === null ? null : m.structDiedOpen ? 'linha ate a borda' : 'linha ja encerrada', withS)

  // --- P3.9 fair ------------------------------------------------------------
  console.log('\n## P3.9 — `fair` como confound\n')
  console.log(HEAD)
  line('COM struct, SEM fair', withS.filter((m) => !m.hasFair))
  line('COM struct, COM fair', withS.filter((m) => m.hasFair))
  line('SEM struct, SEM fair', noS.filter((m) => !m.hasFair))
  line('SEM struct, COM fair', noS.filter((m) => m.hasFair))

  // --- P3.11/12 -------------------------------------------------------------
  console.log('\n## P3.11 — discovery / holdout 70-30\n')
  console.log(HEAD)
  for (const [label, pred] of [
    ['DISC', (m: Mark) => m.frac < DISCOVERY_FRAC],
    ['HOLD', (m: Mark) => m.frac >= DISCOVERY_FRAC],
  ] as [string, (m: Mark) => boolean][]) {
    line(`${label} COM structural`, marks.filter((m) => pred(m) && m.hasStructural))
    line(`${label} SEM structural`, marks.filter((m) => pred(m) && !m.hasStructural))
  }
  console.log('\n## P3.12 — quatro blocos temporais\n')
  console.log(HEAD)
  for (let b = 0; b < 4; b += 1) {
    const inB = (m: Mark) => m.frac >= b / 4 && m.frac < (b + 1) / 4
    line(`Q${b + 1} COM structural`, marks.filter((m) => inB(m) && m.hasStructural))
    line(`Q${b + 1} SEM structural`, marks.filter((m) => inB(m) && !m.hasStructural))
  }

  // --- TF / direcao ---------------------------------------------------------
  console.log('\n## P3.11-14 — por timeframe e direcao\n')
  console.log(HEAD)
  for (const tf of ['15m', '1h', '4h']) {
    line(`${tf} COM structural`, marks.filter((m) => m.timeframe === tf && m.hasStructural))
    line(`${tf} SEM structural`, marks.filter((m) => m.timeframe === tf && !m.hasStructural))
  }
  for (const dir of ['bullish', 'bearish'] as Direction[]) {
    line(`${dir} COM structural`, marks.filter((m) => m.direction === dir && m.hasStructural))
    line(`${dir} SEM structural`, marks.filter((m) => m.direction === dir && !m.hasStructural))
  }

  // --- P3.13 por simbolo ----------------------------------------------------
  console.log('\n## P3.13 — por simbolo (>=3 marcas dos dois lados)\n')
  const per: [string, number, number, number][] = []
  for (const sym of new Set(marks.map((m) => m.symbol))) {
    const a = marks.filter((m) => m.symbol === sym && m.hasStructural)
    const b = marks.filter((m) => m.symbol === sym && !m.hasStructural)
    if (a.length < 3 || b.length < 3) continue
    per.push([sym, stat(measure(a, PRIMARY_H).ev).win - stat(measure(b, PRIMARY_H).ev).win, a.length, b.length])
  }
  per.sort((x, y) => y[1] - x[1])
  const better = per.filter(([, d]) => d > 0).length
  console.log(
    `simbolos comparaveis: ${per.length} — melhoram ${better}` +
      (per.length ? ` (${((100 * better) / per.length).toFixed(0)}%), mediana ${per[Math.floor(per.length / 2)][1].toFixed(1)}pp` : ''),
  )
  if (per.length) {
    console.log('  top5:    ' + per.slice(0, 5).map(([s, d, a, b]) => `${s} ${d > 0 ? '+' : ''}${d.toFixed(0)}pp(${a}/${b})`).join('  '))
    console.log('  bottom5: ' + per.slice(-5).map(([s, d, a, b]) => `${s} ${d > 0 ? '+' : ''}${d.toFixed(0)}pp(${a}/${b})`).join('  '))
  }
  report.per_symbol = per.map(([s, d, a, b]) => ({ symbol: s, delta: d, nWith: a, nWithout: b }))

  // --- P3.10 H4 exactly-2 ---------------------------------------------------
  console.log('\n## P3.10 — H4 com exatamente 2 familias (o negativo da P2.0)\n')
  console.log(HEAD)
  const h4two = marks.filter((m) => m.timeframe === '4h' && m.nFam === 2)
  line('H4 exactly-2 (todos)', h4two)
  line('  COM structural', h4two.filter((m) => m.hasStructural))
  line('  SEM structural', h4two.filter((m) => !m.hasStructural))
  for (const dir of ['bullish', 'bearish'] as Direction[])
    line(`  ${dir}`, h4two.filter((m) => m.direction === dir))
  for (const k of new Set(h4two.map((m) => m.families.join('+'))))
    line(`  ${k}`, h4two.filter((m) => m.families.join('+') === k))
  for (let b = 0; b < 4; b += 1)
    line(`  Q${b + 1}`, h4two.filter((m) => m.frac >= b / 4 && m.frac < (b + 1) / 4))
  for (const k of ['exc1.0-1.25', 'exc1.25-1.75', 'exc1.75+'])
    line(`  ${k}`, h4two.filter((m) => excBucket(m.excursionAtr) === k))
  for (const k of ['wick2-3', 'wick3-5', 'wick5+'])
    line(`  ${k}`, h4two.filter((m) => wickBucket(m.wickBodyRatio) === k))
  const h4syms = new Map<string, number>()
  for (const m of h4two) h4syms.set(m.symbol, (h4syms.get(m.symbol) ?? 0) + 1)
  console.log(
    `\n  concentracao: ${h4syms.size} simbolos para ${h4two.length} marcas; ` +
      `maiores: ${[...h4syms].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([s, n]) => `${s}=${n}`).join(' ')}`,
  )
  {
    const withPhase = h4two.filter((m) => phases.has(`${m.symbol}|${m.timeframe}|${m.timestamp}`))
    console.log(HEAD)
    line('  |fase|<50 (diagnostico)', withPhase.filter((m) => Math.abs(phases.get(`${m.symbol}|${m.timeframe}|${m.timestamp}`)!) < 50))
    line('  |fase|>=50 (diagnostico)', withPhase.filter((m) => Math.abs(phases.get(`${m.symbol}|${m.timeframe}|${m.timestamp}`)!) >= 50))
  }

  // --- P3.15 near-miss ------------------------------------------------------
  console.log('\n## P3.15 — near-miss: 1 familia, structural fora da tolerancia\n')
  const om = oneFamily.filter((o) => o.family !== 'structural')
  const near = om.filter((o) => o.nearestStructAtr !== null && o.nearestStructAtr <= NEAR_STRUCTURAL_ATR)
  console.log(
    `candidatos com exatamente 1 familia (nao-structural): ${om.length}\n` +
      `  com um nivel structural vivo a <= ${NEAR_STRUCTURAL_ATR} ATR do trecho varrido: ${near.length} ` +
      `(${om.length ? ((100 * near.length) / om.length).toFixed(1) : 0}%)`,
  )
  const dist = new Map<string, number>()
  for (const o of near) {
    const k = distBucket(o.nearestStructAtr!)
    dist.set(k, (dist.get(k) ?? 0) + 1)
  }
  for (const k of [...dist.keys()].sort()) console.log(`    ${k} ATR: ${dist.get(k)}`)
  report.near_miss_structural = { oneFamily: om.length, near: near.length }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
}

main()
