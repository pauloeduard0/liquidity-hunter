import { useEffect, useState } from 'react'

import { fetchDashboardData, fetchOverview } from './api/dashboard'
import { BehaviorDivergencePanel } from './components/BehaviorDivergencePanel'
import { IndicatorMenu } from './components/IndicatorMenu'
import type { IndicatorGroup } from './components/IndicatorMenu'
import { KpiRow } from './components/KpiRow'
import { Logo } from './components/Logo'
import { MainChart } from './components/MainChart'
import type { VwapMode } from './components/MainChart'
import { MultiTimeframePanel } from './components/MultiTimeframePanel'
import { SymbolPicker } from './components/SymbolPicker'
import type { DashboardData, MarketOverview, TimeFrame } from './types/dashboard'
import { isChartBusy } from './utils/chartActivity'
import { chartTimezoneLabel } from './utils/chartTime'
import { formatPrice, setPriceFormatMode } from './utils/format'

const REFRESH_INTERVAL_MS = 5_000
// The ladder's readings change at most once per candle (per timeframe), and
// the backend caches each timeframe with a proportional TTL — polling faster
// than this only re-reads caches.
const OVERVIEW_REFRESH_INTERVAL_MS = 30_000

// A poll tick worth skipping: applying the snapshot is a synchronous burst, so
// it is deferred while the user is dragging/zooming the chart (the moment the
// stutter is felt) and while the tab is hidden (nobody is watching). Both cost
// at most one interval — the next tick applies whatever is current then.
function shouldDeferPoll(): boolean {
  return document.hidden || isChartBusy()
}

// Snapshots already fetched this session, so switching back to a
// symbol/timeframe renders instantly from cache (then revalidates on the next
// poll). A first-visit switch keeps the previous snapshot on screen, dimmed,
// instead of tearing the dashboard down to the skeleton.
const snapshotCache = new Map<string, DashboardData>()
const overviewCache = new Map<string, MarketOverview>()

const snapshotKey = (symbol: string, timeframe: TimeFrame) => `${symbol}|${timeframe}`

const SYMBOL_OPTIONS: { value: string; label: string }[] = [
  { value: 'BTCUSDT', label: 'BTC' },
  { value: 'ETHUSDT', label: 'ETH' },
  { value: 'SOLUSDT', label: 'SOL' },
  { value: 'NEARUSDT', label: 'NEAR' },
  { value: 'AAVEUSDT', label: 'AAVE' },
  { value: 'DASHUSDT', label: 'DASH' },
  { value: 'XAUUSDT', label: 'XAU' },
  { value: 'AEROUSDT', label: 'AERO' },
  { value: 'ENAUSDT', label: 'ENA' },
  { value: 'HYPEUSDT', label: 'HYPE' },
  { value: 'ETHBTC', label: 'ETH/BTC' },
  { value: 'MUUSDT', label: 'MU' },
  { value: 'ZECUSDT', label: 'ZEC' },
  // On-chain pairs: `<network>:<token address>`, served by GeckoTerminal
  // instead of Binance (see `RoutingOHLCVProvider`). Priced in market cap,
  // the axis a memecoin is actually read on. No futures layers behind these
  // (no OI, funding, or liquidation map) and no volume delta -- an on-chain
  // OHLCV row carries no taker split.
  {
    value: 'solana:Ge87EtsjwRQbHaqQmKRno69RFTwh9bfSsm99XNxTpump',
    label: 'JIMOTHY',
  },
  {
    value: 'solana:Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump',
    label: 'CATE',
  },
]

const TIMEFRAME_OPTIONS: { value: TimeFrame; label: string }[] = [
  { value: '5m', label: '5M' },
  { value: '15m', label: '15M' },
  { value: '30m', label: '30M' },
  { value: '1h', label: '1H' },
  { value: '4h', label: '4H' },
  { value: '1d', label: '1D' },
  { value: '1w', label: '1W' },
  { value: '1M', label: 'MN' },
]

// The `⌀ VWAP` button's plain click walks this cycle; see `vwapMode` below.
const VWAP_MODE_CYCLE: Record<VwapMode, VwapMode> = {
  off: 'line',
  line: 'bands',
  bands: 'off',
}

function LoadingSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* KPI skeleton */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-7">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="skeleton h-[76px]" />
        ))}
      </div>
      {/* Chart skeleton */}
      <div className="flex min-h-0 flex-1 gap-2">
        <div className="skeleton min-h-0 flex-1" />
        <div className="skeleton w-72 flex-none" />
      </div>
    </div>
  )
}

function StatusBar({ data, symbol }: { data: DashboardData | null; symbol: string }) {
  const now = new Date()
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`

  return (
    <div className="flex items-center justify-between border-t border-[#1a1f2e] px-1 py-1">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-[6px] w-[6px]">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#26a69a] opacity-40" />
            <span className="relative inline-flex h-[6px] w-[6px] rounded-full bg-[#26a69a]" />
          </span>
          <span className="text-[10px] font-medium text-[#26a69a]">LIVE</span>
        </div>
        <span className="text-[10px] text-[#3d4455]">|</span>
        <span className="font-mono text-[10px] text-[#5d6477]">{symbol}</span>
      </div>
      <div className="flex items-center gap-3">
        {data && (
          <>
            <span className="text-[10px] text-[#3d4455]">
              {data.candles.length} candles
            </span>
            <span className="text-[10px] text-[#3d4455]">|</span>
            <span className="text-[10px] text-[#3d4455]">
              {data.market_structure_events.length + data.internal_structure_events.length} events
            </span>
            <span className="text-[10px] text-[#3d4455]">|</span>
          </>
        )}
        <span className="font-mono text-[10px] text-[#5d6477]">{time}</span>
      </div>
    </div>
  )
}

function App() {
  const [symbol, setSymbol] = useState<string>('BTCUSDT')
  const [timeframe, setTimeframe] = useState<TimeFrame>('1h')
  const [data, setData] = useState<DashboardData | null>(null)
  const [overview, setOverview] = useState<MarketOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [divChartVisible, setDivChartVisible] = useState(true)
  // VSA has three states cycled by clicking: 'recent' (only the last N candles,
  // the default — recent context without clutter), 'full' (whole history), and
  // 'off'. Order: recent -> full -> off -> recent.
  const [vsaMode, setVsaMode] = useState<'off' | 'recent' | 'full'>('recent')
  const [sweptZonesVisible, setSweptZonesVisible] = useState(false)
  const [huntWindowVisible, setHuntWindowVisible] = useState(false)
  const [continuationWindowVisible, setContinuationWindowVisible] = useState(false)
  // On by default: a confirmed range is the "why is the chart silent here"
  // answer, the whole point of detecting it.
  const [rangeBoxesVisible, setRangeBoxesVisible] = useState(true)
  const [obVisible, setObVisible] = useState(true)
  const [sweepVisible, setSweepVisible] = useState(true)
  const [eqlVisible, setEqlVisible] = useState(true)
  const [volumeVisible, setVolumeVisible] = useState(true)
  const [rsiDivVisible, setRsiDivVisible] = useState(false)
  const [supertrendVisible, setSupertrendVisible] = useState(false)
  // VWAP reclaims that followed a test of an order block. Off by default: the
  // pane already carries the structure staircase, the blocks and the sweeps,
  // and this reading is a close look at where two of them coincide.
  const [blockReclaimVisible, setBlockReclaimVisible] = useState(false)
  const [smcVisible, setSmcVisible] = useState(true)
  // The periodic VWAP cycles through three states on plain click: off → the
  // average alone → the average plus its ±1σ/±2σ bands. The line is the
  // reading (a population's break-even); the bands are dispersion, worth a
  // deliberate third press rather than riding along by default.
  const [vwapMode, setVwapMode] = useState<VwapMode>('off')
  // Anchored VWAPs (the CHoCH / sweep break-evens) ride the same button
  // under a modifier, like the volume profile's delta mode.
  const [anchoredVwapVisible, setAnchoredVwapVisible] = useState(false)
  const [volumeProfileVisible, setVolumeProfileVisible] = useState(false)
  const [volumeProfileDelta, setVolumeProfileDelta] = useState(false)
  const [controlOscVisible, setControlOscVisible] = useState(true)
  const [ribbonVisible, setRibbonVisible] = useState(false)
  const [defendedVisible, setDefendedVisible] = useState(false)
  const [indicatorsVisible, setIndicatorsVisible] = useState(false)
  const [, setTick] = useState(0)

  // The rendered snapshot lags the selection while a first-visit combo loads;
  // dim the dashboard and show the loading pill instead of a skeleton.
  const dataStale = data !== null && (data.symbol !== symbol || data.timeframe !== timeframe)

  // The control oscillator is CVD aggression crossed with open interest, and
  // an on-chain pool has no open interest at all (nor does a spot-only pair),
  // so `market_control` comes back null and the pane would render empty.
  // Gating on the field itself rather than on the symbol keeps it truthful for
  // every source: the pane is available exactly when there is a reading.
  const controlAvailable = data?.market_control != null

  // An on-chain symbol is charted by market cap, so every price on screen
  // switches to the abbreviated scale (7.17M / 500.00K). Set here, before the
  // children that format prices render, and driven by the symbol rather than
  // by the data so it is already right while a snapshot is loading.
  setPriceFormatMode(symbol)

  // Switching keeps the current snapshot on screen (dimmed via the staleness
  // check below) instead of tearing down to the skeleton; if the target combo
  // was already visited this session, the fetch effect renders it instantly
  // from `snapshotCache` while revalidating.
  const switchTimeframe = (tf: TimeFrame) => {
    if (tf === timeframe) return
    const cached = snapshotCache.get(snapshotKey(symbol, tf))
    if (cached) setData(cached)
    setError(null)
    setTimeframe(tf)
  }

  const switchSymbol = (sym: string) => {
    if (sym === symbol) return
    const cached = snapshotCache.get(snapshotKey(sym, timeframe))
    if (cached) setData(cached)
    setOverview(overviewCache.get(sym) ?? null)
    setError(null)
    setSymbol(sym)
  }

  // Fetch global data (sidebar panels + chart when synced)
  useEffect(() => {
    let cancelled = false

    const load = (force = false) => {
      if (!force && shouldDeferPoll()) return
      fetchDashboardData({ symbol, timeframe })
        .then((result) => {
          snapshotCache.set(snapshotKey(symbol, timeframe), result)
          if (!cancelled) setData(result)
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err))
        })
    }

    load(true)
    const interval = setInterval(load, REFRESH_INTERVAL_MS)
    // Coming back to the tab shouldn't wait out a full interval on stale data.
    const onVisible = () => {
      if (!document.hidden) load(true)
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      cancelled = true
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [symbol, timeframe])

  // Fetch the multi-timeframe structure ladder (sidebar)
  useEffect(() => {
    let cancelled = false

    const load = () => {
      fetchOverview(symbol)
        .then((result) => {
          overviewCache.set(symbol, result)
          if (!cancelled) setOverview(result)
        })
        .catch(() => {
          // Secondary panel: keep the last ladder on transient errors rather
          // than tearing down the whole dashboard.
        })
    }

    load()
    const interval = setInterval(load, OVERVIEW_REFRESH_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [symbol])

  // Tick the clock in the status bar
  useEffect(() => {
    const interval = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(interval)
  }, [])

  // Every switchable chart layer, in one list. Grouped by what the layer
  // reads (structure / flow / bands) rather than by when it was added, and
  // each carries the colour it is drawn in so the row matches the pane.
  const indicatorGroups: IndicatorGroup[] = [
    {
      label: 'Estrutura',
      items: [
        {
          id: 'smc',
          label: 'SMC staircase',
          glyph: '⌗',
          color: '#2EE6B8',
          active: smcVisible,
          onToggle: () => setSmcVisible((v) => !v),
          title: 'BOS / CHoCH / CHoCH ✕ lines and labels',
        },
        {
          id: 'ob',
          label: 'Order blocks',
          glyph: '▦',
          color: '#2979ff',
          active: obVisible,
          onToggle: () => setObVisible((v) => !v),
          title: 'Order block (POI) zones',
        },
        {
          id: 'eql',
          label: 'Liquidity targets',
          glyph: '═',
          color: '#26a69a',
          active: eqlVisible,
          onToggle: () => setEqlVisible((v) => !v),
          title: 'Target zone lines (EQH/EQL, OB, FVG, swings)',
        },
        {
          id: 'sweep',
          label: 'Sweeps',
          glyph: '⌇',
          color: '#ab47bc',
          active: sweepVisible,
          onToggle: () => setSweepVisible((v) => !v),
          title: 'Liquidity sweep (SWEEP + RTO) markers',
        },
        {
          id: 'swept',
          label: 'Swept zones',
          glyph: '⊟',
          color: '#ff9800',
          active: sweptZonesVisible,
          onToggle: () => setSweptZonesVisible((v) => !v),
          title: 'Swept EQH/EQL zones',
        },
        {
          id: 'range',
          label: 'Consolidation',
          glyph: '▭',
          color: '#90a4ae',
          active: rangeBoxesVisible,
          onToggle: () => setRangeBoxesVisible((v) => !v),
          title: 'Consolidation (lateral range) boxes',
        },
        {
          id: 'hunt',
          label: 'Hunt window',
          glyph: '⚡',
          color: '#ffb300',
          active: huntWindowVisible,
          onToggle: () => setHuntWindowVisible((v) => !v),
          title: 'Liquidity-hunt window shading (counter-trend flip → capture)',
        },
        {
          id: 'cont',
          label: 'Continuation grabs',
          glyph: '↗',
          color: '#42a5f5',
          active: continuationWindowVisible,
          onToggle: () => setContinuationWindowVisible((v) => !v),
          title: 'Aligned trend-continuation liquidity grabs (pullback swept internal liquidity, then resumed)',
        },
        {
          id: 'piso',
          label: 'Piso (defended)',
          glyph: '⛨',
          color: '#ffca28',
          // The mark is measured against the Tide envelope, so turning it on
          // brings up the band it refers to -- otherwise the wick's reference
          // is invisible.
          active: defendedVisible,
          onToggle: () =>
            setDefendedVisible((v) => {
              if (!v) setRibbonVisible(true)
              return !v
            }),
          title:
            'Nível testado na borda da fita e defendido: o pavio limpou ±1σ dentro de zonas de 2+ famílias e o candle fechou de volta. O número é quantas famílias concordaram.',
        },
      ],
    },
    {
      label: 'Volume e fluxo',
      items: [
        {
          id: 'vol',
          label: 'Volume bars',
          glyph: '▬',
          color: '#26c6da',
          active: volumeVisible,
          onToggle: () => setVolumeVisible((v) => !v),
          title: 'Raw volume bars (base of the main pane)',
        },
        {
          id: 'vp',
          label: 'Volume profile',
          glyph: '▤',
          color: '#5b8dff',
          active: volumeProfileVisible,
          onToggle: () => setVolumeProfileVisible((v) => !v),
          badge: volumeProfileDelta ? 'Δ' : undefined,
          title: 'Volume-at-price (POC red, value area blue)',
          secondary: {
            glyph: 'Δ',
            title: 'Colour the bands by aggressor side (estimated per candle)',
            active: volumeProfileDelta,
            onToggle: () => setVolumeProfileDelta((v) => !v),
          },
        },
        {
          id: 'vsa',
          label: 'VSA signals',
          glyph: '≈',
          color: '#e040fb',
          active: vsaMode !== 'off',
          // Three states, cycled by the row: recent -> full history -> off.
          onToggle: () =>
            setVsaMode((m) => (m === 'recent' ? 'full' : m === 'full' ? 'off' : 'recent')),
          badge: vsaMode === 'recent' ? 'recent' : vsaMode === 'full' ? 'full' : undefined,
          title: 'Volume-spread signals — click to cycle: recent (last candles) → full history → off',
        },
        {
          id: 'control',
          label: 'Control oscillator',
          glyph: '⚑',
          color: '#26a69a',
          active: controlOscVisible,
          onToggle: () => setControlOscVisible((v) => !v),
          disabled: !controlAvailable,
          title: controlAvailable
            ? 'CVD aggression × OI — who is in control, and how strongly'
            : 'Sem open interest nesta fonte (par on-chain ou spot) — não há leitura de controle para desenhar',
        },
        {
          id: 'panes',
          label: 'Vol delta / RSI panes',
          glyph: '⊞',
          color: '#42a5f5',
          active: indicatorsVisible,
          onToggle: () => setIndicatorsVisible((v) => !v),
          title: 'Volume delta / RSI indicator panes',
        },
        {
          id: 'rsidiv',
          label: 'RSI divergence',
          glyph: '∿',
          color: '#ab47bc',
          active: rsiDivVisible,
          onToggle: () => setRsiDivVisible((v) => !v),
          title: 'RSI divergence trendlines mirrored onto the price structure',
        },
      ],
    },
    {
      label: 'Médias e bandas',
      items: [
        {
          id: 'vwap',
          label: 'VWAP',
          glyph: '⌀',
          color: '#e0a13a',
          active: vwapMode !== 'off' || anchoredVwapVisible,
          onToggle: () => setVwapMode((m) => VWAP_MODE_CYCLE[m]),
          badge: `${vwapMode === 'bands' ? 'σ' : vwapMode === 'line' ? 'line' : ''}${
            anchoredVwapVisible ? ' ⚓' : ''
          }`.trim() || undefined,
          title: 'Cycle the periodic VWAP — off → line (the average price paid since the anchor) → line + ±1σ/±2σ bands',
          secondary: {
            glyph: '⚓',
            title: 'Anchored VWAPs from the last CHoCH and sweep (that crowd’s break-even)',
            active: anchoredVwapVisible,
            onToggle: () => setAnchoredVwapVisible((v) => !v),
          },
        },
        {
          id: 'tide',
          label: 'Tide ribbon',
          glyph: '◈',
          color: '#e0b341',
          active: ribbonVisible,
          onToggle: () => setRibbonVisible((v) => !v),
          title: 'VWAP envelope coloured by SMC structure, desaturated when no fresh money backs the move',
        },
        {
          id: 'st',
          label: 'Supertrend',
          glyph: '⌁',
          color: '#26a69a',
          active: supertrendVisible,
          onToggle: () => setSupertrendVisible((v) => !v),
          title: 'ATR-trailing trend envelope, flip markers on the turn',
        },
        {
          id: 'obvwap',
          label: 'OB · VWAP reclaims',
          glyph: '⟡',
          color: '#d8a949',
          active: blockReclaimVisible,
          onToggle: () => setBlockReclaimVisible((v) => !v),
          title: 'A VWAP reclaim right after price tested an order block, drawn only where the two sit within about one ATR of each other. The label is that distance.',
        },
      ],
    },
  ]

  const resetIndicators = () => {
    setSmcVisible(false)
    setObVisible(false)
    setEqlVisible(false)
    setSweepVisible(false)
    setSweptZonesVisible(false)
    setRangeBoxesVisible(false)
    setHuntWindowVisible(false)
    setContinuationWindowVisible(false)
    setDefendedVisible(false)
    setVolumeVisible(false)
    setVolumeProfileVisible(false)
    setVsaMode('off')
    setControlOscVisible(false)
    setIndicatorsVisible(false)
    setRsiDivVisible(false)
    setVwapMode('off')
    setAnchoredVwapVisible(false)
    setRibbonVisible(false)
    setSupertrendVisible(false)
    setBlockReclaimVisible(false)
  }

  return (
    <div className="flex h-screen flex-col bg-[#0a0d14] text-[#d1d4dc]">
      {/* ── Header ───────────────────────────────────────────── */}
      <header className="flex flex-none items-center justify-between border-b border-[#1a1f2e] px-4 py-2.5">
        <div className="flex items-center gap-3">
          <Logo size={26} />
          <div className="flex items-baseline gap-2">
            <h1 className="text-sm font-bold tracking-tight text-[#e1e4ec]">
              LIQUIDITY HUNTER
            </h1>
            <span className="text-[10px] font-medium tracking-widest text-[#2962ff]">
              RESEARCH
            </span>
          </div>
        </div>

        {/* Symbol and timeframe are both selected in the chart toolbar. */}
      </header>

      {/* ── Content ──────────────────────────────────────────── */}
      <main className="relative flex min-h-0 flex-1 flex-col px-3 py-2">
        {dataStale && (
          <div className="pointer-events-none absolute left-1/2 top-4 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full border border-[#1a1f2e] bg-[#0f1319f0] px-3 py-1.5 text-[11px] font-medium text-[#9ca3b4] shadow-lg">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-[#2962ff]" />
            Loading {symbol} · {timeframe.toUpperCase()}…
          </div>
        )}
        {error && (
          <div className="flex items-center gap-3 rounded-lg border border-[#ef535030] bg-[#ef53500a] p-4">
            <span className="text-sm text-[#ef5350]">⬡</span>
            <div>
              <div className="text-xs font-medium text-[#ef5350]">Connection Error</div>
              <div className="mt-0.5 text-[11px] text-[#9ca3b4]">{error}</div>
            </div>
          </div>
        )}

        {!error && !data && <LoadingSkeleton />}

        {data && (
          <div
            className={`flex min-h-0 flex-1 flex-col gap-2 transition-opacity duration-150 ${
              dataStale ? 'pointer-events-none opacity-40' : ''
            }`}
          >
            <KpiRow data={data} />
            <div className="flex min-h-0 flex-1 gap-2">
              {/* Chart area */}
              <div className="flex min-h-0 min-w-0 flex-1 flex-col rounded-lg border border-[#1a1f2e] bg-[#0f1319]">
                {/* Chart toolbar */}
                <div className="flex items-center justify-between border-b border-[#1a1f2e] px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <SymbolPicker
                      options={SYMBOL_OPTIONS}
                      value={symbol}
                      onChange={switchSymbol}
                    />
                    <span className="text-[10px] text-[#3d4455]">•</span>
                    <div className="flex items-center rounded border border-[#1a1f2e] bg-[#0a0d14] p-px">
                      {TIMEFRAME_OPTIONS.map((opt) => (
                        <button
                          key={opt.value}
                          onClick={() => switchTimeframe(opt.value)}
                          className="rounded-[3px] px-2 py-0.5 text-[10px] font-bold tracking-wide transition-all duration-150"
                          style={{
                            color: timeframe === opt.value ? '#e1e4ec' : '#5d6477',
                            backgroundColor: timeframe === opt.value ? '#1a1f2e' : 'transparent',
                            boxShadow: timeframe === opt.value ? '0 1px 2px rgba(0,0,0,0.2)' : 'none',
                          }}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                    <IndicatorMenu
                      groups={indicatorGroups}
                      onReset={resetIndicators}
                    />
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-[#3d4455]">
                    {(() => {
                      const d = data
                      const tz = chartTimezoneLabel(d.timeframe)
                      const last = d.candles.at(-1)
                      return last ? (
                        <>
                          <span>O <span className="font-mono text-[#9ca3b4]">{formatPrice(last.open, last.close)}</span></span>
                          <span>H <span className="font-mono text-[#26a69a]">{formatPrice(last.high, last.close)}</span></span>
                          <span>L <span className="font-mono text-[#ef5350]">{formatPrice(last.low, last.close)}</span></span>
                          <span>C <span className="font-mono text-[#9ca3b4]">{formatPrice(last.close, last.close)}</span></span>
                          <span
                            className="rounded bg-[#1a1f2e] px-1 py-0.5 font-mono text-[9px] text-[#5d6477]"
                            title={
                              tz === 'UTC'
                                ? 'Chart times are exchange time (UTC)'
                                : `Chart times are your local time (${tz})`
                            }
                          >
                            {tz}
                          </span>
                        </>
                      ) : null
                    })()}
                  </div>
                </div>
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-b-lg p-1">
                  {/* Keyed by the *snapshot's* identity, not the selection:
                      the mounted chart keeps rendering the previous snapshot
                      while a switch loads, and remounts only when the new
                      combo's data actually arrives. */}
                  <MainChart key={`${data.symbol}-${data.timeframe}`} data={data} showConsolidationRanges={rangeBoxesVisible} showDivergenceMarkers={divChartVisible} vsaMode={vsaMode} showSweptZones={sweptZonesVisible} showOrderBlocks={obVisible} showSweeps={sweepVisible} showSmc={smcVisible} showEqlZones={eqlVisible} showIndicators={indicatorsVisible} showHuntWindow={huntWindowVisible} showContinuationWindow={continuationWindowVisible} showVolume={volumeVisible} showRsiDivergence={rsiDivVisible} showSupertrend={supertrendVisible} showBlockReclaims={blockReclaimVisible} vwapMode={vwapMode} showAnchoredVwap={anchoredVwapVisible} showVolumeProfile={volumeProfileVisible} volumeProfileMode={volumeProfileDelta ? 'delta' : 'value-area'} showControlOscillator={controlOscVisible && controlAvailable} showRibbon={ribbonVisible} showDefendedLevels={defendedVisible} />
                </div>
              </div>

              {/* Sidebar */}
              <div className="flex w-72 flex-none flex-col overflow-hidden rounded-lg border border-[#1a1f2e] bg-[#0f1319]">
                <div className="border-b border-[#1a1f2e] px-3 py-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[#5d6477]">
                    Analysis
                  </span>
                </div>
                <div className="flex-1 overflow-y-auto p-3">
                  <div className="flex flex-col gap-4 divide-y divide-[#1a1f2e] [&>*:not(:first-child)]:pt-4">
                    {overview && (
                      <MultiTimeframePanel
                        overview={overview}
                        activeTimeframe={timeframe}
                        onSelectTimeframe={switchTimeframe}
                      />
                    )}
                    {data.behavior_divergences.length > 0 && (
                      <BehaviorDivergencePanel
                        divergences={data.behavior_divergences}
                        chartVisible={divChartVisible}
                        onToggleChart={() => setDivChartVisible((v) => !v)}
                      />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* ── Status Bar ───────────────────────────────────────── */}
      <StatusBar data={data} symbol={symbol} />
    </div>
  )
}

export default App
