import type {
  MarketDirection,
  MarketOverview,
  StructureEvent,
  TimeFrame,
  TimeframeOverview,
} from '../types/dashboard'
import { CollapsibleSection } from './CollapsibleSection'

const TF_LABELS: Record<string, string> = {
  '1m': '1M',
  '5m': '5M',
  '15m': '15M',
  '30m': '30M',
  '1h': '1H',
  '4h': '4H',
  '1d': '1D',
  '1w': '1W',
}

const TREND_STYLES: Record<MarketDirection, { icon: string; label: string; color: string }> = {
  bullish: { icon: '▲', label: 'BULL', color: '#26a69a' },
  bearish: { icon: '▼', label: 'BEAR', color: '#ef5350' },
  neutral: { icon: '◆', label: 'FLAT', color: '#5d6477' },
}

const EVENT_LABELS: Partial<Record<StructureEvent, string>> = {
  break_of_structure: 'BOS',
  change_of_character: 'CHoCH',
  choch_failed: 'CHoCH ✕',
  liquidity_sweep: 'SWEEP',
}

const HUNT_STYLES: Record<string, { icon: string; color: string; title: string }> = {
  counter_trend: { icon: '⚠', color: '#ef5350', title: 'counter-trend — opposing pools intact' },
  hunt_in_progress: { icon: '⚡', color: '#ffb300', title: 'hunt in progress' },
  captured: { icon: '✓', color: '#26a69a', title: 'pools captured' },
}

function directionArrow(direction: MarketDirection | null): string {
  if (direction === 'bullish') return '▲'
  if (direction === 'bearish') return '▼'
  return ''
}

function directionColor(direction: MarketDirection | null): string {
  if (direction === 'bullish') return '#26a69a'
  if (direction === 'bearish') return '#ef5350'
  return '#5d6477'
}

function rowTitle(entry: TimeframeOverview): string {
  const parts = [`${TF_LABELS[entry.timeframe] ?? entry.timeframe} ${entry.trend}`]
  if (entry.higher_timeframe && entry.higher_timeframe_direction) {
    parts.push(`vs ${TF_LABELS[entry.higher_timeframe] ?? entry.higher_timeframe} ${entry.higher_timeframe_direction}`)
  }
  if (entry.last_event) {
    const label = EVENT_LABELS[entry.last_event] ?? entry.last_event
    parts.push(`${label} ${entry.last_event_direction ?? ''} ${entry.last_event_candles_ago ?? '?'} candles ago`)
  }
  if (entry.forming_event) {
    parts.push(`${EVENT_LABELS[entry.forming_event] ?? entry.forming_event}? forming`)
  }
  if (entry.in_consolidation) {
    parts.push(
      `consolidating${entry.consolidation_candles != null ? ` for ${entry.consolidation_candles} candles` : ''}`,
    )
  }
  if (entry.hunt_phase !== 'none') {
    parts.push(
      `hunting ${entry.hunted_side}s ${entry.hunt_targets_captured}/${entry.hunt_targets_total} pools`,
    )
  }
  return parts.join(' · ')
}

function TimeframeRow({
  entry,
  active,
  onSelect,
}: {
  entry: TimeframeOverview
  active: boolean
  onSelect: () => void
}) {
  const trend = TREND_STYLES[entry.trend]
  const eventLabel = entry.last_event ? (EVENT_LABELS[entry.last_event] ?? null) : null
  const formingLabel = entry.forming_event
    ? (EVENT_LABELS[entry.forming_event] ?? null)
    : null
  const hunt = entry.hunt_phase !== 'none' ? HUNT_STYLES[entry.hunt_phase] : null

  return (
    <button
      type="button"
      onClick={onSelect}
      title={rowTitle(entry)}
      className="flex w-full items-center gap-2 rounded-md border px-2 py-[5px] text-left transition-colors duration-150"
      style={{
        borderColor: active ? '#2962ff50' : '#1a1f2e',
        backgroundColor: active ? '#2962ff10' : '#0a0d14',
      }}
    >
      {/* Timeframe */}
      <span
        className="w-7 flex-none font-mono text-[10px] font-bold tracking-wide"
        style={{ color: active ? '#e1e4ec' : '#9ca3b4' }}
      >
        {TF_LABELS[entry.timeframe] ?? entry.timeframe}
      </span>

      {/* Trend */}
      <span
        className="flex w-12 flex-none items-center gap-1 text-[10px] font-bold"
        style={{ color: trend.color }}
      >
        <span>{trend.icon}</span>
        <span className="tracking-wide">{trend.label}</span>
      </span>

      {/* Last structural event */}
      <span className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden whitespace-nowrap font-mono text-[9px]">
        {eventLabel && (
          <>
            <span style={{ color: directionColor(entry.last_event_direction) }}>
              {eventLabel} {directionArrow(entry.last_event_direction)}
            </span>
            {entry.last_event_candles_ago != null && (
              <span className="text-[#3d4455]">·{entry.last_event_candles_ago}c</span>
            )}
          </>
        )}
        {formingLabel && (
          <span className="opacity-60" style={{ color: directionColor(entry.forming_direction) }}>
            {formingLabel}? {directionArrow(entry.forming_direction)}
          </span>
        )}
        {entry.in_consolidation && (
          <span className="text-[#90a4ae]" title="Price is inside a confirmed lateral range">
            ▭ RANGE
            {entry.consolidation_candles != null && (
              <span className="text-[#3d4455]"> ·{entry.consolidation_candles}c</span>
            )}
          </span>
        )}
      </span>

      {/* Hunt phase */}
      {hunt && (
        <span
          className="flex-none font-mono text-[9px] font-bold"
          style={{ color: hunt.color }}
        >
          {hunt.icon}
          {entry.hunt_phase === 'hunt_in_progress' &&
            ` ${entry.hunt_targets_captured}/${entry.hunt_targets_total}`}
        </span>
      )}
    </button>
  )
}

/** The M5 → W1 structural ladder: which way each timeframe points, its last
 *  structural event, and its liquidity-hunt phase. Clicking a row opens that
 *  timeframe on the chart. Purely descriptive — a state reading per
 *  timeframe, not a signal. */
export function MultiTimeframePanel({
  overview,
  activeTimeframe,
  onSelectTimeframe,
}: {
  overview: MarketOverview
  activeTimeframe: TimeFrame
  onSelectTimeframe: (timeframe: TimeFrame) => void
}) {
  const bullish = overview.entries.filter((e) => e.trend === 'bullish').length
  const bearish = overview.entries.filter((e) => e.trend === 'bearish').length
  const neutral = overview.entries.length - bullish - bearish

  const alignment = (
    <span className="flex items-center gap-1.5 font-mono text-[9px] font-bold">
      {bullish > 0 && <span style={{ color: '#26a69a' }}>{bullish}▲</span>}
      {bearish > 0 && <span style={{ color: '#ef5350' }}>{bearish}▼</span>}
      {neutral > 0 && <span style={{ color: '#5d6477' }}>{neutral}◆</span>}
    </span>
  )

  return (
    <CollapsibleSection title="Structure Ladder" trailing={alignment}>
      <div className="flex flex-col gap-1">
        {overview.entries.map((entry) => (
          <TimeframeRow
            key={entry.timeframe}
            entry={entry}
            active={entry.timeframe === activeTimeframe}
            onSelect={() => onSelectTimeframe(entry.timeframe)}
          />
        ))}
      </div>
    </CollapsibleSection>
  )
}
