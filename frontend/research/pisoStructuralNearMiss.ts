/**
 * P4 — do the structural near-misses deserve to be marks?
 *
 * The one thread P3 left that could *add* coverage instead of filtering it.
 * Among candidates that already clear the excursion and wick gates but hold
 * exactly one non-structural family, 51 have a live structural level sitting
 * within 0.5 ATR of the swept range — just outside the half-width the rule
 * gives a level. Nobody has looked at what happened after them.
 *
 * The definition, fixed before any result was seen (P4.1)
 * ------------------------------------------------------
 * A near-miss is a candle that:
 *   1. crossed a ±1σ edge and closed back inside;
 *   2. cleared `MIN_EXCURSION_ATR`;
 *   3. cleared `MIN_WICK_BODY`;
 *   4. holds exactly one live family;
 *   5. that family is not `structural`;
 *   6. and at least one live structural level sits outside the swept range.
 * The distance is the gap from the swept range to the nearest such level, in
 * the candle's own causal ATR. Nothing here changes if a bucket disappoints.
 *
 * The trap this is built around
 * -----------------------------
 * P3 flagged the confound and it governs the design: a bigger excursion sweeps
 * a wider range, so it is both more likely to sit near a structural level and a
 * different kind of event. Proximity has to be shown to matter *at fixed
 * excursion*, or it is excursion wearing a disguise. Hence two controls — a
 * random one for "does this separate from the market at all", and a **placebo
 * drawn from the candidate pool itself**, matched on symbol, timeframe,
 * direction, period and both the excursion and wick buckets, for "does
 * proximity add anything to a candle that already looks like this".
 *
 * Nothing in production is touched: `LEVEL_TOLERANCE_ATR` stays at 0.5, no gate
 * is added, `structural` is not promoted, and the rule runs exactly as shipped.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoStructuralNearMiss.ts --json research/piso_structural_nearmiss_baseline.json
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

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const
const PRIMARY_H = 5
const DISCOVERY_FRAC = 0.7
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250

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
    let best: MarketStructure | null = null
    let gap = Infinity
    for (const e of byTime.get(l.born) ?? []) {
      if (e.reference_price_level == null) continue
      const g = Math.abs(e.reference_price_level - centre)
      if (g < gap) {
        gap = g
        best = e
      }
    }
    if (best && gap <= Math.max(1e-9, Math.abs(centre) * 1e-9)) out.set(l, best)
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
// Events.
// ---------------------------------------------------------------------------

/** Every candle that cleared excursion and wick, mark or not. */
interface Event {
  symbol: string
  timeframe: string
  timestamp: string
  index: number
  direction: Direction
  /** '' for a real mark; otherwise the single family it holds. */
  families: LevelFamily[]
  isMark: boolean
  hasStructural: boolean
  hasFair: boolean
  excursionAtr: number
  wickBodyRatio: number
  atr: number
  frac: number
  /** Near-misses only: gap from the swept range to the nearest live structural
   *  level, in ATR. `null` when no live structural level exists at all. */
  structGapAtr: number | null
  structEvent: string | null
  structAge: number | null
}

const excBucket = (v: number): string =>
  v < 1.25 ? 'exc1.0-1.25' : v < 1.75 ? 'exc1.25-1.75' : 'exc1.75+'
const wickBucket = (v: number): string => (v < 3 ? 'wick2-3' : v < 5 ? 'wick3-5' : 'wick5+')
const gapBucket = (v: number | null): string =>
  v === null
    ? 'F sem structural'
    : v <= 0.25
      ? 'C1 0-0.25'
      : v <= 0.5
        ? 'C2 0.25-0.5'
        : v <= 1.0
          ? 'D 0.5-1.0'
          : v <= 2.0
            ? 'E 1.0-2.0'
            : 'E2 >2.0'
const ageBucket = (v: number): string =>
  v <= 10 ? '0-10' : v <= 30 ? '11-30' : v <= 100 ? '31-100' : '100+'

function eventsFor(data: DashboardData): Event[] {
  const levels = buildDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const cands = diagnoseDefendedMarks(data, levels, { causalAtr: true })
  const prov = structuralProvenance(data, levels)
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const atrSeries = meanTrueRangePctSeries(data.candles)
  const structLevels = levels.filter((l) => l.family === 'structural')
  const n = data.candles.length

  const out: Event[] = []
  for (const c of cands) {
    // Gates 1-3 of the definition: excursion and wick must already be clear.
    if (c.failed.some((g) => g !== 'families')) continue
    const i = idx.get(c.timestamp)
    if (i === undefined) continue
    const atr = atrSeries[i] * data.candles[i].close
    if (atr <= 0) continue
    const isMark = c.failed.length === 0
    // Near-misses: exactly one family, and it is not structural.
    if (!isMark && !(c.families.length === 1 && c.families[0] !== 'structural')) continue

    const low = Math.min(c.edge, c.price)
    const high = Math.max(c.edge, c.price)
    let gap: number | null = null
    let nearest: DefenceLevel | null = null
    for (const l of structLevels) {
      if (l.born > c.timestamp) continue
      if (l.died !== null && l.died < c.timestamp) continue
      const g = l.high < low ? low - l.high : l.low > high ? l.low - high : 0
      if (gap === null || g < gap) {
        gap = g
        nearest = l
      }
    }
    const e = nearest ? prov.get(nearest) : undefined
    out.push({
      symbol: data.symbol,
      timeframe: data.timeframe,
      timestamp: c.timestamp,
      index: i,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      families: c.families,
      isMark,
      hasStructural: c.families.includes('structural'),
      hasFair: c.families.includes('fair'),
      excursionAtr: c.excursionAtr,
      wickBodyRatio: c.wickBodyRatio,
      atr,
      frac: i / n,
      structGapAtr: isMark ? null : gap === null ? null : gap / atr,
      structEvent: isMark ? null : (e?.event ?? null),
      structAge: isMark || !nearest ? null : i - (idx.get(nearest.born) ?? i),
    })
  }
  return out
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
  'grupo                        n    MFE    MAE  MFE/MAE  MFE>MAE   move  barra | aleat  z | placebo casado  z'

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const events: Event[] = []
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
    events.push(...eventsFor(d))
  }

  const atrCache = new Map<string, number[]>()
  for (const [f, d] of fixtures) atrCache.set(f, meanTrueRangePctSeries(d.candles))
  const random = rng(20260910)
  const report: Record<string, unknown> = { skipped }

  const marks = events.filter((e) => e.isMark)
  const near = events.filter((e) => !e.isMark)

  console.log('# PISO — P4: near-miss structural\n')
  console.log(`painel: ${fixtures.size} fixtures — modo causal (producao)`)
  console.log(`PISO reais: ${marks.length}   near-miss (1 familia nao-structural): ${near.length}\n`)

  // The matched-placebo pool: every event, indexed by the strata that matter.
  const poolKey = (e: Event) =>
    `${e.symbol}|${e.timeframe}|${e.direction}|${excBucket(e.excursionAtr)}|${wickBucket(e.wickBodyRatio)}`
  const pool = new Map<string, Event[]>()
  for (const e of events) {
    const k = poolKey(e)
    if (!pool.has(k)) pool.set(k, [])
    pool.get(k)!.push(e)
  }

  function measure(rows: Event[], h: number): { ev: Agg; rand: Agg; plac: Agg } {
    const ev = emptyAgg()
    const rand = emptyAgg()
    const plac = emptyAgg()
    for (const m of rows) {
      const f = `${m.symbol}_${m.timeframe}.json`
      const d = fixtures.get(f)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (o) add(ev, o)

      // Random control: same symbol, timeframe, direction, nearby in time.
      const series = atrCache.get(f)!
      const lo = Math.max(0, m.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - h - 1, m.index + CONTROL_WINDOW)
      if (hi > lo) {
        for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
          const j = lo + Math.floor(random() * (hi - lo))
          const co = forward(d.candles, j, h, m.direction, series[j] * d.candles[j].close)
          if (co) add(rand, co)
        }
      }

      // Placebo: another candle that looks like this one — same symbol, TF,
      // direction, excursion bucket and wick bucket, within the same stretch of
      // time. This is what separates "proximity matters" from "big candle".
      const peers = (pool.get(poolKey(m)) ?? []).filter(
        (p) => p !== m && Math.abs(p.index - m.index) <= CONTROL_WINDOW,
      )
      for (const p of peers) {
        const po = forward(d.candles, p.index, h, p.direction, p.atr)
        if (po) add(plac, po)
      }
    }
    return { ev, rand, plac }
  }

  function line(label: string, rows: Event[], h = PRIMARY_H): void {
    if (rows.length === 0) {
      console.log(`${label.padEnd(26)} ${String(0).padStart(5)}  —`)
      return
    }
    const { ev, rand, plac } = measure(rows, h)
    const s = stat(ev)
    console.log(
      `${label.padEnd(26)} ${fmt(s)} | ${(stat(rand).win.toFixed(1) + '%').padStart(5)} ` +
        `${zProp(ev, rand).toFixed(2).padStart(5)} | ` +
        `${(plac.n ? stat(plac).win.toFixed(1) + '%' : '—').padStart(14)} ` +
        `${(plac.n ? zProp(ev, plac).toFixed(2) : '—').padStart(5)}`,
    )
  }

  // --- P4.1/2 distribuicao ---------------------------------------------------
  console.log('## P4.2 — near-miss por distancia ao structural vivo\n')
  const counts = new Map<string, number>()
  for (const e of near) counts.set(gapBucket(e.structGapAtr), (counts.get(gapBucket(e.structGapAtr)) ?? 0) + 1)
  for (const k of [...counts.keys()].sort()) console.log(`  ${k.padEnd(18)} ${counts.get(k)}`)
  report.buckets = Object.fromEntries(counts)

  // --- P4.3 grupos -----------------------------------------------------------
  const g = {
    A: marks.filter((m) => m.hasStructural),
    B: marks.filter((m) => !m.hasStructural),
    C: near.filter((e) => e.structGapAtr !== null && e.structGapAtr <= 0.5),
    D: near.filter((e) => e.structGapAtr !== null && e.structGapAtr > 0.5 && e.structGapAtr <= 1.0),
    E: near.filter((e) => e.structGapAtr !== null && e.structGapAtr > 1.0 && e.structGapAtr <= 2.0),
    F: near.filter((e) => e.structGapAtr === null || e.structGapAtr > 2.0),
  }
  console.log('\n## P4.3/4 — grupos de comparacao (h=5 principal)\n')
  console.log(HEAD)
  line('A PISO com structural', g.A)
  line('B PISO sem structural', g.B)
  line('C near <=0.5 ATR', g.C)
  line('D near 0.5-1.0', g.D)
  line('E near 1.0-2.0', g.E)
  line('F near >2.0 / sem', g.F)

  console.log('\n### outros horizontes\n')
  console.log(HEAD)
  for (const h of HORIZONS) {
    line(`C near <=0.5  h=${h}`, g.C, h)
    line(`A PISO c/ str h=${h}`, g.A, h)
  }
  report.groups = Object.fromEntries(
    Object.entries(g).map(([k, rows]) => [
      k,
      Object.fromEntries(
        HORIZONS.map((h) => {
          const { ev, rand, plac } = measure(rows, h)
          return [h, { event: stat(ev), random: stat(rand), placebo: stat(plac), zRand: zProp(ev, rand), zPlac: zProp(ev, plac) }]
        }),
      ),
    ]),
  )

  // --- P4.5 excursao ---------------------------------------------------------
  console.log('\n## P4.5 — proximidade a excursao FIXA (o confound que a P3 apontou)\n')
  console.log(HEAD)
  for (const b of ['exc1.0-1.25', 'exc1.25-1.75', 'exc1.75+']) {
    line(`${b} C <=0.5`, g.C.filter((e) => excBucket(e.excursionAtr) === b))
    line(`${b} F >2/sem`, g.F.filter((e) => excBucket(e.excursionAtr) === b))
  }
  console.log('\n### delta estratificado C vs F (TF x dir x excursao x pavio x bloco x familia)\n')
  const stratum = (e: Event) =>
    `${e.timeframe}|${e.direction}|${excBucket(e.excursionAtr)}|${wickBucket(e.wickBodyRatio)}|Q${Math.min(3, Math.floor(e.frac * 4))}|${e.families.join('+')}`
  for (const h of HORIZONS) {
    const strata = new Map<string, { a: Agg; b: Agg }>()
    for (const [rows, side] of [
      [g.C, 'a'],
      [g.F, 'b'],
    ] as [Event[], 'a' | 'b'][]) {
      for (const e of rows) {
        const d = fixtures.get(`${e.symbol}_${e.timeframe}.json`)
        if (!d) continue
        const o = forward(d.candles, e.index, h, e.direction, e.atr)
        if (!o) continue
        const k = stratum(e)
        if (!strata.has(k)) strata.set(k, { a: emptyAgg(), b: emptyAgg() })
        add(strata.get(k)![side], o)
      }
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
      `h=${String(h).padStart(2)}  estratos ${String(usable).padStart(3)}  n(C)=${String(nA).padStart(3)} n(F)=${String(nB).padStart(3)}  ` +
        `delta MFE>MAE = ${weight > 0 ? ((100 * weighted) / weight).toFixed(1) : '—'}pp`,
    )
  }

  // --- P4.6/7/8/9 ------------------------------------------------------------
  const slice = (label: string, keyOf: (e: Event) => string | null, rows: Event[]): void => {
    console.log(`\n## ${label}\n`)
    console.log(HEAD)
    const groups = new Map<string, Event[]>()
    for (const e of rows) {
      const k = keyOf(e)
      if (k === null) continue
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)!.push(e)
    }
    for (const k of [...groups.keys()].sort()) line(k, groups.get(k)!)
  }
  slice('P4.6 — tipo do structural proximo (C, <=0.5)', (e) => e.structEvent, g.C)
  slice('P4.7 — idade do structural proximo (C)', (e) =>
    e.structAge === null ? null : ageBucket(e.structAge), g.C)
  slice('P4.8 — a familia que o near-miss ja tem (C)', (e) => e.families[0] ?? null, g.C)
  console.log('\n## P4.9 — `fair` como confound\n')
  console.log(HEAD)
  line('C <=0.5 SEM fair', g.C.filter((e) => !e.hasFair))
  line('C <=0.5 COM fair', g.C.filter((e) => e.hasFair))

  // --- P4.10/11 --------------------------------------------------------------
  console.log('\n## P4.10/11 — timeframe e direcao (C, <=0.5)\n')
  console.log(HEAD)
  for (const tf of ['15m', '1h', '4h']) {
    line(`${tf} C <=0.5`, g.C.filter((e) => e.timeframe === tf))
    line(`${tf} PISO real`, marks.filter((m) => m.timeframe === tf))
  }
  for (const dir of ['bullish', 'bearish'] as Direction[])
    line(`${dir} C <=0.5`, g.C.filter((e) => e.direction === dir))

  // --- P4.12 -----------------------------------------------------------------
  console.log('\n## P4.12 — temporalidade (C, <=0.5)\n')
  console.log(HEAD)
  line('DISC (70%)', g.C.filter((e) => e.frac < DISCOVERY_FRAC))
  line('HOLD (30%)', g.C.filter((e) => e.frac >= DISCOVERY_FRAC))
  for (let b = 0; b < 4; b += 1)
    line(`Q${b + 1}`, g.C.filter((e) => e.frac >= b / 4 && e.frac < (b + 1) / 4))

  // --- P4.13 -----------------------------------------------------------------
  console.log('\n## P4.13 — robustez por simbolo (C, <=0.5)\n')
  const symCount = new Map<string, number>()
  for (const e of g.C) symCount.set(e.symbol, (symCount.get(e.symbol) ?? 0) + 1)
  const top = [...symCount].sort((a, b) => b[1] - a[1])
  console.log(
    `simbolos com near-miss <=0.5: ${symCount.size} para ${g.C.length} eventos ` +
      `(top5: ${top.slice(0, 5).map(([s, n]) => `${s}=${n}`).join(' ')})`,
  )
  const perSym: [string, number, number][] = []
  for (const [sym, n] of symCount) {
    if (n < 3) continue
    const rows = g.C.filter((e) => e.symbol === sym)
    const { ev, plac } = measure(rows, PRIMARY_H)
    if (plac.n === 0) continue
    perSym.push([sym, stat(ev).win - stat(plac).win, n])
  }
  perSym.sort((a, b) => b[1] - a[1])
  const better = perSym.filter(([, d]) => d > 0).length
  console.log(
    `  comparaveis (>=3 eventos e placebo): ${perSym.length}` +
      (perSym.length
        ? ` — melhoram ${better} (${((100 * better) / perSym.length).toFixed(0)}%), mediana ${perSym[Math.floor(perSym.length / 2)][1].toFixed(1)}pp`
        : ''),
  )
  if (perSym.length)
    console.log('  ' + perSym.slice(0, 5).map(([s, d, n]) => `${s} ${d > 0 ? '+' : ''}${d.toFixed(0)}pp(n=${n})`).join('  '))
  report.per_symbol = perSym.map(([s, d, n]) => ({ symbol: s, delta: d, n }))

  // --- P4.14 -----------------------------------------------------------------
  console.log('\n## P4.14 — cobertura contrafactual (research, NAO proposta)\n')
  console.log('recorte        PISO  +C(<=0.5)   novo total   crescimento')
  const cov = (label: string, pred: (e: Event) => boolean) => {
    const a = marks.filter(pred).length
    const b = g.C.filter(pred).length
    console.log(
      `${label.padEnd(14)} ${String(a).padStart(4)}  ${String(b).padStart(9)}   ` +
        `${String(a + b).padStart(10)}   ${a ? ((100 * b) / a).toFixed(0) : '—'}%`,
    )
  }
  cov('PAINEL', () => true)
  for (const tf of ['15m', '1h', '4h']) cov(tf, (e) => e.timeframe === tf)
  for (const dir of ['bullish', 'bearish'] as Direction[]) cov(dir, (e) => e.direction === dir)

  // --- P4.15 casos reais -----------------------------------------------------
  console.log('\n## P4.15 — casos reais (BTC / ETH / SOL)\n')
  const show = (label: string, e: Event): void => {
    const d = fixtures.get(`${e.symbol}_${e.timeframe}.json`)!
    const o = forward(d.candles, e.index, PRIMARY_H, e.direction, e.atr)
    console.log(
      `${label.padEnd(18)} ${e.symbol} ${e.timeframe} ${e.timestamp}  ${e.direction.padEnd(7)} ` +
        `exc=${e.excursionAtr.toFixed(2)} pavio=${e.wickBodyRatio.toFixed(1)} ` +
        `fam=${(e.families.join('+') || '—').padEnd(24)} ` +
        `str=${e.structGapAtr === null ? '—' : e.structGapAtr.toFixed(2) + ' ATR'} ${(e.structEvent ?? '').padEnd(20)} ` +
        `-> h5 MFE=${o ? o.mfe.toFixed(2) : '—'} MAE=${o ? o.mae.toFixed(2) : '—'} ${o ? (o.won ? 'OK' : 'falhou') : ''}`,
    )
  }
  const majors = (e: Event) => ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'].includes(e.symbol)
  const withOutcome = (e: Event) => {
    const d = fixtures.get(`${e.symbol}_${e.timeframe}.json`)!
    return forward(d.candles, e.index, PRIMARY_H, e.direction, e.atr)
  }
  const cGood = g.C.filter((e) => majors(e) && withOutcome(e)?.won)
  const cBad = g.C.filter((e) => majors(e) && withOutcome(e) && !withOutcome(e)!.won)
  const dAny = g.D.filter(majors)
  const aAny = g.A.filter(majors)
  if (cGood[0]) show('A) near<=0.5 OK', cGood[0])
  if (cBad[0]) show('B) near<=0.5 falha', cBad[0])
  if (dAny[0]) show('C) near 0.5-1.0', dAny[0])
  if (aAny[0]) show('D) PISO real', aAny[0])

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
}

main()
