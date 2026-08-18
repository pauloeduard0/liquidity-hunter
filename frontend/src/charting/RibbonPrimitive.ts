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

export interface RibbonSegmentInput {
  time: Time
  upper: number
  lower: number
  mid: number
  /** Hue channel: the standing structural trend. */
  trend: 'bullish' | 'bearish' | 'neutral'
  /** Saturation channel, 0-1: how much conviction is behind the move. */
  conviction: number
  /** Which side is *credited* with control, if any. Drawn on the envelope's
   *  edges, so a band whose hue and border disagree is a trend the other side
   *  is paying for. */
  controller: 'buyers' | 'sellers' | 'balanced'
  /** Whether a side is actually *credited* with control. Drawn as the
   *  midline's solidity, kept separate from `conviction` because the two
   *  answer different questions: how hard, versus whether it is fresh money. */
  funded: boolean
}

interface ResolvedPoint {
  x: number
  yUpper: number
  yLower: number
  yMid: number
  color: [number, number, number]
  edgeColor: [number, number, number]
  alpha: number
  funded: boolean
}

// Hue by structural trend. Neutral is not a third opinion — it is the absence
// of one, so it takes the same slate the rest of the UI uses for "no reading".
const TREND_RGB: Record<string, [number, number, number]> = {
  bullish: [38, 166, 154],
  bearish: [239, 83, 80],
  neutral: [90, 98, 118],
}
// Desaturated toward this as conviction falls. Not grey-out to invisibility:
// a trend nobody is paying for is information, not absence of data.
const QUIET_RGB: [number, number, number] = [108, 116, 134]
// Even at full conviction some grey is mixed in, so the ribbon never competes
// with the candles it sits behind.
const MAX_SATURATION = 0.85

// Who is *paying*, drawn on the edges. Deliberately the same two hues the
// trend uses: when the two agree the border vanishes into the band, and the
// only thing that catches the eye is the disagreement — a bullish band edged
// in red is a trend the sellers are funding.
const CONTROLLER_RGB: Record<string, [number, number, number]> = {
  buyers: [38, 166, 154],
  sellers: [239, 83, 80],
}

const FILL_ALPHA_MIN = 0.06
const FILL_ALPHA_MAX = 0.18
const EDGE_ALPHA = 0.5
// A credited controller gets a heavier border than the plain envelope outline,
// so the conflict reads at a glance instead of needing to be looked for.
const EDGE_WIDTH = 1
const CONTROLLED_EDGE_WIDTH = 1.75
const MID_ALPHA = 0.75
// A gap of more than this many pixels between consecutive bands is a session
// rollover, not a step — the ribbon breaks rather than drawing the jump.
const MAX_GAP_PX = 60

function mix(
  a: [number, number, number],
  b: [number, number, number],
  t: number,
): [number, number, number] {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ]
}

/** Contiguous runs sharing one colour — the ribbon is filled per run so a
 *  trend change or a funding change produces a visible seam. */
function runsOf(points: ResolvedPoint[]): ResolvedPoint[][] {
  const runs: ResolvedPoint[][] = []
  let current: ResolvedPoint[] = []
  let key = ''
  let lastX = Number.NaN
  for (const p of points) {
    const k = `${p.color.join(',')}|${p.edgeColor.join(',')}|${p.funded}`
    const broken = Number.isFinite(lastX) && Math.abs(p.x - lastX) > MAX_GAP_PX
    if (k !== key || broken) {
      // Carry the boundary point into the next run so the fill has no seam gap,
      // unless the break is a real discontinuity (session rollover).
      if (current.length > 0) {
        runs.push(current)
        current = broken ? [] : [current[current.length - 1]]
      }
      key = k
    }
    current.push(p)
    lastX = p.x
  }
  if (current.length > 0) runs.push(current)
  return runs.filter((r) => r.length >= 2)
}

class RibbonRenderer implements IPrimitivePaneRenderer {
  private readonly _points: ResolvedPoint[]

  constructor(points: ResolvedPoint[]) {
    this._points = points
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context }) => {
      for (const run of runsOf(this._points)) {
        const [r, g, b] = run[0].color
        const alpha = run[0].alpha

        // Envelope body: upper edge forward, lower edge back.
        context.beginPath()
        context.moveTo(run[0].x, run[0].yUpper)
        for (const p of run) context.lineTo(p.x, p.yUpper)
        for (let i = run.length - 1; i >= 0; i -= 1) context.lineTo(run[i].x, run[i].yLower)
        context.closePath()
        context.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`
        context.fill()

        // Edges — thin, so the band reads as a channel rather than a blob —
        // but carrying the controller's colour, which is what makes a band
        // whose hue and border disagree legible as such.
        const [er, eg, eb] = run[0].edgeColor
        context.strokeStyle = `rgba(${er}, ${eg}, ${eb}, ${EDGE_ALPHA})`
        context.lineWidth = run[0].funded ? CONTROLLED_EDGE_WIDTH : EDGE_WIDTH
        for (const edge of ['yUpper', 'yLower'] as const) {
          context.beginPath()
          context.moveTo(run[0].x, run[0][edge])
          for (const p of run) context.lineTo(p.x, p[edge])
          context.stroke()
        }

        // The VWAP itself — the population's break-even, the line that matters.
        context.strokeStyle = `rgba(${r}, ${g}, ${b}, ${MID_ALPHA})`
        context.lineWidth = 1.75
        context.setLineDash(run[0].funded ? [] : [5, 4])
        context.beginPath()
        context.moveTo(run[0].x, run[0].yMid)
        for (const p of run) context.lineTo(p.x, p.yMid)
        context.stroke()
        context.setLineDash([])
      }
    })
  }
}

class RibbonPaneView implements IPrimitivePaneView {
  private readonly _source: RibbonPrimitive

  constructor(source: RibbonPrimitive) {
    this._source = source
  }

  zOrder(): 'bottom' {
    // Behind the candles: the ribbon is the ground the price action sits on,
    // never something that covers it.
    return 'bottom'
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, series, segments } = this._source
    if (!chart || !series || segments.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedPoint[] = []
    for (const s of segments) {
      const x = timeScale.timeToCoordinate(s.time)
      const yUpper = series.priceToCoordinate(s.upper)
      const yLower = series.priceToCoordinate(s.lower)
      const yMid = series.priceToCoordinate(s.mid)
      if (x === null || yUpper === null || yLower === null || yMid === null) continue
      const base = TREND_RGB[s.trend] ?? TREND_RGB.neutral
      const t = Math.min(1, Math.max(0, s.conviction))
      const color = mix(QUIET_RGB, base, t * MAX_SATURATION)
      // With no side credited there is nothing to say about who is paying, so
      // the edge stays the band's own colour and the envelope reads as before.
      const controlBase = CONTROLLER_RGB[s.controller]
      resolved.push({
        x,
        yUpper,
        yLower,
        yMid,
        color,
        edgeColor: controlBase ? mix(QUIET_RGB, controlBase, t * MAX_SATURATION) : color,
        alpha: FILL_ALPHA_MIN + t * (FILL_ALPHA_MAX - FILL_ALPHA_MIN),
        funded: s.funded,
      })
    }

    if (resolved.length < 2) return null
    return new RibbonRenderer(resolved)
  }
}

/**
 * Draws the Tide ribbon on the main pane: the VWAP ±1σ envelope filled per
 * candle, hue carrying the structural trend, saturation carrying how much
 * conviction is behind it, and the edges carrying which side is credited with
 * control (see `utils/tideRibbon.ts` for why the layers are separate channels
 * rather than one average).
 *
 * Rendered beneath the candles. Attach once to the candlestick series and call
 * `setSegments()` on each refresh.
 */
export class RibbonPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  segments: RibbonSegmentInput[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new RibbonPaneView(this)]
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

  setSegments(segments: RibbonSegmentInput[]): void {
    this.segments = segments
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
