import type { ManipulationCycle } from '../types/dashboard'
import { TREND_ICONS } from '../theme'

const PHASE_STYLES: Record<string, { label: string; color: string }> = {
  accumulation: { label: 'ACCUMULATION', color: '#ffb74d' },
  manipulation: { label: 'MANIPULATION', color: '#ef5350' },
  expansion: { label: 'EXPANSION', color: '#26a69a' },
}

const STATUS_STYLES: Record<string, { label: string; color: string; pulse: boolean }> = {
  in_progress: { label: 'Active', color: '#ffb74d', pulse: true },
  confirmed: { label: 'Confirmed', color: '#26a69a', pulse: false },
  failed: { label: 'Failed', color: '#8a8f9c', pulse: false },
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

function CycleCard({ cycle }: { cycle: ManipulationCycle }) {
  const phase = PHASE_STYLES[cycle.phase] ?? PHASE_STYLES.accumulation
  const status = STATUS_STYLES[cycle.status] ?? STATUS_STYLES.in_progress
  const arrow = TREND_ICONS[cycle.direction] ?? '▬'
  const dirColor = cycle.direction === 'bullish' ? '#26a69a' : '#ef5350'
  const zoneLabel = ZONE_LABELS[cycle.target_zone_type] ?? cycle.target_zone_type
  const zonePrice = formatPrice(
    (cycle.target_zone_price_high + cycle.target_zone_price_low) / 2,
  )

  return (
    <div className="rounded-md border border-[#1f2430] bg-[#0e1117] p-3">
      {/* Header: direction + phase badge + status */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span style={{ color: dirColor }} className="text-base font-bold">
            {arrow}
          </span>
          <span
            style={{ color: phase.color, borderColor: phase.color }}
            className="rounded border px-1.5 py-0.5 text-[10px] font-bold tracking-wider"
          >
            {phase.label}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {status.pulse && (
            <span
              className="inline-block h-2 w-2 animate-pulse rounded-full"
              style={{ backgroundColor: status.color }}
            />
          )}
          <span style={{ color: status.color }} className="text-xs font-medium">
            {status.label}
          </span>
        </div>
      </div>

      {/* Target zone */}
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="text-[#8a8f9c]">Target Zone</span>
        <span className="font-mono text-[#d1d4dc]">
          {zoneLabel} @ {zonePrice}
        </span>
      </div>

      {/* Consolidation */}
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="text-[#8a8f9c]">Consolidation</span>
        <span className="font-mono text-[#d1d4dc]">{cycle.consolidation_candles} candles</span>
      </div>

      {/* Sweep info (if reached manipulation phase) */}
      {cycle.sweep_timestamp && (
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-[#8a8f9c]">Sweep</span>
          <span className="font-mono text-[#d1d4dc]">
            {formatPrice(cycle.sweep_extreme!)} @ {formatTimestamp(cycle.sweep_timestamp)}
          </span>
        </div>
      )}

      {/* Expansion info (if confirmed) */}
      {cycle.expansion_timestamp && (
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="text-[#8a8f9c]">Expansion BOS</span>
          <span className="font-mono text-[#d1d4dc]">
            {formatPrice(cycle.expansion_price!)} @ {formatTimestamp(cycle.expansion_timestamp)}
          </span>
        </div>
      )}

      {/* Volume delta summary */}
      {cycle.sweep_volume_delta != null && (
        <div className="mt-2 border-t border-[#1f2430] pt-1.5">
          <div className="flex gap-3 text-[10px]">
            <span className="text-[#8a8f9c]">
              Sweep VD:{' '}
              <span
                className="font-mono font-medium"
                style={{ color: cycle.sweep_volume_delta >= 0 ? '#26a69a' : '#ef5350' }}
              >
                {cycle.sweep_volume_delta >= 0 ? '+' : ''}
                {cycle.sweep_volume_delta.toFixed(1)}
              </span>
            </span>
            {cycle.expansion_volume_delta != null && (
              <span className="text-[#8a8f9c]">
                Exp VD:{' '}
                <span
                  className="font-mono font-medium"
                  style={{ color: cycle.expansion_volume_delta >= 0 ? '#26a69a' : '#ef5350' }}
                >
                  {cycle.expansion_volume_delta >= 0 ? '+' : ''}
                  {cycle.expansion_volume_delta.toFixed(1)}
                </span>
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

interface ManipulationCyclesPanelProps {
  cycles: ManipulationCycle[]
}

export function ManipulationCyclesPanel({ cycles }: ManipulationCyclesPanelProps) {
  const sorted = [...cycles].sort((a, b) => {
    const statusOrder: Record<string, number> = { in_progress: 0, confirmed: 1, failed: 2 }
    const sa = statusOrder[a.status] ?? 1
    const sb = statusOrder[b.status] ?? 1
    if (sa !== sb) return sa - sb
    return new Date(b.accumulation_start).getTime() - new Date(a.accumulation_start).getTime()
  })

  return (
    <div className="flex flex-col">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8a8f9c]">
        Manipulation Cycles
      </h2>
      {sorted.length === 0 ? (
        <p className="text-xs text-[#8a8f9c]">No cycles detected</p>
      ) : (
        <div className="flex flex-col gap-2">
          {sorted.map((cycle, i) => (
            <CycleCard key={i} cycle={cycle} />
          ))}
        </div>
      )}
    </div>
  )
}
