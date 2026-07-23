import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'

export interface EqlZoneInput {
  /** Left edge: the candle that formed the pool. */
  x0: Time
  /** Right edge: a far-future sentinel keeps the pool running to the edge. */
  x1: Time
  priceLow: number
  priceHigh: number
  /** Zone color (hex, e.g. '#636efa'). */
  color: string
  /** Pool strength 0-1 (touch count); scales fill opacity. */
  strength: number
  /** True once the pool has been swept (mitigated). */
  swept: boolean
}

interface ResolvedZone {
  x0: number | null
  x1: number | null
  yTop: number
  yBottom: number
  rgb: [number, number, number]
  fillAlpha: number
  swept: boolean
}

// A pool's band is intentionally subtle so candles stay legible; the border
// carries the eye. Fill scales between these by strength.
const FILL_ALPHA_MIN = 0.06
const FILL_ALPHA_MAX = 0.16
// Swept pools are drawn ghosted — consumed liquidity, kept for context.
const SWEPT_ALPHA_FACTOR = 0.4
// A degenerate pool (equal_high === equal_low) still needs a visible band.
const MIN_BAND_HEIGHT = 3

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ]
}

class EqlZonesRenderer implements IPrimitivePaneRenderer {
  private readonly _zones: ResolvedZone[]

  constructor(zones: ResolvedZone[]) {
    this._zones = zones
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const zone of this._zones) {
        const left = Math.max(0, zone.x0 ?? 0)
        const right = Math.min(mediaSize.width, zone.x1 ?? mediaSize.width)
        const top = Math.min(zone.yTop, zone.yBottom)
        const bottom = Math.max(zone.yTop, zone.yBottom)
        const height = Math.max(bottom - top, MIN_BAND_HEIGHT)
        if (left >= right) continue

        const [r, g, b] = zone.rgb

        // Filled pool band (formation -> right edge / sweep).
        context.fillStyle = `rgba(${r}, ${g}, ${b}, ${zone.fillAlpha.toFixed(3)})`
        context.fillRect(left, top, right - left, height)

        // Top and bottom boundaries — the actual equal-level edges, where the
        // resting orders sit. Dashed once swept.
        const borderAlpha = Math.min(1, zone.fillAlpha + (zone.swept ? 0.25 : 0.55))
        context.strokeStyle = `rgba(${r}, ${g}, ${b}, ${borderAlpha.toFixed(3)})`
        context.lineWidth = zone.swept ? 1 : 1.5
        context.setLineDash(zone.swept ? [4, 3] : [])
        context.beginPath()
        context.moveTo(left, top)
        context.lineTo(right, top)
        context.moveTo(left, top + height)
        context.lineTo(right, top + height)
        context.stroke()
        context.setLineDash([])
      }
    })
  }
}

class EqlZonesPaneView implements IPrimitivePaneView {
  private readonly _source: EqlZonesPrimitive

  constructor(source: EqlZonesPrimitive) {
    this._source = source
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, zones } = this._source
    if (!chart || !series || zones.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedZone[] = []
    for (const zone of zones) {
      const yTop = series.priceToCoordinate(zone.priceHigh)
      const yBottom = series.priceToCoordinate(zone.priceLow)
      if (yTop === null || yBottom === null) continue

      const t = Math.min(1, Math.max(0, zone.strength))
      let fillAlpha = FILL_ALPHA_MIN + t * (FILL_ALPHA_MAX - FILL_ALPHA_MIN)
      if (zone.swept) fillAlpha *= SWEPT_ALPHA_FACTOR

      resolved.push({
        x0: timeScale.timeToCoordinate(zone.x0),
        x1: timeScale.timeToCoordinate(zone.x1),
        yTop,
        yBottom,
        rgb: hexToRgb(zone.color),
        fillAlpha,
        swept: zone.swept,
      })
    }

    if (resolved.length === 0) return null
    return new EqlZonesRenderer(resolved)
  }
}

/**
 * Draws equal-high / equal-low liquidity pools as shaded horizontal bands on the
 * main pane: each spans the pool's full price thickness (`priceLow`→`priceHigh`,
 * the equal-level edges where resting orders sit) from its formation candle to
 * the right edge. Fill opacity scales with pool strength (touch count); swept
 * pools render ghosted with dashed edges. Attach once to the candlestick series
 * and call `setZones()` on each data refresh.
 */
export class EqlZonesPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  zones: EqlZoneInput[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new EqlZonesPaneView(this)]
  private _requestUpdate: (() => void) | null = null

  attached({ chart, series, requestUpdate }: SeriesAttachedParameter<Time>): void {
    this.chart = chart
    this.series = series as ISeriesApi<SeriesType>
    this._requestUpdate = requestUpdate
  }

  detached(): void {
    this.chart = null
    this.series = null
    this._requestUpdate = null
  }

  setZones(zones: EqlZoneInput[]): void {
    this.zones = zones
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
