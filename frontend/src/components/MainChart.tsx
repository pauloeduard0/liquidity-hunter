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
import { POIBoxesPrimitive, type POIBox } from '../charting/POIBoxesPrimitive'
import type { DashboardData, MarketStructure, POIZone } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  DARK_BG,
  DEFAULT_ZONE_COLOR,
  FONT_COLOR,
  GRID_COLOR,
  POI_BOX_STYLES,
  RTO_COLORS,
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
 * Where `event`'s line should stop.
 *
 * - BOS / Sweep: ends at the next BOS or CHoCH of the same scope and direction
 *   (moves that active level), otherwise extends to `lastCandleTime`.
 * - CHoCH: extends until the *opposite*-direction CHoCH of the same scope
 *   supersedes it (the new trend nullifies the prior reversal reference);
 *   a same-direction BOS does not end a CHoCH line.
 */
function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toUtcTimestamp(event.timestamp)

  if (event.event === 'change_of_character') {
    const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
    const supersededAt = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.direction === oppositeDirection &&
          other.event === 'change_of_character' &&
          toUtcTimestamp(other.timestamp) > eventTime,
      )
      .map((other) => toUtcTimestamp(other.timestamp))
    return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
  }

  const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        toUtcTimestamp(other.timestamp) > eventTime &&
        ((other.direction === event.direction &&
          (other.event === 'break_of_structure' || other.event === 'change_of_character')) ||
          (other.direction === oppositeDirection && other.event === 'change_of_character')),
    )
    .map((other) => toUtcTimestamp(other.timestamp))

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}

/**
 * The right edge of a POI box.
 *
 * Always extends to the first *internal* BOS of the same direction that fires
 * after the zone was created -- regardless of mitigation status (mitigation
 * only affects the box style, not its end point). If no such BOS exists yet,
 * returns a far-future sentinel so the box reaches the right pane edge: LWC
 * returns null from `timeToCoordinate` for out-of-range times, and the
 * primitive clamps null to `mediaSize.width`.
 */
function poiBoxEndTime(
  zone: POIZone,
  internalEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
): UTCTimestamp {
  const zoneTime = toUtcTimestamp(zone.created_at)
  const nextBos = internalEvents
    .filter(
      (e) =>
        e.scope === 'internal' &&
        e.event === 'break_of_structure' &&
        e.direction === zone.direction &&
        toUtcTimestamp(e.timestamp) > zoneTime,
    )
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))[0]
  return nextBos ? toUtcTimestamp(nextBos.timestamp) : ((lastCandleTime + 9_999_999) as UTCTimestamp)
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
  const poiBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
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

    const poiBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(poiBoxesPrimitive)
    poiBoxesPrimitiveRef.current = poiBoxesPrimitive

    const handleResize = () => chart.applyOptions({ width: container.clientWidth })
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      overlaySeriesRef.current = []
      labelsPrimitiveRef.current = null
      poiBoxesPrimitiveRef.current = null
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
      labels.push({ time: startTime, price, color, text: title })
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

      // For CHoCH, anchor on `reference_price_level` (the validated level
      // that was broken) rather than `price_level` (the confirming pivot's
      // own extreme, which can be far beyond the level it confirmed) -- so
      // the marker sits on the structural level that flipped. BOS and Sweep
      // keep `price_level`, where it coincides with the breaking level.
      const linePrice =
        event.event === 'change_of_character' && event.reference_price_level != null
          ? event.reference_price_level
          : event.price_level

      // CHoCH lines originate from the validated_choch pivot (the LH/HL that
      // was promoted to the reversal reference), not the candle that broke it.
      const lineStartTime =
        event.event === 'change_of_character' && event.reference_timestamp != null
          ? toUtcTimestamp(event.reference_timestamp)
          : startTime

      const structureSeries = chart.addSeries(LineSeries, {
        color: isInternal ? `${style.color}80` : style.color,
        lineWidth: 1,
        lineStyle: isInternal ? LineStyle.Dotted : LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      structureSeries.setData(lineFrom(lineStartTime, endTime, linePrice))
      overlaySeriesRef.current.push(structureSeries)

      labels.push({
        time: startTime,
        price: linePrice,
        color: style.color,
        text: `${style.label}${isInternal ? ' (Internal)' : ''} ${directionIcon} · ${formatPrice(linePrice)}`,
      })
    }

    // POI order block zones: TradingView-style filled rectangles via
    // POIBoxesPrimitive. Invalidated zones are not rendered. Mitigated zones
    // keep their directional color but at lower opacity (not gray).
    const poiBoxes: POIBox[] = []
    for (const zone of data.poi_zones ?? []) {
      if (zone.status === 'invalidated') continue

      const isMitigated = zone.status === 'mitigated'
      const dirStyle = POI_BOX_STYLES[zone.direction] ?? POI_BOX_STYLES.mitigated
      // Mitigated: same directional color, dimmer border + fainter fill.
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

    // RTO sweep events: label at the recovery candle's midpoint.
    for (const rto of data.poi_sweep_events ?? []) {
      const color = RTO_COLORS[rto.direction] ?? '#888888'
      const midPrice = (rto.zone_price_low + rto.zone_price_high) / 2
      labels.push({
        time: toUtcTimestamp(rto.timestamp),
        price: midPrice,
        color,
        text: `RTO ${rto.direction === 'bullish' ? '▲' : '▼'} · ${formatPrice(midPrice)}`,
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
