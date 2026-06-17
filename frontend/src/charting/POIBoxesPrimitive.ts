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

export interface POIBox {
  x0: Time
  x1: Time
  priceLow: number
  priceHigh: number
  borderColor: string
  fillColor: string
  label?: string
}

interface ResolvedBox {
  /** null = off the left edge of the visible pane (use 0) */
  x0: number | null
  /** null = off the right edge of the visible pane (use pane width) */
  x1: number | null
  yTop: number
  yBottom: number
  borderColor: string
  fillColor: string
  label?: string
}

class POIBoxesRenderer implements IPrimitivePaneRenderer {
  private readonly _boxes: ResolvedBox[]

  constructor(boxes: ResolvedBox[]) {
    this._boxes = boxes
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const box of this._boxes) {
        const left = Math.max(0, box.x0 ?? 0)
        const right = Math.min(mediaSize.width, box.x1 ?? mediaSize.width)
        const top = Math.min(box.yTop, box.yBottom)
        const bottom = Math.max(box.yTop, box.yBottom)

        if (left >= right || top >= bottom) continue

        // Filled background
        context.fillStyle = box.fillColor
        context.fillRect(left, top, right - left, bottom - top)

        // Border drawn inside the fill
        context.strokeStyle = box.borderColor
        context.lineWidth = 1.5
        context.strokeRect(left + 0.75, top + 0.75, right - left - 1.5, bottom - top - 1.5)

        // Small label in the top-left corner of the box
        if (box.label) {
          const PADDING = 4
          context.font = '10px sans-serif'
          context.textBaseline = 'top'
          context.textAlign = 'left'
          context.fillStyle = box.borderColor
          context.fillText(box.label, left + PADDING, top + PADDING)
        }
      }
    })
  }
}

class POIBoxesPaneView implements IPrimitivePaneView {
  private readonly _source: POIBoxesPrimitive

  constructor(source: POIBoxesPrimitive) {
    this._source = source
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, boxes } = this._source
    if (!chart || !series || boxes.length === 0) return null

    const timeScale = chart.timeScale()
    const visibleRange = timeScale.getVisibleRange()

    const resolved: ResolvedBox[] = []

    for (const box of boxes) {
      // Skip boxes entirely outside the visible time range.
      if (
        visibleRange &&
        (box.x0 as number) > (visibleRange.to as number) + 1 &&
        (box.x1 as number) > (visibleRange.to as number) + 1
      ) {
        continue
      }
      if (
        visibleRange &&
        (box.x0 as number) < (visibleRange.from as number) - 1 &&
        (box.x1 as number) < (visibleRange.from as number) - 1
      ) {
        continue
      }

      const yTop = series.priceToCoordinate(box.priceHigh)
      const yBottom = series.priceToCoordinate(box.priceLow)
      // Skip if price is entirely off the vertical viewport.
      if (yTop === null || yBottom === null) continue

      // null means off-screen; left edge uses 0, right edge uses pane width.
      const x0 = timeScale.timeToCoordinate(box.x0)
      const x1 = timeScale.timeToCoordinate(box.x1)

      resolved.push({
        // x0 null → box started before the visible window → clamp to left edge
        x0: x0,
        // x1 null → box extends past the visible window → clamp to right edge
        x1: x1,
        yTop,
        yBottom,
        borderColor: box.borderColor,
        fillColor: box.fillColor,
        label: box.label,
      })
    }

    if (resolved.length === 0) return null
    return new POIBoxesRenderer(resolved)
  }
}

/**
 * Draws TradingView-style filled rectangle boxes directly on the chart canvas,
 * one box per POI order block zone.  Attach once to the candlestick series and
 * call `setBoxes()` on each data refresh.
 */
export class POIBoxesPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  boxes: POIBox[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new POIBoxesPaneView(this)]
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

  setBoxes(boxes: POIBox[]): void {
    this.boxes = boxes
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
