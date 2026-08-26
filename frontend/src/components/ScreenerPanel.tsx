import type { BlockReclaimScanEntry, BlockReclaimScreen } from '../types/dashboard'
import { formatPrice } from '../utils/format'
import { CollapsibleSection } from './CollapsibleSection'

const TF_LABELS: Record<string, string> = {
  '15m': '15M',
  '30m': '30M',
  '1h': '1H',
  '4h': '4H',
}

/**
 * The measured gate: inside 1.0 ATR the reading is the studied population
 * (`docs/block_reclaim.md`); outside it the row is shown dimmed — observed,
 * never hidden, the same emit-don't-filter contract the detector keeps.
 */
const GATE_R_ATR = 1.0

/**
 * The accumulation floor: on M15, a reclaim against a session VWAP younger
 * than ~15 candles measured 46% against the gate's 55% (walk-forwarded,
 * PBO 0.000 — see `docs/block_reclaim.md`). Young-VWAP rows get a `·youngV`
 * hint rather than being hidden, the same emit-don't-filter contract.
 */
const VWAP_CANDLES_FLOOR = 15

const MAX_ROWS = 14

function rowTitle(entry: BlockReclaimScanEntry): string {
  const parts = [
    `${entry.symbol} ${TF_LABELS[entry.timeframe] ?? entry.timeframe}`,
    entry.status === 'fired' ? 'reclaim fired' : 'block under test, waiting for the trigger',
    `block ${formatPrice(entry.block_price_low)}–${formatPrice(entry.block_price_high)}`,
  ]
  if (entry.r_atr != null) parts.push(`r_atr ${entry.r_atr.toFixed(2)}`)
  if (entry.reclaim) {
    parts.push(`trigger ${entry.reclaim.trigger_line} · pinbar ${entry.reclaim.pinbar_grade}`)
    if (!entry.reclaim.color_agrees) {
      parts.push('candle closed against the trade — a tiebreaker, not a veto')
    }
    parts.push(`VWAP ${entry.reclaim.vwap_candles} candles old`)
    if (entry.reclaim.vwap_candles < VWAP_CANDLES_FLOOR) {
      parts.push('young VWAP — below the measured accumulation floor')
    }
    if (entry.reclaim.provisional) parts.push('candle still forming')
  }
  return parts.join(' · ')
}

function ScreenerRow({ entry }: { entry: BlockReclaimScanEntry }) {
  const bullish = entry.direction === 'bullish'
  const dirColor = bullish ? '#26a69a' : '#ef5350'
  const fired = entry.status === 'fired'
  const inGate = entry.r_atr != null && entry.r_atr <= GATE_R_ATR
  const dim = fired && !inGate
  return (
    <div
      className="flex items-center gap-2 rounded px-1.5 py-1 text-[11px]"
      style={{ opacity: dim ? 0.45 : 1 }}
      title={rowTitle(entry)}
    >
      <span
        className="w-10 flex-none rounded px-1 text-center text-[9px] font-bold uppercase"
        style={{
          color: fired ? '#0e1117' : '#ffb300',
          backgroundColor: fired ? dirColor : '#ffb30022',
        }}
      >
        {fired ? 'FIRED' : 'ARMED'}
      </span>
      <span className="w-16 flex-none font-semibold text-[#c8cede]">{entry.symbol.replace('USDT', '')}</span>
      <span className="w-7 flex-none text-[#5d6477]">{TF_LABELS[entry.timeframe] ?? entry.timeframe}</span>
      <span className="w-4 flex-none" style={{ color: dirColor }}>
        {bullish ? '▲' : '▼'}
      </span>
      <span className="flex-1 text-right text-[#8a91a5]">
        {entry.r_atr != null ? (
          <span style={{ color: inGate ? '#26a69a' : '#5d6477' }}>
            {entry.r_atr.toFixed(2)} ATR
          </span>
        ) : (
          <span className="text-[#ffb300]">testing</span>
        )}
      </span>
      <span className="w-10 flex-none text-right text-[#5d6477]">
        {entry.candles_ago === 0 ? 'live' : `${entry.candles_ago}c`}
        {entry.reclaim?.provisional ? '?' : ''}
      </span>
      {entry.reclaim != null && entry.reclaim.vwap_candles < VWAP_CANDLES_FLOOR && (
        <span className="flex-none text-[9px] text-[#ffb300]" title="young VWAP — below the measured accumulation floor">
          ·youngV
        </span>
      )}
    </div>
  )
}

interface ScreenerPanelProps {
  screen: BlockReclaimScreen
}

/**
 * Universe-wide block-reclaim screener: one list instead of seventy charts.
 * The setup fires ~0.5×/month per symbol, so scarcity on a watchlist is a
 * coverage problem — this panel is the coverage.
 */
export function ScreenerPanel({ screen }: ScreenerPanelProps) {
  const entries = screen.entries.slice(0, MAX_ROWS)
  const fired = screen.entries.filter((e) => e.status === 'fired').length
  const armed = screen.entries.length - fired
  return (
    <CollapsibleSection
      title="Block Reclaim Screener"
      trailing={
        <span className="text-[10px] text-[#5d6477]">
          {fired}⚡ {armed}◔ · {screen.symbols_scanned} syms
          {screen.symbols_failed.length > 0 ? ` · ${screen.symbols_failed.length} failed` : ''}
        </span>
      }
    >
      {entries.length === 0 ? (
        <div className="px-1.5 py-2 text-[11px] text-[#5d6477]">
          nothing armed or recently fired across the universe
        </div>
      ) : (
        <div className="flex flex-col">
          {entries.map((entry) => (
            <ScreenerRow
              key={`${entry.symbol}|${entry.timeframe}|${entry.status}|${entry.timestamp}|${entry.direction}`}
              entry={entry}
            />
          ))}
          {screen.entries.length > MAX_ROWS && (
            <div className="px-1.5 pt-1 text-[10px] text-[#5d6477]">
              +{screen.entries.length - MAX_ROWS} more
            </div>
          )}
        </div>
      )}
    </CollapsibleSection>
  )
}
