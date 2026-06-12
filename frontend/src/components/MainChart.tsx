import { useEffect, useRef } from 'react'
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'

import type { DashboardData } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  DARK_BG,
  DEFAULT_ZONE_COLOR,
  FONT_COLOR,
  GRID_COLOR,
  STRUCTURE_EVENT_STYLES,
  ZONE_COLORS,
} from '../theme'

/**
 * Plotting every detected zone (there can be dozens of swing points) makes
 * the chart unreadable, so only the highest-ranked zones are overlaid here
 * -- mirroring `dashboard.charts.main_chart`'s `DEFAULT_TOP_N_ZONES`.
 */
const TOP_N_ZONES = 5

function toUtcTimestamp(isoTimestamp: string): UTCTimestamp {
  return (Date.parse(isoTimestamp) / 1000) as UTCTimestamp
}

function formatZoneType(zoneType: string): string {
  return zoneType
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

interface MainChartProps {
  data: DashboardData
}

/** Primary chart: candlesticks with the top-ranked liquidity zones and market structure events. */
export function MainChart({ data }: MainChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const zoneSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
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
    markersPluginRef.current = createSeriesMarkers(series, [])

    const handleResize = () => chart.applyOptions({ width: container.clientWidth })
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      zoneSeriesRef.current = []
      markersPluginRef.current = null
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

    for (const zoneSeries of zoneSeriesRef.current) {
      chart.removeSeries(zoneSeries)
    }
    zoneSeriesRef.current = []

    const lastCandleTime = toUtcTimestamp(data.candles[data.candles.length - 1].timestamp)

    for (const scored of data.ranked_zones.slice(0, TOP_N_ZONES)) {
      const { zone, score } = scored
      const color = ZONE_COLORS[zone.zone_type] ?? DEFAULT_ZONE_COLOR
      const title = `${formatZoneType(zone.zone_type)} (${zone.strength.toFixed(2)}) · ${score.toFixed(0)}`
      const price = (zone.price_high + zone.price_low) / 2
      const startTime = toUtcTimestamp(zone.formed_at)

      const points =
        startTime < lastCandleTime
          ? [
              { time: startTime, value: price },
              { time: lastCandleTime, value: price },
            ]
          : [{ time: lastCandleTime, value: price }]

      const zoneSeries = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        lastValueVisible: true,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title,
      })
      zoneSeries.setData(points)
      zoneSeriesRef.current.push(zoneSeries)
    }

    const markers: SeriesMarker<Time>[] = data.market_structure_events.map((event) => {
      const style = STRUCTURE_EVENT_STYLES[event.event] ?? {
        label: event.event,
        color: DEFAULT_ZONE_COLOR,
      }
      const isBullish = event.direction === 'bullish'
      return {
        time: toUtcTimestamp(event.timestamp),
        position: isBullish ? 'aboveBar' : 'belowBar',
        shape: isBullish ? 'arrowUp' : 'arrowDown',
        color: style.color,
        text: style.label,
      }
    })
    markersPluginRef.current?.setMarkers(markers)

    // Only auto-fit on the first load -- later refreshes shouldn't reset the user's zoom/pan.
    if (!hasFittedRef.current) {
      chart.timeScale().fitContent()
      hasFittedRef.current = true
    }
  }, [data])

  return <div ref={containerRef} className="w-full" />
}
