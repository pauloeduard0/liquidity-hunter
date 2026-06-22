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

import {
  HEATMAP_GRADIENT,
  HEATMAP_MAX_ALPHA,
  HEATMAP_MAX_WIDTH,
  HEATMAP_MIN_WIDTH,
} from '../theme'

export interface HeatmapBand {
  priceLow: number
  priceHigh: number
  /** Normalized heat, 0-100. */
  heat: number
}

interface ResolvedBand {
  yTop: number
  yBottom: number
  /** Normalized heat in [0, 1]. */
  t: number
  rgb: [number, number, number]
}

/** Interpolate the cold->hot gradient at a normalized position t in [0, 1]. */
function gradientRgb(t: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, t))
  let lo = HEATMAP_GRADIENT[0]
  let hi = HEATMAP_GRADIENT[HEATMAP_GRADIENT.length - 1]
  for (let i = 0; i < HEATMAP_GRADIENT.length - 1; i++) {
    if (clamped >= HEATMAP_GRADIENT[i].stop && clamped <= HEATMAP_GRADIENT[i + 1].stop) {
      lo = HEATMAP_GRADIENT[i]
      hi = HEATMAP_GRADIENT[i + 1]
      break
    }
  }
  const span = hi.stop - lo.stop || 1
  const f = (clamped - lo.stop) / span
  return [
    Math.round(lo.rgb[0] + (hi.rgb[0] - lo.rgb[0]) * f),
    Math.round(lo.rgb[1] + (hi.rgb[1] - lo.rgb[1]) * f),
    Math.round(lo.rgb[2] + (hi.rgb[2] - lo.rgb[2]) * f),
  ]
}

class HeatmapStripRenderer implements IPrimitivePaneRenderer {
  private readonly _bands: ResolvedBand[]

  constructor(bands: ResolvedBand[]) {
    this._bands = bands
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const right = mediaSize.width
      for (const band of this._bands) {
        const top = Math.min(band.yTop, band.yBottom)
        const bottom = Math.max(band.yTop, band.yBottom)
        const height = bottom - top
        if (height < 0.5) continue

        const length = HEATMAP_MIN_WIDTH + band.t * (HEATMAP_MAX_WIDTH - HEATMAP_MIN_WIDTH)
        const left = right - length
        const [r, g, b] = band.rgb
        const alpha = band.t * HEATMAP_MAX_ALPHA

        // Horizontal alpha fade: solid at the right (chart edge), tapering to
        // near-transparent at the inward tip — like a volume-profile plume.
        const gradient = context.createLinearGradient(left, 0, right, 0)
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(alpha * 0.12).toFixed(3)})`)
        gradient.addColorStop(0.35, `rgba(${r}, ${g}, ${b}, ${(alpha * 0.7).toFixed(3)})`)
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`)

        context.fillStyle = gradient
        context.fillRect(left, top, length, height)
      }
    })
  }
}

class HeatmapStripPaneView implements IPrimitivePaneView {
  private readonly _source: HeatmapStripPrimitive

  constructor(source: HeatmapStripPrimitive) {
    this._source = source
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { series, bands } = this._source
    if (!series || bands.length === 0) return null

    const resolved: ResolvedBand[] = []
    for (const band of bands) {
      if (band.heat <= 0) continue
      const yTop = series.priceToCoordinate(band.priceHigh)
      const yBottom = series.priceToCoordinate(band.priceLow)
      if (yTop === null || yBottom === null) continue

      const t = band.heat / 100
      resolved.push({ yTop, yBottom, t, rgb: gradientRgb(t) })
    }

    if (resolved.length === 0) return null
    return new HeatmapStripRenderer(resolved)
  }
}

/**
 * Draws a "liquidity heatmap" volume-profile along the right edge of the main
 * pane: one horizontal bar per price bucket, colored cold->hot and with length
 * proportional to estimated resting-liquidity concentration ("stop magnets").
 * Hotter levels project further into the chart. Attach once to the candlestick
 * series and call `setBands()` on each data refresh.
 */
export class HeatmapStripPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  bands: HeatmapBand[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new HeatmapStripPaneView(this)]
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

  setBands(bands: HeatmapBand[]): void {
    this.bands = bands
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
