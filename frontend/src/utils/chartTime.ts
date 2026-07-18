import type { UTCTimestamp } from 'lightweight-charts'

import type { TimeFrame } from '../types/dashboard'

// Lightweight Charts has no timezone support: it renders every `UTCTimestamp`
// in UTC. A 15m candle printed at 21:30 in São Paulo therefore labels 00:30 on
// the *next* day — a three-hour lie to anyone reading a local clock, and one
// that twice sent this project's structure review chasing the wrong candle
// ("the BOS at 05:15" was really 02:15 local). The library's documented
// workaround is to shift the timestamps by the viewer's UTC offset before
// handing them over, which is what `toChartTime` does — hence the name: the
// returned value is a *chart* coordinate, no longer a true UTC timestamp.
//
// Daily and weekly candles are exempt. Their timestamp *is* the exchange day
// (00:00 UTC), so shifting would relabel the 14 Jul daily bar as "13 Jul
// 21:00". Those timeframes keep exchange time, like every other platform.
const EXCHANGE_TIME_TIMEFRAMES: ReadonlySet<TimeFrame> = new Set<TimeFrame>(['1d', '1w'])

// The offset is module state rather than a per-call argument because *every*
// chart time — candles, overlay series, canvas primitives, and the pure
// helpers in `MainChart` that compare event times against candle times — flows
// through `toChartTime`, and they must all share one offset to stay mutually
// consistent (the comparisons are shift-invariant only while the shift is
// uniform). `MainChart` sets it during render, before the memos that consume
// it, and is remounted on every symbol/timeframe change, so a stale mode
// cannot outlive the data it was set for.
let usesLocalTime = true

export function setChartTimezoneMode(timeframe: TimeFrame): void {
  usesLocalTime = !EXCHANGE_TIME_TIMEFRAMES.has(timeframe)
}

export function toChartTime(isoTimestamp: string): UTCTimestamp {
  const ms = Date.parse(isoTimestamp)
  if (!usesLocalTime) return (ms / 1000) as UTCTimestamp
  // `getTimezoneOffset()` is minutes *behind* UTC (UTC-3 → +180), so subtract
  // to move the instant onto the local wall clock. Evaluated per timestamp, so
  // candles on either side of a DST transition each get their own offset.
  return ((ms - new Date(ms).getTimezoneOffset() * 60_000) / 1000) as UTCTimestamp
}

// The label for the chart's time axis, so which clock the chart speaks is never
// a guess: the viewer's UTC offset intraday, plain `UTC` on the exchange-time
// timeframes (or anywhere the viewer already sits at UTC).
export function chartTimezoneLabel(timeframe: TimeFrame): string {
  if (EXCHANGE_TIME_TIMEFRAMES.has(timeframe)) return 'UTC'
  const minutes = -new Date().getTimezoneOffset()
  if (minutes === 0) return 'UTC'
  const sign = minutes < 0 ? '-' : '+'
  const abs = Math.abs(minutes)
  const hours = Math.floor(abs / 60)
  const rest = abs % 60
  return `UTC${sign}${hours}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`
}
