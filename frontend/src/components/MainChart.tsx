import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'

import { LineLabelsPrimitive, type LineLabel } from '../charting/LineLabelsPrimitive'
import { HuntWindowPrimitive, type HuntWindow } from '../charting/HuntWindowPrimitive'
import { POIBoxesPrimitive, type POIBox } from '../charting/POIBoxesPrimitive'
import { HeatmapStripPrimitive, type HeatmapBand } from '../charting/HeatmapStripPrimitive'
import {
  LiquidationBandsPrimitive,
  type LiquidationBandInput,
} from '../charting/LiquidationBandsPrimitive'
import type { BehaviorDivergence, DashboardData, LiquidationBand, ManipulationCycle, MarketStructure, OIParticipation, POIZone, VolumeSpreadSignal } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  CONSOLIDATION_BOX_STYLES,
  DARK_BG,
  DEFAULT_ZONE_COLOR,
  FONT_COLOR,
  DIVERGENCE_STYLES,
  MANIPULATION_BOX_STYLES,
  POI_BOX_STYLES,
  RSI_DIV_BEARISH_COLOR,
  RSI_DIV_BULLISH_COLOR,
  RSI_LINE_COLOR,
  RSI_OVERBOUGHT_COLOR,
  RSI_OVERSOLD_COLOR,
  STRUCTURE_DIRECTION_COLORS,
  STRUCTURE_EVENT_STYLES,
  TREND_ICONS,
  VOLUME_DOWN_COLOR,
  VOLUME_UP_COLOR,
  VSA_STYLES,
  ZONE_COLORS,
  ZONE_TYPE_LABELS,
} from '../theme'
import { setChartTimezoneMode, toChartTime } from '../utils/chartTime'

const TOP_N_ZONES = 5
const MAX_INTERNAL_SWEEPS = 3

// Suffix appended to a structure event label when the OI analysis qualified
// it: ⊕ new money behind the break, ⊖ break driven by position unwinding,
// ⚡ sweep that flushed leveraged positions. FLAT adds nothing.
const OI_PARTICIPATION_SUFFIX: Record<OIParticipation, string> = {
  new_money: '⊕',
  covering: '⊖',
  flush: '⚡',
  flat: '',
}

const DELTA_CHART_RATIO = 0.16
const RSI_CHART_RATIO = 0.16
const CONTROL_CHART_RATIO = 0.14
const MIN_TOTAL_HEIGHT = 500
const PRICE_SCALE_MIN_WIDTH = 110

// Colors for the control oscillator (CVD × OI): buyers green, sellers red,
// balanced (aggression unwinding / flat, no conviction-backed control) dim.
const CONTROL_BUYERS_COLOR = '#26a69a'
const CONTROL_SELLERS_COLOR = '#ef5350'
const CONTROL_BALANCED_COLOR = '#4a5163'

// Split the available height across the panes. The volume-delta + RSI panes are
// one group (`showIndicators`); the control oscillator toggles *independently*
// (`showControl`). Each hidden pane collapses to 0 and the main candlestick
// pane absorbs the freed height, so opening only the control pane shows only it.
function paneHeights(totalHeight: number, showIndicators: boolean, showControl: boolean) {
  const deltaHeight = showIndicators ? Math.round(totalHeight * DELTA_CHART_RATIO) : 0
  const rsiHeight = showIndicators ? Math.round(totalHeight * RSI_CHART_RATIO) : 0
  const controlHeight = showControl ? Math.round(totalHeight * CONTROL_CHART_RATIO) : 0
  const mainHeight = totalHeight - deltaHeight - rsiHeight - controlHeight
  return { mainHeight, deltaHeight, controlHeight, rsiHeight }
}

// Which pane carries the visible time axis. RSI carries it whenever the
// indicator group is open (the long-standing, well-tested path — labels fall
// back to it). Otherwise the *main* pane keeps its own axis, even when the
// control oscillator is open below it: the control pane never carries the axis,
// so the BOS/CHoCH label primitive always resolves time->x from a live,
// perfectly-synced scale (the main's own when visible, else RSI) and never from
// the control pane — which was desyncing labels on a timeframe switch.
function axisVisibility(showIndicators: boolean) {
  return {
    main: !showIndicators,
    control: false,
    rsi: showIndicators,
  }
}

const RSI_PERIOD = 14
const DIV_PIVOT_LOOKBACK = 5
const DIV_RANGE_LOWER = 5
const DIV_RANGE_UPPER = 60

function computeRSI(closes: number[], period: number): (number | null)[] {
  const rsi: (number | null)[] = []
  if (closes.length < period + 1) {
    return closes.map(() => null)
  }

  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1]
    if (change > 0) avgGain += change
    else avgLoss -= change
  }
  avgGain /= period
  avgLoss /= period

  for (let i = 0; i < period; i++) rsi.push(null)
  rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))

  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1]
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? -change : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))
  }

  return rsi
}

interface Divergence {
  type: 'bullish' | 'bearish'
  startIndex: number
  endIndex: number
  startRSI: number
  endRSI: number
}

function findPivots(
  values: (number | null)[],
  lookback: number,
  comparator: (val: number, neighbor: number) => boolean,
): number[] {
  const pivots: number[] = []
  for (let i = lookback; i < values.length - lookback; i++) {
    const v = values[i]
    if (v === null) continue
    let isPivot = true
    for (let j = 1; j <= lookback; j++) {
      const left = values[i - j]
      const right = values[i + j]
      if (left === null || right === null || !comparator(v, left) || !comparator(v, right)) {
        isPivot = false
        break
      }
    }
    if (isPivot) pivots.push(i)
  }
  return pivots
}

function detectDivergences(
  closes: number[],
  rsiValues: (number | null)[],
): Divergence[] {
  const divergences: Divergence[] = []

  const pivotHighs = findPivots(rsiValues, DIV_PIVOT_LOOKBACK, (v, n) => v > n)
  const pivotLows = findPivots(rsiValues, DIV_PIVOT_LOOKBACK, (v, n) => v < n)

  // Bearish: price HH + RSI LH, RSI > 50
  for (let i = 1; i < pivotHighs.length; i++) {
    const curr = pivotHighs[i]
    const prev = pivotHighs[i - 1]
    if (curr - prev < DIV_RANGE_LOWER || curr - prev > DIV_RANGE_UPPER) continue
    const currRSI = rsiValues[curr]!
    const prevRSI = rsiValues[prev]!
    if (closes[curr] > closes[prev] && currRSI < prevRSI && currRSI > 50) {
      divergences.push({ type: 'bearish', startIndex: prev, endIndex: curr, startRSI: prevRSI, endRSI: currRSI })
    }
  }

  // Bullish: price LL + RSI HL, RSI < 50
  for (let i = 1; i < pivotLows.length; i++) {
    const curr = pivotLows[i]
    const prev = pivotLows[i - 1]
    if (curr - prev < DIV_RANGE_LOWER || curr - prev > DIV_RANGE_UPPER) continue
    const currRSI = rsiValues[curr]!
    const prevRSI = rsiValues[prev]!
    if (closes[curr] < closes[prev] && currRSI > prevRSI && currRSI < 50) {
      divergences.push({ type: 'bullish', startIndex: prev, endIndex: curr, startRSI: prevRSI, endRSI: currRSI })
    }
  }

  return divergences
}

// Lightweight Charts defaults the candlestick series to `precision: 2,
// minMove: 0.01`, so a low-priced pair (ETHBTC ~0.03, ENAUSDT sub-1) snaps to
// 0.01 ticks and every intrabar move collapses into a handful of levels. Derive
// the price format from the current magnitude so the axis keeps ~5 significant
// digits: precision = 4 - floor(log10(ref)), clamped to [2, 8].
function priceFormatFor(ref: number): { precision: number; minMove: number } {
  if (!Number.isFinite(ref) || ref <= 0) return { precision: 2, minMove: 0.01 }
  const exponent = Math.floor(Math.log10(ref))
  const precision = Math.min(8, Math.max(2, 4 - exponent))
  return { precision, minMove: 10 ** -precision }
}

function lineFrom(
  startTime: UTCTimestamp,
  lastCandleTime: UTCTimestamp,
  value: number,
  minTime?: UTCTimestamp,
) {
  // Clamp the start to the first visible candle. Overlay series live only on
  // the main chart; a point before candles[0] (e.g. a CHoCH whose
  // reference_timestamp predates the visible window — its pivot can come from
  // the buffered bootstrap series) would add an extra slot to the main chart's
  // time scale that the delta/RSI panes lack, shifting their logical-range sync
  // by one bar and desyncing the crosshair.
  const start = minTime !== undefined && startTime < minTime ? minTime : startTime
  return start < lastCandleTime
    ? [
        { time: start, value },
        { time: lastCandleTime, value },
      ]
    : [{ time: lastCandleTime, value }]
}

// If a provisional CHoCH is later invalidated, returns the timestamp of the
// `choch_failed` event that paired with it (a same-direction failure firing
// before any other same-direction CHoCH intervenes); otherwise `null`. A failed
// CHoCH never actually reversed structure — the prior trend resumed — so it must
// stay transparent to *other* lines' termination (it doesn't cut them), while
// its *own* line stops at this failure point. A *fizzle marker* (a provisional
// `choch_failed`) is different: the state-machine trend never flipped back, so
// the CHoCH still genuinely reversed structure and must keep cutting other
// lines — only its own line stops at the reclaim. Callers pass
// `includeFizzle: false` when deciding transparency.
function failedChochTime(
  choch: MarketStructure,
  allEvents: MarketStructure[],
  { includeFizzle = true }: { includeFizzle?: boolean } = {},
): UTCTimestamp | null {
  if (choch.event !== 'change_of_character') return null
  const chochTime = toChartTime(choch.timestamp)
  const failedTimes = allEvents
    .filter(
      (e) =>
        e.scope === choch.scope &&
        e.event === 'choch_failed' &&
        (includeFizzle || !e.provisional) &&
        e.direction === choch.direction &&
        toChartTime(e.timestamp) > chochTime,
    )
    .map((e) => toChartTime(e.timestamp))
  if (failedTimes.length === 0) return null
  const firstFailed = Math.min(...failedTimes) as UTCTimestamp
  // Pair the failure with its CHoCH: ignore it if a later same-direction CHoCH
  // sits between them (that one owns the failure instead).
  const interveningChoch = allEvents.some(
    (e) =>
      e.scope === choch.scope &&
      e.event === 'change_of_character' &&
      e.direction === choch.direction &&
      toChartTime(e.timestamp) > chochTime &&
      toChartTime(e.timestamp) < firstFailed,
  )
  return interveningChoch ? null : firstFailed
}

function isFailedChoch(choch: MarketStructure, allEvents: MarketStructure[]): boolean {
  return failedChochTime(choch, allEvents, { includeFizzle: false }) !== null
}

function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toChartTime(event.timestamp)

  if (event.event === 'change_of_character') {
    // A CHoCH line runs until the next real CHoCH supersedes it — of *either*
    // direction. An opposite-direction CHoCH is a reversal that clears the
    // stale reference; a *same*-direction CHoCH is simply a newer reference for
    // that side, so the older one stops there rather than both running to the
    // edge (the case where the internal trend briefly flipped and back without
    // surfacing a drawn opposite CHoCH, emitting two same-direction CHoCHs).
    // Failed/provisional CHoCHs don't count — one that never took hold or is
    // still forming isn't the active reference.
    const candidates = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'change_of_character' &&
          !other.provisional &&
          !isFailedChoch(other, allEvents) &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    // If this CHoCH itself failed, its line stops at the failure point.
    const ownFailure = failedChochTime(event, allEvents)
    if (ownFailure !== null) candidates.push(ownFailure)
    // A later same-direction BOS whose reference sits on the *wrong side* of
    // this CHoCH's level (below it for a bullish CHoCH, above for bearish)
    // means the trend collapsed through the level and rebuilt from the other
    // side — an excursion whose opposite CHoCH failed, so it is transparent
    // above, yet the old reversal reference is plainly stale (ENA 4H 2026-06:
    // a bullish CHoCH at 0.086 ran to the edge across a dive to 0.070 because
    // both superseding bearish CHoCHs failed). A normal leg's staircase only
    // moves away from the CHoCH level, so this never fires mid-trend.
    if (event.reference_price_level != null) {
      const rebasedAt = allEvents
        .filter(
          (other) =>
            other.scope === event.scope &&
            other.event === 'break_of_structure' &&
            !other.provisional &&
            other.direction === event.direction &&
            other.reference_price_level != null &&
            (event.direction === 'bullish'
              ? other.reference_price_level < event.reference_price_level!
              : other.reference_price_level > event.reference_price_level!) &&
            toChartTime(other.timestamp) > eventTime,
        )
        .map((other) => toChartTime(other.timestamp))
      candidates.push(...rebasedAt)
    }
    return candidates.length > 0 ? (Math.min(...candidates) as UTCTimestamp) : lastCandleTime
  }

  const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        !other.provisional &&
        toChartTime(other.timestamp) > eventTime &&
        ((other.direction === event.direction &&
          (other.event === 'break_of_structure' ||
            (other.event === 'change_of_character' && !isFailedChoch(other, allEvents)) ||
            // A real same-direction CHOCH_FAILED invalidates the leg this BOS
            // extended and reverts the trend, so the BOS reference is no longer
            // standing — the line ends at the ✕ instead of running to the edge
            // (a leg that ends via failure has no opposite CHoCH to end it).
            (event.event === 'break_of_structure' && other.event === 'choch_failed'))) ||
          (other.direction === oppositeDirection &&
            other.event === 'change_of_character' &&
            !isFailedChoch(other, allEvents))),
    )
    .map((other) => toChartTime(other.timestamp))

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}

// A POI box spans the zone's real lifecycle: it stays open (full width) while
// the zone is ACTIVE — an armed order block price may still return to — and
// closes at the candle whose close broke through it (`invalidated_at`).
// Price touching inside the zone does not retire it.
function poiBoxEndTime(zone: POIZone, lastCandleTime: UTCTimestamp): UTCTimestamp {
  return zone.invalidated_at
    ? toChartTime(zone.invalidated_at)
    : ((lastCandleTime + 9_999_999) as UTCTimestamp)
}

// Short labels per MSB zone kind: order block / breaker block / mitigation block.
const POI_KIND_LABELS: Record<string, string> = {
  order_block: 'OB',
  breaker_block: 'BB',
  mitigation_block: 'MB',
}

const DIVERGENCE_MARKER_SHAPES: Record<string, { shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'; position: 'aboveBar' | 'belowBar' }> = {
  distribution: { shape: 'arrowDown', position: 'aboveBar' },
  accumulation: { shape: 'arrowUp', position: 'belowBar' },
  exhaustion: { shape: 'circle', position: 'aboveBar' },
  absorption: { shape: 'square', position: 'belowBar' },
}

// Chart-only declutter for liquidation bands: the API returns the full set
// (including far/old liquidations, kept for the backtest), but the chart shows
// only what's actionable near current price — the still-live (untriggered)
// pools plus a few of the most recent hits for context.
const LIQ_PRICE_WINDOW = 0.08 // ±8% of current price
const LIQ_MAX_BANDS = 12
const LIQ_MAX_RECENT_HITS = 4

/** Interleave two intensity-sorted lists, keeping both sides represented. */
function balancedTake(above: LiquidationBand[], below: LiquidationBand[], budget: number): LiquidationBand[] {
  const out: LiquidationBand[] = []
  let i = 0
  while (out.length < budget && (i < above.length || i < below.length)) {
    if (i < below.length) out.push(below[i])
    if (out.length < budget && i < above.length) out.push(above[i])
    i++
  }
  return out
}

function selectVisibleLiquidationBands(
  bands: LiquidationBand[],
  currentPrice: number,
  liveOnly: boolean,
): LiquidationBand[] {
  const mid = (b: LiquidationBand) => (b.price_low + b.price_high) / 2
  const inWindow = bands.filter(
    (b) => Math.abs(mid(b) - currentPrice) <= currentPrice * LIQ_PRICE_WINDOW,
  )
  // Relevance blends proximity to current price (dominant) with intensity, so
  // the nearest live pools always surface instead of far-but-strong ones.
  const relevance = (b: LiquidationBand) => {
    const distPct = Math.abs(mid(b) - currentPrice) / currentPrice
    const proximity = Math.max(0, 1 - distPct / LIQ_PRICE_WINDOW)
    return 0.6 * proximity + 0.4 * (b.intensity / 100)
  }
  const byRelevance = (a: LiquidationBand, b: LiquidationBand) => relevance(b) - relevance(a)
  const live = inWindow.filter((b) => b.end_time === null)
  const hits = liveOnly
    ? []
    : inWindow
        .filter((b) => b.end_time !== null)
        .sort((a, b) => Date.parse(b.end_time as string) - Date.parse(a.end_time as string))

  // Reserve a few slots for recent hits (context), then fill with live pools
  // balanced across both sides of price so above and below stay visible.
  const recentHits = hits.slice(0, LIQ_MAX_RECENT_HITS)
  const liveBudget = LIQ_MAX_BANDS - recentHits.length
  const above = live.filter((b) => mid(b) >= currentPrice).sort(byRelevance)
  const below = live.filter((b) => mid(b) < currentPrice).sort(byRelevance)
  const selected = [...balancedTake(above, below, liveBudget), ...recentHits]
  if (selected.length < LIQ_MAX_BANDS) {
    selected.push(...hits.slice(recentHits.length, recentHits.length + (LIQ_MAX_BANDS - selected.length)))
  }
  return selected
}

// Chart-only declutter for POI order blocks: an ACTIVE zone legitimately
// extends to the right edge while armed, so over time far-from-price ones
// accumulate as clutter. Keep only the most recent few per direction, and only
// those within a price window derived from the *visible candle range* — not a
// fixed % of price, which would need retuning per asset/timeframe volatility.
// Invalidated zones are dropped (the script deletes broken boxes). Each MSB
// emits up to two same-direction zones (OB + breaker/mitigation block), so
// the cap covers two full breaks per direction.
const POI_MAX_ACTIVE_PER_DIRECTION = 4
const POI_PRICE_WINDOW_RANGE_FRACTION = 0.35

function selectVisiblePoiZones(
  zones: POIZone[],
  currentPrice: number,
  visiblePriceRange: number,
): POIZone[] {
  const window = visiblePriceRange * POI_PRICE_WINDOW_RANGE_FRACTION
  // Distance from current price to the zone itself (0 when price is inside it).
  const distance = (z: POIZone) =>
    Math.max(z.price_low - currentPrice, currentPrice - z.price_high, 0)
  const active = zones.filter((z) => z.status === 'active' && distance(z) <= window)
  const byRecency = (a: POIZone, b: POIZone) =>
    Date.parse(b.created_at) - Date.parse(a.created_at)
  const takeRecent = (direction: POIZone['direction']) =>
    active
      .filter((z) => z.direction === direction)
      .sort(byRecency)
      .slice(0, POI_MAX_ACTIVE_PER_DIRECTION)
  return [...takeRecent('bullish'), ...takeRecent('bearish')]
}

function buildDivergenceMarkers(divergences: BehaviorDivergence[]): SeriesMarker<Time>[] {
  return [...divergences]
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    .map((div) => {
      const style = DIVERGENCE_STYLES[div.divergence_type]
      const markerStyle = DIVERGENCE_MARKER_SHAPES[div.divergence_type] ?? DIVERGENCE_MARKER_SHAPES.exhaustion
      const dirIcon = div.direction === 'bullish' ? '▲' : '▼'
      return {
        time: toChartTime(div.timestamp) as Time,
        position: markerStyle.position,
        shape: markerStyle.shape,
        color: style?.color ?? '#888888',
        text: `${style?.label ?? div.divergence_type} ${dirIcon}`,
        size: 1.5,
      } as SeriesMarker<Time>
    })
}

function buildVsaMarkers(signals: VolumeSpreadSignal[]): SeriesMarker<Time>[] {
  return [...signals]
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    .map((sig) => {
      const style = VSA_STYLES[sig.pattern]
      return {
        time: toChartTime(sig.timestamp) as Time,
        position: style?.position ?? 'aboveBar',
        shape: sig.direction === 'bullish' ? 'arrowUp' : 'arrowDown',
        color: style?.color ?? '#8a94a6',
        // Label-less: the arrow + its VSA colour identify the pattern; the
        // text above the arrows was cluttering the chart.
        text: '',
        size: 1,
      } as SeriesMarker<Time>
    })
}

const MAX_MANIP_BOXES = 3
const ZONE_PRICE_BUFFER_PCT = 0.003

function buildManipulationBoxes(
  cycles: ManipulationCycle[],
  lastCandleTime: UTCTimestamp,
): POIBox[] {
  const boxes: POIBox[] = []

  const statusOrder: Record<string, number> = { in_progress: 0, confirmed: 1, failed: 2 }
  const sorted = [...cycles].sort((a, b) => {
    const sa = statusOrder[a.status] ?? 2
    const sb = statusOrder[b.status] ?? 2
    if (sa !== sb) return sa - sb
    return new Date(b.accumulation_start).getTime() - new Date(a.accumulation_start).getTime()
  }).slice(0, MAX_MANIP_BOXES)

  for (const cycle of sorted) {
    const style = MANIPULATION_BOX_STYLES[cycle.status] ?? MANIPULATION_BOX_STYLES.failed

    const zoneMid = (cycle.target_zone_price_high + cycle.target_zone_price_low) / 2
    const buffer = zoneMid * ZONE_PRICE_BUFFER_PCT
    const priceLow =
      cycle.target_zone_price_low === cycle.target_zone_price_high
        ? cycle.target_zone_price_low - buffer
        : cycle.target_zone_price_low
    const priceHigh =
      cycle.target_zone_price_low === cycle.target_zone_price_high
        ? cycle.target_zone_price_high + buffer
        : cycle.target_zone_price_high

    const x0 = toChartTime(cycle.accumulation_start)
    const x1 = cycle.sweep_timestamp
      ? toChartTime(cycle.sweep_timestamp)
      : cycle.phase === 'accumulation'
        ? ((lastCandleTime + 9_999_999) as UTCTimestamp)
        : toChartTime(cycle.accumulation_end)

    const dirIcon = cycle.direction === 'bullish' ? '▲' : '▼'
    const phaseLabel =
      cycle.phase === 'accumulation'
        ? 'ACC'
        : cycle.phase === 'manipulation'
          ? 'MANIP'
          : 'CONF'

    boxes.push({
      x0,
      x1,
      priceLow,
      priceHigh,
      borderColor: style.border,
      fillColor: style.fill,
      label: `${phaseLabel} ${dirIcon}`,
    })
  }

  return boxes
}

interface MainChartProps {
  data: DashboardData
  showConsolidationRanges?: boolean
  showManipulationBoxes?: boolean
  showDivergenceMarkers?: boolean
  showVsaMarkers?: boolean
  showHeatmap?: boolean
  showLiquidationBands?: boolean
  liquidationLiveOnly?: boolean
  showSweptZones?: boolean
  showOrderBlocks?: boolean
  showSweeps?: boolean
  showEqlZones?: boolean
  showIndicators?: boolean
  showHuntWindow?: boolean
  showContinuationWindow?: boolean
  showVolume?: boolean
  showRsiDivergence?: boolean
  showControlOscillator?: boolean
}

export function MainChart({
  data,
  showConsolidationRanges = true,
  showManipulationBoxes = true,
  showDivergenceMarkers = true,
  showVsaMarkers = true,
  showHeatmap = true,
  showLiquidationBands = true,
  liquidationLiveOnly = false,
  showSweptZones = true,
  showOrderBlocks = true,
  showSweeps = true,
  showEqlZones = true,
  showIndicators = true,
  showHuntWindow = false,
  showContinuationWindow = false,
  showVolume = true,
  showRsiDivergence = false,
  showControlOscillator = false,
}: MainChartProps) {
  // Which clock this chart's times are drawn on -- local intraday, exchange
  // (UTC) on the daily/weekly bars. Set during render, before the effects below
  // convert anything through `toChartTime`; `App` remounts this component on
  // every symbol/timeframe change, so the mode never outlives its data.
  setChartTimezoneMode(data.timeframe)

  const wrapperRef = useRef<HTMLDivElement>(null)
  const mainContainerRef = useRef<HTMLDivElement>(null)
  const deltaContainerRef = useRef<HTMLDivElement>(null)
  const controlContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const deltaChartRef = useRef<IChartApi | null>(null)
  const controlChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const deltaSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const controlSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiOverlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiDivSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const labelsPrimitiveRef = useRef<LineLabelsPrimitive | null>(null)
  const huntWindowPrimitiveRef = useRef<HuntWindowPrimitive | null>(null)
  const poiBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const manipBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const rangeBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const heatmapPrimitiveRef = useRef<HeatmapStripPrimitive | null>(null)
  const liquidationBandsPrimitiveRef = useRef<LiquidationBandsPrimitive | null>(null)
  const divergenceMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const hasFittedRef = useRef(false)
  const isSyncingRef = useRef(false)
  // Read by the ResizeObserver (created once) so it recomputes pane heights
  // against the current minimize state. Kept in sync by the effect below.
  const showIndicatorsRef = useRef(showIndicators)
  const showControlRef = useRef(showControlOscillator)

  useEffect(() => {
    const wrapper = wrapperRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const controlContainer = controlContainerRef.current
    const rsiContainer = rsiContainerRef.current
    if (!wrapper || !mainContainer || !deltaContainer || !controlContainer || !rsiContainer) return

    const totalHeight = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const indicatorsOpen = showIndicatorsRef.current
    const controlOpen = showControlRef.current
    const { mainHeight, deltaHeight, controlHeight, rsiHeight } = paneHeights(
      totalHeight,
      indicatorsOpen,
      controlOpen,
    )
    const av = axisVisibility(indicatorsOpen)

    const chartOptions = {
      layout: {
        background: { type: ColorType.Solid as const, color: DARK_BG },
        textColor: FONT_COLOR,
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
    }

    const chart = createChart(mainContainer, {
      ...chartOptions,
      width: mainContainer.clientWidth,
      height: mainHeight,
      // The bottom-most visible pane carries the time axis (see axisVisibility):
      // RSI when the indicator group is open, the control pane when only it is
      // open, else the main pane itself.
      timeScale: { ...chartOptions.timeScale, visible: av.main },
      rightPriceScale: { minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    chartRef.current = chart

    const deltaChart = createChart(deltaContainer, {
      ...chartOptions,
      width: deltaContainer.clientWidth,
      height: deltaHeight,
      timeScale: { ...chartOptions.timeScale, visible: false },
      rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    deltaChartRef.current = deltaChart

    const controlChart = createChart(controlContainer, {
      ...chartOptions,
      width: controlContainer.clientWidth,
      height: controlHeight,
      timeScale: { ...chartOptions.timeScale, visible: av.control },
      rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    controlChartRef.current = controlChart

    const rsiChart = createChart(rsiContainer, {
      ...chartOptions,
      width: rsiContainer.clientWidth,
      height: rsiHeight,
      timeScale: { ...chartOptions.timeScale, visible: av.rsi },
      rightPriceScale: { scaleMargins: { top: 0.05, bottom: 0.05 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    rsiChartRef.current = rsiChart

    const series = chart.addSeries(CandlestickSeries, {
      upColor: CANDLE_UP_COLOR,
      downColor: CANDLE_DOWN_COLOR,
      borderVisible: false,
      wickUpColor: CANDLE_UP_COLOR,
      wickDownColor: CANDLE_DOWN_COLOR,
    })
    seriesRef.current = series

    // Raw volume histogram, overlaid on the base of the main pane. Its own
    // overlay price scale (`priceScaleId: ''`) with a large top scale margin
    // pins the bars to the bottom ~18% so they sit behind the candles without
    // rescaling the price axis.
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })
    volumeSeriesRef.current = volumeSeries

    const deltaSeries = deltaChart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
    })
    deltaSeriesRef.current = deltaSeries

    const controlSeries = controlChart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
      base: 0,
    })
    controlSeriesRef.current = controlSeries

    const rsiSeries = rsiChart.addSeries(LineSeries, {
      color: RSI_LINE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    })
    rsiSeriesRef.current = rsiSeries

    // RSI reference lines (70 overbought, 30 oversold)
    const rsiOverbought = rsiChart.addSeries(LineSeries, {
      color: RSI_OVERBOUGHT_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiOverlaySeriesRef.current.push(rsiOverbought)

    const rsiOversold = rsiChart.addSeries(LineSeries, {
      color: RSI_OVERSOLD_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiOverlaySeriesRef.current.push(rsiOversold)

    const labelsPrimitive = new LineLabelsPrimitive()
    // When the main pane hides its time axis (another pane carries it), the main
    // chart's time-scale coordinate API returns null. The other charts share the
    // synced, equal-width time scale, so the labels primitive falls back to
    // whichever pane currently holds the visible axis. The main pane keeps its
    // own axis unless the RSI pane carries it, so the fallback is always RSI
    // (only consulted while the main axis is hidden — the indicator-group case).
    labelsPrimitive.fallbackChart = rsiChart
    series.attachPrimitive(labelsPrimitive)
    labelsPrimitiveRef.current = labelsPrimitive

    // Background shading (zOrder 'bottom'): painted beneath candles/overlays.
    const huntWindowPrimitive = new HuntWindowPrimitive()
    series.attachPrimitive(huntWindowPrimitive)
    huntWindowPrimitiveRef.current = huntWindowPrimitive

    const poiBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(poiBoxesPrimitive)
    poiBoxesPrimitiveRef.current = poiBoxesPrimitive

    const manipBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(manipBoxesPrimitive)
    manipBoxesPrimitiveRef.current = manipBoxesPrimitive

    const rangeBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(rangeBoxesPrimitive)
    rangeBoxesPrimitiveRef.current = rangeBoxesPrimitive

    const heatmapPrimitive = new HeatmapStripPrimitive()
    series.attachPrimitive(heatmapPrimitive)
    heatmapPrimitiveRef.current = heatmapPrimitive

    const liquidationBandsPrimitive = new LiquidationBandsPrimitive()
    series.attachPrimitive(liquidationBandsPrimitive)
    liquidationBandsPrimitiveRef.current = liquidationBandsPrimitive

    const divergenceMarkers = createSeriesMarkers(series)
    divergenceMarkersRef.current = divergenceMarkers

    // Sync time scales across the *currently visible* panes only. A collapsed
    // pane (display:none, zero width) has a degenerate time scale: writing a
    // range to it — or letting it broadcast one — corrupts the shared range and,
    // because it sits before the control pane in the loop, stops the control
    // pane from ever receiving the update (the "control only follows zoom when
    // vol/rsi is also on" bug). So a hidden pane neither sends nor receives.
    const charts = [chart, deltaChart, controlChart, rsiChart]
    const isPaneActive = (c: IChartApi) =>
      c === chart ||
      (showIndicatorsRef.current && (c === deltaChart || c === rsiChart)) ||
      (showControlRef.current && c === controlChart)
    for (const src of charts) {
      src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (isSyncingRef.current || !range || !isPaneActive(src)) return
        isSyncingRef.current = true
        for (const dst of charts) {
          if (dst !== src && isPaneActive(dst)) dst.timeScale().setVisibleLogicalRange(range)
        }
        isSyncingRef.current = false
      })
    }

    // Sync crosshairs. Each pane maps a hovered time onto the others — but only
    // the *active* ones: calling setCrosshairPosition on a collapsed (display:
    // none, zero-width) pane can throw, and if it does the old code left
    // `isSyncingRef` stuck `true`, silently killing the logical-range (zoom)
    // sync afterwards — the "control pane only follows zoom when vol/rsi is also
    // on" bug (vol/rsi visible → no hidden pane touched → no throw). A
    // try/finally guarantees the guard is always released.
    const crosshairPanes: { chart: IChartApi; series: ISeriesApi<SeriesType> }[] = [
      { chart, series },
      { chart: deltaChart, series: deltaSeries },
      { chart: controlChart, series: controlSeries },
      { chart: rsiChart, series: rsiSeries },
    ]
    for (const src of crosshairPanes) {
      src.chart.subscribeCrosshairMove((param) => {
        if (isSyncingRef.current || !isPaneActive(src.chart)) return
        isSyncingRef.current = true
        try {
          for (const dst of crosshairPanes) {
            if (dst.chart === src.chart || !isPaneActive(dst.chart)) continue
            if (param.time) dst.chart.setCrosshairPosition(NaN, param.time, dst.series)
            else dst.chart.clearCrosshairPosition()
          }
        } finally {
          isSyncingRef.current = false
        }
      })
    }

    const ro = new ResizeObserver(() => {
      const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
      const { mainHeight: mh, deltaHeight: dh, controlHeight: ch, rsiHeight: rh } = paneHeights(
        h,
        showIndicatorsRef.current,
        showControlRef.current,
      )
      chart.applyOptions({ width: mainContainer.clientWidth, height: mh })
      deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: dh })
      controlChart.applyOptions({ width: controlContainer.clientWidth, height: ch })
      rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rh })
    })
    ro.observe(wrapper)

    return () => {
      ro.disconnect()
      chart.remove()
      deltaChart.remove()
      controlChart.remove()
      rsiChart.remove()
      chartRef.current = null
      deltaChartRef.current = null
      controlChartRef.current = null
      rsiChartRef.current = null
      seriesRef.current = null
      volumeSeriesRef.current = null
      deltaSeriesRef.current = null
      controlSeriesRef.current = null
      rsiSeriesRef.current = null
      overlaySeriesRef.current = []
      rsiOverlaySeriesRef.current = []
      rsiDivSeriesRef.current = []
      labelsPrimitiveRef.current = null
      poiBoxesPrimitiveRef.current = null
      manipBoxesPrimitiveRef.current = null
      rangeBoxesPrimitiveRef.current = null
      heatmapPrimitiveRef.current = null
      liquidationBandsPrimitiveRef.current = null
      divergenceMarkersRef.current = null
      hasFittedRef.current = false
    }
  }, [])

  // Toggle the volume-delta / RSI panes: give the main pane the full height and
  // move the visible time axis onto it while minimized, restore the split when open.
  useEffect(() => {
    const wrapper = wrapperRef.current
    const chart = chartRef.current
    const deltaChart = deltaChartRef.current
    const controlChart = controlChartRef.current
    const rsiChart = rsiChartRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const controlContainer = controlContainerRef.current
    const rsiContainer = rsiContainerRef.current
    showIndicatorsRef.current = showIndicators
    showControlRef.current = showControlOscillator
    if (
      !wrapper || !chart || !deltaChart || !controlChart || !rsiChart ||
      !mainContainer || !deltaContainer || !controlContainer || !rsiContainer
    )
      return

    const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const { mainHeight, deltaHeight, controlHeight, rsiHeight } = paneHeights(
      h,
      showIndicators,
      showControlOscillator,
    )
    const av = axisVisibility(showIndicators)

    // While a pane is closed its container is display:none (zero width), so it
    // never tracks the main chart's time scale (and its one-shot fitContent ran
    // at zero width). Reopening it would otherwise reveal a stale, desynced
    // range -- and resizing from zero width can echo that bad range back onto
    // the main chart. Suppress the sync feedback across the resize, then drive
    // every reopened pane from the main chart's current range.
    isSyncingRef.current = true
    chart.applyOptions({
      width: mainContainer.clientWidth,
      height: mainHeight,
      timeScale: { visible: av.main },
    })
    deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: deltaHeight })
    controlChart.applyOptions({
      width: controlContainer.clientWidth,
      height: controlHeight,
      timeScale: { visible: av.control },
    })
    rsiChart.applyOptions({
      width: rsiContainer.clientWidth,
      height: rsiHeight,
      timeScale: { visible: av.rsi },
    })

    const range = chart.timeScale().getVisibleLogicalRange()
    if (range) {
      if (showIndicators) {
        deltaChart.timeScale().setVisibleLogicalRange(range)
        rsiChart.timeScale().setVisibleLogicalRange(range)
      }
      if (showControlOscillator) {
        controlChart.timeScale().setVisibleLogicalRange(range)
      }
    }
    // Release the guard after this frame's layout (and any resize-triggered
    // range echo) settles.
    requestAnimationFrame(() => {
      isSyncingRef.current = false
    })
  }, [showIndicators, showControlOscillator])

  useEffect(() => {
    const chart = chartRef.current
    const deltaChart = deltaChartRef.current
    const rsiChart = rsiChartRef.current
    const series = seriesRef.current
    const deltaSeries = deltaSeriesRef.current
    const rsiSeries = rsiSeriesRef.current
    if (
      !chart ||
      !deltaChart ||
      !rsiChart ||
      !series ||
      !deltaSeries ||
      !rsiSeries ||
      data.candles.length === 0
    )
      return

    // Adapt the price axis precision to the pair's magnitude so low-priced
    // pairs (ETHBTC, ENAUSDT) don't collapse onto 0.01 ticks. Use the latest
    // close as the reference magnitude (stable within a window).
    series.applyOptions({
      priceFormat: {
        type: 'price',
        ...priceFormatFor(data.candles[data.candles.length - 1].close),
      },
    })

    series.setData(
      data.candles.map((candle) => ({
        time: toChartTime(candle.timestamp),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    )

    // Raw volume overlay (bottom of the main pane), colored by candle direction.
    const volumeSeries = volumeSeriesRef.current
    if (volumeSeries) {
      volumeSeries.applyOptions({ visible: showVolume })
      volumeSeries.setData(
        showVolume
          ? data.candles.map((candle) => ({
              time: toChartTime(candle.timestamp),
              value: candle.volume,
              color: candle.close >= candle.open ? VOLUME_UP_COLOR : VOLUME_DOWN_COLOR,
            }))
          : [],
      )
    }

    // Volume delta histogram
    // VSA tint per candle timestamp: a flagged candle's volume-delta bar is
    // colored by what the pattern *means* (climax/thrust/quiet) rather than
    // just its direction — the whole point of reading volume with price.
    const vsaColorByTs = new Map<string, string>()
    if (showVsaMarkers) {
      for (const sig of data.volume_spread_signals ?? []) {
        vsaColorByTs.set(sig.timestamp, VSA_STYLES[sig.pattern]?.color ?? '#8a94a6')
      }
    }
    deltaSeries.setData(
      data.candles.map((candle) => {
        const delta = 2 * candle.taker_buy_volume - candle.volume
        const vsaColor = vsaColorByTs.get(candle.timestamp)
        return {
          time: toChartTime(candle.timestamp),
          value: delta,
          color:
            vsaColor ?? (candle.close >= candle.open ? CANDLE_UP_COLOR : CANDLE_DOWN_COLOR),
        }
      }),
    )

    // Control oscillator (CVD aggression × OI): signed conviction per candle,
    // colored by who is credited with control (buyers green / sellers red /
    // balanced dim). A single histogram doubles as "who + how strongly".
    const controlSeries = controlSeriesRef.current
    if (controlSeries) {
      const controlColor: Record<string, string> = {
        buyers: CONTROL_BUYERS_COLOR,
        sellers: CONTROL_SELLERS_COLOR,
        balanced: CONTROL_BALANCED_COLOR,
      }
      // Index the sparse control readings by candle timestamp, then emit an
      // entry for *every* candle -- a real bar where there's a reading, a
      // whitespace `{ time }` otherwise -- so bar indices match the main/delta
      // charts and the logical-range sync stays aligned (same fix as RSI).
      const controlByTs = new Map(
        (data.market_control?.series ?? []).map((p) => [p.timestamp, p]),
      )
      controlSeries.setData(
        data.candles.map((candle) => {
          const time = toChartTime(candle.timestamp)
          const p = controlByTs.get(candle.timestamp)
          return p
            ? { time, value: p.control_score, color: controlColor[p.controller] ?? CONTROL_BALANCED_COLOR }
            : { time }
        }),
      )
    }

    // RSI — include whitespace entries for the bootstrap period so bar indices
    // match the main/delta charts and the logical-range sync stays aligned.
    const closes = data.candles.map((c) => c.close)
    const rsiValues = computeRSI(closes, RSI_PERIOD)
    const rsiData = data.candles.map((candle, i) => {
      const time = toChartTime(candle.timestamp)
      const v = rsiValues[i]
      return v !== null ? { time, value: v } : { time }
    })
    rsiSeries.setData(rsiData)

    // RSI 70/30 reference lines
    const [overboughtSeries, oversoldSeries] = rsiOverlaySeriesRef.current
    if (overboughtSeries && oversoldSeries && rsiData.length >= 2) {
      const firstTime = rsiData[0].time
      const lastTime = rsiData[rsiData.length - 1].time
      overboughtSeries.setData([
        { time: firstTime, value: 70 },
        { time: lastTime, value: 70 },
      ])
      oversoldSeries.setData([
        { time: firstTime, value: 30 },
        { time: lastTime, value: 30 },
      ])
    }

    // RSI divergence lines
    for (const s of rsiDivSeriesRef.current) {
      rsiChart.removeSeries(s)
    }
    rsiDivSeriesRef.current = []

    const divergences = detectDivergences(closes, rsiValues)
    for (const div of divergences) {
      const color = div.type === 'bullish' ? RSI_DIV_BULLISH_COLOR : RSI_DIV_BEARISH_COLOR
      const divSeries = rsiChart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      divSeries.setData([
        { time: toChartTime(data.candles[div.startIndex].timestamp), value: div.startRSI },
        { time: toChartTime(data.candles[div.endIndex].timestamp), value: div.endRSI },
      ])
      rsiDivSeriesRef.current.push(divSeries)
    }

    for (const overlaySeries of overlaySeriesRef.current) {
      chart.removeSeries(overlaySeries)
    }
    overlaySeriesRef.current = []

    const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
    const firstCandleTime = toChartTime(data.candles[0].timestamp)

    const labels: LineLabel[] = []

    // RSI divergence lines mirrored onto the price structure: a bearish
    // divergence (price HH + RSI LH) connects the two swing highs, a bullish
    // one (price LL + RSI HL) the two swing lows -- the price-side counterpart
    // of the same trendline drawn on the RSI pane above.
    for (const div of showRsiDivergence ? divergences : []) {
      const bearish = div.type === 'bearish'
      const color = bearish ? RSI_DIV_BEARISH_COLOR : RSI_DIV_BULLISH_COLOR
      const startCandle = data.candles[div.startIndex]
      const endCandle = data.candles[div.endIndex]
      const startPrice = bearish ? startCandle.high : startCandle.low
      const endPrice = bearish ? endCandle.high : endCandle.low
      const startTime = toChartTime(startCandle.timestamp)
      const endTime = toChartTime(endCandle.timestamp)

      const divSeries = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      divSeries.setData([
        { time: startTime, value: startPrice },
        { time: endTime, value: endPrice },
      ])
      overlaySeriesRef.current.push(divSeries)
      labels.push({
        time: startTime,
        timeEnd: endTime,
        price: endPrice,
        color,
        text: `RSI Div ${bearish ? '▼' : '▲'}`,
      })
    }

    for (const scored of showEqlZones ? data.ranked_zones.slice(0, TOP_N_ZONES) : []) {
      const { zone, score } = scored
      const color = ZONE_COLORS[zone.zone_type] ?? DEFAULT_ZONE_COLOR
      const label = ZONE_TYPE_LABELS[zone.zone_type] ?? zone.zone_type
      const title = `${label} (${zone.strength.toFixed(2)}) · ${score.toFixed(0)}`
      const price = (zone.price_high + zone.price_low) / 2
      const startTime = toChartTime(zone.formed_at)

      const zoneSeries = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      zoneSeries.setData(lineFrom(startTime, lastCandleTime, price, firstCandleTime))
      overlaySeriesRef.current.push(zoneSeries)
      labels.push({ time: startTime, price, color, text: title })
    }

    // Swept (mitigated) zones
    if (showSweptZones && data.timeframe !== '5m') {
      const SWEPT_TTL_CANDLES = 200
      const MAX_SWEPT_ZONES = 20
      const ttlCutoff =
        data.candles.length >= SWEPT_TTL_CANDLES
          ? toChartTime(data.candles[data.candles.length - SWEPT_TTL_CANDLES].timestamp)
          : toChartTime(data.candles[0].timestamp)
      const mitigatedZones = data.liquidity_zones
        .filter(
          (z) =>
            z.is_mitigated &&
            (z.zone_type === 'equal_highs' || z.zone_type === 'equal_lows') &&
            z.invalidated_at != null &&
            toChartTime(z.invalidated_at) >= ttlCutoff,
        )
        .sort((a, b) => Date.parse(b.invalidated_at!) - Date.parse(a.invalidated_at!))
        .slice(0, MAX_SWEPT_ZONES)
      for (const zone of mitigatedZones) {
        const color = ZONE_COLORS[zone.zone_type] ?? DEFAULT_ZONE_COLOR
        const label = ZONE_TYPE_LABELS[zone.zone_type] ?? zone.zone_type
        const price = (zone.price_high + zone.price_low) / 2
        const startTime = toChartTime(zone.formed_at)
        const endTime = zone.invalidated_at ? toChartTime(zone.invalidated_at) : lastCandleTime

        const sweptSeries = chart.addSeries(LineSeries, {
          color: color + '4d',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        sweptSeries.setData(lineFrom(startTime, endTime, price, firstCandleTime))
        overlaySeriesRef.current.push(sweptSeries)
        labels.push({
          time: startTime,
          price,
          color: color + '66',
          text: `${label} (swept)`,
        })
      }
    }

    // Structure events: all timeframes render the internal-structure detector,
    // with liquidity sweeps capped to the most recent few so the chart stays
    // readable.
    const scopeEvents = data.internal_structure_events

    const recentSweeps = new Set(
      scopeEvents
        .filter((e) => e.event === 'liquidity_sweep')
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
        .slice(0, MAX_INTERNAL_SWEEPS),
    )

    const structureEvents = scopeEvents.filter(
      (event) =>
        event.event in STRUCTURE_EVENT_STYLES &&
        (event.event !== 'liquidity_sweep' || (showSweeps && recentSweeps.has(event))),
    )

    // The event that flipped this timeframe counter to its higher timeframe —
    // the liquidity hunt's window start. Its label gets a ⚠ suffix: the
    // entrants of that break are the resting liquidity being hunted. Only the
    // *standing* flip is marked; historical events would need the HTF trend as
    // of their own time, which a snapshot does not carry.
    const huntFlipTimestamp =
      data.liquidity_hunt && data.liquidity_hunt.phase !== 'none'
        ? data.liquidity_hunt.counter_structure_timestamp
        : null

    // OI qualification per structure event (keyed by timestamp + type), so
    // each BOS/CHoCH/SWEEP label can carry its participation suffix.
    const oiSuffixByEvent = new Map<string, string>()
    for (const qualified of data.oi_analysis?.qualified_events ?? []) {
      const suffix = OI_PARTICIPATION_SUFFIX[qualified.participation]
      if (suffix) {
        oiSuffixByEvent.set(`${qualified.event_timestamp}|${qualified.event_type}`, suffix)
      }
    }

    // Structure-confluence badge per event (keyed by timestamp + type): how
    // many orthogonal reads (VSA / OB / OI / volume delta / sweep) confirm the
    // break. Shown as `✦N` for 2+ confirming factors — a single factor is too
    // weak to flag.
    const confluenceByEvent = new Map<string, number>()
    for (const conf of data.structure_confluence ?? []) {
      if (conf.factors.length >= 2) {
        confluenceByEvent.set(`${conf.event_timestamp}|${conf.event_type}`, conf.factors.length)
      }
    }

    for (const event of structureEvents) {
      // A CHoCH that later failed is represented solely by its `CHoCH ✕`
      // marker (which spans the same origin->failure lifetime). Drawing the
      // original CHoCH line too would plot two overlapping CHoCHs, so skip it —
      // the failure mark replaces it. (Fizzle markers are excluded from
      // `isFailedChoch`, so a fizzled CHoCH still renders normally.)
      if (event.event === 'change_of_character' && isFailedChoch(event, scopeEvents)) {
        continue
      }
      const style = STRUCTURE_EVENT_STYLES[event.event]
      const oiSuffix = oiSuffixByEvent.get(`${event.timestamp}|${event.event}`)
      const confluenceCount = confluenceByEvent.get(`${event.timestamp}|${event.event}`)
      // BOS/CHoCH are colored by direction (green bullish, red bearish), so
      // their labels drop the ▲/▼ arrow — the color already says it. Neutral
      // events (Sweep, CHoCH ✕) keep their own color and the arrow.
      const directionColored =
        event.event === 'break_of_structure' || event.event === 'change_of_character'
      const baseColor =
        (directionColored ? STRUCTURE_DIRECTION_COLORS[event.direction] : undefined) ??
        style.color
      const directionIcon = directionColored ? '' : (TREND_ICONS[event.direction] ?? '')
      const startTime = toChartTime(event.timestamp)
      const linePrice =
        (event.event === 'change_of_character' ||
          event.event === 'choch_failed' ||
          event.event === 'break_of_structure') &&
        event.reference_price_level != null
          ? event.reference_price_level
          : event.price_level

      // A failed CHoCH is a point-in-time invalidation, not a live reference
      // level: its line spans only the CHoCH's own lifetime (the broken level's
      // origin -> the failure candle) and never runs forward into later price
      // action the way a BOS/CHoCH reference line does.
      const endTime =
        event.event === 'choch_failed'
          ? startTime
          : structureLineEndTime(event, scopeEvents, lastCandleTime)

      const lineStartTime =
        (event.event === 'change_of_character' ||
          event.event === 'break_of_structure' ||
          event.event === 'choch_failed') &&
        event.reference_timestamp != null
          ? toChartTime(event.reference_timestamp)
          : startTime

      // A CHoCH that broke a *weak* reference (a re-anchor/fallback level or a
      // wick-only-break promotion -- the ones the new-cycle persistence barrier
      // governs) renders dotted and dimmed with a `*` label suffix, so a
      // conservative-sequence CHoCH (structural leg origin) is tellable at a
      // glance.
      const weakChoch =
        event.event === 'change_of_character' && event.reference_structural === false
      // A provisional BOS is a live-edge continuation whose floor already
      // closed-broke but whose confirming swing pivots have not formed yet.
      // Same dimmed/dotted treatment as a weak CHoCH, with a `?` suffix
      // (`BOS? ▼`): it is superseded by the confirmed BOS once pivots form, or
      // vanishes if the trend flips first.
      const provisionalBos =
        event.event === 'break_of_structure' && event.provisional === true
      // A provisional CHoCH is the mirror for a live-edge *reversal*: a
      // structural CHoCH reference has been sustained-closed-broken but its
      // confirming swing pivot has not formed yet. Same dimmed/dotted treatment
      // with a `?` suffix (`CHoCH? ▼`): superseded by the confirmed CHoCH once
      // the pivot forms, or it vanishes if price reclaims the level (a sweep).
      const provisionalChoch =
        event.event === 'change_of_character' && event.provisional === true
      // A fizzle marker (provisional `choch_failed`) never replaces its
      // CHoCH's line -- the fizzled CHoCH still renders normally and its own
      // line already stops at the reclaim -- so drawing the marker's line
      // would trace the exact same segment twice. Label only, anchored at
      // the reclaim candle.
      const fizzleMarker = event.event === 'choch_failed' && event.provisional === true
      // A re-fired (re-activated) CHoCH: its re-arm reference carries the
      // failure's own timestamp, so a prior same-direction real `CHoCH ✕`
      // sitting exactly at `reference_timestamp` identifies it. Rendered with
      // a `↻` suffix so a re-activation is tellable from a fresh CHoCH.
      const reactivatedChoch =
        event.event === 'change_of_character' &&
        event.provisional !== true &&
        event.reference_timestamp != null &&
        scopeEvents.some(
          (other) =>
            other.scope === event.scope &&
            other.event === 'choch_failed' &&
            other.provisional !== true &&
            other.direction === event.direction &&
            other.timestamp === event.reference_timestamp,
        )
      const dimmed = weakChoch || provisionalBos || provisionalChoch
      const lineColor = dimmed ? `${baseColor}99` : baseColor
      // A provisional mark against a weak reference (emit_provisional_choch_weak)
      // is both forming and weak: `?` (the stronger caveat -- it may repaint
      // entirely) leads, with `*` appended (`CHoCH?* ▲`).
      const labelSuffix =
        provisionalBos || provisionalChoch
          ? weakChoch
            ? '?*'
            : '?'
          : weakChoch
            ? '*'
            : ''
      const counterHtfFlip =
        huntFlipTimestamp != null &&
        event.timestamp === huntFlipTimestamp &&
        !event.provisional &&
        (event.event === 'change_of_character' ||
          event.event === 'break_of_structure' ||
          event.event === 'choch_failed')

      if (!fizzleMarker) {
        const structureSeries = chart.addSeries(LineSeries, {
          color: lineColor,
          lineWidth: 1,
          lineStyle: dimmed ? LineStyle.SparseDotted : LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        structureSeries.setData(lineFrom(lineStartTime, endTime, linePrice, firstCandleTime))
        overlaySeriesRef.current.push(structureSeries)
      }

      // Centered on the line segment (TradingView-style): the break candle
      // sits at one end of the line, where the label would be buried in the
      // candles -- the middle of the drawn segment is the open gap. A
      // line-less fizzle marker anchors at the reclaim candle instead.
      // TradingView-style placement: bullish labels sit above their line,
      // bearish ones below, so the label always hangs on the side price broke
      // *from* and stays out of the move that followed.
      labels.push({
        time: fizzleMarker ? startTime : lineStartTime,
        timeEnd: fizzleMarker ? startTime : endTime,
        price: linePrice,
        color: lineColor,
        below: event.direction === 'bearish',
        text: `${style.label}${labelSuffix}${reactivatedChoch ? ' ↻' : ''}${directionIcon ? ` ${directionIcon}` : ''}${oiSuffix ? ` ${oiSuffix}` : ''}${counterHtfFlip ? ' ⚠' : ''}${confluenceCount ? ` ✦${confluenceCount}` : ''}`,
      })
    }

    // POI order block zones (MSB-anchored; box starts at the OB candle)
    {
      const visiblePriceRange =
        Math.max(...data.candles.map((c) => c.high)) -
        Math.min(...data.candles.map((c) => c.low))
      const poiBoxes: POIBox[] = []
      for (const zone of showOrderBlocks
        ? selectVisiblePoiZones(data.poi_zones ?? [], data.current_price, visiblePriceRange)
        : []) {
        const style = POI_BOX_STYLES[zone.direction] ?? POI_BOX_STYLES.bearish
        const endTime = poiBoxEndTime(zone, lastCandleTime)
        // No direction arrow: the box color already encodes it.
        const kindLabel = POI_KIND_LABELS[zone.kind] ?? 'OB'

        poiBoxes.push({
          x0: toChartTime(zone.ob_candle_timestamp),
          x1: endTime,
          priceLow: zone.price_low,
          priceHigh: zone.price_high,
          borderColor: style.border,
          fillColor: style.fill,
          label: kindLabel,
        })
      }
      poiBoxesPrimitiveRef.current?.setBoxes(poiBoxes)
    }

    // Manipulation cycle accumulation boxes
    const manipBoxes = showManipulationBoxes
      ? buildManipulationBoxes(data.manipulation_cycles ?? [], lastCandleTime)
      : []
    manipBoxesPrimitiveRef.current?.setBoxes(manipBoxes)

    // Consolidation (lateral range) boxes: the stretches where the structure
    // detector was correctly silent, made explicit. A live (unresolved) range
    // extends to the right edge via the far-future sentinel clamp.
    const rangeBoxes: POIBox[] = []
    for (const range of showConsolidationRanges ? (data.consolidation_ranges ?? []) : []) {
      const style = CONSOLIDATION_BOX_STYLES[range.status] ?? CONSOLIDATION_BOX_STYLES.active
      // Label is just the resolution arrow (nothing while the range is live):
      // the box itself already reads as "lateral", the RANGE text was noise.
      const resolvedIcon =
        range.resolved_direction != null ? (TREND_ICONS[range.resolved_direction] ?? '') : ''
      rangeBoxes.push({
        x0: toChartTime(range.start_timestamp),
        x1: range.end_timestamp
          ? toChartTime(range.end_timestamp)
          : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        priceLow: range.price_low,
        priceHigh: range.price_high,
        borderColor: style.border,
        fillColor: style.fill,
        label: resolvedIcon,
      })
    }
    rangeBoxesPrimitiveRef.current?.setBoxes(rangeBoxes)

    // Behavior divergence + VSA markers share one marker plugin (a series
    // holds a single marker set), merged and re-sorted ascending by time.
    const divMarkers = showDivergenceMarkers
      ? buildDivergenceMarkers(data.behavior_divergences ?? [])
      : []
    const vsaMarkers = showVsaMarkers
      ? buildVsaMarkers(data.volume_spread_signals ?? [])
      : []
    const mergedMarkers = [...divMarkers, ...vsaMarkers].sort(
      (a, b) => (a.time as number) - (b.time as number),
    )
    divergenceMarkersRef.current?.setMarkers(mergedMarkers)

    // Liquidity heatmap strip
    const heatmapBands: HeatmapBand[] =
      showHeatmap && data.liquidity_heatmap
        ? data.liquidity_heatmap.buckets.map((bucket) => ({
            priceLow: bucket.price_low,
            priceHigh: bucket.price_high,
            heat: bucket.heat,
          }))
        : []
    heatmapPrimitiveRef.current?.setBands(heatmapBands)

    // Leverage liquidation bands (time-bounded: entry formation -> liq hit).
    // Declutter to the relevant subset near current price (full set stays in
    // the API for the backtest).
    const liquidationBands: LiquidationBandInput[] =
      showLiquidationBands && data.liquidation_map
        ? selectVisibleLiquidationBands(
            data.liquidation_map.bands,
            data.current_price,
            liquidationLiveOnly,
          ).map((band) => ({
            x0: toChartTime(band.start_time) as Time,
            x1: (band.end_time
              ? toChartTime(band.end_time)
              : ((lastCandleTime + 9_999_999) as UTCTimestamp)) as Time,
            priceLow: band.price_low,
            priceHigh: band.price_high,
            intensity: band.intensity,
            leverage: band.leverage,
            hit: band.end_time !== null,
          }))
        : []
    liquidationBandsPrimitiveRef.current?.setBands(liquidationBands)

    // Liquidity-hunt window: full-height shading from the counter-trend flip
    // to the capture that concluded the hunt (right edge while still running).
    // Amber while the counter-trend entrants are still being consumed, green
    // once the mapped pools were captured and OI stopped unwinding.
    const hunt = data.liquidity_hunt
    const huntWindows: HuntWindow[] = []
    const history = data.liquidity_hunt_history ?? []
    if (showHuntWindow) {
      // Concluded hunts earlier in the window: dim green shaded bands with a ✓,
      // each ending at the liquidity grab that closed it (short, near-term).
      for (const episode of history) {
        const sideWord = episode.hunted_side === 'short' ? 'shorts' : 'longs'
        // Exhaustion grab (stops run on no new money at the grab candle — CVD×OI)
        // is reversal-prone: purple with a ⚠; a genuine break stays green ✓. What
        // closed the hunt (sources + score) stays in the hover title.
        const exhaustion = episode.capture_quality === 'exhaustion_grab'
        const color = exhaustion ? '#ab47bc' : '#26a69a'
        huntWindows.push({
          x0: toChartTime(episode.start_timestamp),
          x1: toChartTime(episode.end_timestamp),
          color,
          fillColor: color + '0d',
          label: exhaustion ? `⚠ ${sideWord} hunted (exhaustion)` : `✓ ${sideWord} hunted`,
        })
      }
    }
    if (showHuntWindow && hunt && hunt.phase !== 'none' && hunt.counter_structure_timestamp) {
      const captured = hunt.phase === 'captured'
      // An exhaustion-grab capture (stops run on no new money — CVD×OI) is
      // reversal-prone: shade it purple with a distinct label instead of the
      // green "cleared" of a genuine break.
      const exhaustion = captured && hunt.capture_quality === 'exhaustion_grab'
      const color = exhaustion ? '#ab47bc' : captured ? '#26a69a' : '#ff9800'
      const sideWord = hunt.hunted_side === 'short' ? 'shorts' : 'longs'
      // The live window is the *pending* grab only: start it at the last grab
      // already captured in this leg (the latest history episode ending at or
      // after the flip), not the original flip — so it stays near-term and
      // doesn't overlap the green completed hunts.
      const flip = hunt.counter_structure_timestamp
      const lastGrab = history
        .filter((e) => e.end_timestamp >= flip)
        .reduce<string | null>(
          (acc, e) => (acc === null || e.end_timestamp > acc ? e.end_timestamp : acc),
          null,
        )
      huntWindows.push({
        x0: toChartTime(lastGrab ?? flip),
        x1:
          captured && hunt.captured_at
            ? toChartTime(hunt.captured_at)
            : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        color,
        fillColor: color + '0d',
        label: exhaustion
          ? `⚠ ${sideWord} captured (exhaustion)`
          : captured
            ? `✓ ${sideWord} captured`
            : `⚡ hunting ${sideWord}`,
      })
    }
    // Aligned trend-continuation grabs: a separate regime (a leg with the HTF
    // that pulled back, swept internal liquidity, then resumed). Drawn in blue
    // and toggled independently so it never blends with the counter-trend hunt.
    if (showContinuationWindow) {
      const continuation = data.liquidity_continuation_history ?? []
      for (const episode of continuation) {
        const arrow = episode.correction_direction === 'bullish' ? '↗' : '↘'
        const dirWord =
          episode.correction_direction === 'bullish' ? 'bull' : 'bear'
        huntWindows.push({
          x0: toChartTime(episode.start_timestamp),
          x1: toChartTime(episode.end_timestamp),
          color: '#42a5f5',
          fillColor: '#42a5f50d',
          label: `${arrow} ${dirWord} continuation`,
        })
      }
    }
    huntWindowPrimitiveRef.current?.setWindows(huntWindows)

    labelsPrimitiveRef.current?.setLabels(labels)
    // Feed the candles' wick extents to the labels primitive so segment
    // labels (BOS/CHoCH/…) can slide along their line to a candle-free spot.
    const labelCandles = data.candles.map((c) => ({
      time: toChartTime(c.timestamp) as Time,
      high: c.high,
      low: c.low,
    }))
    labelsPrimitiveRef.current?.setCandles(labelCandles)
    // Box labels (OB/MB, accumulation, range) dodge candles the same way.
    poiBoxesPrimitiveRef.current?.setCandles(labelCandles)
    manipBoxesPrimitiveRef.current?.setCandles(labelCandles)
    rangeBoxesPrimitiveRef.current?.setCandles(labelCandles)

    if (!hasFittedRef.current) {
      chart.timeScale().fitContent()
      deltaChart.timeScale().fitContent()
      controlChartRef.current?.timeScale().fitContent()
      rsiChart.timeScale().fitContent()
      hasFittedRef.current = true
    }

  }, [data, showConsolidationRanges, showManipulationBoxes, showDivergenceMarkers, showVsaMarkers, showHeatmap, showLiquidationBands, liquidationLiveOnly, showSweptZones, showOrderBlocks, showSweeps, showEqlZones, showHuntWindow, showContinuationWindow, showVolume, showRsiDivergence])

  return (
    <div ref={wrapperRef} className="flex min-h-0 w-full flex-1 flex-col">
      <div ref={mainContainerRef} className="w-full" />
      <div className={`relative w-full border-t border-[#1e222d] ${showIndicators ? '' : 'hidden'}`}>
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Volume Delta
        </span>
        <div ref={deltaContainerRef} className="w-full" />
      </div>
      <div
        className={`relative w-full border-t border-[#1e222d] ${
          showControlOscillator ? '' : 'hidden'
        }`}
      >
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Control (CVD×OI)
        </span>
        <div ref={controlContainerRef} className="w-full" />
      </div>
      <div className={`relative w-full border-t border-[#1e222d] ${showIndicators ? '' : 'hidden'}`}>
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          RSI ({RSI_PERIOD})
        </span>
        <div ref={rsiContainerRef} className="w-full" />
      </div>
    </div>
  )
}
