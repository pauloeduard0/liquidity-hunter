import type { MarketNarrative, NarrativeAnomaly, NarrativeEvent } from '../types/dashboard'
import { CollapsibleSection } from './CollapsibleSection'

const EVENT_TYPE_STYLES: Record<string, { label: string; color: string; icon: string }> = {
  consolidation: { label: 'CONSOL', color: '#ffb74d', icon: '▬' },
  distribution: { label: 'DIST', color: '#ef5350', icon: '▼' },
  accumulation: { label: 'ACCUM', color: '#26a69a', icon: '▲' },
  sweep: { label: 'SWEEP', color: '#ef5350', icon: '⚡' },
  expansion: { label: 'EXP', color: '#26a69a', icon: '↗' },
  exhaustion: { label: 'EXHAUST', color: '#ffb74d', icon: '◇' },
  absorption: { label: 'ABSORB', color: '#ab63fa', icon: '◆' },
  structure_break: { label: 'STRUCT', color: '#2979ff', icon: '◈' },
  zone_mitigation: { label: 'RTO', color: '#2962ff', icon: '↩' },
}

const SEVERITY_STYLES: Record<string, { color: string; bg: string }> = {
  high: { color: '#ef5350', bg: '#ef535015' },
  medium: { color: '#ffb74d', bg: '#ffb74d15' },
  low: { color: '#5d6477', bg: '#5d647715' },
}

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  accumulation: { label: 'ACCUMULATION', color: '#ffb74d' },
  manipulation: { label: 'MANIPULATION', color: '#ef5350' },
  expansion: { label: 'EXPANSION', color: '#26a69a' },
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function TimelineEvent({ event }: { event: NarrativeEvent }) {
  const style = EVENT_TYPE_STYLES[event.event_type] ?? EVENT_TYPE_STYLES.structure_break
  const dirColor = event.direction === 'bullish' ? '#26a69a' : event.direction === 'bearish' ? '#ef5350' : '#5d6477'

  return (
    <div className="relative flex gap-2.5 pb-3">
      {/* Vertical timeline line */}
      <div className="flex flex-col items-center">
        <span
          className="flex h-5 w-5 flex-none items-center justify-center rounded-full text-[10px]"
          style={{ backgroundColor: `${style.color}18`, color: style.color }}
        >
          {style.icon}
        </span>
        <div className="mt-1 w-px flex-1 bg-[#1a1f2e]" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1 pb-1">
        <div className="mb-1 flex items-center gap-1.5">
          <span
            className="rounded-sm px-1 py-[1px] text-[8px] font-bold tracking-widest"
            style={{ color: style.color, backgroundColor: `${style.color}15` }}
          >
            {style.label}
          </span>
          <span className="text-[9px] font-bold" style={{ color: dirColor }}>
            {event.direction === 'bullish' ? '▲' : event.direction === 'bearish' ? '▼' : ''}
          </span>
          <span className="ml-auto font-mono text-[9px] text-[#3d4455]">
            {formatTimestamp(event.timestamp)}
          </span>
        </div>
        <p className="text-[10px] leading-[1.5] text-[#9ca3b4]">
          {event.description}
        </p>
      </div>
    </div>
  )
}

function AnomalyCallout({ anomaly }: { anomaly: NarrativeAnomaly }) {
  const style = SEVERITY_STYLES[anomaly.severity] ?? SEVERITY_STYLES.low

  return (
    <div
      className="overflow-hidden rounded-md border"
      style={{ borderColor: `${style.color}25`, backgroundColor: '#0a0d14' }}
    >
      <div className="h-[2px]" style={{ background: `linear-gradient(90deg, ${style.color}60, transparent)` }} />
      <div className="p-2.5">
        <div className="mb-1.5 flex items-center justify-between">
          <span
            className="rounded-sm px-1.5 py-[1px] text-[8px] font-bold tracking-widest"
            style={{ color: style.color, backgroundColor: style.bg }}
          >
            {anomaly.severity.toUpperCase()}
          </span>
          <span className="font-mono text-[9px] text-[#3d4455]">
            {formatTimestamp(anomaly.timestamp)}
          </span>
        </div>
        <p className="mb-1 text-[10px] font-medium leading-[1.4] text-[#d1d4dc]">
          {anomaly.description}
        </p>
        <div className="flex flex-col gap-0.5">
          <span className="text-[9px] text-[#5d6477]">
            Expected: <span className="text-[#9ca3b4]">{anomaly.expected}</span>
          </span>
          <span className="text-[9px] text-[#5d6477]">
            Observed: <span style={{ color: style.color }}>{anomaly.observed}</span>
          </span>
        </div>
      </div>
    </div>
  )
}

function ConfluenceMeter({ count, total }: { count: number; total: number }) {
  if (total === 0) return null
  const pct = (count / total) * 100
  const color = pct >= 75 ? '#26a69a' : pct >= 50 ? '#ffb74d' : '#ef5350'

  return (
    <div className="flex items-center gap-2">
      <span className="text-[9px] text-[#5d6477]">Confluence</span>
      <div className="h-[3px] flex-1 overflow-hidden rounded-full bg-[#1a1f2e]">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-[10px] font-medium" style={{ color }}>
        {count}/{total}
      </span>
    </div>
  )
}

const MAX_TIMELINE = 8

interface NarrativePanelProps {
  narrative: MarketNarrative
}

export function NarrativePanel({ narrative }: NarrativePanelProps) {
  const timelineEvents = [...narrative.timeline].reverse().slice(0, MAX_TIMELINE)
  const sortedAnomalies = [...narrative.anomalies].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  )

  return (
    <div className="flex flex-col gap-3">
      {/* Narrative (Phase + Summary) */}
      <CollapsibleSection title="Narrative">
        <div className="rounded-md border border-[#1a1f2e] bg-[#0a0d14] p-3">
          {narrative.phase && (
            <div className="mb-2 flex items-center gap-2">
              <span
                className="rounded-sm px-1.5 py-[2px] text-[9px] font-bold tracking-widest"
                style={{
                  color: PHASE_LABELS[narrative.phase]?.color ?? '#5d6477',
                  backgroundColor: `${PHASE_LABELS[narrative.phase]?.color ?? '#5d6477'}15`,
                }}
              >
                {PHASE_LABELS[narrative.phase]?.label ?? narrative.phase.toUpperCase()}
              </span>
            </div>
          )}
          <p className="text-[11px] leading-[1.6] text-[#d1d4dc]">
            {narrative.summary}
          </p>
          <div className="mt-2 border-t border-[#1a1f2e] pt-2">
            <ConfluenceMeter count={narrative.confluence_count} total={narrative.confluence_total} />
          </div>
        </div>
      </CollapsibleSection>

      {/* Anomalies */}
      {narrative.anomalies.length > 0 && (
        <CollapsibleSection title="Anomalies" count={sortedAnomalies.length}>
          <div className="flex flex-col gap-1.5">
            {sortedAnomalies.map((anomaly, i) => (
              <AnomalyCallout key={i} anomaly={anomaly} />
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* Timeline */}
      {timelineEvents.length > 0 && (
        <CollapsibleSection title="Timeline" count={timelineEvents.length}>
          <div className="flex flex-col">
            {timelineEvents.map((event, i) => (
              <TimelineEvent key={i} event={event} />
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* Empty state */}
      {timelineEvents.length === 0 && narrative.anomalies.length === 0 && !narrative.summary && (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <div className="text-lg text-[#1f2430]">◇</div>
          <p className="text-[10px] text-[#3d4455]">No narrative available</p>
        </div>
      )}
    </div>
  )
}
