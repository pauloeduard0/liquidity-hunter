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

/** Which glyph a divergence type draws. Shape carries the *type*; fill is
 *  reserved for confluence, so the two channels never compete. */
export type DivergenceGlyph = 'triangle' | 'diamond' | 'square'

export interface DivergenceMark {
  /** Candle the divergence is anchored to. */
  time: Time
  /** The candle extreme the mark hangs off (high above / low below). */
  price: number
  /** 'above' draws over the high, 'below' under the low. */
  side: 'above' | 'below'
  /** Glyph shape — the divergence type. */
  glyph: DivergenceGlyph
  /** Stroke/fill color. */
  color: string
  /**
   * VSA-confluence reinforcement: a nearby same-side VSA reversal pattern
   * agrees with this divergence. Drawn filled, at full alpha, with a ✦ badge.
   */
  strong?: boolean
}

interface ResolvedMark {
  cx: number | null
  y: number | null
  side: 'above' | 'below'
  glyph: DivergenceGlyph
  color: string
  strong: boolean
}

/**
 * Geometry in **pixels**, not candle widths. The arcs this replaced scaled
 * with `barSpacing`, which a wide curve needs; a small glyph does not, and a
 * mark that changed its distance from the wick on every zoom read as unstable.
 */
const WHISKER_PX = 6 // hairline from the candle extreme out to the glyph
const GLYPH_RADIUS = 3.5 // half-size of the glyph
const BADGE_GAP_PX = 3 // clearance between the glyph and the ✦

/**
 * Alpha of an unconfluent mark. Near-opaque: the ~55% this started at was
 * inherited from the arcs, where the dimming worked because the shape had
 * ink area to spare. A 7px hollow glyph has none — dimming it did not make it
 * discreet, it made it look unfinished. Discretion here comes from *size*,
 * and the contrast budget is better spent making the small thing crisp.
 */
const QUIET_ALPHA = 'e6' // ~90%

function glyphPath(
  context: CanvasRenderingContext2D,
  glyph: DivergenceGlyph,
  cx: number,
  cy: number,
  r: number,
  dir: -1 | 1,
): void {
  context.beginPath()
  switch (glyph) {
    case 'triangle':
      // Apex points away from price, so the mark leans in the direction the
      // reading is about.
      context.moveTo(cx, cy + dir * r)
      context.lineTo(cx - r, cy - dir * r * 0.8)
      context.lineTo(cx + r, cy - dir * r * 0.8)
      context.closePath()
      break
    case 'diamond':
      context.moveTo(cx, cy - r)
      context.lineTo(cx + r, cy)
      context.lineTo(cx, cy + r)
      context.lineTo(cx - r, cy)
      context.closePath()
      break
    case 'square':
      context.rect(cx - r * 0.85, cy - r * 0.85, r * 1.7, r * 1.7)
      break
  }
}

class DivergenceMarksRenderer implements IPrimitivePaneRenderer {
  private readonly _marks: ResolvedMark[]

  constructor(marks: ResolvedMark[]) {
    this._marks = marks
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context }) => {
      for (const mark of this._marks) {
        if (mark.cx === null || mark.y === null) continue
        const dir = mark.side === 'above' ? -1 : 1
        const color = mark.strong ? mark.color : mark.color + QUIET_ALPHA
        const cy = mark.y + dir * (WHISKER_PX + GLYPH_RADIUS)

        context.lineJoin = 'round'
        context.lineCap = 'round'

        // The whisker: without it, a lone glyph floats between candles once
        // the chart is zoomed out, and which bar it belongs to is a guess.
        context.strokeStyle = mark.color + (mark.strong ? 'b3' : '8c')
        context.lineWidth = 1
        context.beginPath()
        context.moveTo(mark.cx, mark.y)
        context.lineTo(mark.cx, mark.y + dir * WHISKER_PX)
        context.stroke()

        // Hollow while unconfluent, filled once VSA agrees: a categorical
        // difference, readable at a glance, where the old arcs separated the
        // two states by 1px of stroke width.
        glyphPath(context, mark.glyph, mark.cx, cy, GLYPH_RADIUS, dir)
        if (mark.strong) {
          context.fillStyle = color
          context.fill()
        } else {
          context.strokeStyle = color
          context.lineWidth = 1.25
          context.stroke()
        }

        // ✦ confluence badge — the one thing in this layer meant to be seen
        // from across the chart.
        if (mark.strong) {
          context.fillStyle = mark.color
          context.font = 'bold 11px sans-serif'
          context.textAlign = 'center'
          context.textBaseline = mark.side === 'above' ? 'bottom' : 'top'
          context.fillText('✦', mark.cx, cy + dir * (GLYPH_RADIUS + BADGE_GAP_PX))
        }
      }
    })
  }
}

class DivergenceMarksPaneView implements IPrimitivePaneView {
  private readonly _source: DivergenceMarksPrimitive

  constructor(source: DivergenceMarksPrimitive) {
    this._source = source
  }

  zOrder(): PrimitivePaneViewZOrder {
    return 'top'
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, marks } = this._source
    if (!chart || !series || marks.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedMark[] = marks.map((mark) => ({
      cx: timeScale.timeToCoordinate(mark.time),
      y: series.priceToCoordinate(mark.price),
      side: mark.side,
      glyph: mark.glyph,
      color: mark.color,
      strong: mark.strong ?? false,
    }))
    return new DivergenceMarksRenderer(resolved)
  }
}

/**
 * Draws behavior divergences as a small glyph hanging off the candle's
 * extreme: a hairline whisker out of the wick ending in a hollow shape
 * (triangle = distribution, diamond = exhaustion, square = absorption).
 *
 * This replaced a pair of wide bezier arcs (dome above / bowl below). The arc
 * spanned ~8 candles per event, which made the rarest reading on the chart
 * also the most drawn one; the problem was its *area*, so thinning the stroke
 * would not have fixed it. What survived is the part that carries meaning: the
 * side, the candle, and — filled, plus a ✦ — whether VSA agrees.
 *
 * Attach once to the candlestick series and call `setMarks()` on each refresh.
 */
export class DivergenceMarksPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  marks: DivergenceMark[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new DivergenceMarksPaneView(this)]
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

  setMarks(marks: DivergenceMark[]): void {
    this.marks = marks
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
