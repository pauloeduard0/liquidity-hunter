/**
 * Tests for the defended-level ("piso") mark — the `⛨N` reading.
 *
 * Run on node's own runner, like `legState.test.ts`:
 * `node --test src/utils/*.test.ts` (or `npm test`).
 *
 * Written in two halves, deliberately.
 *
 * The first half **freezes the shipped semantics**. Every gate gets a pair of
 * cases that straddle it, so a later change to the rule has to break a test
 * saying which threshold moved. None of these assert that the rule is *right* —
 * only that it is what it is today. The audit that preceded this file found no
 * measurement behind `MIN_WICK_BODY`, and the whole family of events measured
 * negative for entry edge in `research/raid_reversal.py`; freezing the rule is
 * what makes it safe to measure before touching it.
 *
 * The second half is about **causality**. `meanTrueRangePct` averages the whole
 * candle array, including candles *after* the one being judged, so the shipped
 * rule decides a June candle using September's volatility. Those tests
 * reproduce the repaint first (`P1.2`) and then pin the causal mode that fixes
 * it (`P1.6`), and they are permanent: the truncation invariant is the property
 * the historical measurement rests on.
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type {
  Candle,
  DashboardData,
  LiquidityZone,
  MarketStructure,
  POIZone,
  VWAPPoint,
} from '../types/dashboard.ts'
import {
  buildDefenceLevels,
  buildDefendedMarks,
  diagnoseDefendedMarks,
  meanTrueRangePct,
  meanTrueRangePctSeries,
} from './defendedLevels.ts'

// ---------------------------------------------------------------------------
// Synthetic fixtures.
//
// Kept deliberately round: every filler candle has a true range of exactly 1 on
// a close of 100, so `meanTrueRangePct` is 0.01 and one ATR is one price unit.
// That makes each threshold readable as a number in the test rather than as an
// arithmetic accident, and it is why the cases below can straddle a gate by
// hundredths without becoming fragile.
// ---------------------------------------------------------------------------

const T0 = Date.UTC(2026, 0, 1)
const HOUR = 3600_000

/** ISO timestamp for hour `i` of the synthetic series. */
function ts(i: number): string {
  return new Date(T0 + i * HOUR).toISOString().replace('.000Z', 'Z')
}

function candle(i: number, over: Partial<Candle> = {}): Candle {
  return {
    symbol: 'TEST',
    timeframe: '1h',
    timestamp: ts(i),
    open: 100,
    high: 100.5,
    low: 99.5,
    close: 100,
    volume: 10,
    taker_buy_volume: 5,
    ...over,
  } as Candle
}

/** A flat envelope at 100 ± 1 — one ATR wide on each side. */
function vwapPoint(i: number, over: Partial<VWAPPoint> = {}): VWAPPoint {
  return {
    timestamp: ts(i),
    anchor_timestamp: ts(0),
    value: 100,
    upper_1: 101,
    lower_1: 99,
    upper_2: 102,
    lower_2: 98,
    ...over,
  }
}

function structural(price: number, at: number): MarketStructure {
  return {
    symbol: 'TEST',
    timeframe: '1h',
    timestamp: ts(at),
    event: 'break_of_structure',
    direction: 'bullish',
    price_level: price,
    reference_price_level: price,
    provisional: false,
  } as MarketStructure
}

function poi(low: number, high: number, at: number, invalidated: string | null = null): POIZone {
  return {
    symbol: 'TEST',
    timeframe: '1h',
    direction: 'bullish',
    kind: 'order_block',
    price_low: low,
    price_high: high,
    created_at: ts(at),
    ob_candle_timestamp: ts(at),
    status: 'active',
    invalidated_at: invalidated,
  } as POIZone
}

function equal(
  low: number,
  high: number,
  at: number,
  invalidated: string | null = null,
): LiquidityZone {
  return {
    symbol: 'TEST',
    timeframe: '1h',
    zone_type: 'equal_highs',
    side: 'buy_side',
    price_low: low,
    price_high: high,
    formed_at: ts(at),
    invalidated_at: invalidated,
    breached_at: null,
    sweep_rejected: false,
    strength: 1,
    is_mitigated: false,
  } as LiquidityZone
}

/**
 * A window of `n` flat candles with the given candle substituted at `at`.
 *
 * Everything the two functions under test read is present; everything else is
 * left off and the whole thing cast, the same shape `legState.test.ts` uses.
 */
function data(over: Partial<DashboardData> = {}, n = 30): DashboardData {
  const candles = Array.from({ length: n }, (_, i) => candle(i))
  return {
    symbol: 'TEST',
    timeframe: '1h',
    candles,
    internal_structure_events: [],
    poi_zones: [],
    liquidity_zones: [],
    liquidation_map: null,
    volume_profile: null,
    vwap: {
      symbol: 'TEST',
      timeframe: '1h',
      anchor: 'session',
      anchor_timestamp: ts(0),
      label: 'Session',
      band_multipliers: [1, 2],
      points: candles.map((_, i) => vwapPoint(i)),
      estimated: true,
    },
    ...over,
  } as DashboardData
}

/** Replace one candle in a window, keeping the VWAP series aligned. */
function withCandle(base: DashboardData, at: number, over: Partial<Candle>): DashboardData {
  const candles = base.candles.map((c, i) => (i === at ? candle(at, over) : c))
  return { ...base, candles }
}

/** The canonical top defence: wick to 102.5 (1.5 ATR past the +1σ edge at 101),
 *  close back inside at 100.5, so the wick is 2.0 against a body of 0.5. */
const TOP_RAID: Partial<Candle> = { open: 100, high: 102.5, low: 99.5, close: 100.5 }

/** Its mirror: wick to 97.5, close back inside at 99.5. */
const BOTTOM_RAID: Partial<Candle> = { open: 100, high: 100.5, low: 97.5, close: 99.5 }

/** Two families spanning the raided range [101, 102.5]. */
const TWO_FAMILIES: Partial<DashboardData> = {
  internal_structure_events: [structural(101.5, 1)],
  poi_zones: [poi(101.8, 102.2, 1)],
}

/** Structural levels stand forever unless a test says otherwise. */
const STANDING = () => null

function marks(d: DashboardData, standingUntil: () => string | null = STANDING) {
  return buildDefendedMarks(d, buildDefenceLevels(d, standingUntil))
}

/**
 * Appends ten violent candles after the window, carrying no VWAP reading.
 *
 * The tail has to raise the shared volatility unit without becoming a
 * candidate itself, or the test measures ten new marks instead of the fate of
 * the old one. Leaving the envelope undefined there is how the real series
 * behaves across a session rollover, and it is the same `upper_1 === null`
 * skip the rule already applies.
 */
function withLoudTail(base: DashboardData, n = 10): DashboardData {
  const loud = Array.from({ length: n }, (_, i) =>
    candle(30 + i, { open: 100, high: 105, low: 95, close: 100 }),
  )
  return { ...base, candles: [...base.candles, ...loud] } as DashboardData
}

// ---------------------------------------------------------------------------
// P1.1 — the shipped semantics, frozen.
// ---------------------------------------------------------------------------

test('a top raid that clears +1s and closes back inside is marked', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  const got = marks(d)
  assert.equal(got.length, 1)
  assert.equal(got[0].timestamp, ts(20))
  assert.equal(got[0].side, 'top')
  assert.equal(got[0].price, 102.5)
  assert.equal(got[0].edge, 101)
})

test('a bottom raid that clears -1s and closes back inside is marked', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(98.5, 1)],
      poi_zones: [poi(97.8, 98.2, 1)],
    }),
    20,
    BOTTOM_RAID,
  )
  const got = marks(d)
  assert.equal(got.length, 1)
  assert.equal(got[0].side, 'bottom')
  assert.equal(got[0].price, 97.5)
  assert.equal(got[0].edge, 99)
})

test('closing beyond the edge is not a defence — the excursion was kept', () => {
  // Same wick, but the close stays above +1s: nothing was handed back.
  const d = withCandle(data(TWO_FAMILIES), 20, { ...TOP_RAID, close: 101.5 })
  assert.equal(marks(d).length, 0)
})

test('never reaching the edge is not a defence', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 100.9, low: 99.5, close: 100.2 })
  assert.equal(marks(d).length, 0)
})

test('the excursion gate straddles 1.0 ATR', () => {
  // 1.04 ATR past the edge passes; 0.95 does not. Body and wick held constant.
  const pass = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.1, low: 99.5, close: 100.5 })
  const fail = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.0, low: 99.5, close: 100.5 })
  assert.equal(marks(pass).length, 1)
  assert.equal(marks(fail).length, 0)
})

test('the wick/body gate straddles 2.0', () => {
  // High fixed at 102.4, so the excursion gate is comfortably clear (~1.29 ATR)
  // in both cases and only the body moves. A body of 0.75 gives 2.20x (passes);
  // a body of 0.85 gives 1.82x (fails).
  const pass = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.4, low: 99.5, close: 100.75 })
  const fail = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.4, low: 99.5, close: 100.85 })
  assert.equal(marks(pass).length, 1, 'wick 1.65 / body 0.75 = 2.20x')
  assert.equal(marks(fail).length, 0, 'wick 1.55 / body 0.85 = 1.82x')
})

test('one family is not enough, two are', () => {
  const one = withCandle(data({ internal_structure_events: [structural(101.5, 1)] }), 20, TOP_RAID)
  assert.equal(marks(one).length, 0)
  const two = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  assert.equal(marks(two).length, 1)
})

test('two levels of the same family count once', () => {
  // Two order blocks stacked in the raided range are one piece of evidence.
  const d = withCandle(
    data({ poi_zones: [poi(101.2, 101.4, 1), poi(101.8, 102.2, 1)] }),
    20,
    TOP_RAID,
  )
  assert.equal(marks(d).length, 0)
})

test('a level born after the candle does not defend it', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(101.5, 25)],
      poi_zones: [poi(101.8, 102.2, 1)],
    }),
    20,
    TOP_RAID,
  )
  assert.equal(marks(d).length, 0)
})

test('a level that died before the candle does not defend it', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(101.5, 1)],
      poi_zones: [poi(101.8, 102.2, 1, ts(10))],
    }),
    20,
    TOP_RAID,
  )
  assert.equal(marks(d).length, 0)
})

test('a structural level stops defending when its line stops being drawn', () => {
  // `standingUntil` is what MainChart passes: the structural family's lifespan
  // is the chart's own line, so retiring the line retires the evidence.
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  assert.equal(marks(d, () => ts(10)).length, 0)
  assert.equal(marks(d, () => ts(25)).length, 1)
})

test('each family can be supplied by its own source', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(101.2, 1)],
      poi_zones: [poi(101.3, 101.5, 1)],
      liquidity_zones: [equal(101.6, 101.7, 1)],
      volume_profile: {
        symbol: 'TEST',
        timeframe: '1h',
        start_timestamp: ts(0),
        end_timestamp: ts(29),
        price_low: 99,
        price_high: 103,
        bucket_size: 0.1,
        buckets: [],
        poc_price: 102.0,
        value_area_low: 99.5,
        value_area_high: 102.4,
        value_area_pct: 70,
        total_volume: 100,
        delta_estimated: true,
      },
    }),
    20,
    TOP_RAID,
  )
  const got = marks(d)
  assert.equal(got.length, 1)
  assert.deepEqual(got[0].families, ['fair', 'order', 'resting', 'structural'])
})

test('liquidation bands supply the resting family', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(101.5, 1)],
      liquidation_map: {
        symbol: 'TEST',
        timeframe: '1h',
        current_price: 100,
        dominant_leveraged_side: 'long',
        positioning_intensity: 1,
        funding_rate: 0,
        open_interest_change_pct: 0,
        long_short_ratio: 1,
        bands: [
          {
            price_low: 101.8,
            price_high: 102.2,
            leverage: 25,
            side: 'buy_side',
            source_entry_price: 100,
            intensity: 1,
            start_time: ts(1),
            end_time: null,
          },
        ],
      },
    } as Partial<DashboardData>),
    20,
    TOP_RAID,
  )
  const got = marks(d)
  assert.equal(got.length, 1)
  assert.deepEqual(got[0].families, ['resting', 'structural'])
})

test('consumed counts the pools this very candle retired', () => {
  const d = withCandle(
    data({
      internal_structure_events: [structural(101.5, 1)],
      poi_zones: [poi(101.8, 102.2, 1, ts(20))],
    }),
    20,
    TOP_RAID,
  )
  const got = marks(d)
  assert.equal(got.length, 1)
  assert.equal(got[0].consumed, 1, 'the block died on the candle that swept it')
})

test('consumed is zero when nothing retired on the candle', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  assert.equal(marks(d)[0].consumed, 0)
})

test('excursionAtr reports the measured excursion, not the threshold', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  const got = marks(d)[0]
  const atr = meanTrueRangePct(d.candles) * 100.5
  assert.ok(Math.abs(got.excursionAtr - 1.5 / atr) < 1e-9)
})

test('no envelope, no marks', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  assert.equal(marks({ ...d, vwap: null } as DashboardData).length, 0)
  const nullBands = {
    ...d,
    vwap: { ...d.vwap!, points: d.vwap!.points.map((p) => ({ ...p, upper_1: null, lower_1: null })) },
  } as DashboardData
  assert.equal(marks(nullBands).length, 0)
})

// ---------------------------------------------------------------------------
// P1.2 — the ATR repaint, reproduced.
// ---------------------------------------------------------------------------

/**
 * The defect: `meanTrueRangePct` averages the *whole* array, so appending
 * volatile candles after `T` raises the ATR that the excursion gate at `T` is
 * measured against, and a mark that stood at 1.02 ATR silently drops below 1.0.
 *
 * This test asserts the broken behaviour on purpose. When the causal mode
 * becomes the default it should be inverted, not deleted — it is the record of
 * what the default used to do.
 */
test('P1.2: future volatility erases a past mark (shipped behaviour)', () => {
  const before = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.1, low: 99.5, close: 100.5 })
  assert.equal(marks(before).length, 1, 'stands on its own window at 1.04 ATR')

  const after = withLoudTail(before)

  assert.equal(marks(after).length, 0, 'the same candle is no longer a defence')
})

test('P1.2: the repaint is the ATR, not the levels or the envelope', () => {
  // Isolates the cause: hold the level cartography and the envelope fixed and
  // only the window-wide mean changes.
  const base = data(TWO_FAMILIES)
  const short = meanTrueRangePct(base.candles)
  const long = meanTrueRangePct([
    ...base.candles,
    ...Array.from({ length: 10 }, (_, i) => candle(30 + i, { high: 105, low: 95 })),
  ])
  assert.ok(long > short * 2, 'appending loud candles moves the shared unit')
})

// ---------------------------------------------------------------------------
// P1.3/P1.4 — the causal unit.
// ---------------------------------------------------------------------------

test('P1.3: the causal series is the running mean of the shipped formula', () => {
  const candles = data().candles
  const series = meanTrueRangePctSeries(candles)
  assert.equal(series.length, candles.length)
  // Each entry equals the shipped function applied to the prefix — same unit,
  // same formula, only the horizon differs.
  for (const i of [0, 1, 7, 15, candles.length - 1]) {
    const expected = meanTrueRangePct(candles.slice(0, i + 1))
    assert.ok(Math.abs(series[i] - expected) < 1e-12, `prefix ${i}`)
  }
})

test('P1.3: the last causal value equals the shipped whole-window value', () => {
  // The convergence that makes this the least-drift choice: at the live edge,
  // where the user actually reads the chart, causal and shipped agree exactly.
  const candles = data(TWO_FAMILIES).candles
  const series = meanTrueRangePctSeries(candles)
  assert.ok(Math.abs(series[series.length - 1] - meanTrueRangePct(candles)) < 1e-12)
})

test('P1.4: the causal series is computed in one pass', () => {
  // Not a timing assertion — a shape one. A prefix-mean implementation returns
  // a value per candle from a single traversal, so a long series stays linear.
  const long = Array.from({ length: 20_000 }, (_, i) => candle(i))
  const started = process.hrtime.bigint()
  const series = meanTrueRangePctSeries(long)
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6
  assert.equal(series.length, 20_000)
  assert.ok(elapsedMs < 250, `20k candles took ${elapsedMs.toFixed(1)}ms — is this O(N^2)?`)
})

// ---------------------------------------------------------------------------
// P1.5/P1.6 — causal mode, and the truncation invariant.
// ---------------------------------------------------------------------------

test('P1.5: causal mode is opt-in — the default output is unchanged', () => {
  const d = withCandle(data(TWO_FAMILIES), 20, TOP_RAID)
  const shipped = buildDefendedMarks(d, buildDefenceLevels(d, STANDING))
  const explicit = buildDefendedMarks(d, buildDefenceLevels(d, STANDING, { causalAtr: false }), {
    causalAtr: false,
  })
  assert.deepEqual(explicit, shipped)
})

test('P1.6: under causal ATR, future candles cannot erase a past mark', () => {
  const before = withCandle(data(TWO_FAMILIES), 20, { open: 100, high: 102.1, low: 99.5, close: 100.5 })
  const after = withLoudTail(before)

  const causal = (d: DashboardData) =>
    buildDefendedMarks(d, buildDefenceLevels(d, STANDING, { causalAtr: true }), { causalAtr: true })

  assert.equal(causal(before).length, 1)
  assert.equal(causal(after).length, 1, 'the mark survives its own future')
  assert.deepEqual(causal(after)[0], causal(before)[0])
})

/**
 * The invariant the whole historical measurement rests on: what the rule says
 * about the first `T` candles must not depend on candle `T+1` existing. Cutting
 * the window anywhere must reproduce a prefix of the full-window answer.
 *
 * Permanent. Any future input that reaches backwards — a rolling normalization,
 * a lookback profile, a level whose lifespan is recomputed — breaks this test
 * before it reaches a measurement.
 */
test('P1.6: truncation invariant holds at every cut under causal ATR', () => {
  // A window with several raids at different distances, so the cuts land on
  // both sides of real decisions instead of on empty candles.
  const base = data(TWO_FAMILIES, 60)
  let d = base
  for (const [at, over] of [
    [12, { open: 100, high: 102.6, low: 99.5, close: 100.5 }],
    [20, { open: 100, high: 102.1, low: 99.5, close: 100.5 }],
    [31, { open: 100, high: 103.4, low: 99.5, close: 100.4 }],
    [44, { open: 100, high: 102.2, low: 99.5, close: 100.6 }],
  ] as [number, Partial<Candle>][]) {
    d = withCandle(d, at, over)
  }

  const causal = (x: DashboardData) =>
    buildDefendedMarks(x, buildDefenceLevels(x, STANDING, { causalAtr: true }), { causalAtr: true })

  const full = causal(d)
  assert.ok(full.length >= 3, `expected several marks to cut across, got ${full.length}`)

  for (const cut of [15, 22, 33, 40, 46, 55, 60]) {
    const truncated = {
      ...d,
      candles: d.candles.slice(0, cut),
      vwap: { ...d.vwap!, points: d.vwap!.points.slice(0, cut) },
    } as DashboardData
    const got = causal(truncated)
    const expected = full.filter((m) => m.timestamp < ts(cut))
    assert.deepEqual(got, expected, `cut at ${cut}`)
  }
})

test('P1.6: the shipped default fails the same truncation invariant', () => {
  // The counterpart to the test above, and the reason it matters: without the
  // causal unit the invariant simply does not hold.
  const base = data(TWO_FAMILIES, 60)
  let d = base
  for (const [at, over] of [
    [12, { open: 100, high: 102.6, low: 99.5, close: 100.5 }],
    [20, { open: 100, high: 102.1, low: 99.5, close: 100.5 }],
    [31, { open: 100, high: 103.4, low: 99.5, close: 100.4 }],
  ] as [number, Partial<Candle>][]) {
    d = withCandle(d, at, over)
  }
  const full = buildDefendedMarks(d, buildDefenceLevels(d, STANDING))

  let disagreed = false
  for (const cut of [15, 22, 33, 40]) {
    const truncated = {
      ...d,
      candles: d.candles.slice(0, cut),
      vwap: { ...d.vwap!, points: d.vwap!.points.slice(0, cut) },
    } as DashboardData
    const got = buildDefendedMarks(truncated, buildDefenceLevels(truncated, STANDING))
    const expected = full.filter((m) => m.timestamp < ts(cut))
    if (JSON.stringify(got) !== JSON.stringify(expected)) disagreed = true
  }
  assert.ok(disagreed, 'shipped mode is expected to repaint under truncation')
})

test('P1.11: the mark list is exactly the diagnostic rows that failed nothing', () => {
  // The invariant that keeps the funnel honest. `diagnoseDefendedMarks` exists
  // so rejections can be counted, and it is worth nothing if it can drift from
  // the rule that ships — so the two share one implementation and this asserts
  // the projection rather than trusting it.
  const d = withCandle(
    withCandle(data(TWO_FAMILIES, 60), 20, TOP_RAID),
    41,
    { open: 100, high: 101.6, low: 99.5, close: 100.9 },
  )
  for (const causalAtr of [false, true]) {
    const levels = buildDefenceLevels(d, STANDING, { causalAtr })
    const rows = diagnoseDefendedMarks(d, levels, { causalAtr })
    const projected = rows
      .filter((r) => r.failed.length === 0)
      .map((r) => ({
        timestamp: r.timestamp,
        side: r.side,
        price: r.price,
        edge: r.edge,
        families: r.families,
        excursionAtr: r.excursionAtr,
        consumed: r.consumed,
      }))
    assert.deepEqual(buildDefendedMarks(d, levels, { causalAtr }), projected)
    assert.ok(rows.length > projected.length, 'the fixture must contain rejections too')
  }
})

test('P1.11: a rejected candidate reports every gate it missed, with shortfalls', () => {
  // A candle that reaches the edge but barely: short excursion, thin wick, and
  // no levels at all. All three gates must be reported, not just the first.
  const d = withCandle(data({}, 30), 20, { open: 100, high: 101.3, low: 99.5, close: 100.9 })
  const rows = diagnoseDefendedMarks(d, buildDefenceLevels(d, STANDING, { causalAtr: true }), {
    causalAtr: true,
  })
  assert.equal(rows.length, 1)
  const r = rows[0]
  assert.deepEqual(r.failed, ['excursion', 'wick_body', 'families'])
  assert.equal(r.firstFailed, 'excursion')
  assert.ok(r.excursionShortfall! > 0 && r.excursionShortfall! < 1)
  assert.ok(r.wickBodyShortfall! > 0)
  assert.equal(r.familiesShortfall, 2)
})
