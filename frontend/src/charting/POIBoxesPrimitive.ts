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
import type { LabelCandle } from './LineLabelsPrimitive'

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

/** A candle projected into pane pixel space, used for label/candle dodging. */
interface CandleRect {
  x: number
  /** y of the candle's high (smaller value -- canvas y grows downward). */
  top: number
  /** y of the candle's low. */
  bottom: number
}

/** Step (px) between candidate positions when sliding a box label along its top edge. */
const DODGE_STEP = 10
/** Assumed half-width (px) of a candle's wick when testing label/candle overlap. */
const CANDLE_HALF_WIDTH = 2

/**
 * Slide a box label along the top strip of its box to the spot least covered
 * by candles: a fully clear spot nearest the box's left corner wins (the
 * label's home position); if nothing is clear, the least-covered spot.
 */
function bestLabelX(
  lo: number,
  hi: number,
  width: number,
  rectTop: number,
  rectBottom: number,
  candles: CandleRect[],
): number {
  if (hi <= lo) return lo

  const coverage = (x: number): number => {
    const left = x - CANDLE_HALF_WIDTH
    const right = x + width + CANDLE_HALF_WIDTH
    let covered = 0
    for (const c of candles) {
      if (c.x >= left && c.x <= right && c.top < rectBottom && c.bottom > rectTop) {
        covered += 2 * CANDLE_HALF_WIDTH
      }
    }
    return covered
  }

  let bestCovered = lo
  let bestCoveredScore = Infinity
  for (let x = lo; x <= hi + 0.001; x += DODGE_STEP) {
    const cov = coverage(x)
    if (cov === 0) return x
    if (cov < bestCoveredScore) {
      bestCoveredScore = cov
      bestCovered = x
    }
  }
  return bestCovered
}

class POIBoxesRenderer implements IPrimitivePaneRenderer {
  private readonly _boxes: ResolvedBox[]
  private readonly _candles: CandleRect[]

  constructor(boxes: ResolvedBox[], candles: CandleRect[]) {
    this._boxes = boxes
    this._candles = candles
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const box of this._boxes) {
        const left = Math.max(0, box.x0 ?? 0)
        const right = Math.min(mediaSize.width, box.x1 ?? mediaSize.width)
        const top = Math.min(box.yTop, box.yBottom)
        const bottom = Math.max(box.yTop, box.yBottom)

        if (left >= right || top >= bottom) continue

        const width = right - left
        const height = bottom - top
        const radius = Math.min(3, width / 2, height / 2)

        // Soft filled background with rounded corners
        context.beginPath()
        context.roundRect(left, top, width, height, radius)
        context.fillStyle = box.fillColor
        context.fill()

        // Hairline border drawn on the same rounded path
        context.beginPath()
        context.roundRect(left + 0.5, top + 0.5, width - 1, height - 1, radius)
        context.strokeStyle = box.borderColor
        context.lineWidth = 1
        context.stroke()

        // Small label along the top edge of the box, sliding right from the
        // corner to a candle-free spot when a candle sits under its home
        // position (still inside the box).
        if (box.label) {
          const PADDING = 5
          const LABEL_HEIGHT = 10
          context.font =
            '500 9px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
          context.textBaseline = 'top'
          context.textAlign = 'left'
          const textWidth = context.measureText(box.label).width
          const rectTop = top + PADDING
          const x = bestLabelX(
            left + PADDING,
            right - PADDING - textWidth,
            textWidth,
            rectTop,
            rectTop + LABEL_HEIGHT,
            this._candles,
          )
          // Border colors carry alpha (#rrggbbaa); render the text a bit
          // stronger than the hairline so it stays readable.
          context.fillStyle =
            box.borderColor.length === 9 ? box.borderColor.slice(0, 7) + 'cc' : box.borderColor
          context.fillText(box.label, x, rectTop)
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

    // Project the candles into pixel space so box labels can dodge them.
    const paneWidth = timeScale.width()
    const candleRects: CandleRect[] = []
    for (const candle of this._source.candles) {
      const x = timeScale.timeToCoordinate(candle.time)
      if (x === null || x < -CANDLE_HALF_WIDTH || x > paneWidth + CANDLE_HALF_WIDTH) continue
      const top = series.priceToCoordinate(candle.high)
      const bottom = series.priceToCoordinate(candle.low)
      if (top === null || bottom === null) continue
      candleRects.push({ x, top, bottom })
    }

    return new POIBoxesRenderer(resolved, candleRects)
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
  candles: LabelCandle[] = []

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

  /** Candles (in time order) the box labels should dodge. */
  setCandles(candles: LabelCandle[]): void {
    this.candles = candles
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
