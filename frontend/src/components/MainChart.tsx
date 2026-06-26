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
import { POIBoxesPrimitive, type POIBox } from '../charting/POIBoxesPrimitive'
import { HeatmapStripPrimitive, type HeatmapBand } from '../charting/HeatmapStripPrimitive'
import {
  LiquidationBandsPrimitive,
  type LiquidationBandInput,
} from '../charting/LiquidationBandsPrimitive'
import type { BehaviorDivergence, DashboardData, LiquidationBand, ManipulationCycle, MarketStructure, POIZone } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
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
  RTO_COLORS,
  STRUCTURE_EVENT_STYLES,
  TREND_ICONS,
  ZONE_COLORS,
  ZONE_TYPE_LABELS,
} from '../theme'

const TOP_N_ZONES = 5
const MAX_INTERNAL_SWEEPS = 3

const MAIN_CHART_RATIO = 0.68
const DELTA_CHART_RATIO = 0.16
const MIN_TOTAL_HEIGHT = 500
const PRICE_SCALE_MIN_WIDTH = 110

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
// its *own* line stops at this failure point.
function failedChochTime(
  choch: MarketStructure,
  allEvents: MarketStructure[],
): UTCTimestamp | null {
  if (choch.event !== 'change_of_character') return null
  const chochTime = toUtcTimestamp(choch.timestamp)
  const failedTimes = allEvents
    .filter(
      (e) =>
        e.scope === choch.scope &&
        e.event === 'choch_failed' &&
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
  return failedChochTime(choch, allEvents) !== null
}

function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toUtcTimestamp(event.timestamp)

  if (event.event === 'change_of_character') {
    const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
    const candidates = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.direction === oppositeDirection &&
          other.event === 'change_of_character' &&
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

function poiBoxEndTime(
  zone: POIZone,
  internalEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const zoneTime = toUtcTimestamp(zone.created_at)

  const secondBosTime = internalEvents
    .filter(
      (e) =>
        e.scope === 'internal' &&
        e.event === 'break_of_structure' &&
        e.direction === zone.direction &&
        toUtcTimestamp(e.timestamp) > zoneTime,
    )
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))[1]

  const oppositeDirection = zone.direction === 'bullish' ? 'bearish' : 'bullish'
  const oppositeChoch = internalEvents
    .filter(
      (e) =>
        e.scope === 'internal' &&
        e.event === 'change_of_character' &&
        e.direction === oppositeDirection &&
        toUtcTimestamp(e.timestamp) > zoneTime,
    )
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))[0]

  const candidates: UTCTimestamp[] = []
  if (secondBosTime) candidates.push(toUtcTimestamp(secondBosTime.timestamp))
  if (oppositeChoch) candidates.push(toUtcTimestamp(oppositeChoch.timestamp))

  return candidates.length > 0
    ? (Math.min(...candidates) as UTCTimestamp)
    : ((lastCandleTime + 9_999_999) as UTCTimestamp)
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
  showManipulationBoxes?: boolean
  showDivergenceMarkers?: boolean
  showHeatmap?: boolean
  showLiquidationBands?: boolean
  liquidationLiveOnly?: boolean
  showSweptZones?: boolean
}

export function MainChart({
  data,
  showManipulationBoxes = true,
  showDivergenceMarkers = true,
  showHeatmap = true,
  showLiquidationBands = true,
  liquidationLiveOnly = false,
  showSweptZones = true,
}: MainChartProps) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const mainContainerRef = useRef<HTMLDivElement>(null)
  const deltaContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const deltaChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const deltaSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiOverlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiDivSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const labelsPrimitiveRef = useRef<LineLabelsPrimitive | null>(null)
  const poiBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const manipBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const heatmapPrimitiveRef = useRef<HeatmapStripPrimitive | null>(null)
  const liquidationBandsPrimitiveRef = useRef<LiquidationBandsPrimitive | null>(null)
  const divergenceMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const hasFittedRef = useRef(false)
  const isSyncingRef = useRef(false)

  useEffect(() => {
    const wrapper = wrapperRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const rsiContainer = rsiContainerRef.current
    if (!wrapper || !mainContainer || !deltaContainer || !rsiContainer) return

    const totalHeight = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const mainHeight = Math.round(totalHeight * MAIN_CHART_RATIO)
    const deltaHeight = Math.round(totalHeight * DELTA_CHART_RATIO)
    const rsiHeight = totalHeight - mainHeight - deltaHeight

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
      timeScale: { ...chartOptions.timeScale, visible: false },
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
    series.attachPrimitive(labelsPrimitive)
    labelsPrimitiveRef.current = labelsPrimitive

    const poiBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(poiBoxesPrimitive)
    poiBoxesPrimitiveRef.current = poiBoxesPrimitive

    const manipBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(manipBoxesPrimitive)
    manipBoxesPrimitiveRef.current = manipBoxesPrimitive

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
      const mh = Math.round(h * MAIN_CHART_RATIO)
      const dh = Math.round(h * DELTA_CHART_RATIO)
      const rh = h - mh - dh
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
      deltaSeriesRef.current = null
      rsiSeriesRef.current = null
      overlaySeriesRef.current = []
      rsiOverlaySeriesRef.current = []
      rsiDivSeriesRef.current = []
      labelsPrimitiveRef.current = null
      poiBoxesPrimitiveRef.current = null
      manipBoxesPrimitiveRef.current = null
      heatmapPrimitiveRef.current = null
      liquidationBandsPrimitiveRef.current = null
      divergenceMarkersRef.current = null
      hasFittedRef.current = false
    }
  }, [])

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

    for (const scored of data.ranked_zones.slice(0, TOP_N_ZONES)) {
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
        (event.event !== 'liquidity_sweep' || recentSweeps.has(event)),
    )

    for (const event of structureEvents) {
      const style = STRUCTURE_EVENT_STYLES[event.event]
      const directionIcon = TREND_ICONS[event.direction] ?? ''
      const startTime = toUtcTimestamp(event.timestamp)
      const endTime = structureLineEndTime(event, scopeEvents, lastCandleTime)

      const linePrice =
        (event.event === 'change_of_character' || event.event === 'choch_failed') &&
        event.reference_price_level != null
          ? event.reference_price_level
          : event.price_level

      const lineStartTime =
        event.event === 'change_of_character' && event.reference_timestamp != null
          ? toUtcTimestamp(event.reference_timestamp)
          : startTime

      const structureSeries = chart.addSeries(LineSeries, {
        color: style.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      structureSeries.setData(lineFrom(lineStartTime, endTime, linePrice, firstCandleTime))
      overlaySeriesRef.current.push(structureSeries)

      labels.push({
        time: startTime,
        price: linePrice,
        color: style.color,
        text: `${style.label} ${directionIcon}`,
      })
    }

    // POI order block zones and RTO sweeps
    {
      const poiBoxes: POIBox[] = []
      for (const zone of data.poi_zones ?? []) {
        if (zone.status === 'invalidated') continue

        const isMitigated = zone.status === 'mitigated'
        const dirStyle = POI_BOX_STYLES[zone.direction] ?? POI_BOX_STYLES.mitigated
        const style = isMitigated
          ? { border: dirStyle.border + 'aa', fill: dirStyle.border + '18' }
          : dirStyle
        const endTime = poiBoxEndTime(zone, data.internal_structure_events, lastCandleTime)
        const dirIcon = zone.direction === 'bullish' ? '▲' : '▼'

        poiBoxes.push({
          x0: toUtcTimestamp(zone.created_at),
          x1: endTime,
          priceLow: zone.price_low,
          priceHigh: zone.price_high,
          borderColor: style.border,
          fillColor: style.fill,
          label: `OB ${dirIcon}${isMitigated ? ' ✓' : ''}`,
        })
      }
      poiBoxesPrimitiveRef.current?.setBoxes(poiBoxes)

      for (const rto of data.poi_sweep_events ?? []) {
        const color = RTO_COLORS[rto.direction] ?? '#888888'
        const midPrice = (rto.zone_price_low + rto.zone_price_high) / 2
        labels.push({
          time: toUtcTimestamp(rto.timestamp),
          price: midPrice,
          color,
          text: `RTO ${rto.direction === 'bullish' ? '▲' : '▼'}`,
        })
      }
    }

    // Manipulation cycle accumulation boxes
    const manipBoxes = showManipulationBoxes
      ? buildManipulationBoxes(data.manipulation_cycles ?? [], lastCandleTime)
      : []
    manipBoxesPrimitiveRef.current?.setBoxes(manipBoxes)

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

    labelsPrimitiveRef.current?.setLabels(labels)

    if (!hasFittedRef.current) {
      chart.timeScale().fitContent()
      deltaChart.timeScale().fitContent()
      rsiChart.timeScale().fitContent()
      hasFittedRef.current = true
    }

  }, [data, showManipulationBoxes, showDivergenceMarkers, showHeatmap, showLiquidationBands, liquidationLiveOnly, showSweptZones])

  return (
    <div ref={wrapperRef} className="flex min-h-0 w-full flex-1 flex-col">
      <div ref={mainContainerRef} className="w-full" />
      <div className="relative w-full border-t border-[#1e222d]">
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Volume Delta
        </span>
        <div ref={deltaContainerRef} className="w-full" />
      </div>
      <div className="relative w-full border-t border-[#1e222d]">
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          RSI ({RSI_PERIOD})
        </span>
        <div ref={rsiContainerRef} className="w-full" />
      </div>
    </div>
  )
}
