/**
 * P5 — is the H4 PISO broken by its timeframe, or by its anchor?
 *
 * One finding survived P2, P3 and P4 while every hypothesis around it died:
 * **the H4 PISO measures worse than its own control.** 43.3% against 52.9% on
 * the panel, 38.8% with exactly two families, 23.1% among the structural
 * near-misses — diffuse across symbols, stable across three studies, and never
 * once investigated on its own terms, because every time it appeared it was a
 * by-product of a different question.
 *
 * The minimal hypothesis is not about levels at all. `_VWAP_ANCHOR_PERIOD`
 * gives H4 a **weekly** envelope while M15 and H1 get a **session** one, so the
 * "±1σ edge" the rule clears is not the same object in the three timeframes.
 * The same geometry may simply be applied to a different reference.
 *
 * The design, and the one test that matters
 * -----------------------------------------
 * Everything is held fixed except which anchor produced `upper_1`/`lower_1`:
 * the same candles, the same levels (they never depend on the envelope), the
 * same causal ATR, the same lifecycle, the same thresholds. The rule is
 * untouched.
 *
 * Frequency alone would prove nothing — a shorter anchor resets more often and
 * mechanically produces more edges. So the load-bearing comparison is the
 * **cross**: H4 given a session anchor, and H1 given a weekly one. If the
 * anchor is the cause, the two should move in opposite directions. If only H4
 * moves, or neither does, the anchor is not the story and the timeframe is.
 *
 * Nothing here proposes a change. `_VWAP_ANCHOR_PERIOD` is production wiring
 * for the whole Tide ribbon, not a PISO knob, and P5.18 forbids touching it.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoH4AnchorAudit.ts --json research/piso_h4_anchor_baseline.json
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure, VWAPPoint } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import {
  buildDefenceLevels,
  diagnoseDefendedMarks,
  meanTrueRangePctSeries,
  type DefenceLevel,
  type LevelFamily,
} from '../src/utils/defendedLevels.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { ANCHOR_LABEL, productionAnchor, rebuildVwapPoints, type AnchorName } from './vwapAnchor.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const
const PRIMARY_H = 5
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250

/** Which anchors each timeframe is measured under. The production one is first
 *  and is the baseline; the rest are the counterfactuals P5.2 allows — only
 *  periods `VWAPAnchor` already defines, never an invented one. */
const VARIANTS: Record<string, AnchorName[]> = {
  '15m': ['session', 'week'],
  '1h': ['session', 'week', 'month'],
  '4h': ['week', 'session', 'month'],
}

// ---------------------------------------------------------------------------
// Production wiring, reproduced exactly as `MainChart` calls it.
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

/** The same dashboard with a different envelope, and nothing else changed. */
function withAnchor(data: DashboardData, points: VWAPPoint[], anchor: AnchorName): DashboardData {
  return { ...data, vwap: { ...data.vwap!, anchor, points } } as DashboardData
}

// ---------------------------------------------------------------------------
// Forward reading (identical to P1-P4, so the numbers stay comparable).
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
// Marks and envelope geometry, per (fixture, anchor).
// ---------------------------------------------------------------------------

interface Mark {
  key: string
  symbol: string
  timeframe: string
  anchor: AnchorName
  timestamp: string
  index: number
  direction: Direction
  side: 'top' | 'bottom'
  families: LevelFamily[]
  excursionAtr: number
  wickBodyRatio: number
  atr: number
  frac: number
  /** Candles since the envelope last restarted. */
  ageSinceReset: number
  /** ±1σ half-width at the mark, in the candle's own ATR. */
  sigmaAtr: number
  /** UTC weekday, 0 = Monday. */
  weekday: number
}

interface Geometry {
  candles: number
  sigmaAtr: number
  distAtr: number
  touches: number
  candidates: number
  segments: number
  excursionSum: number
}

const WEEKDAYS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom']
const excBucket = (v: number): string =>
  v < 1.25 ? 'exc1.0-1.25' : v < 1.75 ? 'exc1.25-1.75' : 'exc1.75+'
const wickBucket = (v: number): string => (v < 3 ? 'wick2-3' : v < 5 ? 'wick3-5' : 'wick5+')
const ageBucket = (v: number): string =>
  v <= 6 ? '0-6' : v <= 12 ? '7-12' : v <= 24 ? '13-24' : '25+'

function runVariant(
  data: DashboardData,
  levels: DefenceLevel[],
  atrSeries: number[],
  anchor: AnchorName,
): { marks: Mark[]; geometry: Geometry } {
  const points = rebuildVwapPoints(data.candles, anchor)
  const swapped = withAnchor(data, points, anchor)
  const cands = diagnoseDefendedMarks(swapped, levels, { causalAtr: true })
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const n = data.candles.length

  // Geometry over the whole tape, not just the marks: how wide the band is,
  // how far price sits from it, and how often it is reached at all.
  const geometry: Geometry = {
    candles: 0,
    sigmaAtr: 0,
    distAtr: 0,
    touches: 0,
    candidates: cands.length,
    segments: new Set(points.map((p) => p.anchor_timestamp)).size,
    excursionSum: 0,
  }
  const resetAt = new Map<string, number>()
  for (const p of points) {
    const i = idx.get(p.timestamp)
    if (i === undefined) continue
    if (!resetAt.has(p.anchor_timestamp)) resetAt.set(p.anchor_timestamp, i)
    if (p.upper_1 === null || p.lower_1 === null) continue
    const c = data.candles[i]
    const atr = atrSeries[i] * c.close
    if (atr <= 0) continue
    geometry.candles += 1
    geometry.sigmaAtr += (p.upper_1 - p.value) / atr
    geometry.distAtr += Math.abs(c.close - p.value) / atr
    if (c.high > p.upper_1 || c.low < p.lower_1) geometry.touches += 1
  }
  for (const c of cands) geometry.excursionSum += c.excursionAtr

  const byTs = new Map(points.map((p) => [p.timestamp, p]))
  const marks: Mark[] = []
  for (const c of cands) {
    if (c.failed.length > 0) continue
    const i = idx.get(c.timestamp)
    const p = byTs.get(c.timestamp)
    if (i === undefined || !p || p.upper_1 === null) continue
    const atr = c.atrPct * data.candles[i].close
    if (atr <= 0) continue
    const d = new Date(c.timestamp)
    marks.push({
      key: `${data.symbol}_${data.timeframe}.json`,
      symbol: data.symbol,
      timeframe: data.timeframe,
      anchor,
      timestamp: c.timestamp,
      index: i,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      side: c.side,
      families: c.families,
      excursionAtr: c.excursionAtr,
      wickBodyRatio: c.wickBodyRatio,
      atr,
      frac: i / n,
      ageSinceReset: i - (resetAt.get(p.anchor_timestamp) ?? i),
      sigmaAtr: (p.upper_1 - p.value) / atr,
      weekday: (d.getUTCDay() + 6) % 7,
    })
  }
  return { marks, geometry }
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
  'grupo                             n    MFE    MAE  MFE/MAE  MFE>MAE   move  barra | controle      z'

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const atrCache = new Map<string, number[]>()
  const marks: Mark[] = []
  const geometry = new Map<string, Geometry>()
  const skipped: string[] = []

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
    const anchors = VARIANTS[d.timeframe]
    if (!anchors) {
      skipped.push(f)
      continue
    }
    fixtures.set(f, d)
    const atrSeries = meanTrueRangePctSeries(d.candles)
    atrCache.set(f, atrSeries)
    // P5.3: the levels are built once and shared by every variant. They do not
    // read the envelope, so this is not an approximation — it is the guarantee
    // that the anchor is the only thing that differs.
    const levels = buildDefenceLevels(d, standingUntilFor(d), { causalAtr: true })
    for (const anchor of anchors) {
      const r = runVariant(d, levels, atrSeries, anchor)
      marks.push(...r.marks)
      const gk = `${d.timeframe}|${anchor}`
      const g = geometry.get(gk) ?? {
        candles: 0,
        sigmaAtr: 0,
        distAtr: 0,
        touches: 0,
        candidates: 0,
        segments: 0,
        excursionSum: 0,
      }
      g.candles += r.geometry.candles
      g.sigmaAtr += r.geometry.sigmaAtr
      g.distAtr += r.geometry.distAtr
      g.touches += r.geometry.touches
      g.candidates += r.geometry.candidates
      g.segments += r.geometry.segments
      g.excursionSum += r.geometry.excursionSum
      geometry.set(gk, g)
    }
  }

  const random = rng(20260910)
  const report: Record<string, unknown> = { skipped }
  const totalCandles = [...fixtures.values()].reduce((s, d) => s + d.candles.length, 0)

  console.log('# PISO — P5: a ancora da VWAP no H4\n')
  console.log(
    `painel: ${fixtures.size} fixtures, ${totalCandles} velas — modo causal (producao), ` +
      `regra inalterada`,
  )
  console.log(
    'variantes: ' +
      Object.entries(VARIANTS)
        .map(([tf, a]) => `${tf} [${a.map((x) => (x === productionAnchor(tf) ? x + '*' : x)).join(' ')}]`)
        .join('  ') +
      '   (* = producao)\n',
  )

  function measure(rows: Mark[], h: number): { ev: Agg; ctl: Agg } {
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const m of rows) {
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (o) add(ev, o)
      // Control matched on symbol, timeframe, direction and period — the
      // discipline `research/raid_reversal.py` cost this project to learn.
      const series = atrCache.get(m.key)!
      const lo = Math.max(0, m.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - h - 1, m.index + CONTROL_WINDOW)
      if (hi > lo) {
        for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
          const j = lo + Math.floor(random() * (hi - lo))
          const co = forward(d.candles, j, h, m.direction, series[j] * d.candles[j].close)
          if (co) add(ctl, co)
        }
      }
    }
    return { ev, ctl }
  }

  function line(label: string, rows: Mark[], h = PRIMARY_H): Stat | null {
    if (rows.length === 0) {
      console.log(`${label.padEnd(31)} ${String(0).padStart(5)}  —`)
      return null
    }
    const { ev, ctl } = measure(rows, h)
    const s = stat(ev)
    console.log(
      `${label.padEnd(31)} ${fmt(s)} | ${(stat(ctl).win.toFixed(1) + '%').padStart(8)} ` +
        `${zProp(ev, ctl).toFixed(2).padStart(6)}`,
    )
    return s
  }

  const pick = (tf: string, anchor: AnchorName) =>
    marks.filter((m) => m.timeframe === tf && m.anchor === anchor)
  const tfs = Object.keys(VARIANTS)

  // --- P5.0 ancoras ---------------------------------------------------------
  console.log('## P5.0 — a ancora de cada timeframe (`_VWAP_ANCHOR_PERIOD`)\n')
  console.log('  TF     ancora de producao          reset')
  for (const [tf, label] of [
    ['15m', 'SESSION (default)'],
    ['1h', 'SESSION (default)'],
    ['4h', 'WEEK'],
    ['1d', 'MONTH'],
    ['1w', 'MONTH'],
  ] as const) {
    const a = productionAnchor(tf)
    console.log(`  ${tf.padEnd(6)} ${label.padEnd(27)} ${ANCHOR_LABEL[a]}`)
  }
  console.log(
    '\n  todas as fronteiras sao UTC (00:00), sem fuso de bolsa; a semana e ISO\n' +
      '  (segunda). "DAILY" nao existe como ancora separada: SESSION E o dia UTC.\n' +
      '  ROLLING e EVENT existem no enum mas nao desenham a fita do Tide.\n',
  )

  // --- P5.4 frequencia ------------------------------------------------------
  console.log('## P5.4 — frequencia por ancora\n')
  console.log('  TF   ancora    candidatos   PISOs  /1000v  bullish  bearish  simbolos  segmentos')
  const freq: Record<string, unknown>[] = []
  for (const tf of tfs) {
    const velas = [...fixtures.values()]
      .filter((d) => d.timeframe === tf)
      .reduce((s, d) => s + d.candles.length, 0)
    for (const a of VARIANTS[tf]) {
      const rows = pick(tf, a)
      const g = geometry.get(`${tf}|${a}`)!
      const bull = rows.filter((m) => m.direction === 'bullish').length
      const syms = new Set(rows.map((m) => m.symbol)).size
      const flag = a === productionAnchor(tf) ? '*' : ' '
      console.log(
        `  ${tf.padEnd(4)} ${(a + flag).padEnd(9)} ${String(g.candidates).padStart(10)} ` +
          `${String(rows.length).padStart(7)} ${((1000 * rows.length) / Math.max(1, velas)).toFixed(2).padStart(7)} ` +
          `${String(bull).padStart(8)} ${String(rows.length - bull).padStart(8)} ` +
          `${String(syms).padStart(9)} ${String(g.segments).padStart(10)}`,
      )
      freq.push({ tf, anchor: a, candidates: g.candidates, marks: rows.length, bull, syms })
    }
  }
  report.frequency = freq

  // --- P5.5 qualidade -------------------------------------------------------
  console.log('\n## P5.5 — qualidade por ancora (h=5 principal)\n')
  console.log(HEAD)
  const quality: Record<string, unknown>[] = []
  for (const tf of tfs) {
    for (const a of VARIANTS[tf]) {
      const flag = a === productionAnchor(tf) ? '*' : ''
      const s = line(`${tf} ${a}${flag}`, pick(tf, a))
      if (s) quality.push({ tf, anchor: a, h: PRIMARY_H, ...s })
    }
  }
  console.log('\n### os mesmos recortes nos outros horizontes\n')
  console.log(HEAD)
  for (const h of HORIZONS) {
    if (h === PRIMARY_H) continue
    for (const tf of tfs) {
      for (const a of VARIANTS[tf]) {
        const flag = a === productionAnchor(tf) ? '*' : ''
        const s = line(`h=${h} ${tf} ${a}${flag}`, pick(tf, a), h)
        if (s) quality.push({ tf, anchor: a, h, ...s })
      }
    }
  }
  report.quality = quality

  // --- P5.9/P5.10 a cruz ----------------------------------------------------
  console.log('\n## P5.9/P5.10 — a cruz TF x ancora (o teste causal)\n')
  console.log(HEAD)
  line('H4 week   (producao)', pick('4h', 'week'))
  line('H4 session (curta)', pick('4h', 'session'))
  line('H1 session (producao)', pick('1h', 'session'))
  line('H1 week   (longa)', pick('1h', 'week'))
  line('M15 session (producao)', pick('15m', 'session'))
  line('M15 week  (longa)', pick('15m', 'week'))

  // --- P5.6 geometria -------------------------------------------------------
  console.log('\n## P5.6 — geometria da banda\n')
  console.log('  TF   ancora    sigma(ATR)  dist(ATR)  touch%  exc.media  velas/segmento')
  const geo: Record<string, unknown>[] = []
  for (const tf of tfs) {
    for (const a of VARIANTS[tf]) {
      const g = geometry.get(`${tf}|${a}`)!
      const flag = a === productionAnchor(tf) ? '*' : ' '
      const row = {
        tf,
        anchor: a,
        sigma: g.sigmaAtr / Math.max(1, g.candles),
        dist: g.distAtr / Math.max(1, g.candles),
        touch: (100 * g.touches) / Math.max(1, g.candles),
        exc: g.excursionSum / Math.max(1, g.candidates),
        perSeg: g.candles / Math.max(1, g.segments),
      }
      geo.push(row)
      console.log(
        `  ${tf.padEnd(4)} ${(a + flag).padEnd(9)} ${row.sigma.toFixed(2).padStart(10)} ` +
          `${row.dist.toFixed(2).padStart(10)} ${row.touch.toFixed(1).padStart(7)} ` +
          `${row.exc.toFixed(2).padStart(10)} ${row.perSeg.toFixed(1).padStart(15)}`,
      )
    }
  }
  report.geometry = geo

  // --- P5.7 idade desde o reset ---------------------------------------------
  console.log('\n## P5.7 — idade desde o reset da ancora\n')
  console.log(HEAD)
  for (const [tf, a] of [
    ['4h', 'week'],
    ['4h', 'session'],
    ['1h', 'session'],
    ['1h', 'week'],
  ] as [string, AnchorName][]) {
    const rows = pick(tf, a)
    if (rows.length === 0) continue
    for (const b of ['0-6', '7-12', '13-24', '25+']) {
      line(`${tf} ${a} ${b}`, rows.filter((m) => ageBucket(m.ageSinceReset) === b))
    }
  }

  // --- P5.8 posicao na semana -----------------------------------------------
  console.log('\n## P5.8 — posicao na semana (H4, ancora semanal de producao)\n')
  console.log(HEAD)
  for (let d = 0; d < 7; d += 1) {
    line(`4h week ${WEEKDAYS[d]}`, pick('4h', 'week').filter((m) => m.weekday === d))
  }

  // --- P5.11 matching -------------------------------------------------------
  console.log('\n## P5.11 — delta estratificado variante vs baseline\n')
  console.log('  (estratos: simbolo x direcao x excursao x pavio x bloco temporal)\n')
  function stratified(tf: string, variant: AnchorName, h: number): string {
    const base = productionAnchor(tf)
    const key = (m: Mark) =>
      `${m.symbol}|${m.direction}|${excBucket(m.excursionAtr)}|${wickBucket(m.wickBodyRatio)}|${Math.floor(m.frac * 4)}`
    const bucket = new Map<string, { a: Agg; b: Agg }>()
    for (const m of marks) {
      if (m.timeframe !== tf) continue
      if (m.anchor !== variant && m.anchor !== base) continue
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (!o) continue
      const k = key(m)
      if (!bucket.has(k)) bucket.set(k, { a: emptyAgg(), b: emptyAgg() })
      add(m.anchor === variant ? bucket.get(k)!.a : bucket.get(k)!.b, o)
    }
    let weight = 0
    let delta = 0
    let na = 0
    let nb = 0
    let strata = 0
    for (const { a, b } of bucket.values()) {
      if (a.n === 0 || b.n === 0) continue
      const w = (a.n * b.n) / (a.n + b.n)
      delta += w * (a.won / a.n - b.won / b.n)
      weight += w
      na += a.n
      nb += b.n
      strata += 1
    }
    return (
      `  h=${String(h).padStart(2)} ${tf} ${variant} vs ${base}:  estratos ${String(strata).padStart(3)}  ` +
      `n(var)=${String(na).padStart(4)} n(base)=${String(nb).padStart(4)}  ` +
      `delta MFE>MAE = ${weight > 0 ? ((100 * delta) / weight).toFixed(1) : '—'}pp`
    )
  }
  for (const h of HORIZONS) {
    console.log(stratified('4h', 'session', h))
    console.log(stratified('1h', 'week', h))
  }

  // --- P5.12 identidade do stream -------------------------------------------
  console.log('\n## P5.12 — identidade do stream (nao so contagem)\n')
  console.log('  TF   variante   identicos  so baseline  so variante  lado muda  familias mudam')
  for (const tf of tfs) {
    const base = productionAnchor(tf)
    for (const a of VARIANTS[tf]) {
      if (a === base) continue
      const bm = new Map(pick(tf, base).map((m) => [`${m.symbol}|${m.timestamp}`, m]))
      const vm = new Map(pick(tf, a).map((m) => [`${m.symbol}|${m.timestamp}`, m]))
      let same = 0
      let sideChanged = 0
      let famChanged = 0
      for (const [k, m] of vm) {
        const o = bm.get(k)
        if (!o) continue
        same += 1
        if (o.side !== m.side) sideChanged += 1
        if (o.families.join('+') !== m.families.join('+')) famChanged += 1
      }
      const onlyBase = [...bm.keys()].filter((k) => !vm.has(k)).length
      const onlyVar = [...vm.keys()].filter((k) => !bm.has(k)).length
      console.log(
        `  ${tf.padEnd(4)} ${a.padEnd(10)} ${String(same).padStart(9)} ${String(onlyBase).padStart(12)} ` +
          `${String(onlyVar).padStart(12)} ${String(sideChanged).padStart(10)} ${String(famChanged).padStart(15)}`,
      )
    }
  }

  // --- P5.14 temporalidade --------------------------------------------------
  console.log('\n## P5.14 — quatro blocos temporais\n')
  console.log(HEAD)
  for (const [tf, a] of [
    ['4h', 'week'],
    ['4h', 'session'],
    ['1h', 'session'],
    ['1h', 'week'],
  ] as [string, AnchorName][]) {
    for (let q = 0; q < 4; q += 1) {
      line(`${tf} ${a} Q${q + 1}`, pick(tf, a).filter((m) => Math.floor(m.frac * 4) === q))
    }
  }

  // --- P5.12b direcao -------------------------------------------------------
  console.log('\n## P5.12b — direcao\n')
  console.log(HEAD)
  for (const [tf, a] of [
    ['4h', 'week'],
    ['4h', 'session'],
    ['1h', 'session'],
    ['1h', 'week'],
  ] as [string, AnchorName][]) {
    line(`${tf} ${a} bullish`, pick(tf, a).filter((m) => m.direction === 'bullish'))
    line(`${tf} ${a} bearish`, pick(tf, a).filter((m) => m.direction === 'bearish'))
  }

  // --- P5.15 robustez por simbolo -------------------------------------------
  console.log('\n## P5.15 — robustez por simbolo (H4 session vs week, h=5)\n')
  {
    const won = (m: Mark): number | null => {
      const d = fixtures.get(m.key)
      if (!d) return null
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      return o ? (o.won ? 1 : 0) : null
    }
    const per = new Map<string, { a: number[]; b: number[] }>()
    for (const m of marks) {
      if (m.timeframe !== '4h') continue
      if (m.anchor !== 'session' && m.anchor !== 'week') continue
      const w = won(m)
      if (w === null) continue
      if (!per.has(m.symbol)) per.set(m.symbol, { a: [], b: [] })
      ;(m.anchor === 'session' ? per.get(m.symbol)!.a : per.get(m.symbol)!.b).push(w)
    }
    const deltas: { s: string; d: number; na: number; nb: number }[] = []
    for (const [s, { a, b }] of per) {
      if (a.length < 3 || b.length < 3) continue
      const mean = (x: number[]) => (100 * x.reduce((p, c) => p + c, 0)) / x.length
      deltas.push({ s, d: mean(a) - mean(b), na: a.length, nb: b.length })
    }
    deltas.sort((x, y) => y.d - x.d)
    const up = deltas.filter((x) => x.d > 0).length
    const med = deltas.length ? deltas[Math.floor(deltas.length / 2)].d : 0
    const show = (x: { s: string; d: number; na: number; nb: number }) =>
      `${x.s} ${x.d >= 0 ? '+' : ''}${x.d.toFixed(0)}pp(${x.na}/${x.nb})`
    console.log(
      `  simbolos comparaveis (>=3 marcas nos dois lados): ${deltas.length} — ` +
        `melhoram ${up}${deltas.length ? ` (${((100 * up) / deltas.length).toFixed(0)}%)` : ''}, ` +
        `mediana ${med.toFixed(1)}pp`,
    )
    console.log(`  top5:    ${deltas.slice(0, 5).map(show).join('  ') || '—'}`)
    console.log(`  bottom5: ${deltas.slice(-5).map(show).join('  ') || '—'}`)
    report.perSymbol = deltas
  }

  // --- P5.13 casos reais ----------------------------------------------------
  console.log('\n## P5.13 — casos reais (BTC / ETH / SOL, H4)\n')
  {
    const majors = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    const bm = new Map(
      pick('4h', 'week')
        .filter((m) => majors.includes(m.symbol))
        .map((m) => [`${m.symbol}|${m.timestamp}`, m]),
    )
    const vm = new Map(
      pick('4h', 'session')
        .filter((m) => majors.includes(m.symbol))
        .map((m) => [`${m.symbol}|${m.timestamp}`, m]),
    )
    const describe = (tag: string, m: Mark): void => {
      const d = fixtures.get(m.key)!
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      console.log(
        `  ${tag} ${m.symbol.padEnd(8)} ${m.timestamp}  ${m.anchor.padEnd(7)} ${m.direction.padEnd(7)} ` +
          `exc=${m.excursionAtr.toFixed(2)} sigma=${m.sigmaAtr.toFixed(2)}ATR idade=${String(m.ageSinceReset).padStart(2)} ` +
          `fam=${m.families.join('+').padEnd(26)} -> h5 ${o ? `MFE=${o.mfe.toFixed(2)} MAE=${o.mae.toFixed(2)} ${o.won ? 'OK' : 'falhou'}` : 'sem horizonte'}`,
      )
    }
    let shown = 0
    for (const [k, m] of bm) {
      if (vm.has(k) || shown >= 3) continue
      describe('A) so week (producao)  ', m)
      shown += 1
    }
    shown = 0
    for (const [k, m] of vm) {
      if (bm.has(k) || shown >= 3) continue
      describe('D) so session (variante)', m)
      shown += 1
    }
    shown = 0
    for (const [k, m] of bm) {
      if (!vm.has(k) || shown >= 2) continue
      describe('C) as duas concordam   ', m)
      describe('   idem, session       ', vm.get(k)!)
      shown += 1
    }
  }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
  if (skipped.length) console.log(`\nfixtures ignoradas: ${skipped.length}`)
}

main()
