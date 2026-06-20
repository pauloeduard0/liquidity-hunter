import type { BehaviorDivergence } from '../types/dashboard'
import { DIVERGENCE_STYLES } from '../theme'

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function formatPrice(price: number): string {
  return price.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
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

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 70 ? '#26a69a' : value >= 40 ? '#ffb74d' : '#5d6477'
  return (
    <div className="flex items-center gap-2">
      <div className="h-[3px] flex-1 overflow-hidden rounded-full bg-[#1a1f2e]">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${value}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-[10px] font-medium" style={{ color }}>
        {value.toFixed(0)}%
      </span>
    </div>
  )
}

function DivergenceCard({ divergence }: { divergence: BehaviorDivergence }) {
  const style = DIVERGENCE_STYLES[divergence.divergence_type] ?? DIVERGENCE_STYLES.exhaustion
  const dirColor = divergence.direction === 'bullish' ? '#26a69a' : '#ef5350'
  const dirIcon = divergence.direction === 'bullish' ? '▲' : '▼'
  const vdColor = divergence.volume_delta_avg >= 0 ? '#26a69a' : '#ef5350'

  return (
    <div
      className="overflow-hidden rounded-md border transition-colors duration-200"
      style={{ borderColor: `${style.color}20`, backgroundColor: '#0a0d14' }}
    >
      <div className="h-[2px]" style={{ background: `linear-gradient(90deg, ${style.color}60, transparent)` }} />

      <div className="p-3">
        {/* Header */}
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold" style={{ color: dirColor }}>
              {dirIcon}
            </span>
            <span
              className="rounded-sm px-1.5 py-[2px] text-[9px] font-bold tracking-widest"
              style={{ color: style.color, backgroundColor: style.bg }}
            >
              {style.label}
            </span>
          </div>
          <span className="font-mono text-[10px] text-[#5d6477]">
            {formatTimestamp(divergence.timestamp)}
          </span>
        </div>

        {/* Data */}
        <DataRow label="Price" value={formatPrice(divergence.price_level)} />
        <DataRow
          label="Avg VD"
          value={`${divergence.volume_delta_avg >= 0 ? '+' : ''}${divergence.volume_delta_avg.toFixed(1)}`}
          valueColor={vdColor}
        />
        <DataRow
          label="Price Δ"
          value={`${divergence.price_change_pct >= 0 ? '+' : ''}${(divergence.price_change_pct * 100).toFixed(2)}%`}
          valueColor={dirColor}
        />
        {divergence.nearest_zone_side && (
          <DataRow
            label="Near zone"
            value={`${divergence.nearest_zone_side === 'buy_side' ? 'Buy' : 'Sell'} @ ${formatPrice(divergence.nearest_zone_price_low!)}`}
          />
        )}

        {/* Confidence */}
        <div className="mt-2 border-t border-[#1a1f2e] pt-2">
          <ConfidenceBar value={divergence.confidence} />
        </div>
      </div>
    </div>
  )
}

interface BehaviorDivergencePanelProps {
  divergences: BehaviorDivergence[]
}

const MAX_DISPLAY = 5

export function BehaviorDivergencePanel({ divergences }: BehaviorDivergencePanelProps) {
  const sorted = [...divergences]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, MAX_DISPLAY)

  return (
    <div className="flex flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[#5d6477]">
          Behavior Divergences
        </h2>
        {sorted.length > 0 && (
          <span className="rounded-sm bg-[#1a1f2e] px-1.5 py-[2px] font-mono text-[9px] font-medium text-[#5d6477]">
            {sorted.length}
          </span>
        )}
      </div>
      {sorted.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <div className="text-lg text-[#1f2430]">◇</div>
          <p className="text-[10px] text-[#3d4455]">No divergences detected</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {sorted.map((div, i) => (
            <DivergenceCard key={i} divergence={div} />
          ))}
        </div>
      )}
    </div>
  )
}
