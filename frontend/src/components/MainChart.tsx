import { useEffect, useMemo, useRef } from 'react'
import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'

import { LineLabelsPrimitive, type LineLabel } from '../charting/LineLabelsPrimitive'
import { HuntWindowPrimitive, type HuntWindow } from '../charting/HuntWindowPrimitive'
import { DivergenceArcPrimitive, type DivergenceArc } from '../charting/DivergenceArcPrimitive'
import { POIBoxesPrimitive, type POIBox } from '../charting/POIBoxesPrimitive'
import { HeatmapStripPrimitive, type HeatmapBand } from '../charting/HeatmapStripPrimitive'
import {
  VolumeProfilePrimitive,
  type VolumeProfileBar,
  type VolumeProfileMode,
} from '../charting/VolumeProfilePrimitive'
import { EqlZonesPrimitive, type EqlZoneInput } from '../charting/EqlZonesPrimitive'
import { RibbonPrimitive } from '../charting/RibbonPrimitive'
import { buildPhase, buildRibbon, structureTrendByCandle } from '../utils/tideRibbon'
import type { DefendedMark } from '../utils/defendedLevels'
import { buildDefenceLevels, buildDefendedMarks } from '../utils/defendedLevels'
import type { BehaviorDivergence, DashboardData, LiquidityGrab, LiquiditySide, LiquidityZone, LiquidityZoneType, ManipulationCycle, MarketStructure, OIParticipation, POIZone, SupertrendBreak, SupertrendPoint, VolumeSpreadSignal, VWAPSeries } from '../types/dashboard'
import {
  CANDLE_DOWN_COLOR,
  CANDLE_UP_COLOR,
  CONSOLIDATION_BOX_STYLES,
  DARK_BG,
  DEFAULT_ZONE_COLOR,
  FONT_COLOR,
  DIVERGENCE_STYLES,
  MANIPULATION_BOX_STYLES,
  POI_BOX_STYLES,
  RSI_DIV_BEARISH_COLOR,
  RSI_DIV_BULLISH_COLOR,
  RSI_LINE_COLOR,
  RSI_OVERBOUGHT_COLOR,
  RSI_OVERSOLD_COLOR,
  STRUCTURE_DIRECTION_COLORS,
  STRUCTURE_EVENT_STYLES,
  TREND_ICONS,
  VOLUME_DOWN_COLOR,
  VOLUME_UP_COLOR,
  VSA_STYLES,
  ZONE_COLORS,
  ZONE_TYPE_LABELS,
  SUPERTREND_DOWN_COLOR,
  SUPERTREND_LINE_WIDTH,
  SUPERTREND_STOP_RUN_COLOR,
  DEFENDED_LEVEL_COLOR,
  SUPERTREND_UP_COLOR,
  VP_BAR_MAX_PX,
  VP_BAR_MIN_PX,
  VP_MAX_LENGTH_BARS,
  VP_RIGHT_MARGIN,
  VWAP_ANCHORED_COLORS,
  VWAP_ANCHORED_LINE_WIDTH,
  VWAP_BAND_1_COLOR,
  VWAP_BAND_2_COLOR,
  VWAP_COLOR,
  VWAP_LINE_WIDTH,
} from '../theme'
import {
  markChartDragEnd,
  markChartDragStart,
  markChartGesture,
} from '../utils/chartActivity'
import { setChartTimezoneMode, toChartTime } from '../utils/chartTime'

// Standing pools are drawn as *targets*: the ones price can still hunt. So
// the selection is the nearest few beyond price on each side, not the top of
// a global score — the composite score mixes distance with touch and
// timeframe weight, so a far pool with a strong volume history outranks the
// one price is actually walking into, and ranking both sides together lets
// one of them take every slot. Nearest-per-side is ordinal, which is what
// this needs: a fixed distance filter (3 ATR) was measured on the defended
// levels work and removed a whole real staircase at 6.9/9.7/12 ATR.
const NEAREST_POOLS_PER_SIDE = 2
// How many already-taken pools stay on the chart. Measured across six
// symbol/timeframe combos, a 1200-candle window accumulates 48-80 grabbed
// pools -- drawing them all would bury the standing ones, and a pool grabbed
// hundreds of candles ago is no longer anyone's memory.
const MAX_GRABBED_POOLS = 3
const MAX_INTERNAL_SWEEPS = 3
// A sweep is a momentary stop-grab at a wick, not a standing reference: draw
// it as a short segment anchored at the sweep candle rather than a line that
// runs to the next event / chart edge.
const SWEEP_LINE_CANDLES = 6
// A confirmed BOS marks a staircase step: the level it broke and the candle
// that closed through it. Once that close prints, the level is consumed, so the
// line stops a few candles past the break (room for the label) instead of
// running to the next superseding event / chart edge — which was what made the
// staircase overlap itself and clutter the pane. A superseding event still cuts
// it shorter; provisional (`BOS?`) marks are unaffected, they are the live read.
const BOS_LINE_TRAIL_CANDLES = 4
// VSA 'recent' mode shows only signals within the last N candles — recent
// context without cluttering the whole history.
const VSA_RECENT_CANDLES = 120

// Suffix appended to a structure event label when the OI analysis qualified
// it: ⊕ new money behind the break, ⊖ break driven by position unwinding,
// ⚡ sweep that flushed leveraged positions. FLAT adds nothing.
const OI_PARTICIPATION_SUFFIX: Record<OIParticipation, string> = {
  new_money: '⊕',
  covering: '⊖',
  flush: '⚡',
  flat: '',
}

const DELTA_CHART_RATIO = 0.16
const RSI_CHART_RATIO = 0.16
const CONTROL_CHART_RATIO = 0.14
const MIN_TOTAL_HEIGHT = 500
const PRICE_SCALE_MIN_WIDTH = 110

// Colors for the control oscillator (CVD × OI). Two channels on one bar:
// the *hue* is the aggressor side (green buying / red selling), the *fill* is
// the nature of the flow -- solid where open interest confirms fresh money
// (`*_buildup`), washed out where the aggression is only positions closing
// (`short_covering` / `long_liquidation`). A rally carried by shorts covering
// therefore draws as a hollow-looking green bar: price is being pushed, but by
// people leaving, not by anyone arriving. A histogram series has no stroke, so
// "hollow" is rendered as the same hue at low alpha.
const CONTROL_BUYERS_COLOR = '#26a69a'
const CONTROL_SELLERS_COLOR = '#ef5350'
const CONTROL_BALANCED_COLOR = '#4a5163'
// Exit-flow fill: same hue, ~30% alpha.
const CONTROL_UNWIND_ALPHA = '4d'
const CONTROL_REGIME_COLORS: Record<string, string> = {
  long_buildup: CONTROL_BUYERS_COLOR,
  short_buildup: CONTROL_SELLERS_COLOR,
  short_covering: `${CONTROL_BUYERS_COLOR}${CONTROL_UNWIND_ALPHA}`,
  long_liquidation: `${CONTROL_SELLERS_COLOR}${CONTROL_UNWIND_ALPHA}`,
  flat: CONTROL_BALANCED_COLOR,
}

// The phase line sits over those bars. It gets its own hue rather than
// repeating the structural trend the ribbon already carries -- the same colour
// twice would add no information, and the line's job here is to be readable
// *against* the control bars, not to restate them.
const PHASE_NEUTRAL_COLOR = '#e0b341'
// The fill between the line and the zero baseline. Faint on purpose: it tints
// which side of break-even price is on without competing with the bars the
// line is meant to be read against.
const PHASE_ABOVE_FILL = 'rgba(38, 166, 154, 0.28)'
const PHASE_BELOW_FILL = 'rgba(239, 83, 80, 0.28)'
const PHASE_FILL_FADE = 'rgba(0, 0, 0, 0)'
// The +/-1 sigma rails, dim enough to read as a scale rather than as a level.
const PHASE_RAIL_COLOR = 'rgba(224, 179, 65, 0.28)'

// Split the available height across the panes. The volume-delta + RSI panes are
// one group (`showIndicators`); the control oscillator toggles *independently*
// (`showControl`). Each hidden pane collapses to 0 and the main candlestick
// pane absorbs the freed height, so opening only the control pane shows only it.
function paneHeights(totalHeight: number, showIndicators: boolean, showControl: boolean) {
  const deltaHeight = showIndicators ? Math.round(totalHeight * DELTA_CHART_RATIO) : 0
  const rsiHeight = showIndicators ? Math.round(totalHeight * RSI_CHART_RATIO) : 0
  const controlHeight = showControl ? Math.round(totalHeight * CONTROL_CHART_RATIO) : 0
  const mainHeight = totalHeight - deltaHeight - rsiHeight - controlHeight
  return { mainHeight, deltaHeight, controlHeight, rsiHeight }
}

// Which pane carries the visible time axis. RSI carries it whenever the
// indicator group is open (the long-standing, well-tested path — labels fall
// back to it). Otherwise the *main* pane keeps its own axis, even when the
// control oscillator is open below it: the control pane never carries the axis,
// so the BOS/CHoCH label primitive always resolves time->x from a live,
// perfectly-synced scale (the main's own when visible, else RSI) and never from
// the control pane — which was desyncing labels on a timeframe switch.
function axisVisibility(showIndicators: boolean) {
  return {
    main: !showIndicators,
    control: false,
    rsi: showIndicators,
  }
}

const RSI_PERIOD = 14
const DIV_PIVOT_LOOKBACK = 5
const DIV_RANGE_LOWER = 5
const DIV_RANGE_UPPER = 60

// Series that only ever grow (or move) at their tail, one point per candle.
// They are excluded from the redraw signature and collapsed to their length:
// while a candle is still forming they change on every poll, and redrawing the
// whole chart for the forming bar's last point is precisely the cost this
// signature exists to avoid. The forming bar itself is kept current by the
// incremental `update()` effect below; these lag by at most one candle, since a
// closed candle changes the length and forces a full redraw.
const TAIL_SERIES_FIELDS = new Set([
  'candles',
  'vwap',
  'anchored_vwaps',
  'supertrend',
  'market_control',
])

/**
 * A string that changes exactly when the chart's *drawn* content must be
 * rebuilt — every structural payload, plus the candle window's shape (length
 * and end points), but not the forming candle's OHLCV.
 *
 * The overlay rebuild (dozens of line series destroyed and recreated, four
 * panes of `setData`, every primitive refreshed) is the expensive part of a
 * refresh, and on a 5s poll it almost always redraws an identical picture. The
 * big effect keys off this instead of the `data` object identity, so a poll
 * that only advanced the live price does no work beyond the tail update.
 */
function drawSignature(data: DashboardData): string {
  const { candles } = data
  const shape = `${candles.length}|${candles[0]?.timestamp ?? ''}|${candles[candles.length - 1]?.timestamp ?? ''}`
  const rest = JSON.stringify(data, (key, value) =>
    TAIL_SERIES_FIELDS.has(key) ? (Array.isArray(value) ? value.length : value == null ? null : 1) : value,
  )
  return `${shape}|${rest}`
}

// The chart's opening view — what TradingView's "reset chart view" (Alt+R)
// lands on. `fitContent` is the wrong default here: the window is 1200 candles
// wide, so fitting it all gives about a pixel per bar and the candles read as a
// smear. Instead show the most recent stretch at a legible bar width, anchored
// to the right with a small margin, and let the price scale auto-fit to it.
const RESET_BAR_SPACING_PX = 8
const RESET_RIGHT_MARGIN_BARS = 6
const RESET_MIN_BARS = 60

/**
 * Keeps an overlay out of the price scale's autoscale.
 *
 * The candles are what the scale should frame. An annotation drawn at an old
 * level — a structure line with no terminating event still running to the right
 * edge, a swept pool, a band segment — has data points inside the visible
 * window at a price far from it, and autoscale obediently zooms out to include
 * them: on ETH 1H a June reference near 1712 opened the chart with the whole
 * price action squeezed into a third of the pane. Overlays annotate the frame;
 * they no longer set it.
 */
const OVERLAY_SCALE_EXEMPT = { autoscaleInfoProvider: () => null }

/**
 * How many bar-widths the volume profile occupies on the right of the pane.
 * Mirrors `VolumeProfilePrimitive`'s own length formula (bar units, bounded in
 * pixels) so the space the chart reserves is exactly the space the histogram
 * takes — otherwise the profile paints over the most recent candles.
 */
function volumeProfileReservedBars(barSpacing: number): number {
  if (barSpacing <= 0) return 0
  const widthPx =
    Math.min(Math.max(VP_MAX_LENGTH_BARS * barSpacing, VP_BAR_MIN_PX), VP_BAR_MAX_PX) +
    VP_RIGHT_MARGIN
  return Math.ceil(widthPx / barSpacing)
}

/** Logical range for the opening view of a series with `count` candles. */
function resetViewRange(
  count: number,
  paneWidthPx: number,
  reservedBars = 0,
): { from: number; to: number } {
  const fitsOnScreen = paneWidthPx > 0 ? Math.floor(paneWidthPx / RESET_BAR_SPACING_PX) : count
  const visible = Math.min(count, Math.max(RESET_MIN_BARS, fitsOnScreen - reservedBars))
  return { from: count - visible, to: count - 1 + RESET_RIGHT_MARGIN_BARS + reservedBars }
}

function computeRSI(closes: number[], period: number): (number | null)[] {
  const rsi: (number | null)[] = []
  if (closes.length < period + 1) {
    return closes.map(() => null)
  }

  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1]
    if (change > 0) avgGain += change
    else avgLoss -= change
  }
  avgGain /= period
  avgLoss /= period

  for (let i = 0; i < period; i++) rsi.push(null)
  rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))

  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1]
    const gain = change > 0 ? change : 0
    const loss = change < 0 ? -change : 0
    avgGain = (avgGain * (period - 1) + gain) / period
    avgLoss = (avgLoss * (period - 1) + loss) / period
    rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss))
  }

  return rsi
}

interface Divergence {
  type: 'bullish' | 'bearish'
  startIndex: number
  endIndex: number
  startRSI: number
  endRSI: number
}

function findPivots(
  values: (number | null)[],
  lookback: number,
  comparator: (val: number, neighbor: number) => boolean,
): number[] {
  const pivots: number[] = []
  for (let i = lookback; i < values.length - lookback; i++) {
    const v = values[i]
    if (v === null) continue
    let isPivot = true
    for (let j = 1; j <= lookback; j++) {
      const left = values[i - j]
      const right = values[i + j]
      if (left === null || right === null || !comparator(v, left) || !comparator(v, right)) {
        isPivot = false
        break
      }
    }
    if (isPivot) pivots.push(i)
  }
  return pivots
}

function detectDivergences(
  closes: number[],
  rsiValues: (number | null)[],
): Divergence[] {
  const divergences: Divergence[] = []

  const pivotHighs = findPivots(rsiValues, DIV_PIVOT_LOOKBACK, (v, n) => v > n)
  const pivotLows = findPivots(rsiValues, DIV_PIVOT_LOOKBACK, (v, n) => v < n)

  // Bearish: price HH + RSI LH, RSI > 50
  for (let i = 1; i < pivotHighs.length; i++) {
    const curr = pivotHighs[i]
    const prev = pivotHighs[i - 1]
    if (curr - prev < DIV_RANGE_LOWER || curr - prev > DIV_RANGE_UPPER) continue
    const currRSI = rsiValues[curr]!
    const prevRSI = rsiValues[prev]!
    if (closes[curr] > closes[prev] && currRSI < prevRSI && currRSI > 50) {
      divergences.push({ type: 'bearish', startIndex: prev, endIndex: curr, startRSI: prevRSI, endRSI: currRSI })
    }
  }

  // Bullish: price LL + RSI HL, RSI < 50
  for (let i = 1; i < pivotLows.length; i++) {
    const curr = pivotLows[i]
    const prev = pivotLows[i - 1]
    if (curr - prev < DIV_RANGE_LOWER || curr - prev > DIV_RANGE_UPPER) continue
    const currRSI = rsiValues[curr]!
    const prevRSI = rsiValues[prev]!
    if (closes[curr] < closes[prev] && currRSI > prevRSI && currRSI < 50) {
      divergences.push({ type: 'bullish', startIndex: prev, endIndex: curr, startRSI: prevRSI, endRSI: currRSI })
    }
  }

  return divergences
}

// Lightweight Charts defaults the candlestick series to `precision: 2,
// minMove: 0.01`, so a low-priced pair (ETHBTC ~0.03, ENAUSDT sub-1) snaps to
// 0.01 ticks and every intrabar move collapses into a handful of levels. Derive
// the price format from the current magnitude so the axis keeps ~5 significant
// digits: precision = 4 - floor(log10(ref)), clamped to [2, 8].
function priceFormatFor(ref: number): { precision: number; minMove: number } {
  if (!Number.isFinite(ref) || ref <= 0) return { precision: 2, minMove: 0.01 }
  const exponent = Math.floor(Math.log10(ref))
  const precision = Math.min(8, Math.max(2, 4 - exponent))
  return { precision, minMove: 10 ** -precision }
}

function lineFrom(
  startTime: UTCTimestamp,
  lastCandleTime: UTCTimestamp,
  value: number,
  minTime?: UTCTimestamp,
) {
  // Clamp the start to the first visible candle. Overlay series live only on
  // the main chart; a point before candles[0] (e.g. a CHoCH whose
  // reference_timestamp predates the visible window — its pivot can come from
  // the buffered bootstrap series) would add an extra slot to the main chart's
  // time scale that the delta/RSI panes lack, shifting their logical-range sync
  // by one bar and desyncing the crosshair.
  const start = minTime !== undefined && startTime < minTime ? minTime : startTime
  return start < lastCandleTime
    ? [
        { time: start, value },
        { time: lastCandleTime, value },
      ]
    : [{ time: lastCandleTime, value }]
}

// If a provisional CHoCH is later invalidated, returns the timestamp of the
// `choch_failed` event that paired with it (a same-direction failure firing
// before any other same-direction CHoCH intervenes); otherwise `null`. A failed
// CHoCH never actually reversed structure — the prior trend resumed — so it must
// stay transparent to *other* lines' termination (it doesn't cut them), while
// its *own* line stops at this failure point. A *fizzle marker* (a provisional
// `choch_failed`) is different: the state-machine trend never flipped back, so
// the CHoCH still genuinely reversed structure and must keep cutting other
// lines — only its own line stops at the reclaim. Callers pass
// `includeFizzle: false` when deciding transparency.
function failedChochTime(
  choch: MarketStructure,
  allEvents: MarketStructure[],
  { includeFizzle = true }: { includeFizzle?: boolean } = {},
): UTCTimestamp | null {
  if (choch.event !== 'change_of_character') return null
  const chochTime = toChartTime(choch.timestamp)
  const failedTimes = allEvents
    .filter(
      (e) =>
        e.scope === choch.scope &&
        e.event === 'choch_failed' &&
        (includeFizzle || !e.provisional) &&
        e.direction === choch.direction &&
        toChartTime(e.timestamp) > chochTime,
    )
    .map((e) => toChartTime(e.timestamp))
  if (failedTimes.length === 0) return null
  const firstFailed = Math.min(...failedTimes) as UTCTimestamp
  // Pair the failure with its CHoCH: ignore it if a later same-direction CHoCH
  // sits between them (that one owns the failure instead).
  const interveningChoch = allEvents.some(
    (e) =>
      e.scope === choch.scope &&
      e.event === 'change_of_character' &&
      e.direction === choch.direction &&
      toChartTime(e.timestamp) > chochTime &&
      toChartTime(e.timestamp) < firstFailed,
  )
  return interveningChoch ? null : firstFailed
}

function isFailedChoch(choch: MarketStructure, allEvents: MarketStructure[]): boolean {
  return failedChochTime(choch, allEvents, { includeFizzle: false }) !== null
}

function structureLineEndTime(
  event: MarketStructure,
  allEvents: MarketStructure[],
  lastCandleTime: UTCTimestamp,
  bosTrailEnd?: UTCTimestamp,
): UTCTimestamp {
  const eventTime = toChartTime(event.timestamp)

  if (event.event === 'change_of_character') {
    // A CHoCH line runs until the next real CHoCH supersedes it — of *either*
    // direction. An opposite-direction CHoCH is a reversal that clears the
    // stale reference; a *same*-direction CHoCH is simply a newer reference for
    // that side, so the older one stops there rather than both running to the
    // edge (the case where the internal trend briefly flipped and back without
    // surfacing a drawn opposite CHoCH, emitting two same-direction CHoCHs).
    // Failed/provisional CHoCHs don't count — one that never took hold or is
    // still forming isn't the active reference.
    const candidates = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'change_of_character' &&
          !other.provisional &&
          !isFailedChoch(other, allEvents) &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    // If this CHoCH itself failed, its line stops at the failure point.
    const ownFailure = failedChochTime(event, allEvents)
    if (ownFailure !== null) candidates.push(ownFailure)
    // A later same-direction BOS whose reference sits on the *wrong side* of
    // this CHoCH's level (below it for a bullish CHoCH, above for bearish)
    // means the trend collapsed through the level and rebuilt from the other
    // side — an excursion whose opposite CHoCH failed, so it is transparent
    // above, yet the old reversal reference is plainly stale (ENA 4H 2026-06:
    // a bullish CHoCH at 0.086 ran to the edge across a dive to 0.070 because
    // both superseding bearish CHoCHs failed). A normal leg's staircase only
    // moves away from the CHoCH level, so this never fires mid-trend.
    if (event.reference_price_level != null) {
      const rebasedAt = allEvents
        .filter(
          (other) =>
            other.scope === event.scope &&
            other.event === 'break_of_structure' &&
            !other.provisional &&
            other.direction === event.direction &&
            other.reference_price_level != null &&
            (event.direction === 'bullish'
              ? other.reference_price_level < event.reference_price_level!
              : other.reference_price_level > event.reference_price_level!) &&
            toChartTime(other.timestamp) > eventTime,
        )
        .map((other) => toChartTime(other.timestamp))
      candidates.push(...rebasedAt)
    }
    // An opposite-direction BOS also ends the line. A BOS is only emitted in
    // the direction of the standing trend, so a bearish BOS is proof the trend
    // *is* bearish — the bullish reversal reference is spent, whether or not
    // the CHoCH that opened that excursion later failed. Without this a failed
    // opposite CHoCH (excluded above as "never took hold") lets the old line
    // run straight through the whole counter-move: BTCUSDT H1 2026-07, where
    // the bullish CHoCH of 07-20 and its BOS both ran to the 07-31 re-fire,
    // across a bearish leg that had already printed real BOS on 07-24/07-28.
    const reversedAt = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'break_of_structure' &&
          !other.provisional &&
          other.direction !== event.direction &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    candidates.push(...reversedAt)
    // The first *confirming* same-direction BOS also ends the line. Until one
    // prints, the CHoCH is still provisional in substance — the level it broke
    // is what a `choch_failed` / re-arm measures, and watching price retest it
    // is half the flip read, so the line has to survive that window. Once the
    // BOS confirms the reversal, the level stops governing anything and the
    // staircase takes over; keeping the line to the *next CHoCH* (the old rule)
    // is what let it run across dozens of candles it no longer describes. This
    // subsumes the wrong-side rebase clause above, which stays as documentation
    // of the case that motivated it.
    const confirmedAt = allEvents
      .filter(
        (other) =>
          other.scope === event.scope &&
          other.event === 'break_of_structure' &&
          !other.provisional &&
          other.direction === event.direction &&
          toChartTime(other.timestamp) > eventTime,
      )
      .map((other) => toChartTime(other.timestamp))
    candidates.push(...confirmedAt)
    return candidates.length > 0 ? (Math.min(...candidates) as UTCTimestamp) : lastCandleTime
  }

  const oppositeDirection = event.direction === 'bullish' ? 'bearish' : 'bullish'
  const supersededAt = allEvents
    .filter(
      (other) =>
        other.scope === event.scope &&
        !other.provisional &&
        toChartTime(other.timestamp) > eventTime &&
        ((other.direction === event.direction &&
          (other.event === 'break_of_structure' ||
            (other.event === 'change_of_character' && !isFailedChoch(other, allEvents)) ||
            // A real same-direction CHOCH_FAILED invalidates the leg this BOS
            // extended and reverts the trend, so the BOS reference is no longer
            // standing — the line ends at the ✕ instead of running to the edge
            // (a leg that ends via failure has no opposite CHoCH to end it).
            (event.event === 'break_of_structure' && other.event === 'choch_failed'))) ||
          (other.direction === oppositeDirection &&
            other.event === 'change_of_character' &&
            !isFailedChoch(other, allEvents))),
    )
    .map((other) => toChartTime(other.timestamp))

  // A confirmed BOS stops shortly after its own break candle (see
  // BOS_LINE_TRAIL_CANDLES); a superseding event can still cut it shorter.
  if (bosTrailEnd !== undefined) supersededAt.push(bosTrailEnd)

  return supersededAt.length > 0 ? (Math.min(...supersededAt) as UTCTimestamp) : lastCandleTime
}

// A POI box spans the zone's real lifecycle: it stays open (full width) while
// the zone is ACTIVE — an armed order block price may still return to — and
// closes at the candle whose close broke through it (`invalidated_at`).
// Price touching inside the zone does not retire it.
function poiBoxEndTime(zone: POIZone, lastCandleTime: UTCTimestamp): UTCTimestamp {
  return zone.invalidated_at
    ? toChartTime(zone.invalidated_at)
    : ((lastCandleTime + 9_999_999) as UTCTimestamp)
}

// Only order blocks are drawn (see `selectVisiblePoiZones`), so every box
// carries the same label; the direction is already in the box color.
const POI_KIND_LABEL = 'OB'

const DIVERGENCE_MARKER_SHAPES: Record<string, { shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'; position: 'aboveBar' | 'belowBar' }> = {
  distribution: { shape: 'arrowDown', position: 'aboveBar' },
  accumulation: { shape: 'arrowUp', position: 'belowBar' },
}

// Exhaustion / absorption divergences are drawn as curved arcs (via
// DivergenceArcPrimitive) rather than markers — a dome above price for a
// bearish top exhaustion, a bowl below for a bullish exhaustion/absorption.
const DIVERGENCE_ARC_TYPES = new Set(['exhaustion', 'absorption'])


/** Interleave two pre-sorted lists, keeping both sides represented. */
function balancedTake<T>(above: T[], below: T[], budget: number): T[] {
  const out: T[] = []
  let i = 0
  while (out.length < budget && (i < above.length || i < below.length)) {
    if (i < below.length) out.push(below[i])
    if (out.length < budget && i < above.length) out.push(above[i])
    i++
  }
  return out
}


// Which POI zones the chart draws. This used to be a declutter pass — a cap
// per direction plus a price window around the last close — because ACTIVE
// zones piled up: the detector retired a box only when price closed through
// *that* box, so every shelf the market never revisited stayed armed forever
// and the pane filled with stale rectangles.
//
// The pile-up was a porting gap, not a display problem, and it is fixed at the
// source now (see `POIDetector`'s queue retirement, which follows the
// indicator's own rule: a break retires the *oldest* box of its queue). A chart
// carries 0-5 surviving order blocks, so there is nothing left to declutter and
// the chart shows exactly what survives — no cap, no distance window, which
// would now be a second, invented filter on top of the indicator's.
//
// The one thing still filtered here is *kind*: only order blocks are drawn.
// Each MSB also emits a breaker/mitigation block from the same break, sitting a
// few ticks away and doubling the ink for one observation. The full set stays in
// `poi_zones` for the API and for the liquidation map's entry anchors.
function selectVisiblePoiZones(zones: POIZone[]): POIZone[] {
  return zones.filter((z) => z.status === 'active' && z.kind === 'order_block')
}

// VWAP: each accumulation is its own line. A session series restarts at every
// UTC rollover, and joining yesterday's closing average to today's opening one
// would draw a jump nobody paid — so the points are split on their
// `anchor_timestamp` and each run becomes its own series, the same break-on-
// discontinuity treatment the Supertrend flip gets below.
/**
 * How much of the periodic VWAP is drawn. The average itself is the reading —
 * the break-even of everyone who entered since the anchor — while the ±1σ/±2σ
 * bands only describe how widely that volume was spread. On a pane already
 * carrying the structure staircase, POI boxes and liquidation pools, four
 * dotted band lines cost more attention than they return, so they sit behind a
 * third press of the button rather than coming along with the line.
 */
export type VwapMode = 'off' | 'line' | 'bands'

interface VwapSegment {
  value: { time: Time; value: number }[]
  upper1: { time: Time; value: number }[]
  lower1: { time: Time; value: number }[]
  upper2: { time: Time; value: number }[]
  lower2: { time: Time; value: number }[]
}

function buildVwapSegments(series: VWAPSeries | null | undefined): VwapSegment[] {
  if (!series) return []
  const segments: VwapSegment[] = []
  let current: VwapSegment | null = null
  let anchor: string | null = null
  for (const point of series.points) {
    if (!current || point.anchor_timestamp !== anchor) {
      anchor = point.anchor_timestamp
      current = { value: [], upper1: [], lower1: [], upper2: [], lower2: [] }
      segments.push(current)
    }
    const time = toChartTime(point.timestamp) as Time
    current.value.push({ time, value: point.value })
    // Bands are undefined on an accumulation's first candles (no dispersion
    // yet); those points simply carry no band data rather than a flat stub.
    if (point.upper_1 != null) current.upper1.push({ time, value: point.upper_1 })
    if (point.lower_1 != null) current.lower1.push({ time, value: point.lower_1 })
    if (point.upper_2 != null) current.upper2.push({ time, value: point.upper_2 })
    if (point.lower_2 != null) current.lower2.push({ time, value: point.lower_2 })
  }
  return segments.filter((segment) => segment.value.length > 0)
}

// Supertrend: the reading follows one band at a time, so a run of same-trend
// points is one continuous line and the flip is a break between runs (Pine's
// `plot.style_linebr`). Each run becomes its own line series so the two trends
// can carry their own colour without a bridging segment across the flip.
interface SupertrendSegment {
  direction: 'bullish' | 'bearish'
  points: { time: Time; value: number }[]
}

function buildSupertrendSegments(points: SupertrendPoint[]): SupertrendSegment[] {
  const segments: SupertrendSegment[] = []
  let current: SupertrendSegment | null = null
  for (const point of points) {
    const direction = point.direction === 'bearish' ? 'bearish' : 'bullish'
    if (!current || current.direction !== direction) {
      current = { direction, points: [] }
      segments.push(current)
    }
    current.points.push({ time: toChartTime(point.timestamp) as Time, value: point.value })
  }
  return segments.filter((segment) => segment.points.length > 0)
}

// A stop-run flip draws a segment along the band it broke, from the break to
// the candle that gave it back — the shape of "they took you out and returned".
// Genuine breaks draw nothing extra: the band itself already tells that story,
// and marking the normal case would bury the exceptional one.
function buildStopRunSegments(
  breaks: SupertrendBreak[],
): { points: { time: Time; value: number }[] }[] {
  return breaks
    .filter((brk) => brk.quality === 'stop_run' && brk.reclaim_timestamp !== null)
    .map((brk) => ({
      points: [
        { time: toChartTime(brk.timestamp) as Time, value: brk.broken_level },
        {
          time: toChartTime(brk.reclaim_timestamp as string) as Time,
          value: brk.broken_level,
        },
      ],
    }))
}

function buildStopRunMarkers(breaks: SupertrendBreak[]): SeriesMarker<Time>[] {
  return breaks
    .filter((brk) => brk.quality === 'stop_run')
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    .map(
      (brk) =>
        ({
          time: toChartTime(brk.timestamp) as Time,
          // Anchored on the side the break poked through, so the warning sits
          // where the stops were.
          position: brk.direction === 'bullish' ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          color: SUPERTREND_STOP_RUN_COLOR,
          text: '⚠ ST',
          size: 1,
        }) as SeriesMarker<Time>,
    )
}

// A defended level: the wick cleared the Tide envelope's edge into standing
// levels from two or more families and the close came straight back inside.
// Anchored on the raided side, so the mark sits where the stops were, and
// labelled with how many families agreed — the count is the reading, since a
// single family is usually the same wick told twice.
function buildDefendedMarkers(marks: DefendedMark[]): SeriesMarker<Time>[] {
  return marks.map(
    (mark) =>
      ({
        time: toChartTime(mark.timestamp) as Time,
        position: mark.side === 'top' ? 'aboveBar' : 'belowBar',
        shape: 'circle',
        color: DEFENDED_LEVEL_COLOR,
        text: `⛨${mark.families.length}`,
        size: 1,
      }) as SeriesMarker<Time>,
  )
}

function buildDivergenceMarkers(divergences: BehaviorDivergence[]): SeriesMarker<Time>[] {
  return [...divergences]
    .filter((div) => !DIVERGENCE_ARC_TYPES.has(div.divergence_type))
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    .map((div) => {
      const style = DIVERGENCE_STYLES[div.divergence_type]
      const markerStyle = DIVERGENCE_MARKER_SHAPES[div.divergence_type] ?? DIVERGENCE_MARKER_SHAPES.distribution
      const dirIcon = div.direction === 'bullish' ? '▲' : '▼'
      return {
        time: toChartTime(div.timestamp) as Time,
        position: markerStyle.position,
        shape: markerStyle.shape,
        color: style?.color ?? '#888888',
        text: `${style?.label ?? div.divergence_type} ${dirIcon}`,
        size: 1.5,
      } as SeriesMarker<Time>
    })
}

// Which extreme a VSA reversal pattern reads at — the top (above price) or the
// bottom (below). Lets a VSA signal be matched to a same-side divergence arc.
const VSA_PATTERN_SIDE: Record<string, 'above' | 'below'> = {
  buying_climax: 'above',
  up_thrust: 'above',
  no_demand: 'above',
  selling_climax: 'below',
  down_thrust: 'below',
  no_supply: 'below',
}

// VSA (single-candle anatomy) and behavior divergence (window flow) measure the
// same reversal at different time resolutions, so they rarely land on the exact
// same candle but often within a few bars. When a same-side VSA reversal sits
// within this many candles of a divergence, the two confirm each other and the
// arc is drawn reinforced (✦). Neither base layer is modified.
const CONFLUENCE_WINDOW_BARS = 3

// Exhaustion/absorption arcs: dome above price for a bearish (top) reading,
// bowl below for a bullish one (bottom exhaustion / absorption). A same-side
// VSA reversal within CONFLUENCE_WINDOW_BARS marks the arc as `strong`.
function buildDivergenceArcs(
  divergences: BehaviorDivergence[],
  vsaSignals: VolumeSpreadSignal[],
  candles: DashboardData['candles'],
): DivergenceArc[] {
  // Anchor each arc to the candle's extreme (high above / low below) rather
  // than its close, so the curve clears the wick with a little breathing room.
  const byTime = new Map(candles.map((c) => [c.timestamp, c]))
  const indexByTime = new Map(candles.map((c, i) => [c.timestamp, i]))

  // Bucket VSA reversal signals by side, as candle indices, for proximity match.
  const vsaIndicesBySide: Record<'above' | 'below', number[]> = { above: [], below: [] }
  for (const sig of vsaSignals) {
    const side = VSA_PATTERN_SIDE[sig.pattern]
    const idx = indexByTime.get(sig.timestamp)
    if (side !== undefined && idx !== undefined) vsaIndicesBySide[side].push(idx)
  }

  return [...divergences]
    .filter((div) => DIVERGENCE_ARC_TYPES.has(div.divergence_type))
    .map((div) => {
      // Which side the arc hugs depends on the type's direction semantics:
      // exhaustion.direction is the fading trend (bullish → top exhausting →
      // dome above); absorption.direction is the net flow (bullish → buyers
      // absorbing at support → bowl below). So they map oppositely.
      const bullish = div.direction === 'bullish'
      const side: DivergenceArc['side'] =
        div.divergence_type === 'exhaustion'
          ? bullish
            ? 'above'
            : 'below'
          : bullish
            ? 'below'
            : 'above'
      const candle = byTime.get(div.timestamp)
      const price = candle
        ? side === 'above'
          ? candle.high
          : candle.low
        : div.price_level
      const divIdx = indexByTime.get(div.timestamp)
      const strong =
        divIdx !== undefined &&
        vsaIndicesBySide[side].some((i) => Math.abs(i - divIdx) <= CONFLUENCE_WINDOW_BARS)
      return {
        time: toChartTime(div.timestamp) as Time,
        price,
        side,
        color: DIVERGENCE_STYLES[div.divergence_type]?.color ?? '#888888',
        strong,
      }
    })
}

function buildVsaMarkers(signals: VolumeSpreadSignal[]): SeriesMarker<Time>[] {
  return [...signals]
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    .map((sig) => {
      const style = VSA_STYLES[sig.pattern]
      return {
        time: toChartTime(sig.timestamp) as Time,
        position: style?.position ?? 'aboveBar',
        shape: sig.direction === 'bullish' ? 'arrowUp' : 'arrowDown',
        color: style?.color ?? '#8a94a6',
        // Label-less: the arrow + its VSA colour identify the pattern; the
        // text above the arrows was cluttering the chart.
        text: '',
        size: 1,
      } as SeriesMarker<Time>
    })
}

const MAX_MANIP_BOXES = 3
const ZONE_PRICE_BUFFER_PCT = 0.003

function buildManipulationBoxes(
  cycles: ManipulationCycle[],
  lastCandleTime: UTCTimestamp,
): POIBox[] {
  const boxes: POIBox[] = []

  const statusOrder: Record<string, number> = { in_progress: 0, confirmed: 1, failed: 2 }
  const sorted = [...cycles].sort((a, b) => {
    const sa = statusOrder[a.status] ?? 2
    const sb = statusOrder[b.status] ?? 2
    if (sa !== sb) return sa - sb
    return new Date(b.accumulation_start).getTime() - new Date(a.accumulation_start).getTime()
  }).slice(0, MAX_MANIP_BOXES)

  for (const cycle of sorted) {
    const style = MANIPULATION_BOX_STYLES[cycle.status] ?? MANIPULATION_BOX_STYLES.failed

    const zoneMid = (cycle.target_zone_price_high + cycle.target_zone_price_low) / 2
    const buffer = zoneMid * ZONE_PRICE_BUFFER_PCT
    const priceLow =
      cycle.target_zone_price_low === cycle.target_zone_price_high
        ? cycle.target_zone_price_low - buffer
        : cycle.target_zone_price_low
    const priceHigh =
      cycle.target_zone_price_low === cycle.target_zone_price_high
        ? cycle.target_zone_price_high + buffer
        : cycle.target_zone_price_high

    const x0 = toChartTime(cycle.accumulation_start)
    const x1 = cycle.sweep_timestamp
      ? toChartTime(cycle.sweep_timestamp)
      : cycle.phase === 'accumulation'
        ? ((lastCandleTime + 9_999_999) as UTCTimestamp)
        : toChartTime(cycle.accumulation_end)

    const dirIcon = cycle.direction === 'bullish' ? '▲' : '▼'
    const phaseLabel =
      cycle.phase === 'accumulation'
        ? 'ACC'
        : cycle.phase === 'manipulation'
          ? 'MANIP'
          : 'CONF'

    boxes.push({
      x0,
      x1,
      priceLow,
      priceHigh,
      borderColor: style.border,
      fillColor: style.fill,
      label: `${phaseLabel} ${dirIcon}`,
    })
  }

  return boxes
}

interface MainChartProps {
  data: DashboardData
  showConsolidationRanges?: boolean
  showManipulationBoxes?: boolean
  showDivergenceMarkers?: boolean
  vsaMode?: 'off' | 'recent' | 'full'
  showHeatmap?: boolean
  showSweptZones?: boolean
  showOrderBlocks?: boolean
  showSweeps?: boolean
  /** BOS/CHoCH/CHoCH ✕ lines and labels (the SMC structure staircase). */
  showSmc?: boolean
  showEqlZones?: boolean
  showIndicators?: boolean
  showHuntWindow?: boolean
  showContinuationWindow?: boolean
  showVolume?: boolean
  showRsiDivergence?: boolean
  showSupertrend?: boolean
  vwapMode?: VwapMode
  showAnchoredVwap?: boolean
  showVolumeProfile?: boolean
  volumeProfileMode?: VolumeProfileMode
  showControlOscillator?: boolean
  showRibbon?: boolean
  showDefendedLevels?: boolean
}

export function MainChart({
  data,
  showConsolidationRanges = true,
  showManipulationBoxes = true,
  showDivergenceMarkers = true,
  vsaMode = 'recent',
  showHeatmap = true,
  showSweptZones = true,
  showOrderBlocks = true,
  showSweeps = true,
  showSmc = true,
  showEqlZones = true,
  showIndicators = true,
  showHuntWindow = false,
  showContinuationWindow = false,
  showVolume = true,
  showRsiDivergence = false,
  showSupertrend = false,
  vwapMode = 'off',
  showAnchoredVwap = false,
  showVolumeProfile = false,
  volumeProfileMode = 'value-area',
  showControlOscillator = false,
  showRibbon = false,
  showDefendedLevels = false,
}: MainChartProps) {
  // Which clock this chart's times are drawn on -- local intraday, exchange
  // (UTC) on the daily/weekly bars. Set during render, before the effects below
  // convert anything through `toChartTime`; `App` remounts this component on
  // every symbol/timeframe change, so the mode never outlives its data.
  setChartTimezoneMode(data.timeframe)

  const wrapperRef = useRef<HTMLDivElement>(null)
  const mainContainerRef = useRef<HTMLDivElement>(null)
  const deltaContainerRef = useRef<HTMLDivElement>(null)
  const controlContainerRef = useRef<HTMLDivElement>(null)
  const rsiContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const deltaChartRef = useRef<IChartApi | null>(null)
  const controlChartRef = useRef<IChartApi | null>(null)
  const rsiChartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const deltaSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const controlSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const overlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiOverlaySeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const rsiDivSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const labelsPrimitiveRef = useRef<LineLabelsPrimitive | null>(null)
  const huntWindowPrimitiveRef = useRef<HuntWindowPrimitive | null>(null)
  const poiBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const manipBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const rangeBoxesPrimitiveRef = useRef<POIBoxesPrimitive | null>(null)
  const heatmapPrimitiveRef = useRef<HeatmapStripPrimitive | null>(null)
  const volumeProfilePrimitiveRef = useRef<VolumeProfilePrimitive | null>(null)
  const eqlZonesPrimitiveRef = useRef<EqlZonesPrimitive | null>(null)
  const ribbonPrimitiveRef = useRef<RibbonPrimitive | null>(null)
  const phaseSeriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const phaseRailSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const divergenceMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const divergenceArcsPrimitiveRef = useRef<DivergenceArcPrimitive | null>(null)
  const hasFittedRef = useRef(false)
  const isSyncingRef = useRef(false)
  // Read by the ResizeObserver (created once) so it recomputes pane heights
  // against the current minimize state. Kept in sync by the effect below.
  const showIndicatorsRef = useRef(showIndicators)
  const showControlRef = useRef(showControlOscillator)
  // The redraw effect keys off `drawSig`, not `data`, so it reads the latest
  // snapshot through this ref (fresher than the render that last changed the
  // signature) instead of closing over a stale prop.
  const dataRef = useRef(data)
  const drawSig = useMemo(() => drawSignature(data), [data])
  // Declared before the redraw effect so it has already landed when that one runs.
  useEffect(() => {
    dataRef.current = data
  }, [data])

  useEffect(() => {
    const wrapper = wrapperRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const controlContainer = controlContainerRef.current
    const rsiContainer = rsiContainerRef.current
    if (!wrapper || !mainContainer || !deltaContainer || !controlContainer || !rsiContainer) return

    const totalHeight = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const indicatorsOpen = showIndicatorsRef.current
    const controlOpen = showControlRef.current
    const { mainHeight, deltaHeight, controlHeight, rsiHeight } = paneHeights(
      totalHeight,
      indicatorsOpen,
      controlOpen,
    )
    const av = axisVisibility(indicatorsOpen)

    const chartOptions = {
      layout: {
        background: { type: ColorType.Solid as const, color: DARK_BG },
        textColor: FONT_COLOR,
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
    }

    const chart = createChart(mainContainer, {
      ...chartOptions,
      width: mainContainer.clientWidth,
      height: mainHeight,
      // The bottom-most visible pane carries the time axis (see axisVisibility):
      // RSI when the indicator group is open, the control pane when only it is
      // open, else the main pane itself.
      timeScale: { ...chartOptions.timeScale, visible: av.main },
      rightPriceScale: { minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    chartRef.current = chart

    const deltaChart = createChart(deltaContainer, {
      ...chartOptions,
      width: deltaContainer.clientWidth,
      height: deltaHeight,
      timeScale: { ...chartOptions.timeScale, visible: false },
      rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    deltaChartRef.current = deltaChart

    const controlChart = createChart(controlContainer, {
      ...chartOptions,
      width: controlContainer.clientWidth,
      height: controlHeight,
      timeScale: { ...chartOptions.timeScale, visible: av.control },
      rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    controlChartRef.current = controlChart

    const rsiChart = createChart(rsiContainer, {
      ...chartOptions,
      width: rsiContainer.clientWidth,
      height: rsiHeight,
      timeScale: { ...chartOptions.timeScale, visible: av.rsi },
      rightPriceScale: { scaleMargins: { top: 0.05, bottom: 0.05 }, minimumWidth: PRICE_SCALE_MIN_WIDTH },
    })
    rsiChartRef.current = rsiChart

    const series = chart.addSeries(CandlestickSeries, {
      upColor: CANDLE_UP_COLOR,
      downColor: CANDLE_DOWN_COLOR,
      borderVisible: false,
      wickUpColor: CANDLE_UP_COLOR,
      wickDownColor: CANDLE_DOWN_COLOR,
    })
    seriesRef.current = series

    // Raw volume histogram, overlaid on the base of the main pane. Its own
    // overlay price scale (`priceScaleId: ''`) with a large top scale margin
    // pins the bars to the bottom ~18% so they sit behind the candles without
    // rescaling the price axis.
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    })
    volumeSeriesRef.current = volumeSeries

    const deltaSeries = deltaChart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
    })
    deltaSeriesRef.current = deltaSeries

    const controlSeries = controlChart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
      base: 0,
    })
    controlSeriesRef.current = controlSeries

    // The phase line rides *over* the control histogram on the same axis: the
    // bars are how hard a side is pushing, the line is how far price has been
    // carried inside its own envelope. The gap between them is the reading --
    // a stretched line over short grey bars is an extension nobody is funding.
    // Baseline at zero -- the VWAP itself, the population's break-even. The
    // line keeps its gold on both sides (it has to stay legible against bars
    // that are already teal and red), and the *fill* to the baseline carries
    // which side of break-even price is being carried on. That is not a
    // restatement of the ribbon's hue: the ribbon says what the structure is
    // doing, this says whether the crowd that entered since the anchor is
    // holding a profit or a loss.
    const phaseSeries = controlChart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      lineWidth: 2,
      topLineColor: PHASE_NEUTRAL_COLOR,
      bottomLineColor: PHASE_NEUTRAL_COLOR,
      topFillColor1: PHASE_ABOVE_FILL,
      topFillColor2: PHASE_FILL_FADE,
      bottomFillColor1: PHASE_FILL_FADE,
      bottomFillColor2: PHASE_BELOW_FILL,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    })
    phaseSeriesRef.current = phaseSeries

    // The +/-1 sigma rails. Without them the line's level is unreadable -- a
    // reading of 40 and one of 90 look the same on an autoscaled pane, yet one
    // is price inside the envelope and the other is price outside it, which is
    // the whole distinction the phase measures. They are a *scale*, not an
    // overbought line: measured across six combos, |phase| exceeds 50 on about
    // half of all candles (49-55%), because sigma is accumulated over a whole
    // session while price walks away from the anchor. The tail worth noticing
    // is |phase| > 100, at 9-13%.
    phaseRailSeriesRef.current = [0, 1].map(() =>
      controlChart.addSeries(LineSeries, {
        color: PHASE_RAIL_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      }),
    )

    const rsiSeries = rsiChart.addSeries(LineSeries, {
      color: RSI_LINE_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
    })
    rsiSeriesRef.current = rsiSeries

    // RSI reference lines (70 overbought, 30 oversold)
    const rsiOverbought = rsiChart.addSeries(LineSeries, {
      color: RSI_OVERBOUGHT_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiOverlaySeriesRef.current.push(rsiOverbought)

    const rsiOversold = rsiChart.addSeries(LineSeries, {
      color: RSI_OVERSOLD_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiOverlaySeriesRef.current.push(rsiOversold)

    const labelsPrimitive = new LineLabelsPrimitive()
    // When the main pane hides its time axis (another pane carries it), the main
    // chart's time-scale coordinate API returns null. The other charts share the
    // synced, equal-width time scale, so the labels primitive falls back to
    // whichever pane currently holds the visible axis. The main pane keeps its
    // own axis unless the RSI pane carries it, so the fallback is always RSI
    // (only consulted while the main axis is hidden — the indicator-group case).
    labelsPrimitive.fallbackChart = rsiChart
    series.attachPrimitive(labelsPrimitive)
    labelsPrimitiveRef.current = labelsPrimitive

    // Background shading (zOrder 'bottom'): painted beneath candles/overlays.
    const huntWindowPrimitive = new HuntWindowPrimitive()
    series.attachPrimitive(huntWindowPrimitive)
    huntWindowPrimitiveRef.current = huntWindowPrimitive

    const poiBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(poiBoxesPrimitive)
    poiBoxesPrimitiveRef.current = poiBoxesPrimitive

    const manipBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(manipBoxesPrimitive)
    manipBoxesPrimitiveRef.current = manipBoxesPrimitive

    const rangeBoxesPrimitive = new POIBoxesPrimitive()
    series.attachPrimitive(rangeBoxesPrimitive)
    rangeBoxesPrimitiveRef.current = rangeBoxesPrimitive

    const heatmapPrimitive = new HeatmapStripPrimitive()
    series.attachPrimitive(heatmapPrimitive)
    heatmapPrimitiveRef.current = heatmapPrimitive

    const volumeProfilePrimitive = new VolumeProfilePrimitive()
    series.attachPrimitive(volumeProfilePrimitive)
    volumeProfilePrimitiveRef.current = volumeProfilePrimitive

    const eqlZonesPrimitive = new EqlZonesPrimitive()
    series.attachPrimitive(eqlZonesPrimitive)
    eqlZonesPrimitiveRef.current = eqlZonesPrimitive

    const ribbonPrimitive = new RibbonPrimitive()
    series.attachPrimitive(ribbonPrimitive)
    ribbonPrimitiveRef.current = ribbonPrimitive

    const divergenceArcsPrimitive = new DivergenceArcPrimitive()
    series.attachPrimitive(divergenceArcsPrimitive)
    divergenceArcsPrimitiveRef.current = divergenceArcsPrimitive

    const divergenceMarkers = createSeriesMarkers(series)
    divergenceMarkersRef.current = divergenceMarkers

    // Sync time scales across the *currently visible* panes only. A collapsed
    // pane (display:none, zero width) has a degenerate time scale: writing a
    // range to it — or letting it broadcast one — corrupts the shared range and,
    // because it sits before the control pane in the loop, stops the control
    // pane from ever receiving the update (the "control only follows zoom when
    // vol/rsi is also on" bug). So a hidden pane neither sends nor receives.
    const charts = [chart, deltaChart, controlChart, rsiChart]
    const isPaneActive = (c: IChartApi) =>
      c === chart ||
      (showIndicatorsRef.current && (c === deltaChart || c === rsiChart)) ||
      (showControlRef.current && c === controlChart)
    for (const src of charts) {
      src.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (isSyncingRef.current || !range || !isPaneActive(src)) return
        isSyncingRef.current = true
        for (const dst of charts) {
          if (dst !== src && isPaneActive(dst)) dst.timeScale().setVisibleLogicalRange(range)
        }
        isSyncingRef.current = false
      })
    }

    // Sync crosshairs. Each pane maps a hovered time onto the others — but only
    // the *active* ones: calling setCrosshairPosition on a collapsed (display:
    // none, zero-width) pane can throw, and if it does the old code left
    // `isSyncingRef` stuck `true`, silently killing the logical-range (zoom)
    // sync afterwards — the "control pane only follows zoom when vol/rsi is also
    // on" bug (vol/rsi visible → no hidden pane touched → no throw). A
    // try/finally guarantees the guard is always released.
    const crosshairPanes: { chart: IChartApi; series: ISeriesApi<SeriesType> }[] = [
      { chart, series },
      { chart: deltaChart, series: deltaSeries },
      { chart: controlChart, series: controlSeries },
      { chart: rsiChart, series: rsiSeries },
    ]
    for (const src of crosshairPanes) {
      src.chart.subscribeCrosshairMove((param) => {
        if (isSyncingRef.current || !isPaneActive(src.chart)) return
        isSyncingRef.current = true
        try {
          for (const dst of crosshairPanes) {
            if (dst.chart === src.chart || !isPaneActive(dst.chart)) continue
            if (param.time) dst.chart.setCrosshairPosition(NaN, param.time, dst.series)
            else dst.chart.clearCrosshairPosition()
          }
        } finally {
          isSyncingRef.current = false
        }
      })
    }

    // Tell the pollers when the chart is under the user's hand, so a snapshot
    // apply never lands mid-drag (see `utils/chartActivity`).
    const onPointerDown = () => markChartDragStart()
    const onPointerUp = () => markChartDragEnd()
    const onWheel = () => markChartGesture()
    wrapper.addEventListener('pointerdown', onPointerDown)
    wrapper.addEventListener('wheel', onWheel, { passive: true })
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)

    const ro = new ResizeObserver(() => {
      const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
      const { mainHeight: mh, deltaHeight: dh, controlHeight: ch, rsiHeight: rh } = paneHeights(
        h,
        showIndicatorsRef.current,
        showControlRef.current,
      )
      chart.applyOptions({ width: mainContainer.clientWidth, height: mh })
      deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: dh })
      controlChart.applyOptions({ width: controlContainer.clientWidth, height: ch })
      rsiChart.applyOptions({ width: rsiContainer.clientWidth, height: rh })
    })
    ro.observe(wrapper)

    return () => {
      ro.disconnect()
      wrapper.removeEventListener('pointerdown', onPointerDown)
      wrapper.removeEventListener('wheel', onWheel)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
      markChartDragEnd(0)
      chart.remove()
      deltaChart.remove()
      controlChart.remove()
      rsiChart.remove()
      chartRef.current = null
      deltaChartRef.current = null
      controlChartRef.current = null
      rsiChartRef.current = null
      seriesRef.current = null
      volumeSeriesRef.current = null
      deltaSeriesRef.current = null
      controlSeriesRef.current = null
      phaseSeriesRef.current = null
      phaseRailSeriesRef.current = []
      rsiSeriesRef.current = null
      overlaySeriesRef.current = []
      rsiOverlaySeriesRef.current = []
      rsiDivSeriesRef.current = []
      labelsPrimitiveRef.current = null
      poiBoxesPrimitiveRef.current = null
      manipBoxesPrimitiveRef.current = null
      rangeBoxesPrimitiveRef.current = null
      heatmapPrimitiveRef.current = null
      volumeProfilePrimitiveRef.current = null
      divergenceMarkersRef.current = null
      divergenceArcsPrimitiveRef.current = null
      hasFittedRef.current = false
    }
  }, [])

  // Toggling the volume profile on/off after mount: slide the visible window so
  // the histogram gets its own strip on the right instead of painting over the
  // most recent candles (and hand the space back when it is turned off).
  const vpReservedRef = useRef(0)
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const timeScale = chart.timeScale()
    const range = timeScale.getVisibleLogicalRange()
    if (!range) return
    // Before the opening view is set, the bar width is still the library's
    // default -- measure against the width the reset view will land on, so the
    // space booked here and the space booked there are the same.
    const barSpacing = hasFittedRef.current
      ? timeScale.options().barSpacing
      : RESET_BAR_SPACING_PX
    const reserved = showVolumeProfile ? volumeProfileReservedBars(barSpacing) : 0
    const delta = reserved - vpReservedRef.current
    vpReservedRef.current = reserved
    if (delta === 0 || !hasFittedRef.current) return
    timeScale.setVisibleLogicalRange({ from: range.from + delta, to: range.to + delta })
  }, [showVolumeProfile])

  // Toggle the volume-delta / RSI panes: give the main pane the full height and
  // move the visible time axis onto it while minimized, restore the split when open.
  useEffect(() => {
    const wrapper = wrapperRef.current
    const chart = chartRef.current
    const deltaChart = deltaChartRef.current
    const controlChart = controlChartRef.current
    const rsiChart = rsiChartRef.current
    const mainContainer = mainContainerRef.current
    const deltaContainer = deltaContainerRef.current
    const controlContainer = controlContainerRef.current
    const rsiContainer = rsiContainerRef.current
    showIndicatorsRef.current = showIndicators
    showControlRef.current = showControlOscillator
    if (
      !wrapper || !chart || !deltaChart || !controlChart || !rsiChart ||
      !mainContainer || !deltaContainer || !controlContainer || !rsiContainer
    )
      return

    const h = Math.max(wrapper.clientHeight, MIN_TOTAL_HEIGHT)
    const { mainHeight, deltaHeight, controlHeight, rsiHeight } = paneHeights(
      h,
      showIndicators,
      showControlOscillator,
    )
    const av = axisVisibility(showIndicators)

    // While a pane is closed its container is display:none (zero width), so it
    // never tracks the main chart's time scale (and its one-shot fitContent ran
    // at zero width). Reopening it would otherwise reveal a stale, desynced
    // range -- and resizing from zero width can echo that bad range back onto
    // the main chart. Suppress the sync feedback across the resize, then drive
    // every reopened pane from the main chart's current range.
    isSyncingRef.current = true
    chart.applyOptions({
      width: mainContainer.clientWidth,
      height: mainHeight,
      timeScale: { visible: av.main },
    })
    deltaChart.applyOptions({ width: deltaContainer.clientWidth, height: deltaHeight })
    controlChart.applyOptions({
      width: controlContainer.clientWidth,
      height: controlHeight,
      timeScale: { visible: av.control },
    })
    rsiChart.applyOptions({
      width: rsiContainer.clientWidth,
      height: rsiHeight,
      timeScale: { visible: av.rsi },
    })

    const range = chart.timeScale().getVisibleLogicalRange()
    if (range) {
      if (showIndicators) {
        deltaChart.timeScale().setVisibleLogicalRange(range)
        rsiChart.timeScale().setVisibleLogicalRange(range)
      }
      if (showControlOscillator) {
        controlChart.timeScale().setVisibleLogicalRange(range)
      }
    }
    // Release the guard after this frame's layout (and any resize-triggered
    // range echo) settles.
    requestAnimationFrame(() => {
      isSyncingRef.current = false
    })
  }, [showIndicators, showControlOscillator])

  useEffect(() => {
    const data = dataRef.current
    const chart = chartRef.current
    const deltaChart = deltaChartRef.current
    const rsiChart = rsiChartRef.current
    const series = seriesRef.current
    const deltaSeries = deltaSeriesRef.current
    const rsiSeries = rsiSeriesRef.current
    if (
      !chart ||
      !deltaChart ||
      !rsiChart ||
      !series ||
      !deltaSeries ||
      !rsiSeries ||
      data.candles.length === 0
    )
      return

    // Adapt the price axis precision to the pair's magnitude so low-priced
    // pairs (ETHBTC, ENAUSDT) don't collapse onto 0.01 ticks. Use the latest
    // close as the reference magnitude (stable within a window).
    series.applyOptions({
      priceFormat: {
        type: 'price',
        ...priceFormatFor(data.candles[data.candles.length - 1].close),
      },
    })

    series.setData(
      data.candles.map((candle) => ({
        time: toChartTime(candle.timestamp),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    )

    // Raw volume overlay (bottom of the main pane), colored by candle direction.
    const volumeSeries = volumeSeriesRef.current
    if (volumeSeries) {
      volumeSeries.applyOptions({ visible: showVolume })
      volumeSeries.setData(
        showVolume
          ? data.candles.map((candle) => ({
              time: toChartTime(candle.timestamp),
              value: candle.volume,
              color: candle.close >= candle.open ? VOLUME_UP_COLOR : VOLUME_DOWN_COLOR,
            }))
          : [],
      )
    }

    // Volume delta histogram
    // VSA tint per candle timestamp: a flagged candle's volume-delta bar is
    // colored by what the pattern *means* (climax/thrust/quiet) rather than
    // just its direction — the whole point of reading volume with price.
    // VSA signals honoring the three-state mode: 'off' → none, 'recent' → only
    // the last VSA_RECENT_CANDLES bars, 'full' → whole history.
    const allVsaSignals = data.volume_spread_signals ?? []
    const vsaRecentCutoff =
      data.candles.length > VSA_RECENT_CANDLES
        ? Date.parse(data.candles[data.candles.length - VSA_RECENT_CANDLES].timestamp)
        : Number.NEGATIVE_INFINITY
    const vsaSignals =
      vsaMode === 'off'
        ? []
        : vsaMode === 'full'
          ? allVsaSignals
          : allVsaSignals.filter((s) => Date.parse(s.timestamp) >= vsaRecentCutoff)

    const vsaColorByTs = new Map<string, string>()
    for (const sig of vsaSignals) {
      vsaColorByTs.set(sig.timestamp, VSA_STYLES[sig.pattern]?.color ?? '#8a94a6')
    }
    deltaSeries.setData(
      data.candles.map((candle) => {
        const delta = 2 * candle.taker_buy_volume - candle.volume
        const vsaColor = vsaColorByTs.get(candle.timestamp)
        return {
          time: toChartTime(candle.timestamp),
          value: delta,
          color:
            vsaColor ?? (candle.close >= candle.open ? CANDLE_UP_COLOR : CANDLE_DOWN_COLOR),
        }
      }),
    )

    // Control oscillator (CVD aggression × OI): signed conviction per candle,
    // hue = the aggressor side, fill = whether open interest backs it (solid
    // buildup vs washed-out covering/liquidation). A single histogram carries
    // "who, how strongly, and with whose money".
    const controlSeries = controlSeriesRef.current
    if (controlSeries) {
      // Index the sparse control readings by candle timestamp, then emit an
      // entry for *every* candle -- a real bar where there's a reading, a
      // whitespace `{ time }` otherwise -- so bar indices match the main/delta
      // charts and the logical-range sync stays aligned (same fix as RSI).
      const controlByTs = new Map(
        (data.market_control?.series ?? []).map((p) => [p.timestamp, p]),
      )
      controlSeries.setData(
        data.candles.map((candle) => {
          const time = toChartTime(candle.timestamp)
          const p = controlByTs.get(candle.timestamp)
          return p
            ? {
                time,
                value: p.control_score,
                color: CONTROL_REGIME_COLORS[p.regime] ?? CONTROL_BALANCED_COLOR,
              }
            : { time }
        }),
      )
    }

    // RSI — include whitespace entries for the bootstrap period so bar indices
    // match the main/delta charts and the logical-range sync stays aligned.
    const closes = data.candles.map((c) => c.close)
    const rsiValues = computeRSI(closes, RSI_PERIOD)
    const rsiData = data.candles.map((candle, i) => {
      const time = toChartTime(candle.timestamp)
      const v = rsiValues[i]
      return v !== null ? { time, value: v } : { time }
    })
    rsiSeries.setData(rsiData)

    // RSI 70/30 reference lines
    const [overboughtSeries, oversoldSeries] = rsiOverlaySeriesRef.current
    if (overboughtSeries && oversoldSeries && rsiData.length >= 2) {
      const firstTime = rsiData[0].time
      const lastTime = rsiData[rsiData.length - 1].time
      overboughtSeries.setData([
        { time: firstTime, value: 70 },
        { time: lastTime, value: 70 },
      ])
      oversoldSeries.setData([
        { time: firstTime, value: 30 },
        { time: lastTime, value: 30 },
      ])
    }

    // RSI divergence lines
    for (const s of rsiDivSeriesRef.current) {
      rsiChart.removeSeries(s)
    }
    rsiDivSeriesRef.current = []

    const divergences = detectDivergences(closes, rsiValues)
    for (const div of divergences) {
      const color = div.type === 'bullish' ? RSI_DIV_BULLISH_COLOR : RSI_DIV_BEARISH_COLOR
      const divSeries = rsiChart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      divSeries.setData([
        { time: toChartTime(data.candles[div.startIndex].timestamp), value: div.startRSI },
        { time: toChartTime(data.candles[div.endIndex].timestamp), value: div.endRSI },
      ])
      rsiDivSeriesRef.current.push(divSeries)
    }

    for (const overlaySeries of overlaySeriesRef.current) {
      chart.removeSeries(overlaySeries)
    }
    overlaySeriesRef.current = []

    const lastCandleTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
    const firstCandleTime = toChartTime(data.candles[0].timestamp)

    const labels: LineLabel[] = []

    // RSI divergence lines mirrored onto the price structure: a bearish
    // divergence (price HH + RSI LH) connects the two swing highs, a bullish
    // one (price LL + RSI HL) the two swing lows -- the price-side counterpart
    // of the same trendline drawn on the RSI pane above.
    for (const div of showRsiDivergence ? divergences : []) {
      const bearish = div.type === 'bearish'
      const color = bearish ? RSI_DIV_BEARISH_COLOR : RSI_DIV_BULLISH_COLOR
      const startCandle = data.candles[div.startIndex]
      const endCandle = data.candles[div.endIndex]
      const startPrice = bearish ? startCandle.high : startCandle.low
      const endPrice = bearish ? endCandle.high : endCandle.low
      const startTime = toChartTime(startCandle.timestamp)
      const endTime = toChartTime(endCandle.timestamp)

      const divSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      divSeries.setData([
        { time: startTime, value: startPrice },
        { time: endTime, value: endPrice },
      ])
      overlaySeriesRef.current.push(divSeries)
      labels.push({
        time: startTime,
        timeEnd: endTime,
        price: endPrice,
        color,
        text: `RSI Div ${bearish ? '▼' : '▲'}`,
      })
    }

    const eqlZones: EqlZoneInput[] = []
    // Only equal-level pools are drawn; standalone swing highs/lows are single
    // pivots (weaker resting liquidity) that just clutter the chart — they still
    // feed scoring/heatmap etc. on the backend, only the render drops them.
    const eqPrice = data.candles[data.candles.length - 1].close
    const standing = showEqlZones
      ? data.ranked_zones.filter(
          (s) => s.zone.zone_type === 'equal_highs' || s.zone.zone_type === 'equal_lows',
        )
      : []
    // A pool is a target only on the side it is hunted from: buy-side liquidity
    // rests above price, sell-side below. One that price has already passed
    // through is behind the market, not ahead of it.
    const distance = (s: (typeof standing)[number]) =>
      s.zone.zone_type === 'equal_highs'
        ? s.zone.price_low - eqPrice
        : eqPrice - s.zone.price_high
    const byProximity = (
      side: 'equal_highs' | 'equal_lows',
    ): typeof standing =>
      standing
        .filter((s) => s.zone.zone_type === side && distance(s) > 0)
        .sort((a, b) => distance(a) - distance(b))
        .slice(0, NEAREST_POOLS_PER_SIDE)
    const poolZones = balancedTake(
      byProximity('equal_highs'),
      byProximity('equal_lows'),
      NEAREST_POOLS_PER_SIDE * 2,
    )
    // What price has already taken is read from `data.liquidity_grabs`, the
    // unified stream: one entry per candle and side, carrying every kind of
    // pool that moment consumed. The layers each knew separately that a level
    // of theirs was gone — an equal-level pool by `invalidated_at`, an order
    // block by the same field on its own zone, and the order block said so by
    // silently ceasing to draw — so a candle that took four stacked highs and
    // the block behind them told five stories in two vocabularies. It is one
    // event, and the kinds are its evidence.
    const grabIndexByTime = new Map(data.candles.map((c, i) => [c.timestamp, i]))
    // The staircase belongs to the *context* price is in now: grabs taken
    // since the last structural advance, on the side the current trend
    // consumes (a bullish leg takes buy-side pools above it, a bearish one
    // sell-side pools below).
    //
    // The anchor is the last non-provisional BOS/CHoCH rather than the trend
    // flip. A leg runs for hundreds of candles and advances several times
    // inside itself; a step taken before the latest advance belongs to a
    // context the structure has already superseded, and price has moved on
    // from it. "The context changed" has a name in this project — it is the
    // advance — so the scope reuses the event stream the chart already draws
    // instead of a recency dial.
    //
    // Scoping to a fixed recent window instead was tried and is wrong, even
    // though it draws more: a leg that has since flipped leaves its staircase
    // standing behind price, and on BTCUSDT 30m that put four rising EQH steps
    // on a chart whose live leg had been falling for 40 candles. Steps from a
    // dead leg are not context, they are the previous answer to a question
    // nobody is asking any more.
    //
    // The cost is real and accepted: an empty staircase is the honest reading
    // of a context that has not taken a pool — the layer says "no steps yet",
    // not "no data".
    const trendByCandle = structureTrendByCandle(data)
    const currentTrend = trendByCandle.get(data.candles[data.candles.length - 1].timestamp)
    const lastAdvance = data.internal_structure_events
      .filter(
        (e) =>
          !e.provisional &&
          (e.event === 'break_of_structure' || e.event === 'change_of_character'),
      )
      .reduce<string | null>(
        (latest, e) => (latest === null || e.timestamp > latest ? e.timestamp : latest),
        null,
      )
    const scopeStart = lastAdvance !== null ? (grabIndexByTime.get(lastAdvance) ?? 0) : 0
    const grabbedSide: LiquiditySide = currentTrend === 'bullish' ? 'buy_side' : 'sell_side'
    const grabs = showEqlZones
      ? data.liquidity_grabs
          .filter((grab) => {
            if (currentTrend !== 'bullish' && currentTrend !== 'bearish') return false
            if (grab.side !== grabbedSide) return false
            const at = grabIndexByTime.get(grab.timestamp)
            return at !== undefined && at >= scopeStart
          })
          .sort((a, b) => (a.timestamp < b.timestamp ? 1 : -1))
          .slice(0, MAX_GRABBED_POOLS)
      : []
    // An equal-level grab is drawn as the pool's own band, ending where it was
    // taken, so the level it stood at is visible. An order-block-only grab
    // draws a label alone: the POI layer already draws that box up to the same
    // invalidation, and a second rectangle over it would be the one wick told
    // twice — the same restraint the fizzle marker uses.
    const backingZone = (grab: LiquidityGrab): LiquidityZone | null => {
      let best: LiquidityZone | null = null
      for (const zone of data.liquidity_zones) {
        if (zone.zone_type !== 'equal_highs' && zone.zone_type !== 'equal_lows') continue
        if (zone.invalidated_at !== grab.timestamp || zone.side !== grab.side) continue
        if (!best || zone.strength > best.strength) best = zone
      }
      return best
    }
    for (const scored of [
      ...poolZones.map((s) => ({ zone: s.zone, score: s.score, grab: null as LiquidityGrab | null })),
      ...grabs.map((grab) => ({ zone: backingZone(grab), score: null as number | null, grab })),
    ]) {
      const { score, grab } = scored
      const zone = scored.zone
      const buySide = grab !== null ? grab.side === 'buy_side' : zone!.zone_type === 'equal_highs'
      const zoneType: LiquidityZoneType = buySide ? 'equal_highs' : 'equal_lows'
      const color = ZONE_COLORS[zoneType] ?? DEFAULT_ZONE_COLOR
      // A grab of order blocks alone is named for what it took, since no
      // equal-level pool was involved; otherwise the pool keeps its own name
      // and the block rides along in the count.
      const label =
        grab !== null && zone === null
          ? 'OB'
          : (ZONE_TYPE_LABELS[zoneType] ?? zoneType)
      let title: string
      if (score !== null && zone !== null) {
        // A standing pool is labelled by how good a target it is. Strength as
        // filled dots: it reports the volume that changed hands at the level
        // while the pool formed, relative to the window — so ●●● is a level
        // the market actually traded at, not merely one it touched three
        // times. The old touch count was pinned at ●●● for almost every pool.
        const dotCount = Math.max(1, Math.min(3, Math.ceil(zone.strength * 3)))
        title = `${label} · ${'●'.repeat(dotCount)} · ${score.toFixed(0)}`
      } else {
        // A taken one is labelled by what happened to it, which is the only
        // thing left to say about it. `⚡` = the wick took the orders and the
        // candle closed back outside; `✕` = a close landed beyond and the
        // level was spent. `×n` counts the pools this one candle consumed —
        // stacked levels are how much was resting there, and the `▣` says an
        // order block was among them.
        const count = grab !== null && grab.pool_count > 1 ? ` ×${grab.pool_count}` : ''
        const block = grab !== null && grab.kinds.includes('order_block') && zone !== null ? ' ▣' : ''
        const mark = grab !== null ? (grab.outcome === 'rejected' ? '⚡' : '✕') : '✕'
        title = `${label} · ${mark}${count}${block}`
      }
      // An order-block-only grab draws no band: the POI layer already draws
      // that box up to the same invalidation, so a second rectangle over it
      // would be the same wick told twice. The tombstone label alone is the
      // addition — the same restraint the fizzle marker uses.
      if (zone === null) {
        const at = toChartTime(grab!.timestamp)
        labels.push({
          time: at,
          timeEnd: at,
          price: grab!.price_level,
          color,
          text: title,
          below: !buySide,
        })
        continue
      }
      // The band runs from the pool's first touch to where it was taken: the
      // stretch it stood is part of the reading, since a level that held for
      // 200 candles before being grabbed is a different pool from one grabbed
      // on the next bar. Clipping a taken pool to the bars just before the
      // grab was tried (to keep an ascending run of grabs from reading as
      // parallel rails) and reverted on visual review — it left each step as a
      // stub floating with no level behind it.
      const startTime = toChartTime(zone.formed_at)
      // A pool stops existing where it was taken, so the band stops there too
      // — the moment of the grab is the reading, and a band running on to the
      // right edge throws it away. Still-standing pools keep the sentinel.
      const endTime = (
        zone.invalidated_at
          ? toChartTime(zone.invalidated_at)
          : ((lastCandleTime + 9_999_999) as UTCTimestamp)
      ) as Time

      eqlZones.push({
        x0: startTime as Time,
        x1: endTime,
        priceLow: zone.price_low,
        priceHigh: zone.price_high,
        color,
        strength: zone.strength,
        swept: zone.is_mitigated,
        rejected: zone.sweep_rejected,
      })
      // Keep the label outside the band: EQH above its upper edge, EQL below
      // its lower edge — never inside the box, where it hides candles.
      // Span the label across the pool (formation -> right edge) as a segment
      // label so it centers on the visible portion and dodges candles instead
      // of pinning at the formation candle, where VSA markers cluster and
      // crowd the read.
      const isEql = zone.zone_type === 'equal_lows'
      labels.push({
        time: startTime,
        timeEnd: endTime,
        price: isEql ? zone.price_low : zone.price_high,
        color,
        text: title,
        below: isEql,
      })
    }
    eqlZonesPrimitiveRef.current?.setZones(eqlZones)

    // Tide ribbon (VWAP envelope x structure x control). Cleared rather than
    // hidden when off, so a toggled-off ribbon costs nothing to draw.
    ribbonPrimitiveRef.current?.setSegments(
      showRibbon
        ? buildRibbon(data).map((b) => ({
            time: toChartTime(b.timestamp),
            upper: b.upper,
            lower: b.lower,
            mid: b.mid,
            trend: b.trend,
            conviction: b.conviction,
            controller: b.controller,
            funded: b.controller !== 'balanced',
          }))
        : [],
    )

    // Phase oscillator, on the control pane's axis. Whitespace entries keep bar
    // indices aligned with the other panes (same rule as the control histogram
    // and RSI) -- the VWAP has no envelope for the first candles of a session.
    const phaseSeries = phaseSeriesRef.current
    if (phaseSeries) {
      const phaseByTs = new Map(buildPhase(data).map((p) => [p.timestamp, p]))
      phaseSeries.setData(
        data.candles.map((candle) => {
          const time = toChartTime(candle.timestamp)
          const p = showRibbon ? phaseByTs.get(candle.timestamp) : undefined
          return p ? { time, value: p.value } : { time }
        }),
      )

      const [railUpper, railLower] = phaseRailSeriesRef.current
      if (railUpper && railLower && data.candles.length >= 2) {
        const firstTime = toChartTime(data.candles[0].timestamp)
        const lastTime = toChartTime(data.candles[data.candles.length - 1].timestamp)
        railUpper.setData(
          showRibbon
            ? [
                { time: firstTime, value: 50 },
                { time: lastTime, value: 50 },
              ]
            : [],
        )
        railLower.setData(
          showRibbon
            ? [
                { time: firstTime, value: -50 },
                { time: lastTime, value: -50 },
              ]
            : [],
        )
      }
    }

    // Swept (mitigated) zones
    if (showSweptZones && data.timeframe !== '5m') {
      const SWEPT_TTL_CANDLES = 200
      const MAX_SWEPT_ZONES = 20
      const ttlCutoff =
        data.candles.length >= SWEPT_TTL_CANDLES
          ? toChartTime(data.candles[data.candles.length - SWEPT_TTL_CANDLES].timestamp)
          : toChartTime(data.candles[0].timestamp)
      const mitigatedZones = data.liquidity_zones
        .filter(
          (z) =>
            z.is_mitigated &&
            (z.zone_type === 'equal_highs' || z.zone_type === 'equal_lows') &&
            z.invalidated_at != null &&
            toChartTime(z.invalidated_at) >= ttlCutoff,
        )
        .sort((a, b) => Date.parse(b.invalidated_at!) - Date.parse(a.invalidated_at!))
        .slice(0, MAX_SWEPT_ZONES)
      for (const zone of mitigatedZones) {
        const color = ZONE_COLORS[zone.zone_type] ?? DEFAULT_ZONE_COLOR
        const label = ZONE_TYPE_LABELS[zone.zone_type] ?? zone.zone_type
        const price = (zone.price_high + zone.price_low) / 2
        const startTime = toChartTime(zone.formed_at)
        const endTime = zone.invalidated_at ? toChartTime(zone.invalidated_at) : lastCandleTime

        const sweptSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
          color: color + '4d',
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        sweptSeries.setData(lineFrom(startTime, endTime, price, firstCandleTime))
        overlaySeriesRef.current.push(sweptSeries)
        labels.push({
          time: startTime,
          price,
          color: color + '66',
          text: `${label} (swept)`,
        })
      }
    }

    // Structure events: all timeframes render the internal-structure detector,
    // with liquidity sweeps capped to the most recent few so the chart stays
    // readable.
    const scopeEvents = data.internal_structure_events

    const recentSweeps = new Set(
      scopeEvents
        .filter((e) => e.event === 'liquidity_sweep')
        .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))
        .slice(0, MAX_INTERNAL_SWEEPS),
    )

    const structureEvents = scopeEvents.filter(
      (event) =>
        event.event in STRUCTURE_EVENT_STYLES &&
        (event.event === 'liquidity_sweep'
          ? showSweeps && recentSweeps.has(event)
          : showSmc),
    )

    // The event that flipped this timeframe counter to its higher timeframe —
    // the liquidity hunt's window start. Its label gets a ⚠ suffix: the
    // entrants of that break are the resting liquidity being hunted. Only the
    // *standing* flip is marked; historical events would need the HTF trend as
    // of their own time, which a snapshot does not carry.
    const huntFlipTimestamp =
      data.liquidity_hunt && data.liquidity_hunt.phase !== 'none'
        ? data.liquidity_hunt.counter_structure_timestamp
        : null

    // OI qualification per structure event (keyed by timestamp + type), so
    // each BOS/CHoCH/SWEEP label can carry its participation suffix.
    const oiSuffixByEvent = new Map<string, string>()
    for (const qualified of data.oi_analysis?.qualified_events ?? []) {
      const suffix = OI_PARTICIPATION_SUFFIX[qualified.participation]
      if (suffix) {
        oiSuffixByEvent.set(`${qualified.event_timestamp}|${qualified.event_type}`, suffix)
      }
    }

    // Structure-confluence badge per event (keyed by timestamp + type): how
    // many orthogonal reads (VSA / OB / OI / volume delta / sweep) confirm the
    // break. Shown as `✦N` for 2+ confirming factors — a single factor is too
    // weak to flag.
    // A provisional mark (`BOS?`/`CHoCH?`) is qualified on past-only evidence,
    // so its tally is partial and can only grow — badged `✦N~`.
    const confluenceByEvent = new Map<string, { count: number; partial: boolean }>()
    for (const conf of data.structure_confluence ?? []) {
      if (conf.factors.length < 2) continue
      const key = `${conf.event_timestamp}|${conf.event_type}`
      // A confirmed reading always wins over a provisional one at the same key.
      if (conf.provisional && confluenceByEvent.get(key)?.partial === false) continue
      confluenceByEvent.set(key, { count: conf.factors.length, partial: conf.provisional })
    }

    for (const event of structureEvents) {
      // A CHoCH that later failed is represented solely by its `CHoCH ✕`
      // marker (which spans the same origin->failure lifetime). Drawing the
      // original CHoCH line too would plot two overlapping CHoCHs, so skip it —
      // the failure mark replaces it. (Fizzle markers are excluded from
      // `isFailedChoch`, so a fizzled CHoCH still renders normally.)
      if (event.event === 'change_of_character' && isFailedChoch(event, scopeEvents)) {
        continue
      }
      const style = STRUCTURE_EVENT_STYLES[event.event]
      const oiSuffix = oiSuffixByEvent.get(`${event.timestamp}|${event.event}`)
      const confluence = confluenceByEvent.get(`${event.timestamp}|${event.event}`)
      const confluenceSuffix = confluence
        ? ` ✦${confluence.count}${confluence.partial ? '~' : ''}`
        : ''
      // BOS/CHoCH are colored by direction (green bullish, red bearish), so
      // their labels drop the ▲/▼ arrow — the color already says it. Neutral
      // events (Sweep, CHoCH ✕) keep their own color and the arrow.
      const directionColored =
        event.event === 'break_of_structure' || event.event === 'change_of_character'
      const baseColor =
        (directionColored ? STRUCTURE_DIRECTION_COLORS[event.direction] : undefined) ??
        style.color
      const directionIcon = directionColored ? '' : (TREND_ICONS[event.direction] ?? '')
      const startTime = toChartTime(event.timestamp)
      const linePrice =
        (event.event === 'change_of_character' ||
          event.event === 'choch_failed' ||
          event.event === 'break_of_structure') &&
        event.reference_price_level != null
          ? event.reference_price_level
          : event.price_level

      // A failed CHoCH is a point-in-time invalidation, not a live reference
      // level: its line spans only the CHoCH's own lifetime (the broken level's
      // origin -> the failure candle) and never runs forward into later price
      // action the way a BOS/CHoCH reference line does.
      let endTime: UTCTimestamp
      if (event.event === 'choch_failed') {
        endTime = startTime
      } else if (event.event === 'liquidity_sweep') {
        // Short segment: from the sweep wick out a few candles, not to the edge.
        const idx = data.candles.findIndex((c) => c.timestamp === event.timestamp)
        const endIdx =
          idx >= 0
            ? Math.min(idx + SWEEP_LINE_CANDLES, data.candles.length - 1)
            : data.candles.length - 1
        endTime = toChartTime(data.candles[endIdx].timestamp)
      } else {
        // A *confirmed* BOS is bounded to a short segment past its break
        // candle; a provisional `BOS?` keeps running to the edge (it is the
        // live reading, and shortening it would hide what is still forming).
        let bosTrailEnd: UTCTimestamp | undefined
        if (event.event === 'break_of_structure' && event.provisional !== true) {
          const idx = data.candles.findIndex((c) => c.timestamp === event.timestamp)
          const endIdx =
            idx >= 0
              ? Math.min(idx + BOS_LINE_TRAIL_CANDLES, data.candles.length - 1)
              : data.candles.length - 1
          bosTrailEnd = toChartTime(data.candles[endIdx].timestamp)
        }
        endTime = structureLineEndTime(event, scopeEvents, lastCandleTime, bosTrailEnd)
      }

      const lineStartTime =
        (event.event === 'change_of_character' ||
          event.event === 'break_of_structure' ||
          event.event === 'choch_failed') &&
        event.reference_timestamp != null
          ? toChartTime(event.reference_timestamp)
          : startTime

      // A CHoCH that broke a *weak* reference (a re-anchor/fallback level or a
      // wick-only-break promotion -- the ones the new-cycle persistence barrier
      // governs) renders dotted and dimmed with a `*` label suffix, so a
      // conservative-sequence CHoCH (structural leg origin) is tellable at a
      // glance.
      const weakChoch =
        event.event === 'change_of_character' && event.reference_structural === false
      // A provisional BOS is a live-edge continuation whose floor already
      // closed-broke but whose confirming swing pivots have not formed yet.
      // Same dimmed/dotted treatment as a weak CHoCH, with a `?` suffix
      // (`BOS? ▼`): it is superseded by the confirmed BOS once pivots form, or
      // vanishes if the trend flips first.
      const provisionalBos =
        event.event === 'break_of_structure' && event.provisional === true
      // A provisional CHoCH is the mirror for a live-edge *reversal*: a
      // structural CHoCH reference has been sustained-closed-broken but its
      // confirming swing pivot has not formed yet. Same dimmed/dotted treatment
      // with a `?` suffix (`CHoCH? ▼`): superseded by the confirmed CHoCH once
      // the pivot forms, or it vanishes if price reclaims the level (a sweep).
      const provisionalChoch =
        event.event === 'change_of_character' && event.provisional === true
      // A fizzle marker (provisional `choch_failed`) never replaces its
      // CHoCH's line -- the fizzled CHoCH still renders normally and its own
      // line already stops at the reclaim -- so drawing the marker's line
      // would trace the exact same segment twice. Label only, anchored at
      // the reclaim candle.
      const fizzleMarker = event.event === 'choch_failed' && event.provisional === true
      // A re-fired (re-activated) CHoCH: its re-arm reference carries the
      // failure's own timestamp, so a prior same-direction real `CHoCH ✕`
      // sitting exactly at `reference_timestamp` identifies it. Rendered with
      // a `↻` suffix so a re-activation is tellable from a fresh CHoCH.
      // Provisional marks qualify too: the live-edge CHoCH now resolves its
      // reference against the re-arm as well, so a forming re-fire reads
      // `CHoCH? ↻ ▼` and its line starts at the `✕` it resumes from.
      const reactivatedChoch =
        event.event === 'change_of_character' &&
        event.reference_timestamp != null &&
        scopeEvents.some(
          (other) =>
            other.scope === event.scope &&
            other.event === 'choch_failed' &&
            other.provisional !== true &&
            other.direction === event.direction &&
            other.timestamp === event.reference_timestamp,
        )
      const dimmed = weakChoch || provisionalBos || provisionalChoch
      const lineColor = dimmed ? `${baseColor}99` : baseColor
      // A provisional mark against a weak reference (emit_provisional_choch_weak)
      // is both forming and weak: `?` (the stronger caveat -- it may repaint
      // entirely) leads, with `*` appended (`CHoCH?* ▲`).
      const labelSuffix =
        provisionalBos || provisionalChoch
          ? weakChoch
            ? '?*'
            : '?'
          : weakChoch
            ? '*'
            : ''
      const counterHtfFlip =
        huntFlipTimestamp != null &&
        event.timestamp === huntFlipTimestamp &&
        !event.provisional &&
        (event.event === 'change_of_character' ||
          event.event === 'break_of_structure' ||
          event.event === 'choch_failed')

      if (!fizzleMarker) {
        const isSweep = event.event === 'liquidity_sweep'
        const structureSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
          color: lineColor,
          lineWidth: isSweep ? 2 : 1,
          lineStyle: isSweep
            ? LineStyle.Dotted
            : dimmed
              ? LineStyle.SparseDotted
              : LineStyle.Dashed,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        structureSeries.setData(lineFrom(lineStartTime, endTime, linePrice, firstCandleTime))
        overlaySeriesRef.current.push(structureSeries)
      }

      // Centered on the line segment (TradingView-style): the break candle
      // sits at one end of the line, where the label would be buried in the
      // candles -- the middle of the drawn segment is the open gap. A
      // line-less fizzle marker anchors at the reclaim candle instead.
      // TradingView-style placement: bullish labels sit above their line,
      // bearish ones below, so the label always hangs on the side price broke
      // *from* and stays out of the move that followed.
      //
      // A sweep is the exception: its line sits at the wick *extreme*
      // (`price_level`), which is exactly where VSA arrow markers anchor
      // (reversal patterns pin to the bar's high/low). Hanging the sweep label
      // on the wick-tip side stacks it on the VSA arrow. Flip it to the inside
      // (toward the candle body) so the two layers read apart.
      const labelBelow =
        event.event === 'liquidity_sweep'
          ? event.direction === 'bullish'
          : event.direction === 'bearish'
      labels.push({
        time: fizzleMarker ? startTime : lineStartTime,
        timeEnd: fizzleMarker ? startTime : endTime,
        price: linePrice,
        color: lineColor,
        below: labelBelow,
        text: `${style.label}${labelSuffix}${reactivatedChoch ? ' ↻' : ''}${directionIcon ? ` ${directionIcon}` : ''}${oiSuffix ? ` ${oiSuffix}` : ''}${counterHtfFlip ? ' ⚠' : ''}${confluenceSuffix}`,
      })
    }

    // POI order block zones (MSB-anchored; box starts at the OB candle)
    {
      const poiBoxes: POIBox[] = []
      for (const zone of showOrderBlocks ? selectVisiblePoiZones(data.poi_zones ?? []) : []) {
        const style = POI_BOX_STYLES[zone.direction] ?? POI_BOX_STYLES.bearish
        const endTime = poiBoxEndTime(zone, lastCandleTime)
        // No direction arrow: the box color already encodes it.
        const kindLabel = POI_KIND_LABEL

        poiBoxes.push({
          x0: toChartTime(zone.ob_candle_timestamp),
          x1: endTime,
          priceLow: zone.price_low,
          priceHigh: zone.price_high,
          borderColor: style.border,
          fillColor: style.fill,
          label: kindLabel,
        })
      }
      poiBoxesPrimitiveRef.current?.setBoxes(poiBoxes)
    }

    // Manipulation cycle accumulation boxes
    const manipBoxes = showManipulationBoxes
      ? buildManipulationBoxes(data.manipulation_cycles ?? [], lastCandleTime)
      : []
    manipBoxesPrimitiveRef.current?.setBoxes(manipBoxes)

    // Consolidation (lateral range) boxes: the stretches where the structure
    // detector was correctly silent, made explicit. A live (unresolved) range
    // extends to the right edge via the far-future sentinel clamp.
    const rangeBoxes: POIBox[] = []
    for (const range of showConsolidationRanges ? (data.consolidation_ranges ?? []) : []) {
      const style = CONSOLIDATION_BOX_STYLES[range.status] ?? CONSOLIDATION_BOX_STYLES.active
      // Label is just the resolution arrow (nothing while the range is live):
      // the box itself already reads as "lateral", the RANGE text was noise.
      const resolvedIcon =
        range.resolved_direction != null ? (TREND_ICONS[range.resolved_direction] ?? '') : ''
      rangeBoxes.push({
        x0: toChartTime(range.start_timestamp),
        x1: range.end_timestamp
          ? toChartTime(range.end_timestamp)
          : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        priceLow: range.price_low,
        priceHigh: range.price_high,
        borderColor: style.border,
        fillColor: style.fill,
        label: resolvedIcon,
      })
    }
    rangeBoxesPrimitiveRef.current?.setBoxes(rangeBoxes)

    // VWAP: what the tape paid, not where it went. The session line restarts
    // each UTC day (one series per accumulation, see `buildVwapSegments`); its
    // ±1σ/±2σ bands are how widely that day's volume was spread, drawn thin so
    // the average itself stays the reading — and only in the `bands` mode.
    for (const segment of vwapMode === 'off' ? [] : buildVwapSegments(data.vwap)) {
      for (const [points, color] of (vwapMode === 'bands'
        ? [
            [segment.upper2, VWAP_BAND_2_COLOR],
            [segment.lower2, VWAP_BAND_2_COLOR],
            [segment.upper1, VWAP_BAND_1_COLOR],
            [segment.lower1, VWAP_BAND_1_COLOR],
          ]
        : []) as readonly (readonly [{ time: Time; value: number }[], string])[]) {
        if (points.length === 0) continue
        const bandSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        bandSeries.setData(points)
        overlaySeriesRef.current.push(bandSeries)
      }
      const vwapSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
        color: VWAP_COLOR,
        lineWidth: VWAP_LINE_WIDTH,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      vwapSeries.setData(segment.value)
      overlaySeriesRef.current.push(vwapSeries)
    }

    // Anchored VWAPs: one unbroken line each (a single accumulation), tagged
    // on the price scale with what it is anchored to — the break-even of the
    // crowd that entered on that CHoCH or sweep, which is what makes the level
    // worth reading at all.
    const anchoredVwaps = showAnchoredVwap ? (data.anchored_vwaps ?? []) : []
    anchoredVwaps.forEach((series, index) => {
      const color = VWAP_ANCHORED_COLORS[index % VWAP_ANCHORED_COLORS.length]
      for (const segment of buildVwapSegments(series)) {
        const anchoredSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
          color,
          lineWidth: VWAP_ANCHORED_LINE_WIDTH,
          lineStyle: LineStyle.Dashed,
          title: `VWAP ${series.label}`.trim(),
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        })
        anchoredSeries.setData(segment.value)
        overlaySeriesRef.current.push(anchoredSeries)
      }
    })

    // Supertrend: one line series per same-trend run, drawn at the active
    // band (a floor under price while bullish, a ceiling above it while
    // bearish). The flip reads from the break between runs, so it carries no
    // marker of its own. Segments live in the overlay pool, so they are torn
    // down with the rest of the overlays on the next render.
    for (const segment of showSupertrend ? buildSupertrendSegments(data.supertrend ?? []) : []) {
      const stSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
        color: segment.direction === 'bullish' ? SUPERTREND_UP_COLOR : SUPERTREND_DOWN_COLOR,
        lineWidth: SUPERTREND_LINE_WIDTH,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      stSeries.setData(segment.points)
      overlaySeriesRef.current.push(stSeries)
    }

    // False breaks of the band: a dashed run along the level from the break to
    // the give-back. Lives under the same toggle as the band it annotates.
    const stopRuns = showSupertrend ? (data.supertrend_breaks ?? []) : []
    for (const run of buildStopRunSegments(stopRuns)) {
      const runSeries = chart.addSeries(LineSeries, {
        ...OVERLAY_SCALE_EXEMPT,
        color: SUPERTREND_STOP_RUN_COLOR,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      })
      runSeries.setData(run.points)
      overlaySeriesRef.current.push(runSeries)
    }

    // Behavior divergence + VSA markers share one marker plugin (a series
    // holds a single marker set), merged and re-sorted ascending by time.
    const divMarkers = showDivergenceMarkers
      ? buildDivergenceMarkers(data.behavior_divergences ?? [])
      : []
    const vsaMarkers = buildVsaMarkers(vsaSignals)
    const stopRunMarkers = buildStopRunMarkers(stopRuns)
    // A structural level stands exactly as long as its line is drawn, so the
    // evidence counted here is the evidence on screen. `structureLineEndTime`
    // answers in chart time; map it back to the ISO timestamp the level rule
    // compares against, and treat "runs to the edge" as still standing.
    const defendedMarkers = showDefendedLevels
      ? buildDefendedMarkers(
          buildDefendedMarks(
            data,
            buildDefenceLevels(data, (event) => {
              const end = structureLineEndTime(event, scopeEvents, lastCandleTime)
              if (end >= lastCandleTime) return null
              return (
                scopeEvents.find((other) => toChartTime(other.timestamp) === end)?.timestamp ?? null
              )
            }),
          ),
        )
      : []
    const mergedMarkers = [...divMarkers, ...vsaMarkers, ...stopRunMarkers, ...defendedMarkers].sort(
      (a, b) => (a.time as number) - (b.time as number),
    )
    divergenceMarkersRef.current?.setMarkers(mergedMarkers)

    // Exhaustion/absorption divergences render as curved arcs, not markers.
    divergenceArcsPrimitiveRef.current?.setArcs(
      showDivergenceMarkers
        ? buildDivergenceArcs(
            data.behavior_divergences ?? [],
            data.volume_spread_signals ?? [],
            data.candles,
          )
        : [],
    )

    // Liquidity heatmap strip
    const heatmapBands: HeatmapBand[] =
      showHeatmap && data.liquidity_heatmap
        ? data.liquidity_heatmap.buckets.map((bucket) => ({
            priceLow: bucket.price_low,
            priceHigh: bucket.price_high,
            heat: bucket.heat,
          }))
        : []
    heatmapPrimitiveRef.current?.setBands(heatmapBands)

    // Volume-at-price over the visible window (POC / value area / HVN-LVN).
    const profile = showVolumeProfile ? data.volume_profile : null
    const volumeProfileBars: VolumeProfileBar[] = profile
      ? profile.buckets.map((bucket) => ({
          priceLow: bucket.price_low,
          priceHigh: bucket.price_high,
          volume: bucket.volume,
          buyVolume: bucket.buy_volume,
          inValueArea: bucket.in_value_area,
          isPoc: bucket.is_poc,
        }))
      : []
    volumeProfilePrimitiveRef.current?.setProfile(
      volumeProfileBars,
      profile
        ? {
            poc: profile.poc_price,
            valueAreaLow: profile.value_area_low,
            valueAreaHigh: profile.value_area_high,
            startTime: toChartTime(profile.start_timestamp),
          }
        : null,
      volumeProfileMode,
    )


    // Liquidity-hunt window: full-height shading from the counter-trend flip
    // to the capture that concluded the hunt (right edge while still running).
    // Amber while the counter-trend entrants are still being consumed, green
    // once the mapped pools were captured and OI stopped unwinding.
    const hunt = data.liquidity_hunt
    const huntWindows: HuntWindow[] = []
    const history = data.liquidity_hunt_history ?? []
    if (showHuntWindow) {
      // Concluded hunts earlier in the window: dim green shaded bands with a ✓,
      // each ending at the liquidity grab that closed it (short, near-term).
      for (const episode of history) {
        // Direction = a bold arrow to the raided side (shorts hunted → stops
        // above → ▲); the label carries status only, so many overlapping bands
        // stay legible without the long "shorts hunted" word repeating.
        const arrow = episode.hunted_side === 'short' ? 'up' : 'down'
        // Exhaustion grab (stops run on no new money at the grab candle — CVD×OI)
        // is reversal-prone: purple with a ⚠; a genuine break stays green ✓. What
        // closed the hunt (sources + score) stays in the hover title.
        const exhaustion = episode.capture_quality === 'exhaustion_grab'
        // A failed-reversal grab is the high-water mark of the whole move (a
        // capture-direction CHoCH that ran the stops there and was invalidated),
        // not one floor in a series — the leg's *principal* hunt. It gets its
        // own rose tone and a stronger fill so it reads as the peak at a
        // glance, ahead of the exhaustion/genuine distinction.
        const color = episode.failed_reversal
          ? '#ec407a'
          : exhaustion
            ? '#ab47bc'
            : '#26a69a'
        huntWindows.push({
          x0: toChartTime(episode.start_timestamp),
          x1: toChartTime(episode.end_timestamp),
          color,
          fillColor: color + (episode.failed_reversal ? '1f' : '0d'),
          arrow,
          // Arrow only — status stays encoded in the color (rose = peak,
          // purple = exhaustion, green = genuine); full detail in the hover.
        })
      }
    }
    if (showHuntWindow && hunt && hunt.phase !== 'none' && hunt.counter_structure_timestamp) {
      const captured = hunt.phase === 'captured'
      // An exhaustion-grab capture (stops run on no new money — CVD×OI) is
      // reversal-prone: shade it purple with a distinct label instead of the
      // green "cleared" of a genuine break.
      const exhaustion = captured && hunt.capture_quality === 'exhaustion_grab'
      const color = exhaustion ? '#ab47bc' : captured ? '#26a69a' : '#ff9800'
      const arrow = hunt.hunted_side === 'short' ? 'up' : 'down'
      // The live window is the *pending* grab only: start it at the last grab
      // already captured in this leg (the latest history episode ending at or
      // after the flip), not the original flip — so it stays near-term and
      // doesn't overlap the green completed hunts.
      const flip = hunt.counter_structure_timestamp
      const lastGrab = history
        .filter((e) => e.end_timestamp >= flip)
        .reduce<string | null>(
          (acc, e) => (acc === null || e.end_timestamp > acc ? e.end_timestamp : acc),
          null,
        )
      huntWindows.push({
        x0: toChartTime(lastGrab ?? flip),
        x1:
          captured && hunt.captured_at
            ? toChartTime(hunt.captured_at)
            : ((lastCandleTime + 9_999_999) as UTCTimestamp),
        color,
        fillColor: color + '0d',
        arrow,
        // Arrow only — status stays in the color (amber = hunting, green =
        // captured, purple = exhaustion capture); detail in the hover.
      })
    }
    // Aligned trend-continuation grabs: a separate regime (a leg with the HTF
    // that pulled back, swept internal liquidity, then resumed). Drawn in blue
    // and toggled independently so it never blends with the counter-trend hunt.
    if (showContinuationWindow) {
      const continuation = data.liquidity_continuation_history ?? []
      for (const episode of continuation) {
        const arrow = episode.correction_direction === 'bullish' ? '↗' : '↘'
        const dirWord =
          episode.correction_direction === 'bullish' ? 'bull' : 'bear'
        huntWindows.push({
          x0: toChartTime(episode.start_timestamp),
          x1: toChartTime(episode.end_timestamp),
          color: '#42a5f5',
          fillColor: '#42a5f50d',
          label: `${arrow} ${dirWord} continuation`,
        })
      }
    }
    huntWindowPrimitiveRef.current?.setWindows(huntWindows)

    labelsPrimitiveRef.current?.setLabels(labels)
    // Feed the candles' wick extents to the labels primitive so segment
    // labels (BOS/CHoCH/…) can slide along their line to a candle-free spot.
    const labelCandles = data.candles.map((c) => ({
      time: toChartTime(c.timestamp) as Time,
      high: c.high,
      low: c.low,
    }))
    labelsPrimitiveRef.current?.setCandles(labelCandles)
    // Box labels (OB/MB, accumulation, range) dodge candles the same way.
    poiBoxesPrimitiveRef.current?.setCandles(labelCandles)
    manipBoxesPrimitiveRef.current?.setCandles(labelCandles)
    rangeBoxesPrimitiveRef.current?.setCandles(labelCandles)

    if (!hasFittedRef.current) {
      const range = resetViewRange(
        data.candles.length,
        mainContainerRef.current?.clientWidth ?? 0,
        profile ? volumeProfileReservedBars(RESET_BAR_SPACING_PX) : 0,
      )
      chart.timeScale().setVisibleLogicalRange(range)
      deltaChart.timeScale().setVisibleLogicalRange(range)
      controlChartRef.current?.timeScale().setVisibleLogicalRange(range)
      rsiChart.timeScale().setVisibleLogicalRange(range)
      hasFittedRef.current = true
    }

  }, [drawSig, showConsolidationRanges, showManipulationBoxes, showDivergenceMarkers, vsaMode, showHeatmap, showSweptZones, showOrderBlocks, showSweeps, showSmc, showEqlZones, showHuntWindow, showContinuationWindow, showVolume, showRsiDivergence, showSupertrend, vwapMode, showAnchoredVwap, showVolumeProfile, volumeProfileMode, showRibbon, showDefendedLevels])

  // Incremental live-price update: the forming candle, and the fixed-reference
  // series derived from it, refreshed in place on every poll. This runs on the
  // `data` identity (i.e. every refresh) and is deliberately cheap — no series
  // is created or destroyed, and nothing is re-laid-out — so the 5s live tick
  // costs a handful of `update()` calls instead of a full chart rebuild. The
  // structural picture is redrawn by the effect above, when `drawSig` changes.
  useEffect(() => {
    const series = seriesRef.current
    const last = data.candles[data.candles.length - 1]
    if (!series || !last) return

    const time = toChartTime(last.timestamp)
    series.update({
      time,
      open: last.open,
      high: last.high,
      low: last.low,
      close: last.close,
    })

    if (showVolume) {
      volumeSeriesRef.current?.update({
        time,
        value: last.volume,
        color: last.close >= last.open ? VOLUME_UP_COLOR : VOLUME_DOWN_COLOR,
      })
    }

    const deltaSeries = deltaSeriesRef.current
    if (deltaSeries) {
      const vsaSignal =
        vsaMode === 'off'
          ? undefined
          : (data.volume_spread_signals ?? []).find((s) => s.timestamp === last.timestamp)
      deltaSeries.update({
        time,
        value: 2 * last.taker_buy_volume - last.volume,
        color:
          (vsaSignal ? VSA_STYLES[vsaSignal.pattern]?.color : undefined) ??
          (last.close >= last.open ? CANDLE_UP_COLOR : CANDLE_DOWN_COLOR),
      })
    }

    const controlSeries = controlSeriesRef.current
    const controlPoint = data.market_control?.series?.find((p) => p.timestamp === last.timestamp)
    if (controlSeries && controlPoint) {
      controlSeries.update({
        time,
        value: controlPoint.control_score,
        color: CONTROL_REGIME_COLORS[controlPoint.regime] ?? CONTROL_BALANCED_COLOR,
      })
    }

    // The phase reading moves with the live close, so the tail update has to
    // recompute it rather than just carry the last value forward.
    const phaseSeries = phaseSeriesRef.current
    if (phaseSeries) {
      const phaseLast = buildPhase(data).at(-1)
      if (phaseLast && phaseLast.timestamp === last.timestamp) {
        phaseSeries.update({ time, value: phaseLast.value })
      }
    }

    const rsiSeries = rsiSeriesRef.current
    if (rsiSeries) {
      const rsiValues = computeRSI(
        data.candles.map((c) => c.close),
        RSI_PERIOD,
      )
      const lastRsi = rsiValues[rsiValues.length - 1]
      if (lastRsi !== null && lastRsi !== undefined) rsiSeries.update({ time, value: lastRsi })
    }
  }, [data, showVolume, vsaMode])

  return (
    <div ref={wrapperRef} className="flex min-h-0 w-full flex-1 flex-col">
      <div ref={mainContainerRef} className="w-full" />
      <div className={`relative w-full border-t border-[#1e222d] ${showIndicators ? '' : 'hidden'}`}>
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Volume Delta
        </span>
        <div ref={deltaContainerRef} className="w-full" />
      </div>
      <div
        className={`relative w-full border-t border-[#1e222d] ${
          showControlOscillator ? '' : 'hidden'
        }`}
      >
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          Control (CVD×OI)
        </span>
        <div ref={controlContainerRef} className="w-full" />
      </div>
      <div className={`relative w-full border-t border-[#1e222d] ${showIndicators ? '' : 'hidden'}`}>
        <span className="pointer-events-none absolute left-2 top-1 z-10 text-xs text-[#8a8f9c]">
          RSI ({RSI_PERIOD})
        </span>
        <div ref={rsiContainerRef} className="w-full" />
      </div>
    </div>
  )
}
