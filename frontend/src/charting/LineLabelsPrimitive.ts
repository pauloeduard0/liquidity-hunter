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
  /** x coordinate when `align` is `'left'`, or `null` to anchor to the right edge of the pane (`align: 'right'`). */
  x: number | null
  y: number
  text: string
  color: string
  align: 'left' | 'right'
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
        const left = (label.align === 'left' ? x : x - width) - 2
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

    const positioned: PositionedLabel[] = []
    for (const label of labels) {
      const y = series.priceToCoordinate(label.price)
      if (y === null) continue

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
