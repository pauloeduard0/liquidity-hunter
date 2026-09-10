/**
 * How a drawn structure event looks, on two axes that must never be multiplied
 * together.
 *
 *   AXIS 1 -- HISTORICAL BASE APPEARANCE (colour + alpha)
 *     What was true about the event when it printed. A pure function of the
 *     event. Causal, permanent, and blind to everything that came after.
 *
 *   AXIS 2 -- CURRENT RELEVANCE HIGHLIGHT (line width)
 *     Whether the event describes the condition on screen right now. A
 *     projection of the present, allowed to change as new events print.
 *     PRESENTATION-ONLY. Do not use for research, replay or structural logic.
 *
 * The separation is the whole point of this file, and it was learned the
 * expensive way. The first version had one channel for both questions: it
 * scaled each event's alpha by how recent it was, so a BOS that printed
 * confirmed and strong at 1.00 was redrawn at 0.40 once newer events arrived,
 * and a weak CHoCH at 0.24. That makes the chart unreadable *as history* --
 * reviewing last month's expansion, every break in it looks like it was weak
 * at the time, which is false. Age is not a property of an event.
 *
 * So axis 1 answers only from the event, and its signatures enforce that:
 * `structureBaseAlpha(event)` cannot see the event list, the stall, the
 * priority or the last candle, because it is not given them. Axis 2 gets the
 * present and spends it on the one channel that carried no meaning before --
 * stroke width (every BOS/CHoCH was `1`, sweeps `2`). Hue stays the structural
 * bias, dashes stay the weak/provisional and sweep caveats, alpha stays
 * history. Nothing was taken from anything.
 *
 * The classification below (`current` / `recent` / `history`) belongs to axis
 * 2 alone. It is a projection of now: when a new advance prints, the previous
 * one drops from `current` to `recent`, so the picture of the past does change
 * -- legitimate for a dashboard, disqualifying for anything that measures.
 * Nothing here may be imported by research code, by a backtest, or by anything
 * that reconstructs what was knowable at a candle.
 *
 * It invents no structural state. There is no new machine, no new event and no
 * reinterpretation: the classification reads the existing event stream and its
 * ordering, and nothing else.
 */
import type { MarketStructure, StructureEvent } from '../types/dashboard'
// Explicit `.ts` (the project sets `allowImportingTsExtensions`): it lets
// `structureRelevance.test.ts` run under `node --test` with no test runner.
import type { LegState } from './legState.ts'

/**
 * The three tiers.
 *
 * `current`  — describes the operational condition now.
 * `recent`   — the sequence that led into it; context, still legible.
 * `history`  — superseded by a newer sequence. Reduced, never hidden.
 */
export type StructurePriority = 'current' | 'recent' | 'history'

/**
 * The events that count as a **structural advance** for presentation.
 *
 * `CHOCH_FAILED` is deliberately absent, and this deliberately differs from
 * the backend. `liquidity/structural_stall.py::_last_advance` (mirroring
 * `app.dashboard_data._advance_boundaries`) *does* include `CHOCH_FAILED`,
 * because there a boundary is "the previous leg ended and another began", and
 * a failure genuinely ends one. Read as a sentence on a chart, though, "the
 * last advance was a failed CHoCH" is false: nothing advanced, an attempt was
 * refused. So the two definitions stay apart on purpose — the detector is not
 * touched to harmonize them, and nothing here is fed back into it.
 *
 * A failed CHoCH remains a first-class structural event: its own colour, its
 * own `✕`, its own pairing rules, its own short line. It can perfectly well be
 * the most recent event in the window. It is simply never called an advance,
 * and so never takes the `current` tier away from the break that did advance.
 */
const ADVANCE_EVENTS: ReadonlySet<StructureEvent> = new Set<StructureEvent>([
  'break_of_structure',
  'change_of_character',
])

/** A non-provisional BOS/CHoCH. Provisional marks are the live-edge read and
 *  may repaint entirely, so they can never own the `current` tier. */
export function isStructuralAdvance(event: MarketStructure): boolean {
  return event.provisional !== true && ADVANCE_EVENTS.has(event.event)
}

/** Identity of a drawn event. The payload has no id, and a timestamp alone is
 *  not unique (a CHoCH and its `✕` can share one), so the tuple is the key. */
function eventKey(event: MarketStructure): string {
  return `${event.timestamp}|${event.event}|${event.direction}`
}

/** The most recent structural advance in the window, or `null` if there is
 *  none. Ordering is lexical on the ISO-8601 timestamps, the same comparison
 *  `structureTrendByCandle` and `legStateAt` use on this payload. */
export function lastStructuralAdvance(
  events: readonly MarketStructure[],
): MarketStructure | null {
  let latest: MarketStructure | null = null
  for (const event of events) {
    if (!isStructuralAdvance(event)) continue
    if (latest === null || event.timestamp > latest.timestamp) latest = event
  }
  return latest
}

/**
 * The classification rule, in full:
 *
 *   - the last structural advance is `current`;
 *   - anything at or after it is `recent` — this is where a `CHOCH_FAILED`
 *     that came after the advance lands, and where the live-edge provisional
 *     marks land;
 *   - anything at or after the *previous* advance is `recent` too: that is the
 *     sequence the current one grew out of, and it is what "recent history"
 *     means here;
 *   - everything older is `history`.
 *
 * With no advance in the window at all, everything is `recent`: there is no
 * basis to call anything current, and the instruction on ambiguity is to
 * prefer `recent` over `current`.
 */
function classify(
  event: MarketStructure,
  current: MarketStructure | null,
  previous: MarketStructure | null,
): StructurePriority {
  if (current === null) return 'recent'
  if (eventKey(event) === eventKey(current)) return 'current'
  if (event.timestamp >= current.timestamp) return 'recent'
  if (previous !== null && event.timestamp >= previous.timestamp) return 'recent'
  return 'history'
}

/**
 * The two most recent advances, newest first. Computed once per payload by
 * `buildStructurePriority` so the per-event lookup stays O(1) — the draw loop
 * runs over every structure event on every apply.
 */
function twoLatestAdvances(
  events: readonly MarketStructure[],
): [MarketStructure | null, MarketStructure | null] {
  let current: MarketStructure | null = null
  let previous: MarketStructure | null = null
  for (const event of events) {
    if (!isStructuralAdvance(event)) continue
    if (current === null || event.timestamp > current.timestamp) {
      previous = current
      current = event
    } else if (previous === null || event.timestamp > previous.timestamp) {
      previous = event
    }
  }
  return [current, previous]
}

/**
 * Whether an event's line is still the **current structural reference**: it
 * survived every superseding rule and still runs to the right edge.
 *
 * Note what this does *not* do — it does not re-derive anything. The caller
 * passes the `endTime` that `structureLineEndTime` already produced for the
 * line it is about to draw, so there is exactly one implementation of "is this
 * reference still alive" in the codebase and this is a reading of it. Prices,
 * starts, ends and meanings are untouched.
 *
 * It is not called a protected level. The backend exposes no such field and
 * this is not the place to invent the concept.
 */
export function isCurrentStructuralReference(
  event: MarketStructure,
  endTime: number,
  lastCandleTime: number,
): boolean {
  // Only a real advance can be the standing reference. A sweep's line is a
  // wick annotation, and a `✕` is a point-in-time invalidation whose line is
  // deliberately short-lived; neither governs anything at the right edge.
  if (!isStructuralAdvance(event)) return false
  return endTime >= lastCandleTime
}

export interface StructurePriorityInput {
  /** The line end `structureLineEndTime` produced, when one was drawn. */
  endTime?: number
  lastCandleTime?: number
}

/**
 * Build the per-payload classifier. Call once per apply, then per event.
 */
export function buildStructurePriority(
  events: readonly MarketStructure[],
): (event: MarketStructure, input?: StructurePriorityInput) => StructurePriority {
  const [current, previous] = twoLatestAdvances(events)
  return (event, input) => {
    const priority = classify(event, current, previous)
    if (priority === 'current') return priority
    // An older advance whose reference line is still alive at the right edge
    // is still governing the chart, so it keeps full weight even though a
    // newer advance owns the `current` label. This is the one promotion.
    if (
      input?.endTime !== undefined &&
      input.lastCandleTime !== undefined &&
      isCurrentStructuralReference(event, input.endTime, input.lastCandleTime)
    ) {
      return 'current'
    }
    return priority
  }
}

/** Single-event convenience, for tests and one-off reads. */
export function structureEventPriority(
  event: MarketStructure,
  events: readonly MarketStructure[],
  input?: StructurePriorityInput,
): StructurePriority {
  return buildStructurePriority(events)(event, input)
}


/**
 * ---------------------------------------------------------------------------
 * AXIS 1 — HISTORICAL BASE APPEARANCE
 *
 * Historical base appearance must remain independent of current relevance.
 *
 * Everything below this line answers "what was true about this event when it
 * happened", and its inputs are the event and nothing else. No event list, no
 * priority, no stall, no leg state, no last candle -- the signatures are the
 * enforcement, not the comments. An event drawn today looks the way it looked
 * the day it printed, and it keeps looking that way however many events print
 * after it.
 * ---------------------------------------------------------------------------
 */

/** Fully confirmed: a real reference, broken and confirmed. */
export const FULL_BASE_ALPHA = 1
/** `0x99 / 0xff` -- the dim the pane has always used for a weak reference or a
 *  live-edge mark that may still repaint. Semantics of the *event*, so it is
 *  historical and permanent, never a statement about age. */
export const WEAK_BASE_ALPHA = 0x99 / 0xff

/**
 * The pane's own caveat axis, in one place: a CHoCH that broke a weak
 * reference (re-anchor / fallback / wick-promoted) or any provisional
 * live-edge mark. Both were drawn at `99` before Etapa 8 and still are.
 */
export function isVisuallyWeak(event: MarketStructure): boolean {
  if (event.provisional === true) return true
  return event.event === 'change_of_character' && event.reference_structural === false
}

/**
 * The alpha an event is drawn at -- a pure function of the event.
 *
 * This is the whole of axis 1, and the deliberate absence of every other
 * parameter is the design. Etapa 8.0 multiplied this by a recency scale, which
 * meant a confirmed BOS printed at 1.00 was redrawn at 0.40 once it aged, and a
 * weak CHoCH at 0.24: two events with different *meanings* pushed together
 * while each drifted away from what it had been. Age is not a property of the
 * event, so it does not belong in the event's appearance.
 */
export function structureBaseAlpha(event: MarketStructure): number {
  return isVisuallyWeak(event) ? WEAK_BASE_ALPHA : FULL_BASE_ALPHA
}

/** `alpha` (0-1) as the two hex bytes the chart's colour strings carry. */
export function alphaHex(alpha: number): string {
  const byte = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
  return byte.toString(16).padStart(2, '0')
}

/** A `#rrggbb` base plus the event's own historical alpha. Takes no present. */
export function structureEventColor(base: string, event: MarketStructure): string {
  return `${base}${alphaHex(structureBaseAlpha(event))}`
}

/**
 * ---------------------------------------------------------------------------
 * AXIS 2 — CURRENT RELEVANCE HIGHLIGHT
 *
 * PRESENTATION-ONLY. Do not use for research, replay or structural logic.
 *
 * Everything below answers "does this event describe the condition on screen
 * right now", and it may never reach back into axis 1. It is allowed to change
 * when a new event prints -- that is what makes it presentation and not a
 * replay -- and it changes exactly one thing: how thick the line is drawn.
 * ---------------------------------------------------------------------------
 */

/** Stroke width in px. Lightweight-charts takes `1..4`. */
export interface HighlightStyle {
  lineWidth: 1 | 2 | 3
}

/** No emphasis: the event is drawn at its historical appearance, full stop. */
export const NO_HIGHLIGHT: HighlightStyle = { lineWidth: 1 }

/**
 * The emphasis the *current* element gets, and only it.
 *
 * Width rather than opacity, because opacity is axis 1 and hue is the
 * structural bias; width was the one channel on a structure line carrying
 * nothing (every BOS/CHoCH was `1`, sweeps `2`). It is also free: same series,
 * same draw, one integer.
 *
 * A stall reaches this function and stops here. It costs the current element a
 * pixel of emphasis -- the reference is still the reference, it has just gone
 * quiet -- and touches no alpha, no colour and no other event. The break that
 * opened the leg looks exactly as it did while the leg was still advancing,
 * because it *was* advancing then, and a chart that says otherwise is lying
 * about the past to make a point about the present.
 *
 * `recent` and `history` both return `NO_HIGHLIGHT` in this version. That is
 * deliberate, not an oversight: current relevance is visually binary for now.
 * The three-way classification stays because it is semantically real and
 * already measured; if a middle emphasis is ever wanted, this is the only
 * function that has to learn about it.
 */
export function currentHighlightStyle(
  priority: StructurePriority,
  legState: LegState,
): HighlightStyle {
  if (priority !== 'current') return NO_HIGHLIGHT
  return { lineWidth: legState === 'stale' ? 2 : 3 }
}
