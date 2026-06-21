import type { ManipulationCycle } from '../types/dashboard'
import { CollapsibleSection } from './CollapsibleSection'

const PHASE_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  accumulation: { label: 'ACC', color: '#ffb74d', bg: '#ffb74d15' },
  manipulation: { label: 'MANIP', color: '#ef5350', bg: '#ef535015' },
  expansion: { label: 'EXP', color: '#26a69a', bg: '#26a69a15' },
}

const STATUS_STYLES: Record<string, { label: string; color: string; pulse: boolean }> = {
  in_progress: { label: 'LIVE', color: '#ffb74d', pulse: true },
  confirmed: { label: 'CONFIRMED', color: '#26a69a', pulse: false },
  failed: { label: 'FAILED', color: '#5d6477', pulse: false },
}

const ZONE_LABELS: Record<string, string> = {
  equal_highs: 'EQH',
  equal_lows: 'EQL',
  swing_high: 'SH',
  swing_low: 'SL',
  order_block: 'OB',
  fair_value_gap: 'FVG',
  liquidity_pool: 'LP',
}

function formatPrice(price: number): string {
  return price.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function DataRow({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="flex items-center justify-between py-[3px]">
      <span className="text-[10px] text-[#5d6477]">{label}</span>
      <span className="font-mono text-[11px]" style={{ color: valueColor ?? '#9ca3b4' }}>
        {value}
      </span>
    </div>
  )
}

function VolumeDelta({ label, value }: { label: string; value: number }) {
  const color = value >= 0 ? '#26a69a' : '#ef5350'
  return (
    <span className="text-[10px] text-[#5d6477]">
      {label}{' '}
      <span className="font-mono font-medium" style={{ color }}>
        {value >= 0 ? '+' : ''}{value.toFixed(1)}
      </span>
    </span>
  )
}

function CycleCard({ cycle }: { cycle: ManipulationCycle }) {
  const phase = PHASE_STYLES[cycle.phase] ?? PHASE_STYLES.accumulation
  const status = STATUS_STYLES[cycle.status] ?? STATUS_STYLES.in_progress
  const dirColor = cycle.direction === 'bullish' ? '#26a69a' : '#ef5350'
  const dirIcon = cycle.direction === 'bullish' ? '▲' : '▼'
  const zoneLabel = ZONE_LABELS[cycle.target_zone_type] ?? cycle.target_zone_type
  const zonePrice = formatPrice(
    (cycle.target_zone_price_high + cycle.target_zone_price_low) / 2,
  )

  return (
    <div
      className="overflow-hidden rounded-md border transition-colors duration-200"
      style={{ borderColor: `${phase.color}20`, backgroundColor: '#0a0d14' }}
    >
      {/* Subtle top accent line */}
      <div className="h-[2px]" style={{ background: `linear-gradient(90deg, ${phase.color}60, transparent)` }} />

      <div className="p-3">
        {/* Header */}
        <div className="mb-2.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold" style={{ color: dirColor }}>
              {dirIcon}
            </span>
            <span
              className="rounded-sm px-1.5 py-[2px] text-[9px] font-bold tracking-widest"
              style={{ color: phase.color, backgroundColor: phase.bg }}
            >
              {phase.label}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {status.pulse && (
              <span className="relative flex h-2 w-2">
                <span
                  className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-50"
                  style={{ backgroundColor: status.color }}
                />
                <span
                  className="relative inline-flex h-2 w-2 rounded-full"
                  style={{ backgroundColor: status.color }}
                />
              </span>
            )}
            <span className="text-[9px] font-bold tracking-wider" style={{ color: status.color }}>
              {status.label}
            </span>
          </div>
        </div>

        {/* Data rows */}
        <DataRow label="Target" value={`${zoneLabel} @ ${zonePrice}`} />
        <DataRow label="Consolidation" value={`${cycle.consolidation_candles} candles`} />
        {cycle.sweep_timestamp && (
          <DataRow
            label="Sweep"
            value={`${formatPrice(cycle.sweep_extreme!)} @ ${formatTimestamp(cycle.sweep_timestamp)}`}
            valueColor="#ef5350"
          />
        )}
        {cycle.expansion_timestamp && (
          <DataRow
            label="Expansion BOS"
            value={`${formatPrice(cycle.expansion_price!)} @ ${formatTimestamp(cycle.expansion_timestamp)}`}
            valueColor="#26a69a"
          />
        )}

        {/* Volume delta */}
        {cycle.sweep_volume_delta != null && (
          <div className="mt-2 flex gap-3 border-t border-[#1a1f2e] pt-2">
            <VolumeDelta label="Sweep" value={cycle.sweep_volume_delta} />
            {cycle.expansion_volume_delta != null && (
              <VolumeDelta label="Exp" value={cycle.expansion_volume_delta} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

interface ManipulationCyclesPanelProps {
  cycles: ManipulationCycle[]
  chartVisible: boolean
  onToggleChart: () => void
}

const MAX_DISPLAY = 5

export function ManipulationCyclesPanel({
  cycles,
  chartVisible,
  onToggleChart,
}: ManipulationCyclesPanelProps) {
  const sorted = [...cycles].sort((a, b) => {
    const latestTs = (c: ManipulationCycle) => {
      const ts = [c.accumulation_end, c.sweep_timestamp, c.expansion_timestamp]
        .filter(Boolean)
        .map((t) => new Date(t!).getTime())
      return Math.max(...ts)
    }
    return latestTs(b) - latestTs(a)
  }).slice(0, MAX_DISPLAY)

  const overlayButton = (
    <button
      onClick={(e) => { e.stopPropagation(); onToggleChart() }}
      title={chartVisible ? 'Hide chart overlay' : 'Show chart overlay'}
      className="flex items-center gap-1 rounded-sm px-1.5 py-[3px] text-[9px] font-bold tracking-wider transition-all duration-200"
      style={{
        color: chartVisible ? '#2962ff' : '#5d6477',
        backgroundColor: chartVisible ? '#2962ff12' : '#0f1319',
      }}
    >
      <span
        className="inline-block h-1.5 w-1.5 rounded-full transition-colors"
        style={{ backgroundColor: chartVisible ? '#2962ff' : '#5d6477' }}
      />
      {chartVisible ? 'OVERLAY' : 'HIDDEN'}
    </button>
  )

  return (
    <CollapsibleSection title="Manipulation Cycles" count={sorted.length} trailing={overlayButton}>
      {sorted.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <div className="text-lg text-[#1f2430]">◇</div>
          <p className="text-[10px] text-[#3d4455]">No cycles detected</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {sorted.map((cycle, i) => (
            <CycleCard key={i} cycle={cycle} />
          ))}
        </div>
      )}
    </CollapsibleSection>
  )
}
