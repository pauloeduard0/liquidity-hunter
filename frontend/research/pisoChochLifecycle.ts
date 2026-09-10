/**
 * P7.1 — does a CHoCH reference deserve a lifecycle of its own?
 *
 * P7 isolated one source out of six whose death does not measure like its
 * control: after `structureLineEndTime` retires a `change_of_character`
 * reference, price comes back to that price and rejects 56.8% against a matched
 * 50.1% (z 3.76, n=947), on 3/3 timeframes, 4/4 blocks and 72% of symbols —
 * while a BOS reference, same family and same death rule, scores z=0.04.
 *
 * That is evidence about *levels*, not about *marks*, and the two have come
 * apart four times in this series already. So this stage does three things in
 * order and refuses to skip to the third: derive the price-invalidation rule
 * from the detector's own semantics (`research/chochLifecycle.ts`), confirm the
 * extra life is where the reaction lives, and only then rebuild the structural
 * family and ask whether the marks it recovers are worth having.
 *
 * The variants
 * ------------
 *   BASELINE  died = `structureLineEndTime` (production)
 *   V1        died = first candle closing beyond `reference_price_level` on the
 *             side that undoes the flip
 *   V2        died = the paired `choch_failed` the domain already emits
 *   V3        never dies before the window ends — a diagnostic ceiling only,
 *             forbidden as a production candidate (P7.1 V3), reported so the
 *             maximum recoverable coverage is known
 *
 * Only the CHoCH changes. BOS keeps `structureLineEndTime`, and is carried
 * through the identical treatment as a negative control (P7.7): if extending a
 * BOS produced the same lift, the finding would be "structural lines live too
 * short", not "the CHoCH needs its own rule". `structural` stays one family
 * (P7.11) and `choch_failed` is not promoted into it (P7.13).
 *
 * Nothing in production changes: not `defendedLevels.ts`, not `MainChart`, not
 * `structureLineEndTime`, no threshold, no tolerance.
 *
 * Usage
 * -----
 *   cd frontend
 *   node --experimental-strip-types --max-old-space-size=6000 \
 *     research/pisoChochLifecycle.ts --json research/piso_choch_lifecycle_baseline.json
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Candle, DashboardData, MarketStructure } from '../src/types/dashboard.ts'
import { toChartTime } from '../src/utils/chartTime.ts'
import {
  diagnoseDefendedMarks,
  meanTrueRangePctSeries,
  type DefenceLevel,
  type LevelFamily,
} from '../src/utils/defendedLevels.ts'
import { failedChochTime, structureLineEndTime } from '../src/utils/structureLines.ts'
import { sourcedDefenceLevels, type SourcedLevel } from './levelSources.ts'
import { chochDeathByClose } from './chochLifecycle.ts'

const FIXTURE_DIR = join(import.meta.dirname, 'fixtures')
const HORIZONS = [5, 10, 20, 40] as const
const PRIMARY_H = 5
const CONTROLS_PER_EVENT = 20
const CONTROL_WINDOW = 250

type Direction = 'bullish' | 'bearish'
export type Variant = 'BASELINE' | 'V1' | 'V2' | 'V3'
const VARIANTS: Variant[] = ['BASELINE', 'V1', 'V2', 'V3']

function standingUntilFor(data: DashboardData): (event: MarketStructure) => string | null {
  const scopeEvents = data.internal_structure_events
  const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
  return (event: MarketStructure) => {
    const end = structureLineEndTime(event, scopeEvents, lastCandleTime)
    if (end >= lastCandleTime) return null
    return scopeEvents.find((other) => toChartTime(other.timestamp) === end)?.timestamp ?? null
  }
}

/** The CHoCH events the rule actually turns into levels, in the same order. */
function chochEvents(data: DashboardData): MarketStructure[] {
  return data.internal_structure_events.filter(
    (e) =>
      !e.provisional && e.event === 'change_of_character' && e.reference_price_level != null,
  )
}

/** The death each variant assigns to one CHoCH. */
function chochDied(
  event: MarketStructure,
  data: DashboardData,
  variant: Variant,
  baseline: string | null,
): string | null {
  if (variant === 'BASELINE') return baseline
  if (variant === 'V3') return null
  if (variant === 'V2') {
    // The domain's own "this CHoCH did not hold" event. Kept as a variant
    // rather than a reinterpretation: `choch_failed` is not promoted into the
    // structural family, it is only read as a death (P7.13).
    const t = failedChochTime(event, data.internal_structure_events, { includeFizzle: false })
    if (t === null) return null
    return (
      data.internal_structure_events.find((o) => toChartTime(o.timestamp) === t)?.timestamp ?? null
    )
  }
  return chochDeathByClose(event, data.candles)
}

/**
 * The production level list with only the CHoCH deaths swapped.
 *
 * Built by rewriting `died` on the levels `sourcedDefenceLevels` already
 * produced, so geometry, tolerance, ordering and every other family are
 * untouched by construction rather than by care.
 */
function levelsFor(
  data: DashboardData,
  base: SourcedLevel[],
  variant: Variant,
): SourcedLevel[] {
  if (variant === 'BASELINE') return base
  const events = chochEvents(data)
  let seen = 0
  return base.map((l) => {
    if (l.source !== 'choch') return l
    const e = events[seen]
    seen += 1
    if (!e) return l
    return { ...l, died: chochDied(e, data, variant, l.died) }
  })
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

interface Mark {
  key: string
  symbol: string
  timeframe: string
  timestamp: string
  index: number
  direction: Direction
  families: LevelFamily[]
  excursionAtr: number
  wickBodyRatio: number
  atr: number
  frac: number
  variant: Variant
  /** Distance from the swept range to the CHoCH level that made it possible,
   *  in ATR — the axis P3 showed can masquerade as an effect. */
  chochGapAtr: number | null
}

/** One revisit of a level after it was declared dead, with its control. */
interface Revisit {
  key: string
  timeframe: string
  index: number
  direction: Direction
  atr: number
  won: boolean
  through: boolean
  reaction: number
}

interface Life {
  timeframe: string
  direction: Direction
  baseline: number
  v1: number
  v2: number
  v3: number
}

function marksFor(
  data: DashboardData,
  levels: SourcedLevel[],
  variant: Variant,
): Mark[] {
  const plain: DefenceLevel[] = levels
  const idx = new Map(data.candles.map((c, i) => [c.timestamp, i]))
  const n = data.candles.length
  const key = `${data.symbol}_${data.timeframe}.json`
  const cands = diagnoseDefendedMarks(data, plain, { causalAtr: true })
  const out: Mark[] = []
  for (const c of cands) {
    if (c.failed.length > 0) continue
    const i = idx.get(c.timestamp)
    if (i === undefined) continue
    const atr = c.atrPct * data.candles[i].close
    if (atr <= 0) continue
    const low = Math.min(c.edge, c.price)
    const high = Math.max(c.edge, c.price)
    // The CHoCH level must overlap the swept range for the family to count, so
    // the gap to it is always zero. The axis that does vary — and the one P3
    // showed can pass for an effect — is how far the level sits from the
    // extreme the wick actually reached.
    let gap: number | null = null
    for (const l of levels) {
      if (l.source !== 'choch') continue
      if (l.born > c.timestamp) continue
      if (l.died !== null && l.died < c.timestamp) continue
      if (l.high < low || l.low > high) continue
      const g = Math.abs((l.low + l.high) / 2 - c.price) / atr
      if (gap === null || g < gap) gap = g
    }
    out.push({
      key,
      symbol: data.symbol,
      timeframe: data.timeframe,
      timestamp: c.timestamp,
      index: i,
      direction: c.side === 'top' ? 'bearish' : 'bullish',
      families: c.families,
      excursionAtr: c.excursionAtr,
      wickBodyRatio: c.wickBodyRatio,
      atr,
      frac: i / n,
      variant,
      chochGapAtr: gap,
    })
  }
  return out
}

/** Revisits inside a window, judged in the direction price came from. */
function revisitsIn(
  data: DashboardData,
  atrSeries: number[],
  low: number,
  high: number,
  fromIdx: number,
  toIdx: number,
): Revisit[] {
  const out: Revisit[] = []
  const key = `${data.symbol}_${data.timeframe}.json`
  for (let k = fromIdx + 1; k < Math.min(toIdx, data.candles.length); k += 1) {
    const c = data.candles[k]
    if (c.high < low || c.low > high) continue
    const prev = data.candles[k - 1]
    const dir: Direction | null = prev.close > high ? 'bullish' : prev.close < low ? 'bearish' : null
    const atr = atrSeries[k] * c.close
    if (dir === null || atr <= 0) continue
    const o = forward(data.candles, k, PRIMARY_H, dir, atr)
    if (!o) continue
    out.push({
      key,
      timeframe: data.timeframe,
      index: k,
      direction: dir,
      atr,
      won: o.won,
      through: data.candles
        .slice(k, Math.min(data.candles.length, k + PRIMARY_H + 1))
        .some((cc) => (dir === 'bullish' ? cc.close < low : cc.close > high)),
      reaction: o.mfe - o.mae,
    })
  }
  return out
}

const excBucket = (v: number): string =>
  v < 1.25 ? 'exc1.0-1.25' : v < 1.75 ? 'exc1.25-1.75' : 'exc1.75+'
const wickBucket = (v: number): string => (v < 3 ? 'wick2-3' : v < 5 ? 'wick3-5' : 'wick5+')

function main(): void {
  const args = process.argv.slice(2)
  const jsonAt = args.indexOf('--json')
  const files = readdirSync(FIXTURE_DIR).filter((f) => f.endsWith('.json')).sort()

  const fixtures = new Map<string, DashboardData>()
  const atrCache = new Map<string, number[]>()
  const marks = new Map<Variant, Mark[]>(VARIANTS.map((v) => [v, []]))
  const lives: Life[] = []
  const extraRevisits: Revisit[] = []
  const p7First: (Revisit & { stillValid: boolean })[] = []
  const bosExtraRevisits: Revisit[] = []
  const groups = { A: 0, B: 0, C: 0, D: 0 }
  let overlapChecked = 0
  let overlapWithBos = 0
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
    const atrSeries = meanTrueRangePctSeries(d.candles)
    atrCache.set(f, atrSeries)
    const base = sourcedDefenceLevels(d, standingUntilFor(d), { causalAtr: true })
    const idx = new Map(d.candles.map((c, i) => [c.timestamp, i]))
    const n = d.candles.length

    for (const v of VARIANTS) marks.get(v)!.push(...marksFor(d, levelsFor(d, base, v), v))

    // --- P7.4/P7.5/P7.6, per CHoCH -----------------------------------------
    const events = chochEvents(d)
    const chochLevels = base.filter((l) => l.source === 'choch')
    const bosLevels = base.filter((l) => l.source === 'bos')
    for (let i = 0; i < chochLevels.length; i += 1) {
      const l = chochLevels[i]
      const e = events[i]
      if (!e) continue
      const bi = idx.get(l.born)
      if (bi === undefined) continue
      const end = (t: string | null): number => (t !== null ? (idx.get(t) ?? n) : n)
      const b = end(l.died)
      const v1 = end(chochDied(e, d, 'V1', l.died))
      const v2 = end(chochDied(e, d, 'V2', l.died))
      lives.push({
        timeframe: d.timeframe,
        direction: e.direction === 'bullish' ? 'bullish' : 'bearish',
        baseline: b - bi,
        v1: v1 - bi,
        v2: v2 - bi,
        v3: n - bi,
      })
      // Group A/B/C/D at the end of the window.
      if (l.died === null && v1 >= n) groups.A += 1
      else if (b < n && v1 > b) groups.B += 1
      else if (b < n && v1 <= b) groups.C += 1
      else groups.D += 1
      // The extra life: dead by the drawing rule, alive by price.
      if (v1 > b) extraRevisits.push(...revisitsIn(d, atrSeries, l.low, l.high, b, v1))
      // The P7 measurement, reproduced here so the two can be reconciled: the
      // FIRST revisit after the drawing death, within 60 candles, regardless of
      // whether price had already invalidated the level. Tagged by which it was.
      if (b < n) {
        const first = revisitsIn(d, atrSeries, l.low, l.high, b, Math.min(n, b + 61))[0]
        if (first) p7First.push({ ...first, stillValid: v1 > b })
      }
      // P7.12 — does the prolonged CHoCH just duplicate a BOS already there?
      if (v1 > b) {
        overlapChecked += 1
        if (
          bosLevels.some(
            (o) =>
              o.high >= l.low &&
              o.low <= l.high &&
              o.born <= (l.died ?? d.candles[n - 1].timestamp) &&
              (o.died === null || (idx.get(o.died) ?? n) > b),
          )
        )
          overlapWithBos += 1
      }
    }
    // --- P7.7 — the same treatment on BOS, diagnostically -------------------
    const bosEvents = d.internal_structure_events.filter(
      (x) => !x.provisional && x.event === 'break_of_structure' && x.reference_price_level != null,
    )
    for (let i = 0; i < bosLevels.length; i += 1) {
      const l = bosLevels[i]
      const e = bosEvents[i]
      if (!e) continue
      const bi = idx.get(l.born)
      if (bi === undefined) continue
      const b = l.died !== null ? (idx.get(l.died) ?? n) : n
      const alt = chochDeathByClose(e, d.candles)
      const v1 = alt !== null ? (idx.get(alt) ?? n) : n
      if (v1 > b) bosExtraRevisits.push(...revisitsIn(d, atrSeries, l.low, l.high, b, v1))
    }
  }

  const random = rng(20260910)
  const report: Record<string, unknown> = { skipped }
  console.log('# PISO — P7.1: lifecycle proprio para o CHoCH\n')
  console.log(
    `painel: ${fixtures.size} fixtures — modo causal (producao), regra e limiares inalterados\n` +
      `CHoCH nivelados: ${lives.length}\n`,
  )

  // --- P7.1/P7.2 -----------------------------------------------------------
  console.log('## P7.1/P7.2 — a regra de invalidacao, derivada do detector\n')
  console.log(
    '  `validated_choch_high` e "o nivel que um CHoCH bullish precisa quebrar", e a\n' +
      '  quebra exige CLOSE, nao pavio ("a single candle that pokes through the\n' +
      '  reference and reverts is a LIQUIDITY_SWEEP"). Entao:\n\n' +
      '    CHoCH bullish  quebrou um HIGH para cima -> morre no 1o CLOSE ABAIXO dele\n' +
      '    CHoCH bearish  quebrou um LOW  para baixo -> morre no 1o CLOSE ACIMA dele\n\n' +
      '  sem grace, sem contagem de velas, sem limiar novo: a comparacao e contra o\n' +
      '  proprio `reference_price_level`. V2 usa o `choch_failed` que o dominio ja\n' +
      '  emite. V3 (nunca morre) e so teto diagnostico, proibido como candidato.\n',
  )

  // --- P7.4 ----------------------------------------------------------------
  console.log('## P7.4 — quanto tempo o CHoCH sobrevive (velas)\n')
  console.log('  recorte          n    baseline p50/p75/p90     V1 p50/p75/p90     V2 p50   teto V3 p50')
  const lifeRow = (label: string, rows: Life[]): void => {
    if (rows.length === 0) return
    const q = (f: (l: Life) => number, p: number) => pctl(rows.map(f), p)
    console.log(
      `  ${label.padEnd(14)} ${String(rows.length).padStart(4)} ` +
        `${`${q((l) => l.baseline, 0.5)}/${q((l) => l.baseline, 0.75)}/${q((l) => l.baseline, 0.9)}`.padStart(21)} ` +
        `${`${q((l) => l.v1, 0.5)}/${q((l) => l.v1, 0.75)}/${q((l) => l.v1, 0.9)}`.padStart(18)} ` +
        `${String(q((l) => l.v2, 0.5)).padStart(8)} ${String(q((l) => l.v3, 0.5)).padStart(13)}`,
    )
  }
  lifeRow('PAINEL', lives)
  for (const tf of ['15m', '1h', '4h']) lifeRow(tf, lives.filter((l) => l.timeframe === tf))
  for (const dir of ['bullish', 'bearish'] as Direction[])
    lifeRow(dir, lives.filter((l) => l.direction === dir))
  const longer = lives.filter((l) => l.v1 > l.baseline).length
  console.log(
    `\n  V1 alonga a vida de ${longer}/${lives.length} (${((100 * longer) / lives.length).toFixed(1)}%); ` +
      `nao muda em ${lives.length - longer}. ` +
      `velas extras: p50 ${pctl(lives.filter((l) => l.v1 > l.baseline).map((l) => l.v1 - l.baseline), 0.5)}, ` +
      `p90 ${pctl(lives.filter((l) => l.v1 > l.baseline).map((l) => l.v1 - l.baseline), 0.9)}`,
  )

  // --- P7.6 ----------------------------------------------------------------
  console.log('\n## P7.6 — baseline visual vs morte por preco\n')
  console.log(
    `  A vivo pelas duas regras                 ${String(groups.A).padStart(5)}\n` +
      `  B morto no desenho, vivo no preco        ${String(groups.B).padStart(5)}\n` +
      `  C morto pelas duas                       ${String(groups.C).padStart(5)}\n` +
      `  D outros (morto no preco, vivo no desenho) ${String(groups.D).padStart(3)}`,
  )

  // --- P7.5/P7.7 -----------------------------------------------------------
  console.log('\n## P7.5/P7.7 — revisitas na vida extra, e o BOS como controle negativo\n')
  function revisitLine(label: string, rows: Revisit[]): void {
    const ev = emptyAgg()
    const ctl = emptyAgg()
    for (const r of rows) {
      const d = fixtures.get(r.key)
      if (!d) continue
      const series = atrCache.get(r.key)!
      const o = forward(d.candles, r.index, PRIMARY_H, r.direction, r.atr)
      if (o) add(ev, o)
      const lo = Math.max(0, r.index - CONTROL_WINDOW)
      const hi = Math.min(d.candles.length - PRIMARY_H - 1, r.index + CONTROL_WINDOW)
      if (hi <= lo) continue
      for (let t = 0; t < 5; t += 1) {
        const j = lo + Math.floor(random() * (hi - lo))
        const co = forward(d.candles, j, PRIMARY_H, r.direction, series[j] * d.candles[j].close)
        if (co) add(ctl, co)
      }
    }
    console.log(
      `  ${label.padEnd(30)} ${String(ev.n).padStart(5)} ` +
        `${((ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1) + '%').padStart(9)} | ` +
        `${((ctl.n ? (100 * ctl.won) / ctl.n : 0).toFixed(1) + '%').padStart(9)} ` +
        `${zProp(ev, ctl).toFixed(2).padStart(6)}  through ` +
        `${((100 * rows.filter((r) => r.through).length) / Math.max(1, rows.length)).toFixed(1)}%  ` +
        `reacao ${(rows.reduce((s, r) => s + r.reaction, 0) / Math.max(1, rows.length)).toFixed(2)}ATR`,
    )
  }
  console.log('  grupo                              n  rejeicao |  controle      z')
  revisitLine('CHoCH, vida extra V1', extraRevisits)
  for (const tf of ['15m', '1h', '4h'])
    revisitLine(`CHoCH ${tf}`, extraRevisits.filter((r) => r.timeframe === tf))
  revisitLine('BOS, vida extra (controle -)', bosExtraRevisits)
  console.log('\n  reconciliando com a P7: o MESMO conjunto que ela mediu, separado por')
  console.log('  o nivel ja ter sido invalidado pelo preco ou nao naquele momento\n')
  console.log('  grupo                              n  rejeicao |  controle      z')
  revisitLine('P7: 1a revisita pos-desenho', p7First)
  revisitLine('  dela, ainda valida no preco', p7First.filter((r) => r.stillValid))
  revisitLine('  dela, JA invalidada no preco', p7First.filter((r) => !r.stillValid))
  console.log(
    `\n  P7.12 — dos ${overlapChecked} CHoCH com vida extra, ${overlapWithBos} ` +
      `(${((100 * overlapWithBos) / Math.max(1, overlapChecked)).toFixed(1)}%) tem um BOS vivo\n` +
      '  sobreposto na mesma faixa: nesses, a familia structural ja existiria sem\n' +
      '  o CHoCH prolongado, e a marca nao seria nova.',
  )

  // --- P7.8/P7.9/P7.10 -----------------------------------------------------
  console.log('\n## P7.8 — identidade do stream de marcas\n')
  const baseMarks = marks.get('BASELINE')!
  const keyOf = (m: Mark) => `${m.symbol}|${m.timeframe}|${m.timestamp}`
  const baseSet = new Map(baseMarks.map((m) => [keyOf(m), m]))
  console.log('  variante  identicas  so baseline  so variante  familias mudam')
  const newByVariant = new Map<Variant, Mark[]>()
  for (const v of VARIANTS) {
    if (v === 'BASELINE') continue
    const vm = new Map(marks.get(v)!.map((m) => [keyOf(m), m]))
    let same = 0
    let famChanged = 0
    for (const [k, m] of vm) {
      const o = baseSet.get(k)
      if (!o) continue
      same += 1
      if (o.families.join('+') !== m.families.join('+')) famChanged += 1
    }
    const onlyVar = [...vm.entries()].filter(([k]) => !baseSet.has(k)).map(([, m]) => m)
    newByVariant.set(v, onlyVar)
    console.log(
      `  ${v.padEnd(9)} ${String(same).padStart(9)} ${String([...baseSet.keys()].filter((k) => !vm.has(k)).length).padStart(12)} ` +
        `${String(onlyVar.length).padStart(12)} ${String(famChanged).padStart(15)}`,
    )
  }

  console.log('\n## P7.9 — qualidade das marcas NOVAS (a etapa decisiva)\n')
  function measure(rows: Mark[], h: number): { ev: Agg; ctl: Agg } {
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
  const HEAD = '  grupo                              n    MFE    MAE  MFE/MAE  MFE>MAE   move | controle      z'
  const line = (label: string, rows: Mark[], h = PRIMARY_H): void => {
    const { ev, ctl } = measure(rows, h)
    const mfe = ev.n ? ev.mfe / ev.n : 0
    const mae = ev.n ? ev.mae / ev.n : 0
    console.log(
      `  ${label.padEnd(30)} ${String(ev.n).padStart(4)} ${mfe.toFixed(2).padStart(6)} ` +
        `${mae.toFixed(2).padStart(6)} ${(mae > 0 ? mfe / mae : 0).toFixed(2).padStart(8)} ` +
        `${((ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1) + '%').padStart(8)} ` +
        `${(ev.n ? ev.move / ev.n : 0).toFixed(2).padStart(6)} | ` +
        `${((ctl.n ? (100 * ctl.won) / ctl.n : 0).toFixed(1) + '%').padStart(8)} ${zProp(ev, ctl).toFixed(2).padStart(6)}`,
    )
  }
  console.log(HEAD)
  line('PISO producao', baseMarks)
  for (const v of ['V1', 'V2', 'V3'] as Variant[])
    line(`novas por ${v}`, newByVariant.get(v) ?? [])
  line('PISO V1 (prod + novas)', marks.get('V1')!)
  console.log('\n  outros horizontes')
  console.log(HEAD)
  for (const h of HORIZONS) {
    if (h === PRIMARY_H) continue
    line(`h=${h} producao`, baseMarks, h)
    line(`h=${h} novas por V1`, newByVariant.get('V1') ?? [], h)
  }

  // --- P7.14 ---------------------------------------------------------------
  console.log('\n## P7.14 — as novas contra placebo casado (nao contra o mercado)\n')
  {
    const nv = newByVariant.get('V1') ?? []
    const poolKey = (m: Mark) =>
      `${m.symbol}|${m.timeframe}|${m.direction}|${excBucket(m.excursionAtr)}|${wickBucket(m.wickBodyRatio)}`
    const pool = new Map<string, Mark[]>()
    for (const m of baseMarks) {
      const k = poolKey(m)
      if (!pool.has(k)) pool.set(k, [])
      pool.get(k)!.push(m)
    }
    const ev = emptyAgg()
    const plac = emptyAgg()
    for (const m of nv) {
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      if (o) add(ev, o)
      for (const p of (pool.get(poolKey(m)) ?? []).filter(
        (x) => Math.abs(x.index - m.index) <= CONTROL_WINDOW,
      )) {
        const po = forward(d.candles, p.index, PRIMARY_H, p.direction, p.atr)
        if (po) add(plac, po)
      }
    }
    console.log(
      `  novas V1 ${ev.n} marcas: ${(ev.n ? (100 * ev.won) / ev.n : 0).toFixed(1)}%  |  ` +
        `placebo casado (PISO de producao, mesmo simbolo/TF/direcao/excursao/pavio) ` +
        `${plac.n} obs: ${(plac.n ? (100 * plac.won) / plac.n : 0).toFixed(1)}%  z ${zProp(ev, plac).toFixed(2)}`,
    )
  }

  // --- P7.10 ---------------------------------------------------------------
  console.log('\n## P7.10 — cobertura\n')
  console.log('  recorte        producao   +V1   total   crescimento')
  const nv1 = newByVariant.get('V1') ?? []
  for (const [label, sel] of [
    ['PAINEL', () => true],
    ['15m', (m: Mark) => m.timeframe === '15m'],
    ['1h', (m: Mark) => m.timeframe === '1h'],
    ['4h', (m: Mark) => m.timeframe === '4h'],
    ['bullish', (m: Mark) => m.direction === 'bullish'],
    ['bearish', (m: Mark) => m.direction === 'bearish'],
  ] as [string, (m: Mark) => boolean][]) {
    const a = baseMarks.filter(sel).length
    const b = nv1.filter(sel).length
    console.log(
      `  ${label.padEnd(12)} ${String(a).padStart(9)} ${String(b).padStart(5)} ${String(a + b).padStart(7)} ` +
        `${(a ? ((100 * b) / a).toFixed(0) + '%' : '—').padStart(13)}`,
    )
  }

  // --- P7.10 by TF/direction quality ---------------------------------------
  console.log('\n## P7.10b — as novas por TF e direcao\n')
  console.log(HEAD)
  for (const tf of ['15m', '1h', '4h']) line(`novas V1 ${tf}`, nv1.filter((m) => m.timeframe === tf))
  for (const dir of ['bullish', 'bearish'] as Direction[])
    line(`novas V1 ${dir}`, nv1.filter((m) => m.direction === dir))
  console.log('\n  P7.16 — sem `fair` (a familia com hindsight)')
  console.log(HEAD)
  line('novas V1 sem fair', nv1.filter((m) => !m.families.includes('fair')))
  line('producao sem fair', baseMarks.filter((m) => !m.families.includes('fair')))

  // --- P7.15 ---------------------------------------------------------------
  console.log('\n## P7.15 — quatro blocos temporais\n')
  console.log(HEAD)
  for (let q = 0; q < 4; q += 1)
    line(`Q${q + 1} novas V1`, nv1.filter((m) => Math.floor(m.frac * 4) === q))

  // --- P7.16 ---------------------------------------------------------------
  console.log('\n## P7.16 — robustez por simbolo (novas V1)\n')
  {
    const per = new Map<string, { n: number; won: number }>()
    for (const m of nv1) {
      const d = fixtures.get(m.key)
      if (!d) continue
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      if (!o) continue
      if (!per.has(m.symbol)) per.set(m.symbol, { n: 0, won: 0 })
      per.get(m.symbol)!.n += 1
      if (o.won) per.get(m.symbol)!.won += 1
    }
    const rows = [...per.entries()].filter(([, v]) => v.n >= 2)
    rows.sort((a, b) => b[1].won / b[1].n - a[1].won / a[1].n)
    const rates = rows.map(([, v]) => (100 * v.won) / v.n)
    const top = [...per.entries()].sort((a, b) => b[1].n - a[1].n)
    console.log(
      `  simbolos afetados: ${per.size} para ${nv1.length} marcas; com n>=2: ${rows.length}, ` +
        `mediana ${rates.length ? pctl(rates, 0.5).toFixed(0) + '%' : '—'}`,
    )
    console.log(
      `  concentracao (top5 por volume): ${top.slice(0, 5).map(([s, v]) => `${s}=${v.n}`).join('  ')}`,
    )
    const show = (r: [string, { n: number; won: number }]) =>
      `${r[0]} ${((100 * r[1].won) / r[1].n).toFixed(0)}%(${r[1].n})`
    console.log(`  top5:    ${rows.slice(0, 5).map(show).join('  ') || '—'}`)
    console.log(`  bottom5: ${rows.slice(-5).map(show).join('  ') || '—'}`)
    report.perSymbol = rows.map(([s, v]) => ({ symbol: s, n: v.n, won: v.won }))
  }

  // --- P7.17 ---------------------------------------------------------------
  console.log('\n## P7.17 — casos reais (BTC / ETH / SOL)\n')
  {
    const majors = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    const show = (tag: string, m: Mark | undefined): void => {
      if (!m) {
        console.log(`  ${tag} —`)
        return
      }
      const d = fixtures.get(m.key)!
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      console.log(
        `  ${tag} ${m.symbol.padEnd(8)} ${m.timeframe.padEnd(4)} ${m.timestamp}  ${m.direction.padEnd(7)} ` +
          `exc=${m.excursionAtr.toFixed(2)} fam=${m.families.join('+').padEnd(28)}` +
          (o ? ` -> h5 MFE=${o.mfe.toFixed(2)} MAE=${o.mae.toFixed(2)} ${o.won ? 'OK' : 'falhou'}` : ''),
      )
    }
    const mine = nv1.filter((m) => majors.includes(m.symbol))
    const won = (m: Mark): boolean => {
      const d = fixtures.get(m.key)!
      const o = forward(d.candles, m.index, PRIMARY_H, m.direction, m.atr)
      return o !== null && o.won
    }
    show('C) PISO novo que funciona   ', mine.find(won) ?? nv1.find(won))
    show('D) PISO novo que falha      ', mine.find((m) => !won(m)) ?? nv1.find((m) => !won(m)))
    const rev = extraRevisits.filter((r) => majors.includes(r.key.split('_')[0]))
    const bosRev = bosExtraRevisits.filter((r) => majors.includes(r.key.split('_')[0]))
    const showRev = (tag: string, r: Revisit | undefined): void => {
      if (!r) {
        console.log(`  ${tag} —`)
        return
      }
      const d = fixtures.get(r.key)!
      console.log(
        `  ${tag} ${r.key.replace('.json', '').padEnd(14)} ${d.candles[r.index].timestamp}  ` +
          `${r.direction.padEnd(7)} ${r.won ? 'REJEITOU' : 'atravessou'} reacao=${r.reaction.toFixed(2)}ATR`,
      )
    }
    showRev('A) linha morta, preco rejeita', rev.find((r) => r.won))
    showRev('B) vida extra, atravessou   ', rev.find((r) => !r.won))
    showRev('E) BOS equivalente          ', bosRev[0])
  }

  if (jsonAt >= 0 && args[jsonAt + 1]) {
    writeFileSync(args[jsonAt + 1], JSON.stringify(report, null, 2))
    console.log(`\nbaseline -> ${args[jsonAt + 1]}`)
  }
  if (skipped.length) console.log(`\nfixtures ignoradas: ${skipped.length}`)
}

if (process.argv[1] && import.meta.filename === process.argv[1]) main()
