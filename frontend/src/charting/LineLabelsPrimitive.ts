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

export interface LineLabel {
  /** Anchor point in time -- the label is drawn just above the line at this point. */
  time: Time
  /**
   * Optional end of the line segment the label belongs to. When set, the label
   * is centered horizontally on the *visible* portion of `[time, timeEnd]`
   * (TradingView-style mid-line placement) instead of anchored at `time`,
   * keeping it out of the candles at either end of the line.
   */
  timeEnd?: Time
  /** Anchor price -- the label is drawn just above the line at this price level. */
  price: number
  text: string
  /** Text color -- matches the line's color, mirroring the Streamlit/Plotly annotations. */
  color: string
}

const FONT = '10px sans-serif'
const GAP_ABOVE_LINE = 2
const EDGE_PADDING = 4
const LINE_HEIGHT = 12
const MAX_STACK = 30

interface PositionedLabel {
  /** x coordinate when `align` is `'left'`/`'center'`, or `null` to anchor to the right edge of the pane (`align: 'right'`). */
  x: number | null
  y: number
  text: string
  color: string
  align: 'left' | 'right' | 'center'
}

class LineLabelsRenderer implements IPrimitivePaneRenderer {
  private readonly _labels: PositionedLabel[]

  constructor(labels: PositionedLabel[]) {
    this._labels = labels
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.font = FONT
      context.textBaseline = 'bottom'

      // Labels anchored close together (in both time and price) would
      // otherwise overlap -- stack each newly-placed label above any
      // already-placed label it collides with, so clusters fan out
      // vertically instead of piling on top of each other.
      const placed: { left: number; right: number; top: number; bottom: number }[] = []
      for (const label of this._labels) {
        context.textAlign = label.align
        const x = label.x ?? mediaSize.width - EDGE_PADDING
        const width = context.measureText(label.text).width + 4
        const left =
          (label.align === 'left' ? x : label.align === 'center' ? x - width / 2 : x - width) - 2
        const right = left + width

        let bottom = label.y - GAP_ABOVE_LINE
        for (let i = 0; i < MAX_STACK; i++) {
          const top = bottom - LINE_HEIGHT
          const collides = placed.some(
            (p) => left < p.right && right > p.left && top < p.bottom && bottom > p.top,
          )
          if (!collides) break
          bottom -= LINE_HEIGHT
        }
        placed.push({ left, right, top: bottom - LINE_HEIGHT, bottom })

        context.fillStyle = label.color
        context.fillText(label.text, x, bottom)
      }
    })
  }
}

class LineLabelsPaneView implements IPrimitivePaneView {
  private readonly _source: LineLabelsPrimitive

  constructor(source: LineLabelsPrimitive) {
    this._source = source
  }

  zOrder(): 'top' {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, labels } = this._source
    if (!chart || !series) return null

    const timeScale = chart.timeScale()
    const visibleRange = timeScale.getVisibleRange()
    const paneWidth = timeScale.width()

    // x coordinate of a time, clamped to the pane edge it scrolled off of
    // (`null` only when nothing about its position can be determined).
    const coordOrEdge = (time: Time): number | null => {
      const x = timeScale.timeToCoordinate(time)
      if (x !== null) return x
      if (!visibleRange) return null
      if ((time as number) <= (visibleRange.from as number)) return 0
      if ((time as number) >= (visibleRange.to as number)) return paneWidth
      return null
    }

    const positioned: PositionedLabel[] = []
    for (const label of labels) {
      const y = series.priceToCoordinate(label.price)
      if (y === null) continue

      // Segment labels: centered on the visible portion of the line
      // (TradingView-style), so the text sits in the open gap the line spans
      // instead of on the candles at the break point.
      if (label.timeEnd !== undefined) {
        const x0 = coordOrEdge(label.time)
        const x1 = coordOrEdge(label.timeEnd)
        if (x0 === null || x1 === null) continue
        const left = Math.max(0, Math.min(x0, x1))
        const right = Math.min(paneWidth, Math.max(x0, x1))
        if (right < left) continue // the whole segment is off-screen
        positioned.push({
          x: (left + right) / 2,
          y,
          text: label.text,
          color: label.color,
          align: 'center',
        })
        continue
      }

      const x = timeScale.timeToCoordinate(label.time)
      if (x !== null) {
        positioned.push({ x, y, text: label.text, color: label.color, align: 'left' })
        continue
      }

      // The anchor point has scrolled out of view, but its line may still be
      // visible across the pane -- pin the label to whichever edge of the
      // pane the anchor fell off, instead of dropping it.
      if (!visibleRange) continue
      const isBefore = (label.time as number) < (visibleRange.from as number)
      positioned.push({
        x: isBefore ? EDGE_PADDING : null,
        y,
        text: label.text,
        color: label.color,
        align: isBefore ? 'left' : 'right',
      })
    }

    return new LineLabelsRenderer(positioned)
  }
}

/**
 * Draws small text labels directly on the chart pane, anchored to a
 * (time, price) point just above it -- used to label horizontal lines
 * (liquidity zones, structure events) along their own position instead of
 * stacking titles on the right price axis.
 */
export class LineLabelsPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  labels: LineLabel[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new LineLabelsPaneView(this)]
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

  setLabels(labels: LineLabel[]): void {
    this.labels = labels
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
