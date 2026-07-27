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

import {
  VP_BAR_MAX_WIDTH,
  VP_DELTA_BUY_COLOR,
  VP_DELTA_SELL_COLOR,
  VP_LEVEL_GAP,
  VP_LEVEL_LINE_COLOR,
  VP_MIN_BAND_PX,
  VP_POC_COLOR,
  VP_POC_LINE_WIDTH,
  VP_RIGHT_MARGIN,
  VP_VA_COLOR,
  VP_VA_LINE_GAP,
  VP_VA_LINE_WIDTH,
} from '../theme'

export interface VolumeProfileBar {
  priceLow: number
  priceHigh: number
  /** Base-asset volume attributed to this band. */
  volume: number
  /** Taker-buy share of `volume`. Estimated per candle, not per trade. */
  buyVolume: number
  inValueArea: boolean
  isPoc: boolean
}

export interface VolumeProfileLevels {
  poc: number
  valueAreaLow: number
  valueAreaHigh: number
  /** First candle of the lookback — where the POC/VAH/VAL lines start. */
  startTime: Time
}

/** How the histogram bands are coloured. */
export type VolumeProfileMode = 'value-area' | 'delta'

interface ResolvedBar {
  yTop: number
  height: number
  /** Bar length in px, proportional to volume. */
  length: number
  color: string
}

/**
 * Draws volume-at-price as a thin-line histogram floating on the **right** of
 * the main pane, growing leftward from an anchor near the price scale — the
 * layout of the classic TradingView volume-profile studies.
 *
 * Two colouring modes:
 *
 * - `value-area` (default) reproduces the reference study: grey outside the
 *   value area, blue inside it, red at the POC, with POC/VAH/VAL lines running
 *   back across the lookback to meet their own band.
 * - `delta` colours each band by which side was the aggressor there. That split
 *   is inferred per candle rather than observed per trade (see
 *   `VolumeProfile.delta_estimated`), so it is a deliberate second read behind
 *   a modifier-click, never the default picture.
 *
 * The anchor is the pane's right edge rather than a bar offset into the future:
 * the panes here are synced by logical range, so reserving future space would
 * have to move every pane. Anchoring to the edge keeps the profile on screen at
 * any zoom or scroll.
 *
 * Attach once to the candlestick series and call `setProfile()` on each refresh.
 */
export class VolumeProfilePrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null
  series: ISeriesApi<SeriesType> | null = null
  bars: VolumeProfileBar[] = []
  levels: VolumeProfileLevels | null = null
  mode: VolumeProfileMode = 'value-area'

  private readonly _paneViews: readonly IPrimitivePaneView[] = [new VolumeProfilePaneView(this)]
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

  setProfile(
    bars: VolumeProfileBar[],
    levels: VolumeProfileLevels | null,
    mode: VolumeProfileMode = 'value-area',
  ): void {
    this.bars = bars
    this.levels = levels
    this.mode = mode
    this._requestUpdate?.()
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }
}

function bandColor(bar: VolumeProfileBar, mode: VolumeProfileMode): string {
  if (bar.isPoc) return VP_POC_COLOR
  if (mode === 'delta') {
    const buyShare = bar.volume > 0 ? bar.buyVolume / bar.volume : 0.5
    return buyShare >= 0.5 ? VP_DELTA_BUY_COLOR : VP_DELTA_SELL_COLOR
  }
  return bar.inValueArea ? VP_VA_COLOR : VP_LEVEL_LINE_COLOR
}

/**
 * Collapse adjacent bands until each one is thick enough to read.
 *
 * The profile covers only its lookback's price range, while the pane's scale
 * spans the whole visible series — so on a chart showing far more history than
 * the profile, 200 bands land inside a fraction of the pane height and every
 * one of them rounds to a single pixel. The histogram then reads as a solid
 * block and the hatched line look, along with the value-area colouring, is
 * lost. Merging by *rendered* thickness keeps the picture honest at any zoom:
 * volumes add, and a merged band inherits value-area/POC membership from any
 * member, so the POC never disappears into its neighbours.
 */
function mergeToVisibleBands(
  bars: VolumeProfileBar[],
  series: ISeriesApi<SeriesType>,
): VolumeProfileBar[] {
  const first = bars[0]
  const last = bars[bars.length - 1]
  const yLow = series.priceToCoordinate(first.priceLow)
  const yHigh = series.priceToCoordinate(last.priceHigh)
  if (yLow === null || yHigh === null) return bars

  const bandPx = Math.abs(yLow - yHigh) / bars.length
  const groupSize = Math.ceil(VP_MIN_BAND_PX / Math.max(bandPx, 0.01))
  if (groupSize <= 1) return bars

  const merged: VolumeProfileBar[] = []
  for (let i = 0; i < bars.length; i += groupSize) {
    const group = bars.slice(i, i + groupSize)
    merged.push({
      priceLow: group[0].priceLow,
      priceHigh: group[group.length - 1].priceHigh,
      volume: group.reduce((sum, b) => sum + b.volume, 0),
      buyVolume: group.reduce((sum, b) => sum + b.buyVolume, 0),
      inValueArea: group.some((b) => b.inValueArea),
      isPoc: group.some((b) => b.isPoc),
    })
  }
  return merged
}

class VolumeProfilePaneView implements IPrimitivePaneView {
  private readonly _source: VolumeProfilePrimitive

  constructor(source: VolumeProfilePrimitive) {
    this._source = source
  }

  renderer(): IPrimitivePaneRenderer | null {
    const { series, chart, bars: rawBars, levels, mode } = this._source
    if (!series || rawBars.length === 0) return null

    const bars = mergeToVisibleBands(rawBars, series)
    if (bars.length === 0) return null

    let peak = 0
    for (const bar of bars) if (bar.volume > peak) peak = bar.volume
    if (peak <= 0) return null

    const resolved: ResolvedBar[] = []
    // Bar lengths for the three levels, so their lines can stop just short of
    // the band they point at (as the reference study does).
    let pocLength = 0
    let vahLength = 0
    let valLength = 0

    for (const bar of bars) {
      if (bar.volume <= 0) continue
      const yTop = series.priceToCoordinate(bar.priceHigh)
      const yBottom = series.priceToCoordinate(bar.priceLow)
      if (yTop === null || yBottom === null) continue

      const top = Math.min(yTop, yBottom)
      const span = Math.abs(yBottom - yTop)
      // A hairline gap between bands gives the hatched look of the reference,
      // but a band must never vanish when the price scale is compressed.
      const height = Math.max(span - VP_LEVEL_GAP, 1)
      const length = (bar.volume / peak) * VP_BAR_MAX_WIDTH

      if (levels !== null) {
        if (bar.isPoc) pocLength = length
        if (levels.valueAreaHigh >= bar.priceLow && levels.valueAreaHigh <= bar.priceHigh) {
          vahLength = length
        }
        if (levels.valueAreaLow >= bar.priceLow && levels.valueAreaLow <= bar.priceHigh) {
          valLength = length
        }
      }

      resolved.push({ yTop: top, height, length, color: bandColor(bar, mode) })
    }

    if (resolved.length === 0) return null

    const timeScale = chart?.timeScale()
    const startX = levels === null ? null : (timeScale?.timeToCoordinate(levels.startTime) ?? null)

    const levelLines =
      levels === null
        ? null
        : {
            startX,
            poc: { y: series.priceToCoordinate(levels.poc), barLength: pocLength },
            vah: { y: series.priceToCoordinate(levels.valueAreaHigh), barLength: vahLength },
            val: { y: series.priceToCoordinate(levels.valueAreaLow), barLength: valLength },
          }

    return new VolumeProfileRenderer(resolved, levelLines)
  }
}

interface LevelLine {
  y: number | null
  barLength: number
}

type LevelLines = {
  startX: number | null
  poc: LevelLine
  vah: LevelLine
  val: LevelLine
} | null

class VolumeProfileRenderer implements IPrimitivePaneRenderer {
  private readonly _bars: ResolvedBar[]
  private readonly _levels: LevelLines

  constructor(bars: ResolvedBar[], levels: LevelLines) {
    this._bars = bars
    this._levels = levels
  }

  draw(target: CanvasRenderingTarget2D): void {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const anchorX = mediaSize.width - VP_RIGHT_MARGIN

      for (const bar of this._bars) {
        context.fillStyle = bar.color
        context.fillRect(anchorX - bar.length, bar.yTop, bar.length, bar.height)
      }

      if (this._levels === null) return
      const { startX, poc, vah, val } = this._levels
      // Lines run back over the lookback the profile was built from. When its
      // first candle is scrolled off, they simply start at the pane edge.
      const left = startX === null ? 0 : Math.max(startX, 0)

      for (const [level, color, width] of [
        [vah, VP_VA_COLOR, VP_VA_LINE_WIDTH],
        [val, VP_VA_COLOR, VP_VA_LINE_WIDTH],
        [poc, VP_POC_COLOR, VP_POC_LINE_WIDTH],
      ] as const) {
        if (level.y === null) continue
        // Stop just short of the band the line points at, so the two read as
        // one mark rather than the line burying its own bar.
        const right = anchorX - level.barLength - VP_VA_LINE_GAP
        if (right <= left) continue
        context.strokeStyle = color
        context.lineWidth = width
        context.beginPath()
        context.moveTo(left, level.y + 0.5)
        context.lineTo(right, level.y + 0.5)
        context.stroke()
      }
    })
  }
}
