/**
 * P7 — are the levels dying too early, or is the lifecycle right?
 *
 * P6 left one number standing above every other in the funnel: after a
 * candidate has cleared the envelope, the excursion and the wick gates,
 * **80-86% of them still fail** — and they fail while the map around them is
 * full. 90 of 96 bullish H4 candidates and 279 of 295 bearish ones have two or
 * more families overlapping the swept range; only 13 and 57 have two or more
 * *alive*. No other gate removes anything close to that, and nobody has ever
 * asked whether the deaths are correct.
 *
 * They are not one question, because the four sources do not share a death.
 * `POIZone` retires on a close beyond its far boundary and explicitly survives
 * being traded through. A liquidation band ends the moment price touches it,
 * which for a liquidation is the entire event. A structural reference stands
 * until a later event supersedes its line. And `LiquidityZone` records **two**
 * deaths, `invalidated_at` (the wick that grabbed the resting orders) and
 * `breached_at` (the close that spent the level) — its own docstring says the
 * first leaves the level "surviving as memory" and only the second means it
 * "stopped being a pool". The rule retires it on the first.
 *
 * So the audit is: map what kills each source, classify every death causally,
 * and then measure what price actually did afterwards at that price. A level
 * that is revisited and rejected after it was declared dead is the empirical
 * form of "died too early". Only after that does a counterfactual get built,
 * one source at a time and only where the domain model itself supplies the
 * alternative semantics.
 *
 * Nothing in production changes. Thresholds, anchors, families and the rule
 * are the shipped ones; "keep every level forever" is not tested, because the
 * first probe in `defendedLevels`' own docstring already measured what that
 * does (998 marks, ~100 per chart).
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoLifecycleAudit.ts --json research/piso_lifecycle_baseline.json
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import {
  diagnoseDefendedMarks,
  meanTrueRangePctSeries,
  type LevelFamily,
} from '../src/utils/defendedLevels.ts'
import { structureLineEndTime } from '../src/utils/structureLines.ts'
import { sourcedDefenceLevels, type LevelSource, type SourcedLevel } from './levelSources.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const
const PRIMARY_H = 5
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250
const MIN_FAMILIES = 2

type Direction = 'bullish' | 'bearish'

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
// P7.1 — the states, each only where its source can actually reach it.
// ---------------------------------------------------------------------------

export type LevelState =
  | 'UNBORN'
  | 'ALIVE'
  | 'TOUCHED_BUT_ALIVE'
  | 'DEAD_BY_WICK'
  | 'DEAD_BY_CLOSE'
  | 'DEAD_BY_STRUCTURE'

/**
 * The state of a level at `ts`, reading nothing dated after `ts`.
 *
 * `TOUCHED_BUT_ALIVE` exists for exactly one source: an equal-level pool whose
 * wick-death has already happened but whose close-death has not. Production
 * calls that state dead; the domain model calls it memory. Naming it is what
 * lets the two be counted separately without changing either.
 */
export function levelStateAt(l: SourcedLevel, ts: string): LevelState {
  if (l.born > ts) return 'UNBORN'
  const dead = l.died !== null && l.died < ts
  if (!dead) {
    if (l.altDied !== null && l.altDied < ts) return 'TOUCHED_BUT_ALIVE'
    return 'ALIVE'
  }
  if (l.deathRule === 'structure') return 'DEAD_BY_STRUCTURE'
  if (l.deathRule === 'close') return 'DEAD_BY_CLOSE'
  // A wick-killed pool that has since also been closed through is spent by
  // either reading; one that has not is the group P7.8 is about.
  if (l.altDied !== null && l.altDied < ts) return 'DEAD_BY_CLOSE'
  return 'DEAD_BY_WICK'
}

/** Alive under the shipped rule. */
const liveNow = (l: SourcedLevel, ts: string): boolean => {
  const s = levelStateAt(l, ts)
  return s === 'ALIVE' || s === 'TOUCHED_BUT_ALIVE' ? l.born <= ts && !(l.died !== null && l.died < ts) : false
}

/**
 * Alive under V1: an equal-level pool stands until a close lands beyond it.
 *
 * The only counterfactual this audit builds, and the only one the sources
 * justify: `POIZone` already survives a touch, a liquidation band is *defined*
 * by its touch, a structural line is ended by an event rather than by price,
 * and `LiquidityZone` is the one source that records a second, later death its
 * own documentation calls the real one.
 */
const liveNowV1 = (l: SourcedLevel, ts: string): boolean => {
  if (l.born > ts) return false
  if (l.source !== 'equal_highs' && l.source !== 'equal_lows') return liveNow(l, ts)
  const death = l.altDied
  return death === null || death >= ts
}

// ---------------------------------------------------------------------------
// Forward reading.
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

function pctl(values: number[], q: number): number {
  if (values.length === 0) return 0
  const s = [...values].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.max(0, Math.floor(q * (s.length - 1))))]
}

// ---------------------------------------------------------------------------
// Records.
// ---------------------------------------------------------------------------

/** A candidate that already cleared reclaim + excursion + wick. */
interface Cand {
  key: string
  symbol: string
  timeframe: string
  timestamp: string
  index: number
  direction: Direction
  low: number
  high: number
  atr: number
  frac: number
  mapFamilies: LevelFamily[]
  liveFamilies: LevelFamily[]
  v1Families: LevelFamily[]
  isMark: boolean
  isMarkV1: boolean
  /** Families the shipped rule lost to a death, and how long ago it happened. */
  lostBy: Map<LevelFamily, LevelState>
  lostSource: LevelSource[]
  deathAgeBars: number | null
}

/** A level's death, and what price did at that price afterwards. */
interface Death {
  key: string
  timeframe: string
  source: LevelSource
  side: string
  state: LevelState
  index: number
  lifeBars: number
  revisited: boolean
  barsToRevisit: number | null
  rejected: boolean | null
  closedThrough: boolean | null
  reactionAtr: number | null
  /** The revisit candle and the direction it would be defended in, so the
   *  rejection rate can be put against a control matched on both. */
  revisitIndex: number | null
  revisitDir: Direction | null
}

const ageBucket = (v: number): string =>
  v <= 1 ? 'A 1 vela' : v <= 5 ? 'B 2-5' : v <= 20 ? 'C 6-20' : v <= 100 ? 'D 21-100' : 'E 100+'

function analyse(
  data: DashboardData,
): { cands: Cand[]; deaths: Death[]; levels: SourcedLevel[]; zombie: Zombie } {
  const levels = sourcedDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const atrSeries = meanTrueRangePctSeries(data.candles)
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const n = data.candles.length
  const key = `${data.symbol}_${data.timeframe}.json`

  // --- candidates -----------------------------------------------------------
  const prodLevels = levels
  const cands: Cand[] = []
  for (const c of diagnoseDefendedMarks(data, prodLevels, { causalAtr: true })) {
    if (c.failed.some((g) => g !== 'families')) continue
    const i = idx.get(c.timestamp)
    if (i === undefined) continue
    const atr = c.atrPct * data.candles[i].close
    if (atr <= 0) continue
    const low = Math.min(c.edge, c.price)
    const high = Math.max(c.edge, c.price)

    const map = new Set<LevelFamily>()
    const live = new Set<LevelFamily>()
    const v1 = new Set<LevelFamily>()
    const lostBy = new Map<LevelFamily, LevelState>()
    const lostSource: LevelSource[] = []
    let newestDeath: string | null = null
    for (const l of levels) {
      if (l.high < low || l.low > high) continue
      if (l.born > c.timestamp) continue
      map.add(l.family)
      const state = levelStateAt(l, c.timestamp)
      if (liveNow(l, c.timestamp)) live.add(l.family)
      else {
        lostBy.set(l.family, state)
        lostSource.push(l.source)
        if (l.died !== null && (newestDeath === null || l.died > newestDeath)) newestDeath = l.died
      }
      if (liveNowV1(l, c.timestamp)) v1.add(l.family)
    }
    for (const f of live) lostBy.delete(f)
    const deathIdx = newestDeath !== null ? (idx.get(newestDeath) ?? null) : null
    cands.push({
      key,
      symbol: data.symbol,
      timeframe: data.timeframe,
      timestamp: c.timestamp,
      index: i,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      low,
      high,
      atr,
      frac: i / n,
      mapFamilies: [...map].sort(),
      liveFamilies: [...live].sort(),
      v1Families: [...v1].sort(),
      isMark: live.size >= MIN_FAMILIES,
      isMarkV1: v1.size >= MIN_FAMILIES,
      lostBy,
      lostSource,
      deathAgeBars: deathIdx !== null ? i - deathIdx : null,
    })
  }

  // --- deaths and what followed --------------------------------------------
  const deaths: Death[] = []
  for (const l of levels) {
    if (l.died === null) continue
    const di = idx.get(l.died)
    const bi = idx.get(l.born)
    if (di === undefined || bi === undefined) continue
    // Look for the first candle after the death whose range re-enters the band.
    let j: number | null = null
    for (let k = di + 1; k < Math.min(n, di + 1 + 60); k += 1) {
      if (data.candles[k].high >= l.low && data.candles[k].low <= l.high) {
        j = k
        break
      }
    }
    let rejected: boolean | null = null
    let closedThrough: boolean | null = null
    let reaction: number | null = null
    let revisitDir: Direction | null = null
    if (j !== null) {
      const prev = data.candles[j - 1]
      // Which way the level would be defended, judged by where price came from.
      const dir: Direction | null =
        prev.close > l.high ? 'bullish' : prev.close < l.low ? 'bearish' : null
      const atr = atrSeries[j] * data.candles[j].close
      if (dir !== null && atr > 0) {
        const o = forward(data.candles, j, PRIMARY_H, dir, atr)
        if (o) {
          revisitDir = dir
          rejected = o.won
          reaction = o.mfe - o.mae
          closedThrough = data.candles
            .slice(j, Math.min(n, j + PRIMARY_H + 1))
            .some((cc) => (dir === 'bullish' ? cc.close < l.low : cc.close > l.high))
        }
      }
    }
    deaths.push({
      key,
      timeframe: data.timeframe,
      source: l.source,
      side: l.side,
      state: levelStateAt(l, data.candles[Math.min(n - 1, di + 1)].timestamp),
      index: di,
      lifeBars: di - bi,
      revisited: j !== null,
      barsToRevisit: j !== null ? j - di : null,
      rejected,
      closedThrough,
      reactionAtr: reaction,
      revisitIndex: rejected !== null ? j : null,
      revisitDir,
    })
  }

  // --- the zombie window: wick-dead but not yet closed through ---------------
  const zombie: Zombie = { levels: 0, bars: 0, revisits: 0, rejected: 0, through: 0 }
  for (const l of levels) {
    if (l.source !== 'equal_highs' && l.source !== 'equal_lows') continue
    if (l.died === null) continue
    const from = idx.get(l.died)
    if (from === undefined) continue
    const to = l.altDied !== null ? (idx.get(l.altDied) ?? n) : n
    if (to <= from) continue
    zombie.levels += 1
    zombie.bars += to - from
    for (let k = from + 1; k < Math.min(to, n); k += 1) {
      if (data.candles[k].high < l.low || data.candles[k].low > l.high) continue
      const prev = data.candles[k - 1]
      const dir: Direction | null =
        prev.close > l.high ? 'bullish' : prev.close < l.low ? 'bearish' : null
      const atr = atrSeries[k] * data.candles[k].close
      if (dir === null || atr <= 0) continue
      const o = forward(data.candles, k, PRIMARY_H, dir, atr)
      if (!o) continue
      zombie.revisits += 1
      if (o.won) zombie.rejected += 1
      if (
        data.candles
          .slice(k, Math.min(n, k + PRIMARY_H + 1))
          .some((cc) => (dir === 'bullish' ? cc.close < l.low : cc.close > l.high))
      )
        zombie.through += 1
    }
  }

  return { cands, deaths, levels, zombie }
}

interface Zombie {
  levels: number
  bars: number
  revisits: number
  rejected: number
  through: number
}

// ---------------------------------------------------------------------------

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const atrCache = new Map<string, number[]>()
  const cands: Cand[] = []
  const deaths: Death[] = []
  const zombie: Zombie = { levels: 0, bars: 0, revisits: 0, rejected: 0, through: 0 }
  const levelStats = new Map<string, { n: number; lives: number[]; died: number }>()
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
    fixtures.set(f, d)
    atrCache.set(f, meanTrueRangePctSeries(d.candles))
    const r = analyse(d)
    cands.push(...r.cands)
    deaths.push(...r.deaths)
    zombie.levels += r.zombie.levels
    zombie.bars += r.zombie.bars
    zombie.revisits += r.zombie.revisits
    zombie.rejected += r.zombie.rejected
    zombie.through += r.zombie.through
    const idx = new Map(d.candles.map((c, i) => [c.timestamp, i]))
    for (const l of r.levels) {
      const k = l.source
      if (!levelStats.has(k)) levelStats.set(k, { n: 0, lives: [], died: 0 })
      const s = levelStats.get(k)!
      s.n += 1
      const bi = idx.get(l.born) ?? 0
      const di = l.died !== null ? (idx.get(l.died) ?? null) : null
      if (di !== null) {
        s.died += 1
        s.lives.push(di - bi)
      } else s.lives.push(d.candles.length - bi)
    }
  }

  const random = rng(20260910)
  const report: Record<string, unknown> = { skipped }
  console.log('# PISO — P7: o lifecycle dos niveis\n')
  console.log(
    `painel: ${fixtures.size} fixtures — modo causal (producao), regra inalterada\n` +
      `candidatos que ja passaram reclaim + excursion + pavio: ${cands.length}\n`,
  )

  // --- P7.0 ----------------------------------------------------------------
  console.log('## P7.0 — como cada fonte nasce e morre (a implementacao, nao a suposicao)\n')
  console.log(
    '  fonte          familia      born                 died                        regra\n' +
      '  bos/choch      structural   event.timestamp      standingUntil ->            evento posterior\n' +
      '                                                   structureLineEndTime        supera a linha\n' +
      '  poi            order        created_at           invalidated_at              CLOSE alem da borda\n' +
      '                                                                               ("trading back inside\n' +
      '                                                                               does not retire it")\n' +
      '  equal_highs    resting      formed_at            invalidated_at              WICK atravessa\n' +
      '  equal_lows     resting      formed_at            (breached_at existe e        (o close e o\n' +
      '                                                    NAO e usado)                breached_at)\n' +
      '  liquidation    resting      start_time           end_time                    WICK toca o nivel\n' +
      '  poc/value_area fair         vp.start_timestamp   —                           nunca morre\n',
  )
  console.log(
    '  as tres mortes NAO sao a mesma semantica. o POI sobrevive ao toque por\n' +
      '  desenho; a banda de liquidacao E consumida no toque, porque e o que uma\n' +
      '  liquidacao faz; o pool de niveis iguais morre no toque embora o proprio\n' +
      '  `LiquidityZone` diga que ali ele "sobrevive como memoria" e so o CLOSE\n' +
      '  (`breached_at`) significa que "deixou de ser um pool". `fair` nao morre e\n' +
      '  ainda carrega hindsight (P7.11): fica fora de toda conclusao principal.\n',
  )

  // --- P7.4 ----------------------------------------------------------------
  console.log('## P7.4 — vida por fonte\n')
  console.log('  fonte              niveis   % que morrem   vida p50   p75   p90')
  for (const k of [...levelStats.keys()].sort()) {
    const s = levelStats.get(k)!
    console.log(
      `  ${k.padEnd(18)} ${String(s.n).padStart(6)} ${((100 * s.died) / s.n).toFixed(1).padStart(14)} ` +
        `${String(pctl(s.lives, 0.5)).padStart(10)} ${String(pctl(s.lives, 0.75)).padStart(5)} ` +
        `${String(pctl(s.lives, 0.9)).padStart(5)}`,
    )
  }

  // --- P7.3 ----------------------------------------------------------------
  console.log('\n## P7.3 — decompor os 80-86%: quem matou o candidato\n')
  const rich = cands.filter((c) => c.mapFamilies.length >= MIN_FAMILIES && !c.isMark)
  console.log(
    `  candidatos com >=2 familias CARTOGRAFADAS que nao viram PISO: ${rich.length} ` +
      `de ${cands.filter((c) => c.mapFamilies.length >= MIN_FAMILIES).length}\n`,
  )
  console.log('  familia perdida    estado                       vezes')
  const cause = new Map<string, number>()
  for (const c of rich) {
    for (const [fam, st] of c.lostBy) cause.set(`${fam}|${st}`, (cause.get(`${fam}|${st}`) ?? 0) + 1)
  }
  for (const [k, v] of [...cause.entries()].sort((a, b) => b[1] - a[1])) {
    const [fam, st] = k.split('|')
    console.log(`  ${fam.padEnd(18)} ${st.padEnd(26)} ${String(v).padStart(6)}`)
  }
  console.log('\n  fonte cujo nivel morto teria dado a familia que faltava')
  const src = new Map<string, number>()
  for (const c of rich) for (const s of new Set(c.lostSource)) src.set(s, (src.get(s) ?? 0) + 1)
  for (const [k, v] of [...src.entries()].sort((a, b) => b[1] - a[1]))
    console.log(`  ${k.padEnd(20)} ${String(v).padStart(6)}`)

  // --- P7.6 ----------------------------------------------------------------
  console.log('\n## P7.6 — ha quanto tempo o nivel tinha morrido\n')
  const ages = new Map<string, number>()
  for (const c of rich) {
    if (c.deathAgeBars === null) continue
    ages.set(ageBucket(c.deathAgeBars), (ages.get(ageBucket(c.deathAgeBars)) ?? 0) + 1)
  }
  for (const k of ['A 1 vela', 'B 2-5', 'C 6-20', 'D 21-100', 'E 100+'])
    console.log(`  ${k.padEnd(12)} ${String(ages.get(k) ?? 0).padStart(5)}`)

  // --- P7.5 ----------------------------------------------------------------
  console.log('\n## P7.5 — o que o preco fez DEPOIS da morte (a pergunta principal)\n')
  console.log(
    '  revisit = o preco voltou a tocar a faixa em ate 60 velas; rejeicao e\n' +
      '  close-through sao lidos no primeiro revisit, h=5, na direcao de onde o\n' +
      '  preco veio.\n',
  )
  console.log(
    '  fonte              mortes  revisit%  velas ate  rejeicao%  controle      z  through%  reacao ATR',
  )
  const bySource = new Map<string, Death[]>()
  for (const d of deaths) {
    if (!bySource.has(d.source)) bySource.set(d.source, [])
    bySource.get(d.source)!.push(d)
  }
  const deathRows: Record<string, unknown>[] = []
  for (const k of [...bySource.keys()].sort()) {
    const rows = bySource.get(k)!
    const rev = rows.filter((d) => d.revisited)
    const judged = rev.filter((d) => d.rejected !== null)
    // The rejection rate means nothing on its own — 50% is roughly what any
    // candle scores. Each revisit gets controls from the same fixture, same
    // direction, same stretch of time.
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const d of judged) {
      const data = fixtures.get(d.key)
      if (!data || d.revisitIndex === null || d.revisitDir === null) continue
      const series = atrCache.get(d.key)!
      const o = forward(
        data.candles,
        d.revisitIndex,
        PRIMARY_H,
        d.revisitDir,
        series[d.revisitIndex] * data.candles[d.revisitIndex].close,
      )
      if (o) add(ev, o)
      const lo = Math.max(0, d.revisitIndex - CONTROL_WINDOW)
      const hi = Math.min(data.candles.length - PRIMARY_H - 1, d.revisitIndex + CONTROL_WINDOW)
      if (hi <= lo) continue
      for (let t = 0; t < 5; t += 1) {
        const jj = lo + Math.floor(random() * (hi - lo))
        const co = forward(
          data.candles,
          jj,
          PRIMARY_H,
          d.revisitDir,
          series[jj] * data.candles[jj].close,
        )
        if (co) add(ctl, co)
      }
    }
    const row = {
      source: k,
      deaths: rows.length,
      revisit: (100 * rev.length) / Math.max(1, rows.length),
      bars: pctl(rev.map((d) => d.barsToRevisit ?? 0), 0.5),
      rejected: (100 * judged.filter((d) => d.rejected).length) / Math.max(1, judged.length),
      through: (100 * judged.filter((d) => d.closedThrough).length) / Math.max(1, judged.length),
      reaction: judged.reduce((s, d) => s + (d.reactionAtr ?? 0), 0) / Math.max(1, judged.length),
      control: ctl.n ? (100 * ctl.won) / ctl.n : 0,
      z: zProp(ev, ctl),
    }
    deathRows.push(row)
    console.log(
      `  ${k.padEnd(18)} ${String(row.deaths).padStart(6)} ${row.revisit.toFixed(1).padStart(9)} ` +
        `${String(row.bars).padStart(10)} ${row.rejected.toFixed(1).padStart(10)} ` +
        `${row.control.toFixed(1).padStart(9)} ${row.z.toFixed(2).padStart(6)} ` +
        `${row.through.toFixed(1).padStart(9)} ${row.reaction.toFixed(2).padStart(11)}`,
    )
  }
  report.deaths = deathRows

  // --- P7.5b: the one source that separated, taken apart ---------------------
  console.log(
    '\n  o unico corte acima do controle e `choch`. antes de chamar isso de\n' +
      '  evidencia, ele e quebrado por TF, bloco temporal e simbolo — o mesmo\n' +
      '  crivo que derrubou as hipoteses de P2 a P5.\n',
  )
  function revisitCut(label: string, rows: Death[]): void {
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const d of rows) {
      const data = fixtures.get(d.key)
      if (!data || d.revisitIndex === null || d.revisitDir === null) continue
      const series = atrCache.get(d.key)!
      const o = forward(
        data.candles,
        d.revisitIndex,
        PRIMARY_H,
        d.revisitDir,
        series[d.revisitIndex] * data.candles[d.revisitIndex].close,
      )
      if (o) add(ev, o)
      const lo = Math.max(0, d.revisitIndex - CONTROL_WINDOW)
      const hi = Math.min(data.candles.length - PRIMARY_H - 1, d.revisitIndex + CONTROL_WINDOW)
      if (hi <= lo) continue
      for (let t = 0; t < 5; t += 1) {
        const jj = lo + Math.floor(random() * (hi - lo))
        const co = forward(
          data.candles,
          jj,
          PRIMARY_H,
          d.revisitDir,
          series[jj] * data.candles[jj].close,
        )
        if (co) add(ctl, co)
      }
    }
    console.log(
      `  ${label.padEnd(26)} ${String(ev.n).padStart(5)} ${((ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1) + '%').padStart(8)} | ` +
        `${((ctl.n ? (100 * ctl.won) / ctl.n : 0).toFixed(1) + '%').padStart(8)} ${zProp(ev, ctl).toFixed(2).padStart(6)}`,
    )
  }
  const chochDeaths = deaths.filter((d) => d.source === 'choch' && d.rejected !== null)
  const bosDeaths = deaths.filter((d) => d.source === 'bos' && d.rejected !== null)
  console.log('  corte                          n  rejeicao | controle      z')
  revisitCut('choch, tudo', chochDeaths)
  revisitCut('bos, tudo (contraste)', bosDeaths)
  for (const tf of ['15m', '1h', '4h'])
    revisitCut(`choch ${tf}`, chochDeaths.filter((d) => d.timeframe === tf))
  for (let q = 0; q < 4; q += 1)
    revisitCut(
      `choch Q${q + 1}`,
      chochDeaths.filter((d) => {
        const data = fixtures.get(d.key)
        return data ? Math.floor((d.index / data.candles.length) * 4) === q : false
      }),
    )
  {
    const per = new Map<string, { n: number; won: number }>()
    for (const d of chochDeaths) {
      const sym = d.key.split('_')[0]
      const data = fixtures.get(d.key)
      if (!data || d.revisitIndex === null || d.revisitDir === null) continue
      const series = atrCache.get(d.key)!
      const o = forward(
        data.candles,
        d.revisitIndex,
        PRIMARY_H,
        d.revisitDir,
        series[d.revisitIndex] * data.candles[d.revisitIndex].close,
      )
      if (!o) continue
      if (!per.has(sym)) per.set(sym, { n: 0, won: 0 })
      per.get(sym)!.n += 1
      if (o.won) per.get(sym)!.won += 1
    }
    const rows = [...per.entries()].filter(([, v]) => v.n >= 5)
    const above = rows.filter(([, v]) => v.won / v.n > 0.5).length
    console.log(
      `\n  por simbolo (n>=5): ${rows.length} simbolos, ${above} acima de 50% ` +
        `(${rows.length ? ((100 * above) / rows.length).toFixed(0) : '—'}%)`,
    )
  }

  // --- P7.8 ----------------------------------------------------------------
  console.log('\n## P7.8 — invalidated vs breached: a janela zumbi dos pools de niveis iguais\n')
  console.log(
    `  pools com wick-morte anterior ao close-morte: ${zombie.levels}\n` +
      `  velas nessa janela:                          ${zombie.bars} (media ${(zombie.bars / Math.max(1, zombie.levels)).toFixed(1)} por pool)\n` +
      `  revisitas dentro da janela:                  ${zombie.revisits}\n` +
      `  delas rejeitadas (MFE>MAE, h=5):             ${zombie.rejected} (${((100 * zombie.rejected) / Math.max(1, zombie.revisits)).toFixed(1)}%)\n` +
      `  delas atravessadas por um close:             ${zombie.through} (${((100 * zombie.through) / Math.max(1, zombie.revisits)).toFixed(1)}%)`,
  )
  const zombieCands = cands.filter(
    (c) => !c.isMark && c.isMarkV1,
  )
  console.log(`\n  candidatos que V1 recuperaria: ${zombieCands.length}`)

  // --- P7.12/14 ------------------------------------------------------------
  console.log('\n## P7.12/14 — V1: o pool vive ate `breached_at` (unica variante justificada)\n')
  console.log(
    '  as outras nao tem base: o POI ja sobrevive ao toque, a banda de liquidacao\n' +
      '  e definida pelo toque, e a linha estrutural termina por evento e nao por\n' +
      '  preco. "ignorar died" nao e testado (P7.13).\n',
  )
  function measure(rows: Cand[], h: number): { ev: Agg; ctl: Agg } {
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const m of rows) {
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (o) add(ev, o)
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
  const line = (label: string, rows: Cand[], h = PRIMARY_H): void => {
    const { ev, ctl } = measure(rows, h)
    const mfe = ev.n ? ev.mfe / ev.n : 0
    const mae = ev.n ? ev.mae / ev.n : 0
    console.log(
      `  ${label.padEnd(30)} ${String(ev.n).padStart(4)} ${mfe.toFixed(2).padStart(6)} ` +
        `${mae.toFixed(2).padStart(6)} ${(mae > 0 ? mfe / mae : 0).toFixed(2).padStart(8)} ` +
        `${((ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1) + '%').padStart(8)} | ` +
        `${((ctl.n ? (100 * ctl.won) / ctl.n : 0).toFixed(1) + '%').padStart(8)} ${zProp(ev, ctl).toFixed(2).padStart(6)}`,
    )
  }
  const HEAD = '  grupo                              n    MFE    MAE  MFE/MAE  MFE>MAE | controle      z'
  console.log(HEAD)
  const prod = cands.filter((c) => c.isMark)
  line('PISO producao', prod)
  line('recuperados por V1', zombieCands)
  line('PISO V1 (producao + recup.)', cands.filter((c) => c.isMarkV1))
  console.log('\n  outros horizontes')
  console.log(HEAD)
  for (const h of HORIZONS) {
    if (h === PRIMARY_H) continue
    line(`h=${h} PISO producao`, prod, h)
    line(`h=${h} recuperados por V1`, zombieCands, h)
  }

  // --- P7.15 ---------------------------------------------------------------
  console.log('\n## P7.15 — cobertura\n')
  console.log('  recorte        PISO   +V1   total   crescimento')
  for (const [label, sel] of [
    ['PAINEL', () => true],
    ['15m', (c: Cand) => c.timeframe === '15m'],
    ['1h', (c: Cand) => c.timeframe === '1h'],
    ['4h', (c: Cand) => c.timeframe === '4h'],
    ['bullish', (c: Cand) => c.direction === 'bullish'],
    ['bearish', (c: Cand) => c.direction === 'bearish'],
  ] as [string, (c: Cand) => boolean][]) {
    const a = prod.filter(sel).length
    const b = zombieCands.filter(sel).length
    console.log(
      `  ${label.padEnd(12)} ${String(a).padStart(5)} ${String(b).padStart(5)} ${String(a + b).padStart(7)} ` +
        `${(a ? ((100 * b) / a).toFixed(0) + '%' : '—').padStart(13)}`,
    )
  }

  // --- P7.16 ---------------------------------------------------------------
  console.log('\n## P7.16 — quatro blocos temporais\n')
  console.log(HEAD)
  for (let q = 0; q < 4; q += 1) {
    line(`Q${q + 1} recuperados`, zombieCands.filter((c) => Math.floor(c.frac * 4) === q))
  }

  // --- P7.17 ---------------------------------------------------------------
  console.log('\n## P7.17 — robustez por simbolo (recuperados)\n')
  {
    const per = new Map<string, { n: number; won: number }>()
    for (const c of zombieCands) {
      const d = fixtures.get(c.key)
      if (!d) continue
      const o = forward(d.candles, c.index, PRIMARY_H, c.direction, c.atr)
      if (!o) continue
      if (!per.has(c.symbol)) per.set(c.symbol, { n: 0, won: 0 })
      per.get(c.symbol)!.n += 1
      if (o.won) per.get(c.symbol)!.won += 1
    }
    const rows = [...per.entries()].filter(([, v]) => v.n >= 3)
    rows.sort((a, b) => b[1].won / b[1].n - a[1].won / a[1].n)
    const rates = rows.map(([, v]) => (100 * v.won) / v.n)
    console.log(
      `  simbolos com recuperados: ${per.size} (${zombieCands.length} eventos); com n>=3: ${rows.length}, ` +
        `mediana ${rates.length ? pctl(rates, 0.5).toFixed(0) + '%' : '—'}`,
    )
    const show = (r: [string, { n: number; won: number }]) =>
      `${r[0]} ${((100 * r[1].won) / r[1].n).toFixed(0)}%(${r[1].n})`
    console.log(`  top5:    ${rows.slice(0, 5).map(show).join('  ') || '—'}`)
    console.log(`  bottom5: ${rows.slice(-5).map(show).join('  ') || '—'}`)
    report.perSymbol = rows.map(([s, v]) => ({ symbol: s, n: v.n, won: v.won }))
  }

  // --- P7.18 ---------------------------------------------------------------
  console.log('\n## P7.18 — casos reais (BTC / ETH / SOL)\n')
  {
    const majors = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    const mine = deaths.filter((d) => majors.includes(d.key.split('_')[0]))
    const show = (tag: string, d: Death | undefined): void => {
      if (!d) {
        console.log(`  ${tag} —`)
        return
      }
      const data = fixtures.get(d.key)!
      console.log(
        `  ${tag} ${d.key.replace('.json', '').padEnd(14)} morte ${data.candles[d.index].timestamp}  ` +
          `${d.source.padEnd(13)} ${d.side.padEnd(7)} vida=${String(d.lifeBars).padStart(3)}v  ` +
          `revisit=${d.revisited ? `${d.barsToRevisit}v` : 'nao'} ` +
          `${d.rejected === null ? '' : d.rejected ? 'REJEITOU' : 'atravessou'}` +
          `${d.reactionAtr !== null ? ` reacao=${d.reactionAtr.toFixed(2)}ATR` : ''}`,
      )
    }
    show('A) morre e o preco atravessa  ', mine.find((d) => d.revisited && d.rejected === false))
    show('B) morre mas o preco rejeita  ', mine.find((d) => d.revisited && d.rejected === true))
    show(
      'C) invalidated, nao breached  ',
      mine.find((d) => d.state === 'DEAD_BY_WICK' && d.revisited),
    )
    show(
      'D) structural recem-morto     ',
      mine.find((d) => (d.source === 'bos' || d.source === 'choch') && d.revisited),
    )
    show('E) order zone em reteste      ', mine.find((d) => d.source === 'poi' && d.revisited))
  }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
  if (skipped.length) console.log(`\nfixtures ignoradas: ${skipped.length}`)
}

// Only when run as a script: `research/pisoLifecycle.test.ts` imports
// `levelStateAt` from here, and importing a module must not run an audit.
if (process.argv[1] && import.meta.filename === process.argv[1]) main()
