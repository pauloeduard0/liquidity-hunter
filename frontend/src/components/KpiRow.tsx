import type { DashboardData } from '../types/dashboard'
import type { MarketDirection, RetailPositioning } from '../types/dashboard'

const DIRECTION_CONFIG: Record<MarketDirection, { color: string; icon: string }> = {
  bullish: { color: '#26a69a', icon: '▲' },
  bearish: { color: '#ef5350', icon: '▼' },
  neutral: { color: '#8a8f9c', icon: '◆' },
}

const BIAS_CONFIG: Record<RetailPositioning, { color: string }> = {
  long: { color: '#26a69a' },
  short: { color: '#ef5350' },
  neutral: { color: '#8a8f9c' },
}

interface KpiCardProps {
  label: string
  value: string
  accent?: string
  sub?: string
  badge?: { text: string; color: string }
}

function KpiCard({ label, value, accent, sub, badge }: KpiCardProps) {
  return (
    <div className="group relative overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319] p-4 transition-all duration-200 hover:border-[#252b3d]">
      <div
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background: accent
            ? `radial-gradient(ellipse at 50% 0%, ${accent}08, transparent 70%)`
            : undefined,
        }}
      />
      <div className="relative">
        <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-[#5d6477]">
          {label}
        </div>
        <div className="flex items-center gap-2">
          <span
            className="font-mono text-lg font-semibold tracking-tight"
            style={{ color: accent ?? '#d1d4dc' }}
          >
            {value}
          </span>
          {badge && (
            <span
              className="rounded-sm px-1.5 py-[1px] text-[9px] font-bold tracking-wider"
              style={{ color: badge.color, backgroundColor: `${badge.color}15` }}
            >
              {badge.text}
            </span>
          )}
        </div>
        {sub && (
          <div className="mt-1 text-[10px] text-[#5d6477]">{sub}</div>
        )}
      </div>
    </div>
  )
}

interface KpiRowProps {
  data: DashboardData
}

export function KpiRow({ data }: KpiRowProps) {
  const bias = data.retail_bias
  const direction = data.higher_timeframe_direction
  const dirCfg = DIRECTION_CONFIG[direction]
  const biasCfg = BIAS_CONFIG[bias.dominant_side]

  const isCounterTrend =
    bias.dominant_side !== 'neutral' &&
    direction !== 'neutral' &&
    ((bias.dominant_side === 'long' && direction === 'bearish') ||
      (bias.dominant_side === 'short' && direction === 'bullish'))
  const isAligned =
    bias.dominant_side !== 'neutral' &&
    direction !== 'neutral' &&
    !isCounterTrend
  const retailBadge = isCounterTrend
    ? { text: '⚠ TRAP', color: '#ef5350' }
    : isAligned
      ? { text: '✓ ALIGNED', color: '#26a69a' }
      : undefined

  const price = data.current_price.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })

  const dominantLiquidity = data.ranked_zones.length
    ? (
        (data.ranked_zones[0].zone.price_high + data.ranked_zones[0].zone.price_low) /
        2
      ).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—'

  const topZoneType = data.ranked_zones.length
    ? data.ranked_zones[0].zone.zone_type.replace(/_/g, ' ')
    : undefined

  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <KpiCard label={`${data.symbol} Price`} value={price} />
      <KpiCard
        label="Retail Bias"
        value={`${bias.dominant_side.toUpperCase()} ${bias.confidence.toFixed(0)}%`}
        accent={biasCfg.color}
        badge={retailBadge}
        sub={bias.confidence >= 70 ? 'High conviction' : bias.confidence >= 40 ? 'Moderate' : 'Low conviction'}
      />
      <KpiCard
        label="Dominant Liquidity"
        value={dominantLiquidity}
        accent="#ab63fa"
        sub={topZoneType}
      />
      <KpiCard
        label="HTF Trend"
        value={`${dirCfg.icon} ${direction.charAt(0).toUpperCase()}${direction.slice(1)}`}
        accent={dirCfg.color}
      />
    </div>
  )
}
