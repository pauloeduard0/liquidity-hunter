import type { DashboardData } from '../types/dashboard'
import { TREND_ICONS } from '../theme'

interface KpiCardProps {
  label: string
  value: string
}

function KpiCard({ label, value }: KpiCardProps) {
  return (
    <div className="rounded-lg border border-[#1f2430] bg-[#161a25] px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-[#8a8f9c]">{label}</div>
      <div className="mt-1 text-xl font-semibold text-[#d1d4dc]">{value}</div>
    </div>
  )
}

interface KpiRowProps {
  data: DashboardData
}

/** Top KPI row: price, retail bias, dominant liquidity, and trend. */
export function KpiRow({ data }: KpiRowProps) {
  const bias = data.retail_bias
  const direction = data.higher_timeframe_direction

  const dominantLiquidity = data.ranked_zones.length
    ? (
        (data.ranked_zones[0].zone.price_high + data.ranked_zones[0].zone.price_low) /
        2
      ).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—'

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <KpiCard
        label={`${data.symbol} Price`}
        value={data.current_price.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}
      />
      <KpiCard
        label="Retail Bias"
        value={`${bias.dominant_side.toUpperCase()} ${bias.confidence.toFixed(0)}%`}
      />
      <KpiCard label="Dominant Liquidity" value={dominantLiquidity} />
      <KpiCard
        label="Trend"
        value={`${TREND_ICONS[direction]} ${direction.charAt(0).toUpperCase()}${direction.slice(1)}`}
      />
    </div>
  )
}
