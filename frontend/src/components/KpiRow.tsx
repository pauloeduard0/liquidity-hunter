import type { DashboardData, LiquidityHuntState, OIRegime } from '../types/dashboard'
import type { MarketDirection, RetailPositioning } from '../types/dashboard'
import { formatPrice } from '../utils/format'

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

// Conviction regimes (new money entering) take directional colors; unwinding
// regimes (positions closing, no new money behind the move) are amber
// warnings regardless of price direction.
const OI_REGIME_CONFIG: Record<
  OIRegime,
  { label: string; color: string; icon: string; conviction: MarketDirection | null }
> = {
  long_buildup: { label: 'Long Buildup', color: '#26a69a', icon: '▲', conviction: 'bullish' },
  short_buildup: { label: 'Short Buildup', color: '#ef5350', icon: '▼', conviction: 'bearish' },
  short_covering: { label: 'Short Covering', color: '#ff9800', icon: '▲', conviction: null },
  long_liquidation: { label: 'Long Liquidation', color: '#ff9800', icon: '▼', conviction: null },
  flat: { label: 'Flat', color: '#8a8f9c', icon: '◆', conviction: null },
}

const fmtPct = (value: number) =>
  `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`

const fmtWhen = (iso: string) => {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// The "conclusion" card: who is the resting liquidity of the current move,
// and how far its capture has progressed. Everything is precomputed by the
// backend LiquidityHuntEngine; this only maps phase -> presentation. `anchor`
// is the HTF pair the reading is measured against ("4H"), so an M15 card
// saying "Hunting longs · vs 1H" reads as the pair's fractal handoff rather
// than a contradiction of the 4H story.
function huntCardProps(
  hunt: LiquidityHuntState | null,
  anchor: string | null,
): Omit<KpiCardProps, 'label'> {
  const vsAnchor = anchor ? ` · vs ${anchor}` : ''
  if (!hunt || hunt.phase === 'none') {
    return { value: '◆ —', sub: `structure aligned with ${anchor ?? 'HTF'}` }
  }
  const side = hunt.hunted_side === 'short' ? 'Shorts' : 'Longs'
  const pools = hunt.targets_total
    ? `${hunt.targets_captured}/${hunt.targets_total} pools swept`
    : 'no pools mapped nearby'
  const oiCtx = hunt.oi_unwinding ? ' · OI unwinding' : ''
  if (hunt.phase === 'captured') {
    return {
      value: `${side} captured`,
      accent: '#26a69a',
      badge: { text: '✓ CLEARED', color: '#26a69a' },
      sub: `${pools}${hunt.captured_at ? ` · ${fmtWhen(hunt.captured_at)}` : ''}${vsAnchor}`,
      title: hunt.description,
    }
  }
  if (hunt.phase === 'hunt_in_progress') {
    return {
      value: `Hunting ${side.toLowerCase()}`,
      accent: '#ff9800',
      badge: { text: '⚡ ACTIVE', color: '#ff9800' },
      sub: pools + oiCtx + vsAnchor,
      title: hunt.description,
    }
  }
  return {
    value: `${side} = liquidity`,
    accent: '#ef5350',
    badge: { text: '⚠ INTACT', color: '#ef5350' },
    sub: pools + oiCtx + vsAnchor,
    title: hunt.description,
  }
}

interface KpiCardProps {
  label: string
  value: string
  accent?: string
  sub?: string
  badge?: { text: string; color: string }
  title?: string
}

function KpiCard({ label, value, accent, sub, badge, title }: KpiCardProps) {
  return (
    <div
      className="group relative overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319] p-4 transition-all duration-200 hover:border-[#252b3d]"
      title={title}
    >
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
  // The anchor pair the HTF reading refers to ("4H" when viewing 1H), so the
  // card says *which* higher timeframe it means; null on the top timeframe.
  const htfAnchor = data.higher_timeframe ? data.higher_timeframe.toUpperCase() : null

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

  const dominantLiquidity = data.ranked_zones.length
    ? formatPrice(
        (data.ranked_zones[0].zone.price_high + data.ranked_zones[0].zone.price_low) / 2,
      )
    : '—'

  const topZoneType = data.ranked_zones.length
    ? data.ranked_zones[0].zone.zone_type.replace(/_/g, ' ')
    : undefined

  // OI Regime: the joint price x open-interest reading. Buildup regimes
  // (new money) get a confluence badge against the HTF trend; unwinding
  // regimes flag the move as running on position closing, not fresh money.
  const oiRegime = data.oi_analysis?.current_regime ?? null
  const oiCfg = oiRegime ? OI_REGIME_CONFIG[oiRegime.regime] : null
  let oiBadge: { text: string; color: string } | undefined
  if (oiRegime && oiCfg) {
    if (oiCfg.conviction && direction !== 'neutral') {
      oiBadge =
        oiCfg.conviction === direction
          ? { text: '✓ CONFLUENT', color: '#26a69a' }
          : { text: '⚠ DIVERGENT', color: '#ef5350' }
    } else if (oiRegime.regime === 'short_covering' || oiRegime.regime === 'long_liquidation') {
      oiBadge = { text: '⚠ UNWIND', color: '#ff9800' }
    }
  }
  const oiSub = oiRegime
    ? `OI ${fmtPct(oiRegime.oi_change_pct)} · Px ${fmtPct(oiRegime.price_change_pct)}`
    : 'no futures OI data'

  // Row reads left to right as the story: who retail is, where the resting
  // liquidity sits, which way the higher timeframe leans, who is behind the
  // move (OI) — and the hunt card concludes it.
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
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
        label={htfAnchor ? `HTF Trend · ${htfAnchor}` : 'HTF Trend'}
        value={`${dirCfg.icon} ${direction.charAt(0).toUpperCase()}${direction.slice(1)}`}
        accent={dirCfg.color}
        sub={htfAnchor ? `${htfAnchor} internal structure` : 'top timeframe — own trend'}
      />
      <KpiCard
        label="OI Regime"
        value={oiCfg ? `${oiCfg.icon} ${oiCfg.label}` : '—'}
        accent={oiCfg?.color}
        badge={oiBadge}
        sub={oiSub}
      />
      <KpiCard
        label="Liquidity Hunt"
        {...huntCardProps(data.liquidity_hunt ?? null, htfAnchor)}
      />
    </div>
  )
}
