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
import type { BehaviorDivergence, DashboardData, LiquidationBand, ManipulationCycle, MarketStructure, OIParticipation, POIZone } from '../types/dashboard'
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
  STRUCTURE_EVENT_STYLES,
  TREND_ICONS,
  VOLUME_DOWN_COLOR,
  VOLUME_UP_COLOR,
  ZONE_COLORS,
  ZONE_TYPE_LABELS,
} from '../theme'

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

const MAIN_CHART_RATIO = 0.68
const DELTA_CHART_RATIO = 0.16
const MIN_TOTAL_HEIGHT = 500
const PRICE_SCALE_MIN_WIDTH = 110

// Split the available height across the three panes. When the indicator panes
// (volume delta + RSI) are minimized, the main candlestick pane takes the whole
// height and the others collapse to 0.
function paneHeights(totalHeight: number, showIndicators: boolean) {
  if (!showIndicators) {
    return { mainHeight: totalHeight, deltaHeight: 0, rsiHeight: 0 }
  }
  const mainHeight = Math.round(totalHeight * MAIN_CHART_RATIO)
  const deltaHeight = Math.round(totalHeight * DELTA_CHART_RATIO)
  const rsiHeight = totalHeight - mainHeight - deltaHeight
  return { mainHeight, deltaHeight, rsiHeight }
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

function toUtcTimestamp(isoTimestamp: string): UTCTimestamp {
  return (Date.parse(isoTimestamp) / 1000) as UTCTimestamp
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
  const chochTime = toUtcTimestamp(choch.timestamp)
  const failedTimes = allEvents
    .filter(
      (e) =>
        e.scope === choch.scope &&
        e.event === 'choch_failed' &&
        (includeFizzle || !e.provisional) &&
        e.direction === choch.direction &&
        toUtcTimestamp(e.timestamp) > chochTime,
    )
    .map((e) => toUtcTimestamp(e.timestamp))
  if (failedTimes.length === 0) return null
  const firstFailed = Math.min(...failedTimes) as UTCTimestamp
  // Pair the failure with its CHoCH: ignore it if a later same-direction CHoCH
  // sits between them (that one owns the failure instead).
  const interveningChoch = allEvents.some(
    (e) =>
      e.scope === choch.scope &&
      e.event === 'change_of_character' &&
      e.direction === choch.direction &&
      toUtcTimestamp(e.timestamp) > chochTime &&
      toUtcTimestamp(e.timestamp) < firstFailed,
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
  const eventTime = toUtcTimestamp(event.timestamp)

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
          toUtcTimestamp(other.timestamp) > eventTime,
      )
      .map((other) => toUtcTimestamp(other.timestamp))
    // If this CHoCH itself failed, its line stops at the failure point.
    const ownFailure = failedChochTime(event, allEvents)
    if (ownFailure !== null) candidates.push(ownFailure)
    return candidates.length > 0 ? (Math.min(...candidates) as UTCTimestamp) : lastCandleTime
  }

  const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        !other.provisional &&
        toUtcTimestamp(other.timestamp) > eventTime &&
        ((other.direction === event.direction &&
          (other.event === 'break_of_structure' ||
            (other.event === 'change_of_character' && !isFailedChoch(other, allEvents)))) ||
          (other.direction === oppositeDirection &&
            other.event === 'change_of_character' &&
            !isFailedChoch(other, allEvents))),
    )
    .map((other) => toUtcTimestamp(other.timestamp))

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}

// A POI box spans the zone's real lifecycle: it stays open (full width) while
// the zone is ACTIVE — an armed order block price may still return to — and
// closes at the candle whose close broke through it (`invalidated_at`).
// Price touching inside the zone does not retire it.
function poiBoxEndTime(zone: POIZone, lastCandleTime: UTCTimestamp): UTCTimestamp {
  return zone.invalidated_at
    ? toUtcTimestamp(zone.invalidated_at)
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
        time: toUtcTimestamp(div.timestamp) as Time,
        position: markerStyle.position,
        shape: markerStyle.shape,
        color: style?.color ?? '#888888',
        text: `${style?.label ?? div.divergence_type} ${dirIcon}`,
        size: 1.5,
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

    const x0 = toUtcTimestamp(cycle.accumulation_start)
    const x1 = cycle.sweep_timestamp
      ? toUtcTimestamp(cycle.sweep_timestamp)
      : cycle.phase === 'accumulation'
        ? ((lastCandleTime + 9_999_999) as UTCTimestamp)
        : toUtcTimestamp(cycle.accumulation_end)

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
  showHeatmap?: boolean
  showLiquidationBands?: boolean
  liquidationLiveOnly?: boolean
  showSweptZones?: boolean
  showOrderBlocks?: boolean
  showSweeps?: boolean
  showEqlZones?: boolean
  showIndicators?: boolean
  showHuntWindow?: boolean
  showVolume?: boolean
  showRsiDivergence?: boolean
}

export function MainChart({
  data,
  showConsolidationRanges = true,
  showManipulationBoxes = true,
  showDivergenceMarkers = true,
  showHeatmap = true,
  showLiquidationBands = true,
  liquidationLiveOnly = false,
  showSweptZones = true,
  showOrderBlocks = true,
  showSweeps = true,
  showEqlZones = true,
  showIndicators = true,
  showHuntWindow = false,
  showVolume = true,
  showRsiDivergence = false,
}: MainChartProps) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const mainContainerRef = useRef<HTMLDivElement>(null)
  const deltaContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const deltaChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const deltaSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
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

  useEffect(() => {
    const wrapper = wrapperRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const rsiContainer = rsiContainerRef.current
    if (!wrapper || !mainContainer || !deltaContainer || !rsiContainer) return

    const totalHeight = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const indicatorsOpen = showIndicatorsRef.current
    const { mainHeight, deltaHeight, rsiHeight } = paneHeights(totalHeight, indicatorsOpen)

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
      // The RSI pane normally carries the visible time axis; when the indicator
      // panes are minimized the main pane shows it instead.
      timeScale: { ...chartOptions.timeScale, visible: !indicatorsOpen },
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

    const rsiChart = createChart(rsiContainer, {
      ...chartOptions,
      width: rsiContainer.clientWidth,
      height: rsiHeight,
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
    // When the indicator panes are open the main pane hides its time axis
    // (the RSI pane carries it), which nulls the main chart's time-scale
    // coordinate API. The RSI chart shares the synced, equal-width time scale,
    // so the labels primitive falls back to it for time -> x in that state.
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

    // Sync time scales across all three charts
    const charts = [chart, deltaChart, rsiChart]
    for (const src of charts) {
      src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (isSyncingRef.current || !range) return
        isSyncingRef.current = true
        for (const dst of charts) {
          if (dst !== src) dst.timeScale().setVisibleLogicalRange(range)
        }
        isSyncingRef.current = false
      })
    }

    // Sync crosshairs
    chart.subscribeCrosshairMove((param) => {
      if (isSyncingRef.current) return
      isSyncingRef.current = true
      if (param.time) {
        deltaChart.setCrosshairPosition(0, param.time, deltaSeries)
        rsiChart.setCrosshairPosition(NaN, param.time, rsiSeries)
      } else {
        deltaChart.clearCrosshairPosition()
        rsiChart.clearCrosshairPosition()
      }
      isSyncingRef.current = false
    })

    deltaChart.subscribeCrosshairMove((param) => {
      if (isSyncingRef.current) return
      isSyncingRef.current = true
      if (param.time) {
        chart.setCrosshairPosition(NaN, param.time, series)
        rsiChart.setCrosshairPosition(NaN, param.time, rsiSeries)
      } else {
        chart.clearCrosshairPosition()
        rsiChart.clearCrosshairPosition()
      }
      isSyncingRef.current = false
    })

    rsiChart.subscribeCrosshairMove((param) => {
      if (isSyncingRef.current) return
      isSyncingRef.current = true
      if (param.time) {
        chart.setCrosshairPosition(NaN, param.time, series)
        deltaChart.setCrosshairPosition(0, param.time, deltaSeries)
      } else {
        chart.clearCrosshairPosition()
        deltaChart.clearCrosshairPosition()
      }
      isSyncingRef.current = false
    })

    const ro = new ResizeObserver(() => {
      const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
      const { mainHeight: mh, deltaHeight: dh, rsiHeight: rh } = paneHeights(h, showIndicatorsRef.current)
      chart.applyOptions({ width: mainContainer.clientWidth, height: mh })
      deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: dh })
      rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rh })
    })
    ro.observe(wrapper)

    return () => {
      ro.disconnect()
      chart.remove()
      deltaChart.remove()
      rsiChart.remove()
      chartRef.current = null
      deltaChartRef.current = null
      rsiChartRef.current = null
      seriesRef.current = null
      volumeSeriesRef.current = null
      deltaSeriesRef.current = null
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
    const rsiChart = rsiChartRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const rsiContainer = rsiContainerRef.current
    showIndicatorsRef.current = showIndicators
    if (!wrapper || !chart || !deltaChart || !rsiChart || !mainContainer || !deltaContainer || !rsiContainer) return

    const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const { mainHeight, deltaHeight, rsiHeight } = paneHeights(h, showIndicators)

    // While the panes are closed their containers are display:none (zero
    // width), so the delta/RSI charts never track the main chart's time scale
    // (and their one-shot fitContent ran at zero width). Reopening them would
    // otherwise reveal a stale, desynced range -- and resizing them from zero
    // width can echo that bad range back onto the main chart. Suppress the sync
    // feedback across the resize, then drive both panes from the main chart's
    // current range.
    isSyncingRef.current = true
    chart.applyOptions({
      width: mainContainer.clientWidth,
      height: mainHeight,
      timeScale: { visible: !showIndicators },
    })
    deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: deltaHeight })
    rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rsiHeight })

    if (showIndicators) {
      const range = chart.timeScale().getVisibleLogicalRange()
      if (range) {
        deltaChart.timeScale().setVisibleLogicalRange(range)
        rsiChart.timeScale().setVisibleLogicalRange(range)
      }
    }
    // Release the guard after this frame's layout (and any resize-triggered
    // range echo) settles.
    requestAnimationFrame(() => {
      isSyncingRef.current = false
    })
  }, [showIndicators])

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

    series.setData(
      data.candles.map((candle) => ({
        time: toUtcTimestamp(candle.timestamp),
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
              time: toUtcTimestamp(candle.timestamp),
              value: candle.volume,
              color: candle.close >= candle.open ? VOLUME_UP_COLOR : VOLUME_DOWN_COLOR,
            }))
          : [],
      )
    }

    // Volume delta histogram
    deltaSeries.setData(
      data.candles.map((candle) => {
        const delta = 2 * candle.taker_buy_volume - candle.volume
        return {
          time: toUtcTimestamp(candle.timestamp),
          value: delta,
          color: candle.close >= candle.open ? CANDLE_UP_COLOR : CANDLE_DOWN_COLOR,
        }
      }),
    )

    // RSI — include whitespace entries for the bootstrap period so bar indices
    // match the main/delta charts and the logical-range sync stays aligned.
    const closes = data.candles.map((c) => c.close)
    const rsiValues = computeRSI(closes, RSI_PERIOD)
    const rsiData = data.candles.map((candle, i) => {
      const time = toUtcTimestamp(candle.timestamp)
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
        { time: toUtcTimestamp(data.candles[div.startIndex].timestamp), value: div.startRSI },
        { time: toUtcTimestamp(data.candles[div.endIndex].timestamp), value: div.endRSI },
      ])
      rsiDivSeriesRef.current.push(divSeries)
    }

    for (const overlaySeries of overlaySeriesRef.current) {
      chart.removeSeries(overlaySeries)
    }
    overlaySeriesRef.current = []

    const lastCandleTime = toUtcTimestamp(data.candles[data.candles.length - 1].timestamp)
    const firstCandleTime = toUtcTimestamp(data.candles[0].timestamp)

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
      const startTime = toUtcTimestamp(startCandle.timestamp)
      const endTime = toUtcTimestamp(endCandle.timestamp)

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
      const startTime = toUtcTimestamp(zone.formed_at)

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
          ? toUtcTimestamp(data.candles[data.candles.length - SWEPT_TTL_CANDLES].timestamp)
          : toUtcTimestamp(data.candles[0].timestamp)
      const mitigatedZones = data.liquidity_zones
        .filter(
          (z) =>
            z.is_mitigated &&
            (z.zone_type === 'equal_highs' || z.zone_type === 'equal_lows') &&
            z.invalidated_at != null &&
            toUtcTimestamp(z.invalidated_at) >= ttlCutoff,
        )
        .sort((a, b) => Date.parse(b.invalidated_at!) - Date.parse(a.invalidated_at!))
        .slice(0, MAX_SWEPT_ZONES)
      for (const zone of mitigatedZones) {
        const color = ZONE_COLORS[zone.zone_type] ?? DEFAULT_ZONE_COLOR
        const label = ZONE_TYPE_LABELS[zone.zone_type] ?? zone.zone_type
        const price = (zone.price_high + zone.price_low) / 2
        const startTime = toUtcTimestamp(zone.formed_at)
        const endTime = zone.invalidated_at ? toUtcTimestamp(zone.invalidated_at) : lastCandleTime

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
      const directionIcon = TREND_ICONS[event.direction] ?? ''
      const startTime = toUtcTimestamp(event.timestamp)
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
          ? toUtcTimestamp(event.reference_timestamp)
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
      const dimmed = weakChoch || provisionalBos || provisionalChoch
      const lineColor = dimmed ? `${style.color}99` : style.color
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

      // Centered on the line segment (TradingView-style): the break candle
      // sits at one end of the line, where the label would be buried in the
      // candles -- the middle of the drawn segment is the open gap.
      labels.push({
        time: lineStartTime,
        timeEnd: endTime,
        price: linePrice,
        color: lineColor,
        text: `${style.label}${labelSuffix} ${directionIcon}${oiSuffix ? ` ${oiSuffix}` : ''}${counterHtfFlip ? ' ⚠' : ''}`,
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
        const dirIcon = zone.direction === 'bullish' ? '▲' : '▼'
        const kindLabel = POI_KIND_LABELS[zone.kind] ?? 'OB'

        poiBoxes.push({
          x0: toUtcTimestamp(zone.ob_candle_timestamp),
          x1: endTime,
          priceLow: zone.price_low,
          priceHigh: zone.price_high,
          borderColor: style.border,
          fillColor: style.fill,
          label: `${kindLabel} ${dirIcon}`,
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
      const resolvedIcon =
        range.resolved_direction != null ? ` ${TREND_ICONS[range.resolved_direction] ?? ''}` : ''
      rangeBoxes.push({
        x0: toUtcTimestamp(range.start_timestamp),
        x1: range.end_timestamp
          ? toUtcTimestamp(range.end_timestamp)
          : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        priceLow: range.price_low,
        priceHigh: range.price_high,
        borderColor: style.border,
        fillColor: style.fill,
        label: `▭ RANGE${resolvedIcon}`,
      })
    }
    rangeBoxesPrimitiveRef.current?.setBoxes(rangeBoxes)

    // Behavior divergence markers
    const divMarkers = showDivergenceMarkers
      ? buildDivergenceMarkers(data.behavior_divergences ?? [])
      : []
    divergenceMarkersRef.current?.setMarkers(divMarkers)

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
            x0: toUtcTimestamp(band.start_time) as Time,
            x1: (band.end_time
              ? toUtcTimestamp(band.end_time)
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
    if (showHuntWindow && hunt && hunt.phase !== 'none' && hunt.counter_structure_timestamp) {
      const captured = hunt.phase === 'captured'
      const color = captured ? '#26a69a' : '#ff9800'
      const sideWord = hunt.hunted_side === 'short' ? 'shorts' : 'longs'
      huntWindows.push({
        x0: toUtcTimestamp(hunt.counter_structure_timestamp),
        x1:
          captured && hunt.captured_at
            ? toUtcTimestamp(hunt.captured_at)
            : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        color,
        fillColor: color + '0d',
        label: captured ? `✓ ${sideWord} captured` : `⚡ hunting ${sideWord}`,
      })
    }
    huntWindowPrimitiveRef.current?.setWindows(huntWindows)

    labelsPrimitiveRef.current?.setLabels(labels)

    if (!hasFittedRef.current) {
      chart.timeScale().fitContent()
      deltaChart.timeScale().fitContent()
      rsiChart.timeScale().fitContent()
      hasFittedRef.current = true
    }

  }, [data, showConsolidationRanges, showManipulationBoxes, showDivergenceMarkers, showHeatmap, showLiquidationBands, liquidationLiveOnly, showSweptZones, showOrderBlocks, showSweeps, showEqlZones, showHuntWindow, showVolume, showRsiDivergence])

  return (
    <div ref={wrapperRef} className="flex min-h-0 w-full flex-1 flex-col">
      <div ref={mainContainerRef} className="w-full" />
      <div className={`relative w-full border-t border-[#1e222d] ${showIndicators ? '' : 'hidden'}`}>
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Volume Delta
        </span>
        <div ref={deltaContainerRef} className="w-full" />
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
