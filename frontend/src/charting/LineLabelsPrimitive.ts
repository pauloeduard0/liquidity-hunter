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
  /**
   * Draw the label just *below* the line instead of above it
   * (TradingView-style: bullish structure labels sit above their line,
   * bearish ones below). Defaults to above.
   */
  below?: boolean
}

const FONT = '500 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
const GAP_ABOVE_LINE = 2
const EDGE_PADDING = 4
const LINE_HEIGHT = 12
const MAX_STACK = 30
/** Step (px) between candidate positions when sliding a segment label along its line. */
const DODGE_STEP = 10
/** Assumed half-width (px) of a candle's wick when testing label/candle overlap. */
const CANDLE_HALF_WIDTH = 2

/** A candle projected into pane pixel space, used for label/candle dodging. */
interface CandleRect {
  x: number
  /** y of the candle's high (smaller value -- canvas y grows downward). */
  top: number
  /** y of the candle's low. */
  bottom: number
}

interface PositionedLabel {
  /** x coordinate when `align` is `'left'`/`'center'`, or `null` to anchor to the right edge of the pane (`align: 'right'`). */
  x: number | null
  y: number
  text: string
  color: string
  align: 'left' | 'right' | 'center'
  below: boolean
  /**
   * Visible pixel extent of the line segment this label belongs to. When set,
   * the renderer slides the label along `[segLeft, segRight]` to the clearest
   * spot instead of pinning it at `x` (see `bestSegmentX`).
   */
  segLeft?: number
  segRight?: number
}

/**
 * Slide a segment label along its line to the spot least covered by candles:
 * a fully clear spot nearest the segment center wins; if nothing is clear,
 * the least-covered spot nearest the segment *start* (the line's origin,
 * where the reference formed -- usually open space before the break).
 */
function bestSegmentX(
  segLeft: number,
  segRight: number,
  halfWidth: number,
  rectTop: number,
  rectBottom: number,
  candles: CandleRect[],
): number {
  const lo = segLeft + halfWidth
  const hi = segRight - halfWidth
  const center = (segLeft + segRight) / 2
  if (hi <= lo) return center

  // Coverage (overlapping px of candle wicks) of the label rect centered at x.
  const coverage = (x: number): number => {
    const left = x - halfWidth - CANDLE_HALF_WIDTH
    const right = x + halfWidth + CANDLE_HALF_WIDTH
    // Candles arrive in ascending x -- binary-search the first that can reach
    // the rect, then walk right until past it.
    let loIdx = 0
    let hiIdx = candles.length
    while (loIdx < hiIdx) {
      const mid = (loIdx + hiIdx) >> 1
      if (candles[mid].x < left) loIdx = mid + 1
      else hiIdx = mid
    }
    let covered = 0
    for (let i = loIdx; i < candles.length && candles[i].x <= right; i++) {
      const c = candles[i]
      if (c.top < rectBottom && c.bottom > rectTop) covered += 2 * CANDLE_HALF_WIDTH
    }
    return covered
  }

  let bestClear: number | null = null // clear spot nearest the center
  let bestCovered = center
  let bestCoveredScore = Infinity
  for (let x = lo; x <= hi + 0.001; x += DODGE_STEP) {
    const cov = coverage(x)
    if (cov === 0) {
      if (bestClear === null || Math.abs(x - center) < Math.abs(bestClear - center)) {
        bestClear = x
      }
    } else if (
      cov < bestCoveredScore ||
      (cov === bestCoveredScore && x < bestCovered)
    ) {
      bestCoveredScore = cov
      bestCovered = x
    }
  }
  return bestClear ?? bestCovered
}

class LineLabelsRenderer implements IPrimitivePaneRenderer {
  private readonly _labels: PositionedLabel[]
  private readonly _candles: CandleRect[]

  constructor(labels: PositionedLabel[], candles: CandleRect[]) {
    this._labels = labels
    this._candles = candles
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
        const width = context.measureText(label.text).width + 4

        // Segment labels dodge candles: slide along the visible line to the
        // clearest spot rather than sitting at the fixed midpoint.
        let x = label.x ?? mediaSize.width - EDGE_PADDING
        if (label.segLeft !== undefined && label.segRight !== undefined) {
          const rectBottom = label.below
            ? label.y + GAP_ABOVE_LINE + LINE_HEIGHT
            : label.y - GAP_ABOVE_LINE
          x = bestSegmentX(
            label.segLeft,
            label.segRight,
            width / 2 + 2,
            rectBottom - LINE_HEIGHT,
            rectBottom,
            this._candles,
          )
        }

        const left =
          (label.align === 'left' ? x : label.align === 'center' ? x - width / 2 : x - width) - 2
        const right = left + width

        // `below` labels start just under the line and fan out downward on
        // collision; above labels (the default) start just over it and fan up.
        let bottom = label.below
          ? label.y + GAP_ABOVE_LINE + LINE_HEIGHT
          : label.y - GAP_ABOVE_LINE
        for (let i = 0; i < MAX_STACK; i++) {
          const top = bottom - LINE_HEIGHT
          const collides = placed.some(
            (p) => left < p.right && right > p.left && top < p.bottom && bottom > p.top,
          )
          if (!collides) break
          bottom += label.below ? LINE_HEIGHT : -LINE_HEIGHT
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
    const { chart, series, labels, fallbackChart } = this._source
    if (!chart || !series) return null

    // When the attached chart's own time axis is hidden (e.g. an upper pane in
    // a multi-pane stack that delegates the visible axis to the bottom pane),
    // its time scale reports `width() === 0`, which collapses every segment
    // label's right edge to 0 and drops them all. Fall back to a sibling chart
    // that shares this one's synced, equal-width time scale (its axis is live,
    // so `width()` and `timeToCoordinate` resolve). Price -> y always uses this
    // chart's own series (its price scale stays functional).
    const ownTimeScale = chart.timeScale()
    const fallbackTimeScale = fallbackChart?.timeScale()
    const timeScale =
      ownTimeScale.width() > 0 || !fallbackTimeScale || fallbackTimeScale.width() <= 0
        ? ownTimeScale
        : fallbackTimeScale
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
          below: label.below ?? false,
          segLeft: left,
          segRight: right,
        })
        continue
      }

      const x = timeScale.timeToCoordinate(label.time)
      if (x !== null) {
        positioned.push({
          x,
          y,
          text: label.text,
          color: label.color,
          align: 'left',
          below: label.below ?? false,
        })
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
        below: label.below ?? false,
      })
    }

    // Project the candles into pixel space (ascending x, matching their time
    // order) so segment labels can dodge them. Only candles that resolve to a
    // coordinate near the visible pane matter.
    const candleRects: CandleRect[] = []
    for (const candle of this._source.candles) {
      const x = timeScale.timeToCoordinate(candle.time)
      if (x === null || x < -CANDLE_HALF_WIDTH || x > paneWidth + CANDLE_HALF_WIDTH) continue
      const top = series.priceToCoordinate(candle.high)
      const bottom = series.priceToCoordinate(candle.low)
      if (top === null || bottom === null) continue
      candleRects.push({ x, top, bottom })
    }

    return new LineLabelsRenderer(positioned, candleRects)
  }
}

/**
 * Draws small text labels directly on the chart pane, anchored to a
 * (time, price) point just above it -- used to label horizontal lines
 * (liquidity zones, structure events) along their own position instead of
 * stacking titles on the right price axis.
 */
/** A candle's time and wick extent, fed to the label/candle dodge logic. */
export interface LabelCandle {
  time: Time
  high: number
  low: number
}

export class LineLabelsPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  labels: LineLabel[] = []
  candles: LabelCandle[] = []
  /**
   * Sibling chart used to resolve time -> x when this primitive's own chart has
   * its time axis hidden (which nulls its public time-scale coordinate API).
   * Must share this chart's synced, equal-width time scale.
   */
  fallbackChart: IChartApi | null = null

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

  /** Candles (in time order) the segment labels should dodge. */
  setCandles(candles: LabelCandle[]): void {
    this.candles = candles
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
