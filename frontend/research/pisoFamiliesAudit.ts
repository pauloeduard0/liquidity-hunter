/**
 * P2.0 — is `MIN_FAMILIES = 2` admitting noise?
 *
 * The P1 audit left one axis looking sharp: among candles that already cleared
 * the excursion and wick gates, marks resting on two families measured like the
 * control (MFE/MAE 0.80, 49.3% MFE>MAE, n=205) while three families measured
 * 1.59 and 57.4% — on n=47. That is a hypothesis, not a finding, and this
 * script exists to try to kill it.
 *
 * Nothing is changed. The rule runs exactly as it ships (causal ATR, all four
 * thresholds where they are); the family count is read off the candidates the
 * rule already produces and used only to slice them.
 *
 * The three traps this is built around
 * ------------------------------------
 * 1. **"3" may really be "structural."** A third family is usually the
 *    structural one, so the count and the presence of one particular source are
 *    confounded. Every headline split is therefore repeated holding the family
 *    count fixed and toggling `structural`.
 * 2. **"3" may really be `fair`.** The volume profile is the one input still
 *    built with hindsight (`died = null`, values computed over the whole
 *    window), so any edge that leans on it is an artefact until that is fixed.
 *    Reported separately, never netted in.
 * 3. **n is small.** 47 events do not survive a 70/30 split into anything worth
 *    reading. Every rate carries a two-proportion z against its own matched
 *    control, and the report says so out loud rather than letting a percentage
 *    point look like a result.
 *
 * Descriptive only, and the horizon is h=5 by decision: P1 found the PISO
 * separates from control immediately after the defence and not later, so
 * reading this as a swing setup would be reinterpreting the event.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoFamiliesAudit.ts --json research/piso_families_baseline.json
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
/** Fraction of each series, oldest first, used for discovery. */
const DISCOVERY_FRAC = 0.7
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250
/** A level that retired within this many candles of the mark counts as "just
 *  died" for the lifecycle note (P2.10). Diagnostic only. */
const RECENTLY_DIED = 3

// ---------------------------------------------------------------------------
// Production wiring — identical to `pisoAudit.ts` and to `MainChart`.
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
// Forward reading.
// ---------------------------------------------------------------------------

type Direction = 'bullish' | 'bearish'

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

// ---------------------------------------------------------------------------
// Stats.
// ---------------------------------------------------------------------------

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

interface Stat {
  n: number
  mfe: number
  mae: number
  ratio: number
  win: number
  move: number
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
  }
}

/**
 * Two-proportion z for "this bucket's MFE>MAE rate beats its control's".
 *
 * The control pool is far larger than the event pool, so the event's own n is
 * what governs. Reported as z rather than as a verdict: with 47 events an
 * eight-point gap simply is not resolvable, and the number says that better
 * than an adjective.
 */
function zProp(a: Agg, c: Agg): number {
  if (a.n === 0 || c.n === 0) return 0
  const p1 = a.won / a.n
  const p2 = c.won / c.n
  const p = (a.won + c.won) / (a.n + c.n)
  const se = Math.sqrt(p * (1 - p) * (1 / a.n + 1 / c.n))
  return se > 0 ? (p1 - p2) / se : 0
}

// ---------------------------------------------------------------------------
// Rows.
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
  consumed: number
  /** Levels overlapping the swept range that retired within the last few
   *  candles — context for the future lifecycle study, not a gate. */
  recentlyDied: number
  /** Position of the candle in its own series, 0..1 — the temporal split axis. */
  frac: number
  atr: number
}

/** Overlap predicate, mirrored from the rule for the lifecycle note only. */
function overlaps(l: DefenceLevel, low: number, high: number): boolean {
  return !(l.high < low || l.low > high)
}

function marksFor(data: DashboardData): Mark[] {
  const levels = buildDefenceLevels(data, standingUntilFor(data), { causalAtr: true })
  const cands = diagnoseDefendedMarks(data, levels, { causalAtr: true })
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const atrSeries = meanTrueRangePctSeries(data.candles)
  const n = data.candles.length

  const out: Mark[] = []
  for (const c of cands) {
    if (c.failed.length > 0) continue
    const i = idx.get(c.timestamp)
    if (i === undefined) continue
    const low = Math.min(c.edge, c.price)
    const high = Math.max(c.edge, c.price)
    const cutoff = data.candles[Math.max(0, i - RECENTLY_DIED)].timestamp
    let recentlyDied = 0
    for (const l of levels) {
      if (!overlaps(l, low, high)) continue
      if (l.died !== null && l.died >= cutoff && l.died <= c.timestamp) recentlyDied += 1
    }
    out.push({
      symbol: data.symbol,
      timeframe: data.timeframe,
      timestamp: c.timestamp,
      index: i,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      families: c.families,
      nFam: c.families.length,
      hasStructural: c.families.includes('structural'),
      hasFair: c.families.includes('fair'),
      consumed: c.consumed,
      recentlyDied,
      frac: i / n,
      atr: atrSeries[i] * data.candles[i].close,
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
    `${s.ratio.toFixed(2).padStart(7)} ${(s.win.toFixed(1) + '%').padStart(7)} ${s.move.toFixed(2).padStart(6)}`
  )
}

const HEAD = 'bucket                     n    MFE    MAE  MFE/MAE  MFE>MAE   move | controle MFE/MAE  MFE>MAE     z'

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const marks: Mark[] = []
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
    marks.push(...marksFor(d))
  }

  const report: Record<string, unknown> = { skipped }
  const symbols = new Set([...fixtures.values()].map((d) => d.symbol))
  const candles = [...fixtures.values()].reduce((s, d) => s + d.candles.length, 0)

  console.log('# PISO — P2.0: MIN_FAMILIES\n')
  console.log(
    `painel: ${fixtures.size} fixtures, ${symbols.size} simbolos, ${candles} velas — modo causal (producao)`,
  )
  if (skipped.length) console.log(`ignoradas (payload invalido): ${skipped.join(', ')}`)
  console.log(`marcas PISO (>=2 familias, regra publicada): ${marks.length}\n`)

  // --- dedup entre timeframes ---------------------------------------------
  // Uma defesa pode aparecer no M15 e na H1 do mesmo simbolo. Cada serie e
  // medida por si, mas a sobreposicao precisa ser dita: se metade das marcas
  // fosse o mesmo evento contado tres vezes, o n efetivo seria outro.
  const bySymbolDay = new Map<string, Set<string>>()
  let overlapping = 0
  for (const m of marks) {
    const key = `${m.symbol}|${m.timestamp.slice(0, 13)}`
    if (!bySymbolDay.has(key)) bySymbolDay.set(key, new Set())
    const s = bySymbolDay.get(key)!
    if (s.size > 0) overlapping += 1
    s.add(m.timeframe)
  }
  console.log(
    `sobreposicao entre TFs (mesmo simbolo, mesma hora): ${overlapping} de ${marks.length} ` +
      `(${((100 * overlapping) / marks.length).toFixed(1)}%) — n efetivo ~${marks.length - overlapping}\n`,
  )
  report.panel = { fixtures: fixtures.size, symbols: symbols.size, candles, marks: marks.length, overlapping }

  // --- controle -------------------------------------------------------------
  const random = rng(20260910)
  const atrCache = new Map<string, number[]>()
  for (const [f, d] of fixtures) atrCache.set(f, meanTrueRangePctSeries(d.candles))

  /** Aggregates a set of marks and a matched control pool at horizon `h`. */
  function measure(rows: Mark[], h: number): { ev: Agg; ct: Agg } {
    const ev = emptyAgg()
    const ct = emptyAgg()
    for (const m of rows) {
      const f = `${m.symbol}_${m.timeframe}.json`
      const d = fixtures.get(f)
      if (!d) continue
      const o = forward(d.candles, m.index, h, m.direction, m.atr)
      if (o) add(ev, o)
      const atrSeries = atrCache.get(f)!
      const lo = Math.max(0, m.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - h - 1, m.index + CONTROL_WINDOW)
      if (hi <= lo) continue
      for (let k = 0; k < CONTROLS_PER_EVENT; k += 1) {
        const j = lo + Math.floor(random() * (hi - lo))
        const co = forward(d.candles, j, h, m.direction, atrSeries[j] * d.candles[j].close)
        if (co) add(ct, co)
      }
    }
    return { ev, ct }
  }

  function line(label: string, rows: Mark[], h = PRIMARY_H): Stat | null {
    if (rows.length === 0) {
      console.log(`${label.padEnd(24)} ${String(0).padStart(5)}  —`)
      return null
    }
    const { ev, ct } = measure(rows, h)
    const s = stat(ev)
    const c = stat(ct)
    console.log(
      `${label.padEnd(24)} ${fmt(s)} | ${c.ratio.toFixed(2).padStart(12)} ` +
        `${(c.win.toFixed(1) + '%').padStart(8)} ${zProp(ev, ct).toFixed(2).padStart(6)}`,
    )
    return s
  }

  // --- 1/2: buckets por numero de familias ---------------------------------
  console.log('## Baldes por numero de familias (h=5, principal)\n')
  console.log(HEAD)
  const bucket = (n: number) => marks.filter((m) => (n === 4 ? m.nFam >= 4 : m.nFam === n))
  for (const n of [2, 3, 4]) line(n === 4 ? '4+ familias' : `${n} familias`, bucket(n))
  line('>=2 (producao)', marks)
  line('>=3 (variante)', marks.filter((m) => m.nFam >= 3))

  console.log('\n### os mesmos baldes nos outros horizontes\n')
  console.log('bucket                   h' + HEAD.slice(25))
  for (const n of [2, 3, 4]) {
    for (const h of HORIZONS) {
      const rows = bucket(n)
      if (rows.length === 0) continue
      const { ev, ct } = measure(rows, h)
      const s = stat(ev)
      const c = stat(ct)
      console.log(
        `${(n === 4 ? '4+ familias' : `${n} familias`).padEnd(22)} ${String(h).padStart(2)} ${fmt(s)} | ` +
          `${c.ratio.toFixed(2).padStart(12)} ${(c.win.toFixed(1) + '%').padStart(8)} ${zProp(ev, ct).toFixed(2).padStart(6)}`,
      )
    }
  }
  report.buckets = Object.fromEntries(
    [2, 3, 4].map((n) => {
      const rows = bucket(n)
      return [
        n === 4 ? '4+' : String(n),
        Object.fromEntries(
          HORIZONS.map((h) => {
            const { ev, ct } = measure(rows, h)
            return [h, { event: stat(ev), control: stat(ct), z: zProp(ev, ct) }]
          }),
        ),
      ]
    }),
  )

  // --- 4: discovery / holdout ----------------------------------------------
  console.log('\n## Split temporal 70/30 (posicao na propria serie)\n')
  console.log(HEAD)
  const disc = marks.filter((m) => m.frac < DISCOVERY_FRAC)
  const hold = marks.filter((m) => m.frac >= DISCOVERY_FRAC)
  for (const [label, rows] of [
    ['DISC >=2', disc],
    ['DISC ==2', disc.filter((m) => m.nFam === 2)],
    ['DISC >=3', disc.filter((m) => m.nFam >= 3)],
    ['HOLD >=2', hold],
    ['HOLD ==2', hold.filter((m) => m.nFam === 2)],
    ['HOLD >=3', hold.filter((m) => m.nFam >= 3)],
  ] as [string, Mark[]][]) {
    line(label, rows)
  }

  // --- 14: quatro blocos temporais -----------------------------------------
  console.log('\n## Quatro blocos temporais (>=3 vs ==2)\n')
  console.log(HEAD)
  for (let b = 0; b < 4; b += 1) {
    const inBlock = (m: Mark) => m.frac >= b / 4 && m.frac < (b + 1) / 4
    line(`Q${b + 1} ==2`, marks.filter((m) => inBlock(m) && m.nFam === 2))
    line(`Q${b + 1} >=3`, marks.filter((m) => inBlock(m) && m.nFam >= 3))
  }

  // --- 7/8: structural desconfundido ---------------------------------------
  console.log('\n## `structural` com o numero de familias FIXO (o teste que separa "3" de "structural")\n')
  console.log(HEAD)
  for (const n of [2, 3]) {
    const rows = bucket(n)
    line(`${n} fam COM structural`, rows.filter((m) => m.hasStructural))
    line(`${n} fam SEM structural`, rows.filter((m) => !m.hasStructural))
  }
  console.log('\n### e o contrario: structural fixo, numero variando\n')
  console.log(HEAD)
  line('COM structural, 2 fam', marks.filter((m) => m.hasStructural && m.nFam === 2))
  line('COM structural, >=3', marks.filter((m) => m.hasStructural && m.nFam >= 3))
  line('SEM structural, 2 fam', marks.filter((m) => !m.hasStructural && m.nFam === 2))
  line('SEM structural, >=3', marks.filter((m) => !m.hasStructural && m.nFam >= 3))

  // --- 9: fair / volume_profile --------------------------------------------
  console.log('\n## `fair` (volume_profile ainda com hindsight) isolado\n')
  console.log(HEAD)
  for (const n of [2, 3]) {
    const rows = bucket(n)
    line(`${n} fam COM fair`, rows.filter((m) => m.hasFair))
    line(`${n} fam SEM fair`, rows.filter((m) => !m.hasFair))
  }
  line('>=3 SEM fair (limpo)', marks.filter((m) => m.nFam >= 3 && !m.hasFair))

  // --- 7: combinacoes -------------------------------------------------------
  console.log('\n## Combinacoes de familias (h=5, n>=10)\n')
  console.log(HEAD)
  const combos = new Map<string, Mark[]>()
  for (const m of marks) {
    const k = m.families.join('+')
    if (!combos.has(k)) combos.set(k, [])
    combos.get(k)!.push(m)
  }
  const comboStats: [string, Stat, number][] = []
  for (const [k, rows] of [...combos].sort((a, b) => b[1].length - a[1].length)) {
    if (rows.length < 10) continue
    const { ev, ct } = measure(rows, PRIMARY_H)
    const s = stat(ev)
    const c = stat(ct)
    comboStats.push([k, s, zProp(ev, ct)])
    console.log(
      `${k.padEnd(24)} ${fmt(s)} | ${c.ratio.toFixed(2).padStart(12)} ` +
        `${(c.win.toFixed(1) + '%').padStart(8)} ${zProp(ev, ct).toFixed(2).padStart(6)}`,
    )
  }
  report.combos = comboStats.map(([k, s, z]) => ({ combo: k, ...s, z }))

  // --- 5/11/12: cobertura, TF, direcao -------------------------------------
  console.log('\n## Cobertura\n')
  const candlesByTf = new Map<string, number>()
  for (const d of fixtures.values())
    candlesByTf.set(d.timeframe, (candlesByTf.get(d.timeframe) ?? 0) + d.candles.length)
  console.log('recorte           >=2   >=3   perda   >=2/1000v  >=3/1000v')
  const cut = (pred: (m: Mark) => boolean, label: string, denom: number) => {
    const a = marks.filter(pred).length
    const b = marks.filter((m) => pred(m) && m.nFam >= 3).length
    console.log(
      `${label.padEnd(16)} ${String(a).padStart(4)}  ${String(b).padStart(4)}  ` +
        `${(a ? (100 * (a - b)) / a : 0).toFixed(1).padStart(5)}%  ` +
        `${((1000 * a) / denom).toFixed(2).padStart(9)}  ${((1000 * b) / denom).toFixed(2).padStart(9)}`,
    )
  }
  cut(() => true, 'PAINEL', candles)
  for (const tf of ['15m', '1h', '4h'])
    cut((m) => m.timeframe === tf, tf, candlesByTf.get(tf) ?? 1)
  for (const tf of ['15m', '1h', '4h'])
    for (const dir of ['bullish', 'bearish'] as Direction[])
      cut((m) => m.timeframe === tf && m.direction === dir, `${tf} ${dir}`, candlesByTf.get(tf) ?? 1)

  console.log('\n### resultado por TF e por direcao (h=5)\n')
  console.log(HEAD)
  for (const tf of ['15m', '1h', '4h']) {
    line(`${tf} ==2`, marks.filter((m) => m.timeframe === tf && m.nFam === 2))
    line(`${tf} >=3`, marks.filter((m) => m.timeframe === tf && m.nFam >= 3))
  }
  for (const dir of ['bullish', 'bearish'] as Direction[]) {
    line(`${dir} ==2`, marks.filter((m) => m.direction === dir && m.nFam === 2))
    line(`${dir} >=3`, marks.filter((m) => m.direction === dir && m.nFam >= 3))
  }

  // --- 13: por simbolo ------------------------------------------------------
  console.log('\n## Por simbolo (>=3 vs ==2, MFE>MAE em h=5, simbolos com >=3 marcas nos dois lados)\n')
  const perSymbol: [string, number, number, number][] = []
  for (const sym of symbols) {
    const two = marks.filter((m) => m.symbol === sym && m.nFam === 2)
    const three = marks.filter((m) => m.symbol === sym && m.nFam >= 3)
    if (two.length < 3 || three.length < 3) continue
    const a = stat(measure(three, PRIMARY_H).ev)
    const b = stat(measure(two, PRIMARY_H).ev)
    perSymbol.push([sym, a.win - b.win, three.length, two.length])
  }
  perSymbol.sort((x, y) => y[1] - x[1])
  const better = perSymbol.filter(([, d]) => d > 0).length
  console.log(
    `simbolos comparaveis: ${perSymbol.length} — melhoram ${better} (${perSymbol.length ? ((100 * better) / perSymbol.length).toFixed(0) : 0}%), ` +
      `mediana do delta ${perSymbol.length ? perSymbol[Math.floor(perSymbol.length / 2)][1].toFixed(1) : '—'}pp`,
  )
  if (perSymbol.length > 0) {
    console.log('  top5:   ' + perSymbol.slice(0, 5).map(([s, d, a, b]) => `${s} ${d > 0 ? '+' : ''}${d.toFixed(0)}pp(${a}/${b})`).join('  '))
    console.log('  bottom5:' + perSymbol.slice(-5).map(([s, d, a, b]) => `${s} ${d > 0 ? '+' : ''}${d.toFixed(0)}pp(${a}/${b})`).join('  '))
  }
  report.per_symbol = perSymbol.map(([s, d, a, b]) => ({ symbol: s, delta_win: d, n3: a, n2: b }))

  // --- 10: nota de lifecycle ------------------------------------------------
  console.log('\n## Nota de lifecycle (nada alterado — contexto para a etapa futura)\n')
  for (const n of [2, 3]) {
    const rows = bucket(n)
    const consumed = rows.filter((m) => m.consumed > 0).length
    const recent = rows.filter((m) => m.recentlyDied > 0).length
    console.log(
      `${n === 3 ? '>=3' : '==2'} familias: ${String(rows.length).padStart(4)} marcas — ` +
        `${consumed} consumiram um nivel no proprio candle, ${recent} tinham nivel morto nas ultimas ${RECENTLY_DIED} velas`,
    )
  }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
}

main()
