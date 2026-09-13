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

export interface HuntWindow {
  /** Counter-trend flip candle (the hunt window opens here). */
  x0: Time
  /** Capture time, or a far-future sentinel to clamp to the right edge. */
  x1: Time
  /** Edge line + label color. */
  color: string
  /** Translucent full-height fill. */
  fillColor: string
  /**
   * Direction the liquidity was raided in — the hunted side, drawn as a bold
   * arrow ahead of the label so direction reads at a glance without the word:
   * 'up' = shorts hunted (stops above), 'down' = longs hunted (stops below).
   */
  arrow?: 'up' | 'down'
  label?: string
  /**
   * Regime texture: 'solid' (counter-trend hunt) or 'hatched' (aligned
   * continuation — a slim solid strip along the top of the band), so the two
   * regimes read apart even when they share a direction hue.
   */
  pattern?: 'solid' | 'hatched'
  /** Live (still open) window: stronger edge and a bolder label. */
  live?: boolean
}

interface ResolvedWindow {
  x0: number | null
  x1: number | null
  color: string
  fillColor: string
  arrow?: 'up' | 'down'
  label?: string
  pattern: 'solid' | 'hatched'
  live: boolean
}

const CONT_STRIP = 3

class HuntWindowRenderer implements IPrimitivePaneRenderer {
  private readonly _windows: ResolvedWindow[]

  constructor(windows: ResolvedWindow[]) {
    this._windows = windows
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      for (const win of this._windows) {
        const left = Math.max(0, win.x0 ?? 0)
        const right = Math.min(mediaSize.width, win.x1 ?? mediaSize.width)
        if (left >= right) continue

        // Full-pane-height shading: the window is a time span, not a price box.
        context.fillStyle = win.fillColor
        context.fillRect(left, 0, right - left, mediaSize.height)

        if (win.pattern === 'hatched') {
          // Continuation texture: a slim solid strip along the top of the band
          // (one fillRect — the diagonal hatch it replaces drew hundreds of
          // full-height lines per band per frame and made panning drag).
          context.fillStyle = win.color + (win.live ? '99' : '55')
          context.fillRect(left, 0, right - left, CONT_STRIP)
        }

        // Dashed vertical edge at the flip candle (and at the capture candle
        // when the window is closed inside the pane). A live window gets a
        // stronger, longer-dashed edge.
        context.strokeStyle = win.color + (win.live ? 'aa' : '66')
        context.lineWidth = win.live ? 1.5 : 1
        context.setLineDash(win.live ? [6, 3] : [3, 3])
        context.beginPath()
        context.moveTo(left + 0.5, 0)
        context.lineTo(left + 0.5, mediaSize.height)
        if (win.x1 !== null && right < mediaSize.width) {
          context.moveTo(right - 0.5, 0)
          context.lineTo(right - 0.5, mediaSize.height)
        }
        context.stroke()
        context.setLineDash([])

        const PADDING = win.pattern === 'hatched' ? 4 + CONT_STRIP : 4
        context.textBaseline = 'top'
        context.textAlign = 'left'
        context.fillStyle = win.color
        let cursor = left + PADDING
        // Direction cue first: a bold arrow pointing to the raided side, the
        // primary read. The regime label follows in smaller type, and only
        // when the band is wide enough to hold it — overlapping labels on a
        // run of narrow bands are worse than none.
        if (win.arrow) {
          context.font = 'bold 13px sans-serif'
          const glyph = win.arrow === 'up' ? '▲' : '▼'
          context.fillText(glyph, cursor, PADDING - 1)
          cursor += context.measureText(glyph).width + 3
        }
        if (win.label) {
          context.font = win.live ? 'bold 10px sans-serif' : '10px sans-serif'
          const width = context.measureText(win.label).width
          if (cursor + width <= right - PADDING) {
            context.fillText(win.label, cursor, PADDING)
          }
        }
      }
    })
  }
}

class HuntWindowPaneView implements IPrimitivePaneView {
  private readonly _source: HuntWindowPrimitive

  constructor(source: HuntWindowPrimitive) {
    this._source = source
  }

  // Background shading: paint beneath the candles and every other overlay.
  zOrder(): PrimitivePaneViewZOrder {
    return 'bottom'
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { chart, windows } = this._source
    if (!chart || windows.length === 0) return null

    const timeScale = chart.timeScale()
    const resolved: ResolvedWindow[] = windows.map((win) => ({
      // null = off-screen; the renderer clamps to the pane edges (a window
      // opened before the visible range still shades from the left edge, and
      // a still-open window runs to the right edge).
      x0: timeScale.timeToCoordinate(win.x0),
      x1: timeScale.timeToCoordinate(win.x1),
      color: win.color,
      fillColor: win.fillColor,
      arrow: win.arrow,
      label: win.label,
      pattern: win.pattern ?? 'solid',
      live: win.live ?? false,
    }))
    return new HuntWindowRenderer(resolved)
  }
}

/**
 * Shades the liquidity-hunt window as a full-height vertical band: from the
 * counter-trend flip candle to the capture that concluded the hunt (or the
 * right edge while it is still running). Attach once to the candlestick
 * series and call `setWindows()` on each data refresh.
 */
export class HuntWindowPrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  windows: HuntWindow[] = []

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new HuntWindowPaneView(this)]
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

  setWindows(windows: HuntWindow[]): void {
    this.windows = windows
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}
