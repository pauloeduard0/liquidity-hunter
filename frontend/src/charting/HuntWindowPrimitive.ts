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
  label?: string
}

interface ResolvedWindow {
  x0: number | null
  x1: number | null
  color: string
  fillColor: string
  label?: string
}

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

        // Dashed vertical edge at the flip candle (and at the capture candle
        // when the window is closed inside the pane).
        context.strokeStyle = win.color + '66'
        context.lineWidth = 1
        context.setLineDash([3, 3])
        context.beginPath()
        context.moveTo(left + 0.5, 0)
        context.lineTo(left + 0.5, mediaSize.height)
        if (win.x1 !== null && right < mediaSize.width) {
          context.moveTo(right - 0.5, 0)
          context.lineTo(right - 0.5, mediaSize.height)
        }
        context.stroke()
        context.setLineDash([])

        if (win.label) {
          const PADDING = 4
          context.font = '10px sans-serif'
          context.textBaseline = 'top'
          context.textAlign = 'left'
          context.fillStyle = win.color
          context.fillText(win.label, left + PADDING, PADDING)
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
      label: win.label,
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
