import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from 'lightweight-charts'
import type { CanvasRenderingTarget2D } from 'fancy-canvas'

export interface DivergenceArc {
  /** Candle the divergence is anchored to (arc centered here). */
  time: Time
  /** Price the divergence formed at (arc offset from this level). */
  price: number
  /** 'above' draws a dome over price, 'below' a bowl under it. */
  side: 'above' | 'below'
  /** Arc stroke color. */
  color: string
  /**
   * VSA-confluence reinforcement: a nearby same-side VSA reversal pattern
   * agrees with this divergence. Drawn thicker/brighter with a ✦ badge.
   */
  strong?: boolean
}

interface ResolvedArc {
  cx: number | null
  y: number | null
  side: 'above' | 'below'
  color: string
  strong: boolean
}

// Arc geometry, expressed in candle widths so it tracks the chart zoom.
const ARC_HALF_BARS = 4 // horizontal half-span, in candles
const GAP_FRAC = 0.3 // clearance from the extreme, as a fraction of half-width
const ARCH_FRAC = 0.45 // apex bulge past the base, as a fraction of half-width
// Clamp so the arc stays legible when zoomed all the way in or out.
const MIN_HALF_WIDTH = 20
const MAX_HALF_WIDTH = 90

class DivergenceArcRenderer implements IPrimitivePaneRenderer {
  private readonly _arcs: ResolvedArc[]
  private readonly _barSpacing: number

  constructor(arcs: ResolvedArc[], barSpacing: number) {
    this._arcs = arcs
    this._barSpacing = barSpacing
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context }) => {
      const halfWidth = Math.max(
        MIN_HALF_WIDTH,
        Math.min(MAX_HALF_WIDTH, this._barSpacing * ARC_HALF_BARS),
      )
      const gap = halfWidth * GAP_FRAC
      const arch = halfWidth * ARCH_FRAC
      for (const arc of this._arcs) {
        if (arc.cx === null || arc.y === null) continue
        const dir = arc.side === 'above' ? -1 : 1
        const baseY = arc.y + dir * gap
        const left = arc.cx - halfWidth
        const right = arc.cx + halfWidth
        // Quadratic bezier: control point pulled 2×arch past the base so the
        // apex sits arch beyond it (dome above / bowl below).
        const ctrlY = baseY + dir * 2 * arch
        const apexY = baseY + dir * arch // curve peak (t = 0.5)

        context.lineJoin = 'round'
        context.lineCap = 'round'

        // A confluent arc gets a soft translucent halo behind the main stroke
        // so it reads as "reinforced" without a different shape.
        if (arc.strong) {
          context.strokeStyle = arc.color + '33'
          context.lineWidth = 7
          context.beginPath()
          context.moveTo(left, baseY)
          context.quadraticCurveTo(arc.cx, ctrlY, right, baseY)
          context.stroke()
        }

        context.strokeStyle = arc.color
        context.lineWidth = arc.strong ? 3.5 : 2.5
        context.beginPath()
        context.moveTo(left, baseY)
        context.quadraticCurveTo(arc.cx, ctrlY, right, baseY)
        context.stroke()

        // ✦ confluence badge just past the apex.
        if (arc.strong) {
          context.fillStyle = arc.color
          context.font = 'bold 12px sans-serif'
          context.textAlign = 'center'
          context.textBaseline = arc.side === 'above' ? 'bottom' : 'top'
          context.fillText('✦', arc.cx, apexY + dir * 4)
        }
      }
    })
  }
}

class DivergenceArcPaneView implements IPrimitivePaneView {
  private readonly _source: DivergenceArcPrimitive

  constructor(source: DivergenceArcPrimitive) {
    this._source = source
  }

  zOrder(): PrimitivePaneViewZOrder {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, arcs } = this._source
    if (!chart || !series || arcs.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedArc[] = arcs.map((arc) => ({
      cx: timeScale.timeToCoordinate(arc.time),
      y: series.priceToCoordinate(arc.price),
      side: arc.side,
      color: arc.color,
      strong: arc.strong ?? false,
    }))
    return new DivergenceArcRenderer(resolved, timeScale.options().barSpacing)
  }
}

/**
 * Draws exhaustion / absorption divergences as curved arcs instead of markers:
 * a dome above price for a bearish (top) exhaustion, a bowl below price for a
 * bullish exhaustion or absorption. Attach once to the candlestick series and
 * call `setArcs()` on each data refresh.
 */
export class DivergenceArcPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  arcs: DivergenceArc[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new DivergenceArcPaneView(this)]
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

  setArcs(arcs: DivergenceArc[]): void {
    this.arcs = arcs
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
