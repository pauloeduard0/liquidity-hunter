import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'

import { LineLabelsPrimitive, type LineLabel } from '../charting/LineLabelsPrimitive'
import type { DashboardData, MarketStructure } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  DARK_BG,
  DEFAULT_ZONE_COLOR,
  FONT_COLOR,
  GRID_COLOR,
  STRUCTURE_EVENT_STYLES,
  TREND_ICONS,
  ZONE_COLORS,
  ZONE_TYPE_LABELS,
} from '../theme'

/**
 * Plotting every detected zone (there can be dozens of swing points) makes
 * the chart unreadable, so only the highest-ranked zones are overlaid here
 * -- mirroring `dashboard.charts.main_chart`'s `DEFAULT_TOP_N_ZONES`.
 */
const TOP_N_ZONES = 5

/**
 * Internal-scope liquidity sweeps accumulate quickly (every failed pivot
 * break against the trailing reference is one), and since an unsuperseded
 * sweep's line always extends to the latest candle, they pile up near the
 * current price -- only the most recent ones are kept, mirroring
 * `TOP_N_ZONES` for liquidity zones.
 */
const MAX_INTERNAL_SWEEPS = 3

function toUtcTimestamp(isoTimestamp: string): UTCTimestamp {
  return (Date.parse(isoTimestamp) / 1000) as UTCTimestamp
}

function formatPrice(price: number): string {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** A horizontal line from `startTime` to `lastCandleTime` at `value`, collapsing to a single point if `startTime` is at or after `lastCandleTime`. */
function lineFrom(startTime: UTCTimestamp, lastCandleTime: UTCTimestamp, value: number) {
  return startTime < lastCandleTime
    ? [
        { time: startTime, value },
        { time: lastCandleTime, value },
      ]
    : [{ time: lastCandleTime, value }]
}

/** The midpoint in time between `start` and `end`, used to anchor a line's label along its middle. */
function midTime(start: UTCTimestamp, end: UTCTimestamp): UTCTimestamp {
  return ((start + end) / 2) as UTCTimestamp
}

/**
 * Whether `event` reports the same pivot as one in `majorEvents`. The
 * internal-scope detector can re-detect the same swing pivot as the
 * major-scope detector (a major extreme is, by construction, also a local
 * extreme at a smaller lookback), so such duplicates are skipped to avoid
 * rendering the same level twice.
 */
function isDuplicateOfMajor(event: MarketStructure, majorEvents: MarketStructure[]): boolean {
  return majorEvents.some(
    (major) =>
      major.timestamp === event.timestamp &&
      major.event === event.event &&
      major.price_level === event.price_level,
  )
}

/**
 * Where `event`'s line should stop: a BOS/CHoCH/Sweep marks the active
 * level on its `direction` side as of `event.timestamp`. Only a *later*
 * BOS or CHoCH of the same scope and direction moves that active level (a
 * Sweep, by definition, leaves it unchanged), so the line is bounded there
 * -- otherwise it extends to `lastCandleTime` as the current active level.
 */
function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toUtcTimestamp(event.timestamp)
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        other.direction === event.direction &&
        (other.event === 'break_of_structure' || other.event === 'change_of_character') &&
        toUtcTimestamp(other.timestamp) > eventTime,
    )
    .map((other) => toUtcTimestamp(other.timestamp))

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}

interface MainChartProps {
  data: DashboardData
}

/** Primary chart: candlesticks with the top-ranked liquidity zones and market structure events. */
export function MainChart({ data }: MainChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const labelsPrimitiveRef = useRef<LineLabelsPrimitive | null>(null)
  const hasFittedRef = useRef(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: DARK_BG },
        textColor: FONT_COLOR,
      },
      grid: {
        vertLines: { color: GRID_COLOR },
        horzLines: { color: GRID_COLOR },
      },
      width: container.clientWidth,
      height: 550,
      timeScale: { timeVisible: true, secondsVisible: false },
    })
    chartRef.current = chart

    const series = chart.addSeries(CandlestickSeries, {
      upColor: CANDLE_UP_COLOR,
      downColor: CANDLE_DOWN_COLOR,
      borderVisible: false,
      wickUpColor: CANDLE_UP_COLOR,
      wickDownColor: CANDLE_DOWN_COLOR,
    })
    seriesRef.current = series

    const labelsPrimitive = new LineLabelsPrimitive()
    series.attachPrimitive(labelsPrimitive)
    labelsPrimitiveRef.current = labelsPrimitive

    const handleResize = () => chart.applyOptions({ width: container.clientWidth })
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      overlaySeriesRef.current = []
      labelsPrimitiveRef.current = null
      hasFittedRef.current = false
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series || data.candles.length === 0) return

    series.setData(
      data.candles.map((candle) => ({
        time: toUtcTimestamp(candle.timestamp),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    )

    for (const overlaySeries of overlaySeriesRef.current) {
      chart.removeSeries(overlaySeries)
    }
    overlaySeriesRef.current = []

    const lastCandleTime = toUtcTimestamp(data.candles[data.candles.length - 1].timestamp)

    // Labels are drawn on the chart pane itself, anchored to each line's own
    // position, instead of stacking as titles on the right price axis.
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
      zoneSeries.setData(lineFrom(startTime, lastCandleTime, price))
      overlaySeriesRef.current.push(zoneSeries)
      labels.push({ time: midTime(startTime, lastCandleTime), price, color, text: title })
    }

    // BOS/CHoCH/liquidity-sweep levels, major and internal (deduped against
    // major). HH/HL/LH/LL pivot events are not rendered on this chart.
    const majorEvents = data.market_structure_events
    const allEvents = [...majorEvents, ...data.internal_structure_events]

    const recentInternalSweeps = new Set(
      allEvents
        .filter((event) => event.scope === 'internal' && event.event === 'liquidity_sweep')
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
        .slice(0, MAX_INTERNAL_SWEEPS),
    )

    const structureEvents = allEvents.filter(
      (event) =>
        event.event in STRUCTURE_EVENT_STYLES &&
        !(event.scope === 'internal' && isDuplicateOfMajor(event, majorEvents)) &&
        (event.event !== 'liquidity_sweep' || event.scope !== 'internal' || recentInternalSweeps.has(event)),
    )

    for (const event of structureEvents) {
      const style = STRUCTURE_EVENT_STYLES[event.event]
      const isInternal = event.scope === 'internal'
      const directionIcon = TREND_ICONS[event.direction] ?? ''
      const startTime = toUtcTimestamp(event.timestamp)
      const endTime = structureLineEndTime(event, allEvents, lastCandleTime)

      const structureSeries = chart.addSeries(LineSeries, {
        color: isInternal ? `${style.color}80` : style.color,
        lineWidth: 1,
        lineStyle: isInternal ? LineStyle.Dotted : LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      structureSeries.setData(lineFrom(startTime, endTime, event.price_level))
      overlaySeriesRef.current.push(structureSeries)

      labels.push({
        time: midTime(startTime, endTime),
        price: event.price_level,
        color: style.color,
        text: `${style.label}${isInternal ? ' (Internal)' : ''} ${directionIcon} · ${formatPrice(event.price_level)}`,
      })
    }

    labelsPrimitiveRef.current?.setLabels(labels)

    // Only auto-fit on the first load -- later refreshes shouldn't reset the user's zoom/pan.
    if (!hasFittedRef.current) {
      chart.timeScale().fitContent()
      hasFittedRef.current = true
    }
  }, [data])

  return <div ref={containerRef} className="w-full" />
}
