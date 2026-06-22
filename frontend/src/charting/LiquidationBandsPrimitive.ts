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
  LIQUIDATION_DEFAULT_COLOR,
  LIQUIDATION_LEVERAGE_COLORS,
  LIQUIDATION_MAX_ALPHA,
  LIQUIDATION_MIN_ALPHA,
} from '../theme'

export interface LiquidationBandInput {
  x0: Time
  /** Right edge: liquidation-hit time, or a far-future sentinel if still live. */
  x1: Time
  priceLow: number
  priceHigh: number
  /** Normalized intensity, 0-100. */
  intensity: number
  leverage: number
  /** True once price reached the level (consumed); false = still-live pool. */
  hit: boolean
}

interface ResolvedBand {
  /** null = off the left edge of the visible pane (use 0). */
  x0: number | null
  /** null = extends past the right edge (use pane width). */
  x1: number | null
  yTop: number
  yBottom: number
  alpha: number
  rgb: [number, number, number]
  leverage: number
  hit: boolean
  showTag: boolean
}

// Already-hit (consumed) bands are rendered fainter than still-live pools.
const HIT_ALPHA_FACTOR = 0.55
// Only label bands strong enough to be worth reading, to cut text clutter.
const TAG_MIN_INTENSITY = 30

class LiquidationBandsRenderer implements IPrimitivePaneRenderer {
  private readonly _bands: ResolvedBand[]

  constructor(bands: ResolvedBand[]) {
    this._bands = bands
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.font = '10px sans-serif'
      context.textBaseline = 'middle'
      context.textAlign = 'left'
      for (const band of this._bands) {
        const left = Math.max(0, band.x0 ?? 0)
        const right = Math.min(mediaSize.width, band.x1 ?? mediaSize.width)
        const top = Math.min(band.yTop, band.yBottom)
        const bottom = Math.max(band.yTop, band.yBottom)
        const height = Math.max(bottom - top, 1.5)
        if (left >= right) continue

        const [r, g, b] = band.rgb
        const mid = top + height / 2

        // Filled time-bounded band (entry formation -> liquidation hit).
        // Consumed (hit) bands render fainter than still-live pools.
        const fillAlpha = band.hit ? band.alpha * HIT_ALPHA_FACTOR : band.alpha
        context.fillStyle = `rgba(${r}, ${g}, ${b}, ${fillAlpha.toFixed(3)})`
        context.fillRect(left, top, right - left, height)

        // Center line: solid for live pools, dashed for already-hit levels.
        context.strokeStyle = `rgba(${r}, ${g}, ${b}, ${Math.min(1, fillAlpha + 0.3).toFixed(3)})`
        context.lineWidth = 1
        context.setLineDash(band.hit ? [4, 3] : [])
        context.beginPath()
        context.moveTo(left, mid)
        context.lineTo(right, mid)
        context.stroke()
        context.setLineDash([])

        // Leverage tag at the band's left edge (only for strong-enough bands).
        if (band.showTag) {
          context.fillStyle = `rgba(${r}, ${g}, ${b}, ${Math.min(1, fillAlpha + 0.45).toFixed(3)})`
          context.fillText(`${band.leverage}x`, left + 3, mid)
        }
      }
    })
  }
}

class LiquidationBandsPaneView implements IPrimitivePaneView {
  private readonly _source: LiquidationBandsPrimitive

  constructor(source: LiquidationBandsPrimitive) {
    this._source = source
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, bands } = this._source
    if (!chart || !series || bands.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedBand[] = []
    for (const band of bands) {
      if (band.intensity <= 0) continue
      const yTop = series.priceToCoordinate(band.priceHigh)
      const yBottom = series.priceToCoordinate(band.priceLow)
      if (yTop === null || yBottom === null) continue

      const rgb = LIQUIDATION_LEVERAGE_COLORS[band.leverage] ?? LIQUIDATION_DEFAULT_COLOR
      const t = band.intensity / 100
      const alpha = LIQUIDATION_MIN_ALPHA + t * (LIQUIDATION_MAX_ALPHA - LIQUIDATION_MIN_ALPHA)
      resolved.push({
        x0: timeScale.timeToCoordinate(band.x0),
        x1: timeScale.timeToCoordinate(band.x1),
        yTop,
        yBottom,
        alpha,
        rgb,
        leverage: band.leverage,
        hit: band.hit,
        showTag: band.intensity >= TAG_MIN_INTENSITY,
      })
    }

    if (resolved.length === 0) return null
    return new LiquidationBandsRenderer(resolved)
  }
}

/**
 * Draws estimated leverage-liquidation bands as time-bounded horizontal boxes on
 * the main pane: each spans from the entry cluster's formation (`x0`) to when
 * price first reached the liquidation level (`x1`, or the chart edge if still
 * live). Color encodes the leverage tier (warmer = higher leverage), opacity
 * scales by intensity. Attach once to the candlestick series and call
 * `setBands()` on each data refresh.
 */
export class LiquidationBandsPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  bands: LiquidationBandInput[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [
    new LiquidationBandsPaneView(this),
  ]
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

  setBands(bands: LiquidationBandInput[]): void {
    this.bands = bands
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
