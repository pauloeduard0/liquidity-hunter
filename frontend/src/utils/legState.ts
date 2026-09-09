import type { StructuralStall } from '../types/dashboard'

/**
 * The standing leg's operational state, derived — never stored.
 *
 * The chart carries two different readings that were, until now, painted with
 * one channel: *which way structure points* (the trend, from the BOS/CHoCH
 * staircase) and *whether the leg it points with is still advancing*. A bullish
 * leg that stopped advancing is not bearish and did not have a CHoCH; it is
 * bullish, inactive. `LegState` is only that second axis.
 *
 * It is a **projection of `DashboardData.structural_stall`**, and nothing else:
 * there is no `leg_state` field in the payload, no enum in the schema and no
 * state kept here. A second copy of this state is the one way it could ever
 * disagree with the backend, so there is not one.
 *
 * What it is not, and must never be read as: `stale` is not a direction, not a
 * reversal, not a forecast, and not a failed CHoCH. `CHOCH_FAILED` (the `✕`) is
 * an *invalidation* — a change of structure that was attempted and refused —
 * and it lives in the event stream, with its own glyph and its own effect on
 * the trend. A stall says only that the leg has gone quiet, leaves every event,
 * line and protected level standing, and is drawn as the `○` at `stale_since`
 * (see `stallMarker.ts`). The two coexist by design: the BTCUSDT H1 leg of
 * 2026-09 is a `✕` on 09-03 *and* a stall from 09-07, and both are true.
 */
export type LegState = 'active' | 'stale'

/**
 * The current leg's state: `stale` when the payload carries a stall at all.
 *
 * `structural_stall` is the *standing* leg's reading, so its mere presence is
 * the answer — the backend clears it the moment a new advance opens a leg
 * (`detect_structural_stall` reads only the last advance). There is no history
 * of past stalls in the payload and none is reconstructed here.
 */
export function deriveLegState(stall: StructuralStall | null | undefined): LegState {
  return stall ? 'stale' : 'active'
}

/**
 * The leg state *at one candle* — which is the reading the chart needs.
 *
 * A stall has a beginning: `stale_since` is the candle where the leg went
 * quiet, and everything the leg did before that was genuinely active. Dimming
 * the whole window would rewrite the history of a leg that was advancing at the
 * time, so the state is `stale` only from `stale_since` forward.
 *
 * Timestamps are compared as ISO-8601 strings, the same ordering
 * `structureTrendByCandle` uses to walk the event stream — these come from the
 * same payload, in the same UTC format, so lexical order is chronological.
 */
export function legStateAt(
  stall: StructuralStall | null | undefined,
  timestamp: string,
): LegState {
  if (!stall?.stale_since) return 'active'
  return timestamp >= stall.stale_since ? 'stale' : 'active'
}

/**
 * How much of its ink a stale segment keeps.
 *
 * Alpha, and not hue or saturation, because those two are already spoken for:
 * hue is the structural direction (changing it would claim a reversal nobody
 * measured) and saturation is Market Control's conviction (`tideRibbon.ts`), so
 * draining it would claim nobody is paying. Opacity is the one free channel on
 * the ribbon, and "less ink" is the same thing the pane already says elsewhere:
 * the structure lines dim to `0x99` and the stall's own `○` sits below them at
 * `0x80` (`STALL_MARK_ALPHA`).
 *
 * A uniform scale rather than a new absolute value, deliberately: it multiplies
 * whatever each channel was already going to be, so it cannot collide with the
 * meaning of any existing alpha, and a stale band keeps its own relative
 * texture — a stale-but-well-funded segment still reads stronger than a stale
 * and unfunded one.
 */
export const STALE_ALPHA_SCALE = 0.5

/** `alpha` as drawn for a leg in this state. Identity while active. */
export function legStateAlpha(alpha: number, state: LegState): number {
  return state === 'stale' ? alpha * STALE_ALPHA_SCALE : alpha
}
