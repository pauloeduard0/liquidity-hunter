# Structure detector — decision log

Full history of design decisions and confirmed behaviors for the
`InternalStructureDetector` / `SwingStructureDetector` pipeline. This was
extracted verbatim from `CLAUDE.md` (2026-07-13) to keep that file under its
size limit. It remains the authoritative changelog; `CLAUDE.md` keeps only a
current-state summary and points here.

## Key design decisions and confirmed behaviors

**Both structure detectors use the same unified architecture** (as of today):
trailing `active_high`/`active_low` references, `candidate_choch_<side>` /
`candidate_choch_<side>_baseline` / `validated_choch_<side>` two-step
promotion gate, persistence-based CHoCH confirmation (`is_sustained_break`),
and the LuxAlgo-style `bos_confluence` filter for BOS emission. Neither
detector uses `volume_delta` or `min_volume_delta_ratio` for any confirmation.
`SwingStructureDetector` defaults: `swing_lookback=10`, `persistence_candles=10`.
`InternalStructureDetector` defaults: `swing_lookback=2`, `persistence_candles=5`.
(The state machines remain unified; they diverge only in what an emitted BOS
*reports* as its reference — see "Internal BOS reference + close-break
re-anchor" below — and in the composition-level re-anchor applied to the
internal detector alone.)

**BOS confirmation** (both detectors): the state machine advances **only when
a candle in the leg *closes* beyond the active reference**
(`find_close_break_index`) — a wick-only overshoot does not advance state and
*freezes* the reference (it is not trailed to the new pivot) until a close
confirms. A continuation BOS must also satisfy the **BOS staircase**: it must
extend the leg beyond the previous BOS level (`last_bear_bos_low` /
`last_bull_bos_high`), so bearish BOS lows keep making lower lows (bullish BOS
highs higher highs) while the trend is unchanged; a break of a higher trailing
low (lower trailing high) during a retrace, which does not beat the previous
BOS extreme, is not a BOS. The staircase is **seeded at each CHoCH with the
CHoCH level itself** (the broken reference), so the first BOS of the new leg
must break *beyond the CHoCH level* — a BOS cannot form on the wrong side of
the CHoCH (e.g. a bullish BOS below a bullish CHoCH after price fell back
through it). Only the first BOS out of the `NEUTRAL` bootstrap is
unconstrained. The `BREAK_OF_STRUCTURE` event is *emitted* once confirmed and
optionally passes the `bos_confluence` shadow-balance filter. SWEEP and CHoCH
detection are unaffected by the close/staircase requirements (sweeps are wick
events).

**BOS reference + close-break re-anchor** (**both detectors**, as of
2026-06-26): a BOS reports `reference_price_level` = the **formed low/high it
broke** (the staircase floor captured at the state-advance — `_PendingBOS.floor`
in the internal detector, `floor_at_advance` in the major) rather than the
trailing pivot, so it plots at the prior swing extreme and the BOS levels form a
clean staircase. A composition-level pass, `_reanchor_bos_close_break` in
`load_dashboard_data`, then re-times each BOS to the first candle that *closes*
beyond that formed level (within the window the BOS stays active) and **drops**
any BOS whose leg only *wicked* past it — a conservative close-confirmation that
can leave long event-free stretches on the macro (intended). It also sets
`reference_timestamp` to the candle that *formed* the level, so the frontend
starts the BOS line at the level's origin and runs it to the break. The pass
runs on both `all_internal_events` and `all_major_events`. Both detectors' state
machines, trailing references, and CHoCH promotion are untouched — only the
reported BOS reference/timestamp change.

**Pre-break-reference BOS drop** (**both detectors**, composition level, as of
2026-07-02): `_drop_pre_break_reference_bos` runs right after
`_reanchor_bos_close_break` on both streams. A wick that pokes beyond the
still-unbroken prior BOS level (a failed break attempt) still ratchets the
detector's staircase extreme (`prev_*_bos_extreme`), so the *next* continuation
BOS would report that pre-break wick as the formed level it broke (the M15
motivating case: a 09:45 candle wicked 61447 above the unbroken 61322 level and
closed 61159; after the 11:45 close finally broke 61322, a 12:45 BOS printed
against 61447). The rule: a continuation BOS whose `reference_timestamp`
strictly predates the confirming close (`timestamp`) of the previous
same-direction BOS in the same leg is **dropped** — a reference may only come
from price action after the prior break confirms. A CHoCH resets the constraint
for its direction (the first BOS of a leg legitimately references the
CHoCH-seeded pre-flip level); unresolved `reference_timestamp` → kept.
Same-timestamp BOS are judged earlier-formed-reference first, so a staged mark
re-timed onto the same confirming candle as the real BOS is judged against it
deterministically. The detectors' state machines and CHoCH promotion are
untouched (composition-level, per the additive-over-state-machine lesson).
Measured (`limit=1200`, BTCUSDT): removes exactly the M15 12:45/61447 BOS
(internal + major), plus the same wick-attempt pathology on M5 (a staged
backwards-staircase mark), M30 (64362 wick vs 64250 level), H1 internal (61870
wick vs 62232), H1 major, and one D1 major (a 2023 31500 wick); every dropped
event was verified as `high > prior level && close < prior level` (or the
bearish mirror) formed before the prior BOS's close. Zero events added, H4
untouched.

**Staleness re-anchor** (**both detectors**, as of 2026-06-29):
`stale_reanchor_candles` (constructor default `None` = off; wired in
`load_dashboard_data` for **both** detectors per timeframe via the shared
`_STALE_REANCHOR_CANDLES`, e.g. H4=60, D1=40) retires a stale cycle. On a coarse
timeframe a trend can lock for a long time: a bearish leg pins the bullish
reversal reference at the leg origin, so the eventual CHoCH only fires once price
climbs all the way back there (the "long-stuck-BOS" pathology). When the trend
runs `stale_reanchor_candles` candles past its last BOS / trend flip (tracked by
`last_advance_index`, set in `emit` for BOS/CHoCH/`CHOCH_FAILED`) without a fresh
one, the reversal reference is pulled to the most recent local swing extreme via
the existing `reanchor_opposite` helper — so a CHoCH can confirm **locally** and
a new cycle begins, **without flipping `trend`** (the CHoCH itself still has to
confirm). It only ever tightens (and only to a level on the correct side of
price), so it tracks the recent extreme as the range unfolds; a confirming
CHoCH/BOS resets the counter. Independent of `reanchor_mode`. The two detectors
differ only in how they pick the local extreme: the major uses `last_high_pivot`
/ `last_low_pivot`; the internal (no such held pivot — references trail) uses the
extreme high/low over a trailing window of `stale_reanchor_candles` candles.
**The internal detector is what the chart renders for all timeframes**
(`MainChart.tsx`: `scopeEvents = data.internal_structure_events`), so the
internal staleness re-anchor is what fixes the visible lock; the major one feeds
`market_structure_events` (in the API, not currently drawn).

**Impulse BOS staging** (`InternalStructureDetector` only, as of 2026-06-29):
`impulse_bos_displacement_pct` (constructor default `None` = off; wired in
`load_dashboard_data` via `_IMPULSE_BOS_DISPLACEMENT_PCT = 0.015`). A clean
impulsive leg (consecutive lower lows / higher highs with **no intervening
opposite pivot**) advances the state machine at each step but, with no pullback
pivot to confirm them, emits at most one *deferred* pending BOS — so a sharp
multi-leg move (e.g. a −20% drop over ~60 H4 candles) prints one long event-free
stretch instead of a staircase. When set, each state-advance whose displacement
beyond the prior BOS level (`prev_bear_bos_extreme`/`prev_bull_bos_extreme`, the
`floor_at_advance`) clears the threshold is recorded as a staged BOS in a
**separate list**; at the end of `detect` the staged BOS are **deduped against
the real emitted BOS** (same direction, `price_level` within
`_STAGED_BOS_DEDUP_PCT = 0.2%` — both report the advance pivot's extreme) and
merged. So it only ever *adds* marks in the impulsive gaps the state machine left
empty; the state machine, references, and CHoCH promotion are **untouched** (with
the flag off the output is byte-for-byte identical). Staged BOS leave
`reference_timestamp` unset so `_reanchor_bos_close_break` anchors their line
origin like any other BOS. The reported `reference_price_level` is the prior
BOS's extreme, so staged steps continue the same descending/ascending staircase.
This was chosen over a composition-level post-pass: re-deriving "current trend +
live staircase floor" outside the detector leaks across segments (a stale floor
from a prior leg bleeds in), whereas in-detector `trend`/`prev_*_bos_extreme` are
already correct. Not mirrored into `SwingStructureDetector` (coarse lookback has
far fewer impulsive gaps and is not drawn).

**Re-anchor min-price-gap guard** (`InternalStructureDetector` only, as of
2026-06-30): `reanchor_min_price_gap_pct` (constructor default `None` = off;
wired in `load_dashboard_data` via `_REANCHOR_MIN_PRICE_GAP_PCT = 0.003`) guards
the *output* of `reanchor_opposite` (every trigger — chain, stale, displacement):
it refuses to set the reversal reference to a local extreme closer than this
fraction to current price. A `"chain"` or staleness re-anchor in a tight lateral
range can land `validated_choch_<side>` on a local high/low sitting almost on top
of price; that reference is hair-trigger, so a trivial bounce confirms a
mid-range CHoCH that immediately fails (the "CHoCH in chop" clutter — e.g. an M5
bullish CHoCH the chain re-anchor wrote ~0.1% above price, which then failed).
Requiring a minimum gap makes breaking the re-anchored level a genuine reversal.
Measured purely additive-or-cleaner: on M5 it removes exactly the spurious CHoCH
(its `CHOCH_FAILED` count drops) while staying **neutral on the coarser
timeframes** (whose re-anchors already land far from price — D1's `CHOCH_FAILED`
are legitimate failed macro reversals, not chop, and are unaffected). A
leg-displacement gate (`reanchor_chain_min_displacement_pct`) was prototyped first
but **rejected by measurement** (inert on M30/D1, destabilizing on M5).

**Chain re-anchor establish-only** (`InternalStructureDetector` only, as of
2026-06-30): `reanchor_chain_establish_only` (constructor default `False`; wired
**`True`** in `load_dashboard_data`) restricts the `"chain"` trigger to
*establishing* a reversal reference that has gone blind (the opposite-side
`validated_choch_<side>` is `None`, as in a clean impulse that nulled it) — it
never *tightens* a reference that already exists. The chain trigger exists for the
blind-impulse case; when a fresh `validated_choch_<side>` was just promoted from a
real pullback, tightening it down to a shallower in-leg high degrades the CHoCH
reference to a weak pullback, so a small reclaim fires a premature CHoCH (e.g. an
M5 bullish CHoCH the chain wrote at a shallow 58,861 local high after a good 59,316
pullback had already been promoted — distinct from the gap-guard case, since that
level was a legitimate distance from price). With this set, a present reference is
left for the staleness re-anchor to tighten only once genuinely stale; the
blind-impulse establish case the chain was added for (e.g. the H4 June drop) is
unaffected. Measured: removes the degraded M5 CHoCH, net-neutral on M15/M30/H4,
trims a couple chain-tightening CHoCH on H1/D1.

**BOS pullback wick filter** (`InternalStructureDetector` only, as of
2026-06-30): `bos_pullback_max_wick_pct` (constructor default `None` = off; wired
**`0.4`** in `load_dashboard_data` via `_BOS_PULLBACK_MAX_WICK_PCT`) filters the
pullback pivot that *confirms* a BOS. A BOS confirms when a pivot forms in the
opposite direction (a high pivot for a bearish BOS, a low for a bullish BOS); with
a small swing lookback that pivot can be a single-candle **wick** — the candle
spikes to the extreme intrabar but its body closes far away (e.g. an M5 bearish
BOS confirmed by a candle that wicked up to 58,732 then closed near its low at
58,350 and made a new low) — so the BOS prints off a "pullback" that never
retraced. When set, the confirming pivot candle's pivot-side wick
(`_pullback_quality_ok`: upper wick for a high pivot, lower for a low pivot) must
be at most this fraction of its range; a wick-only spike does not confirm and the
pending BOS is **kept alive** so a later, real-bodied pullback confirms it instead
(or it never confirms if none forms). Because the confirming pullback also seeds
`candidate_choch_<side>`, the filter propagates correctly into CHoCH detection
(the reversal reference anchors to a genuine pullback too) rather than being a
cosmetic mark drop. Adjusting the swing lookback was measured and rejected as a
fix (it coarsens all structure globally and doesn't target the wick). With the
flag off the output is byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (the major detector is not drawn).

**Wick-rejected BOS staging** (`InternalStructureDetector` only, as of
2026-07-01): `stage_wick_rejected_bos` (constructor default `False`; wired
**`True`** in `load_dashboard_data`) is the *additive* complement to the wick
filter above. When a continuation advance's only pullback is a wick (rejected by
`bos_pullback_max_wick_pct`) and no *real* pullback ever confirms it before the
trend flips, the state machine emits **no BOS** even though the leg genuinely
closed beyond the staircase floor — a visibly-missing mark. This flag stages an
**additive** `BREAK_OF_STRUCTURE` for that break (once per pending BOS, at the
break's close and referencing the floor it broke), merged and deduped against the
real BOS at the end exactly like the impulse staging (`impulse_bos_displacement_pct`).
Crucially it **never touches the state machine or CHoCH promotion** — it does not
seed `candidate_choch_<side>` — so, unlike relaxing the wick filter itself (which
cascades: more confirmed BOS shift the trend state and can turn a correct reversal
CHoCH into a premature one), it cannot change the CHoCH sequence. Purely a mark.
A wick-rejected pending BOS that *later* gets a real bodied pullback still emits
its normal BOS (which dedups the staged mark away), so staging only ever fills the
genuinely-missing gaps. With the flag off the output is byte-for-byte identical.
Not mirrored into `SwingStructureDetector` (not drawn). (Relaxing the wick filter
to a neighborhood-corroboration test was prototyped first and **rejected by
measurement**: it recovered the missing marks but cascaded downstream and
destroyed a known-correct reversal CHoCH — the state-machine-vs-additive lesson.)

**Leg-origin CHoCH reference** (`InternalStructureDetector` only, as of
2026-07-02): `bos_leg_origin_choch_ref` (constructor default `False`; wired
**`True`** in `load_dashboard_data`, with `bos_leg_origin_release_gap_pct =
_BOS_LEG_ORIGIN_RELEASE_GAP_PCT = 0.04`). Every *emitted* BOS promotes its **leg
origin** — the extreme the breaking leg launched from (`_PendingBOS.pullback_ref`:
the fundo a bullish leg rose from / the topo a bearish leg dropped from) — directly
to the opposite `validated_choch_<side>` at emission, marked *structural*
(`validated_choch_<side>_structural`), replacing the current reference
unconditionally (even to a looser level — structure wins). The close-break plus the
confirming pullback is itself the continuation evidence, so the CHoCH reference no
longer waits for the continuation gate (which still runs on top and can tighten to
the newer post-BOS pullback). Two companion rules make it hold:
(1) **re-anchors (stale/chain) refuse to slide a structural reference while it is
reachable** — within the release gap of current price (originally
`bos_leg_origin_release_gap_pct` = 4%; since 2026-07-03 volatility-normalized,
see "Volatility-normalized release gap" below); beyond
that gap the leg has run away and the staleness re-anchor regains authority
(otherwise an impulsive leg that emits no BOS for months pins the reference and
coarse timeframes lose whole reversal sequences — the H4 Feb→Mar regime collapse,
measured); (2) under the flag a re-anchor writes its synthetic level **only into
`validated_choch_<side>`**, never into `active_<side>`/`candidate_choch_<side>`,
whose genuine swing pivots feed the leg-origin snapshot — otherwise the re-anchor
level gets laundered into a "structural" leg origin at the next emission (measured:
an M30 leg-origin of 63650, a stale-window artifact, instead of the genuine 65469
fundo). Motivating cases (measured, `limit=1200`): the M30 bearish CHoCH fires
17/06 08:00 against the 15/06 leg origin 65469 (instead of 18/06 against a
stale-re-anchor 64525), and the H4 May bearish CHoCH fires 17/05 against the
78128 mínima of the 04/05 BOS (instead of a sliding stale-window low 78713).
Neutral on H1/D1 counts; M15 loses one whipsaw pair; M5 gains 2 `CHOCH_FAILED`
(accepted, 4-day window). Threshold measured: 5% degrades M15, 6% loses the H4
April CHoCH. Two stricter variants were **rejected by measurement**: hybrid
promote-only-over-re-anchored refs (first structural ref pins the whole leg) and
an unconditional re-anchor bar (same pinning). With the flag off the output is
byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not drawn).

**Leg-origin promotion on origin-reclaim kill** (`InternalStructureDetector`,
as of 2026-07-03): a *pending* BOS (close-break advanced, awaiting its
confirming pullback) that is discarded because the next opposite pivot is
already **beyond its leg origin** (`price > pullback_ref` bearish / `<`
bullish) now promotes that origin to `validated_choch_<side>` (structural)
before being dropped, so the CHoCH check on that same pivot evaluates against
it. Rationale: the state machine already treated the advance as real
(staircase/leg extremes ratcheted), and the reclaim of the leg origin *is* the
conservative reversal — but emission-only promotion missed it whenever the
pullback was wick-rejected (`bos_pullback_max_wick_pct`) and price then
reclaimed the origin directly. Motivating case (measured, ETHUSDT H1,
`limit=1200`): the 06/06 04:00 bearish BOS's leg origin 1618.85 was never
promoted (its 1592.80 pullback was wick-rejected; the staged mark drew the BOS
but stages never promote), the reference stayed at the stale 1793.66, and the
whole 06/07 rally to 1721.57 printed as sweeps with no bullish CHoCH. With the
fix the CHoCH fires 06-07 08:00 against 1618.85 and the bullish leg develops
(BOS 1721.57). Measured elsewhere: same-pattern improvements only — ETH D1's
Dec→Feb decline gets its bearish CHoCH on 12-19 instead of 02-02 (which
correctly becomes a continuation BOS), BTC 4h finds an earlier 01-02 bullish
CHoCH, ETH 4h gains one honest CHoCH+`CHOCH_FAILED` whipsaw pair, M30/H1 refs
tighten slightly. Gated behind `bos_leg_origin_choch_ref` (off = byte-for-byte
identical).

**Pending-BOS leg origin in the CHoCH reference chain**
(`InternalStructureDetector`, as of 2026-07-03, same-day follow-up to the
above): while a pending BOS is **alive** (close-break advanced, every pullback
attempt wick-rejected so it neither emitted nor got killed), its
`pullback_ref` now participates in the CHoCH reference chain: `validated or
pending.pullback_ref or choch_origin or active_<side>` (`via_validated`
treats a pending-origin trigger like a validated one). Motivating case
(ETHUSDT H1 2026-06-25, verified by state instrumentation): the 06-23 bearish
CHoCH was fallback-triggered (armed no blind-spot origin) and reset the
validated refs; the 06-24 BOS (staged mark at 1633) never emitted — both its
pullbacks (1629.15, 1660.56) were wick-rejected — so nothing ever promoted;
the bullish-CHoCH check fell back to the trailing `active_high` = 1629.15 and
the 1660.56 pivot fired a premature mid-range CHoCH, while the pending BOS
carried the genuine 1692 leg origin the whole time. With the fix: 06-25
becomes a sweep, the 06-25 13:00 close-break of 1551 correctly emits as a
continuation BOS (the wrong CHoCH had flipped the trend and eaten it), the
06-26 `CHOCH_FAILED` disappears with its premature CHoCH, and the bullish
CHoCH fires 06-27 10:00 against 1583 (the leg origin of the newest activated
BOS — the reference correctly ratchets when a newer BOS activates). Measured:
**zero diffs** on ETH 30m/4h/1d and BTC 1h/4h — surgical. `validated` still
outranks the pending origin so the staleness re-anchor retains authority over
a long-lived pending. Gated behind `bos_leg_origin_choch_ref`.

**Cold-start fallback suppressed in the unconfirmed-CHoCH window**
(`InternalStructureDetector`, as of 2026-07-03, third same-day follow-up):
the `active_<side>` cold-start fallback in the CHoCH reference chain is
**suppressed while an unconfirmed CHoCH's origin is armed**
(`bear_choch_origin`/`bull_choch_origin`) — inside that provisional window
the designed reversal exit is `CHOCH_FAILED` at the origin, and the fallback
was undercutting it at a far weaker level. Motivating case (SOLUSDT H1
2026-06-23, verified by instrumentation): the 06-20 `CHOCH_FAILED` reset all
refs and armed nothing (one-shot); the 06-22 bearish CHoCH then fired via the
`active_low` fallback (so `via_validated=False` armed no blind-spot origin);
no bearish BOS had activated yet (the 68.07 fundo was the flip pivot itself),
so the bullish side was fully blind — validated/pending/origin all `None` —
and the check fell to the trailing 69.63 LH, firing a premature bullish CHoCH
(failed next day) while `bear_choch_origin` sat at 74.97. With the
suppression: the 70.36 rally is a sweep, the trend stays bearish, the drop
prints the missing bearish continuation BOS (64.66 breaking the 68.07 fundo,
then 64.00), and the genuine bullish CHoCH fires 06-26 against 69.64 (the
newest BOS's leg origin), confirmed by the 70.97→73.91 bullish BOS staircase.
Measured: ETH 1h / BTC 1h / BTC 4h zero diffs; ETH 30m drops 3 whipsaw CHoCH
in the 06-30–07-02 chop (5 flips → 2 CHoCH + 1 honest `CHOCH_FAILED`, plus
the missing 07-03 bullish BOS); ETH 4h/1d reshape only Dec-2024 regions where
the same pattern held (D1: the 3744 reversal attempt now closes with
`CHOCH_FAILED` at its 3302 origin and the Feb-2025 crash BOS references it —
instead of a double bearish CHoCH). Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_1h_2026_06_13_27.json` (5-column
rows: ts/open/high/low/close — open matters for the wick filter). Gated
behind `bos_leg_origin_choch_ref`.

**Volatility-normalized release gap** (`InternalStructureDetector`, as of
2026-07-03): `bos_leg_origin_release_gap_atr` (constructor default `None` = off;
wired **`3.0`** in `load_dashboard_data` via `_BOS_LEG_ORIGIN_RELEASE_GAP_ATR`,
taking precedence over the fixed `bos_leg_origin_release_gap_pct = 0.04`, which
stays as fallback for series too short to measure a range). The structural-ref
release gap becomes `N × mean true-range%` of the detected series instead of a
fixed fraction of price, so "reachable" means the same number of typical candles
on every asset/timeframe. Motivation (measured): the fixed 4% was worth **8.5
ATR on BTC 30m** (the guard held almost always, pinning the reversal reference
through the 06-23..26 June drop so every bounce fired a bullish CHoCH that then
failed — three whipsaw CHoCH/`CHOCH_FAILED` pairs across a 63k→58k decline)
but **0.6 ATR on SOL D1** (one average candle released it — no guard at all).
Measured (BTC/ETH/SOL × 30m/1h/4h/1d, `limit=1200`): **N ∈ [2, 3] is a stable
plateau** (byte-identical outputs); 8/12 combos unchanged vs the fixed 4% (all
SOL, all H4, BTC D1 — the leg-origin motivating cases M30 17/06 and H4 May
78128 intact); BTC 30m resolves the June drop into one bearish CHoCH at the
63833 leg origin + a 59060→58030 bearish BOS staircase and drops the 06-27..30
chop flips (3 trend flips in ~30h); BTC 1h moves the 06-22 CHoCH 1h earlier
with a tighter ref; ETH 30m gains one whipsaw pair 06-20/21 (accepted); ETH 1d
reshapes an Aug–Sep-2023 region (tighter CHoCH ref 1684 vs 1808, the missing
09-11 continuation BOS appears). N=4 reverts to fixed-pct behavior on the fine
timeframes. Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_30m_2026_06_05_07_02.json` (whipsaw
resolution + an ATR≡equivalent-pct equivalence/precedence test). Off = the
fixed-pct behavior, byte-for-byte.

**New-cycle CHoCH barrier** (`InternalStructureDetector`, as of 2026-07-03):
`choch_weak_ref_persistence_candles` (constructor default `None` = off; wired
**`4`** in `load_dashboard_data` for M5/M15/M30/H1 via
`_CHOCH_WEAK_REF_PERSISTENCE` — coarse timeframes with base persistence 8+ are
left alone, and M1 is deliberately absent since its default base of 12 would be
*weakened* by 4). A CHoCH about to fire against a **weak** reference — a
synthetic re-anchor level (`validated_choch_<side>` present but not
`_structural`; only `reanchor_opposite` writes those) or the trailing
`active_<side>` cold-start fallback — must sustain for this many candles
instead of the base `persistence_candles`. **Structural** references (leg
origin, continuation-promoted candidate, live pending-BOS origin, blind-spot
`choch_origin_<side>`) keep the base persistence: the conservative CHoCH is
never delayed. The `CHOCH_FAILED` check also keeps the base persistence (the
escape valve that undoes a wrong cycle must not be delayed). Rationale: with
intraday base persistence at 2, a brief poke through a weak local level was
enough to flip the trend and start a dirty cycle ("às vezes é só um sweep e já
cria CHoCH de novo ciclo"); a genuine reversal holds past the barrier anyway,
so the cost is bounded at a few candles of confirmation delay. Measured
(BTC/ETH/SOL × 5m/15m/30m/1h, barrier 3/4/5, `limit=1200`): every removal is a
whipsaw CHoCH/`CHOCH_FAILED` pair — BTC 5m double-flip chop (two CHoCH 15 min
apart), BTC 15m two pairs (restoring the 06-30 bearish continuation
staircase), BTC 30m 06-12 pair (needs 4+), SOL 30m one pair; ETH 5m / SOL
5m/15m/1h / BTC 1h / ETH 1h untouched. Costs: one genuine weak-ref CHoCH
delayed 9h (ETH 30m, same level), one delayed 1 candle (ETH 15m). **4
chosen**: strictly better than 3 (adds the BTC 30m pair at no observed cost),
while 5 starts delaying a genuine BTC 30m reversal CHoCH by 6h. Tests: weak-ref
delay on the BTC 30m fixture (06-07 23:00 → 06-08 01:30, same ref), structural
exemption on the SOL H1 fixture (barrier 10 ≡ off, the 06-26 CHoCH vs 69.64
intact). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

**Shallow-pullback leg-origin promotion** (`InternalStructureDetector`, as of
2026-07-03): `bos_leg_origin_min_pullback_atr` (constructor default `None` =
off; requires `bos_leg_origin_choch_ref`; wired **`1.5`** in
`load_dashboard_data` for **M15/M30/H1** via `_BOS_LEG_ORIGIN_MIN_PULLBACK_ATR`).
The leg origin a BOS promotes to the opposite CHoCH reference is normally the
trailing pivot at the state-advance (`active_high` for a bearish BOS /
`active_low` for a bullish one) — the *immediate* pullback high/low. When that
immediate pullback is **shallow** — its height (`active_high − active_low`) is
less than N × the series' mean true-range% of price — it is a minor secondary
high/low well inside the correction, so the CHoCH line lands at a small pivot
rather than the correction's visible top/bottom. In that case the origin is
promoted instead to the correction's **extreme** pivot (`pending_high`/
`pending_low`, already the most extreme high/low accumulated for the leg), but
only when that extreme is genuinely beyond the immediate pullback. The reference
then sits at the visible leg top/bottom; and because it is higher/lower, a
premature poke through the shallow level is reclassified as a `LIQUIDITY_SWEEP`
and the reversal CHoCH fires once price reclaims the true extreme. Motivating
case (measured, AAVEUSDT H1 `limit=1200`): the bullish CHoCH ref goes 86.59 →
**87.82** (the correction top, the 07-01 02:00 swing high), firing 07-03 on the
reclaim instead of 07-02 on the 88.49 poke that fell straight back to 84.28.
Only the promoted origin changes — the state machine, trailing references, and
continuation gate are untouched. Measured (BTC/ETH/SOL/AAVE × 5m..1d): **N=1.5**
is the minimum that catches the AAVE target (immediate depth 1.42 × ATR); every
intraday change is a whipsaw CHoCH/`CHOCH_FAILED` pair reclassified to a sweep
(AAVE 30m/1h, BTC 30m, SOL 1h), M15 near-neutral. **M5 is excluded** (noisy,
net-adds marks) and **4h/1d excluded** (they reshape already-tuned coarse
regions, e.g. BTC 4h May 78128 → 78713 — needs visual review), mirroring the
weak-ref barrier's intraday scope. Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_20_07_04.json` (337-candle
self-contained window; off → CHoCH ref 86.59 @ 07-02, on → 87.82 @ 07-03). Off
= byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**Close-confirmed structural leg origin** (`InternalStructureDetector`, as of
2026-07-04): `bos_leg_origin_require_close_break` (constructor default `False`;
requires `bos_leg_origin_choch_ref`; wired **`True`** in `load_dashboard_data`).
A BOS's state machine advances on a close beyond the trailing `active_<side>`,
but the leg origin it promotes to the opposite CHoCH reference is marked
`validated_choch_<side>_structural = True` **only if a candle actually *closed*
beyond the staircase floor** it reported (`_PendingBOS.floor`, checked with the
same `find_close_break_index`). When the continuation merely *wicked* past the
prior BOS level — the exact break `_reanchor_bos_close_break` drops from the
visible marks anyway — the origin is still promoted to the CHoCH reference but
as a **weak** reference (`_structural = False`), so the new-cycle barrier
(`choch_weak_ref_persistence_candles`) governs the resulting CHoCH and
re-anchors may still slide it, instead of it firing at base persistence off an
unconfirmed break. This closes the gap between the wick-based staircase gate
(which accepts a pivot whose wick beats the prior BOS high) and the close-based
composition drop (which hides that BOS): the promotion now agrees with the
mark. Motivating case (measured, AAVEUSDT H1 `limit=1200`): a bullish leg from
the 72.61 fundo (06-16 14:00) makes its only new high over the prior 77.70 BOS
top as a single-candle wick to 77.94 (06-17 02:00, close 76.94, no close above
77.70). Off, 72.61 is structural → a 06-18 poke fires a premature bearish CHoCH
that fails 06-20 (whipsaw); on, 72.61 is weak → the barrier defers the genuine
bearish CHoCH to 06-23 at the same level. Measured (BTC/ETH/SOL/AAVE ×
5m/15m/30m/1h/4h/1d): **23 of 24 combinations byte-identical**, only AAVE H1
changes (−3/+1: the whipsaw CHoCH/`CHOCH_FAILED` pair plus a duplicate collapse
into one clean 06-23 CHoCH). Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_05_24.json` (457-candle
self-contained window). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn); gates the *emitted*-BOS leg-origin
promotion and (since the same day, see below) the candidate-continuation
promotion; the reclaim-kill structural path is untouched.

*Candidate-continuation extension (2026-07-04, same flag)*: the
continuation-gated candidate promotion (`candidate_choch_<side>` →
`validated_choch_<side>` on a new leg extreme) now also marks the promoted
reference weak when the advance's staircase-floor break was wick-only
(`floor_did_close` false — the same physical test). The emission gate alone was
incomplete: on the production window the wick-leg BOS never *emits* (so the
emission gate never runs), yet the wick advance itself — a new leg extreme by
wick only — promoted the candidate as structural, and a CHoCH fired against it
at base persistence with zero closes beyond the floor (user-reported: AAVE H1
bearish CHoCH 06-23 against a leg whose only break of the 77.70 top was the
06-17 wick; in the full window the premature CHoCH pair was 06-18/72.25 →
failed 06-20, then 06-23/74.45 → failed 06-24). A blanket "skip promotion
without a floor close" variant was **rejected by measurement**: it does not
remove those CHoCHs (the reference arrives via the candidate path) and it
destroys genuine wick-top reversals — AAVE H1 06-28 (99.29 wick top over the
98.18 floor, then −15%: base catches the bearish CHoCH + staircase, the skip
variant loses all of it) and BTC 1d's early Oct-2023 reversal — because a
wick-only top is often exactly the SMC liquidity grab a reversal should be
measured from. Weak-promote keeps those reversals (barrier persistence) while
demoting the premature ones. Measured (BTC/ETH/SOL/AAVE × 5m..1d, all flags
wired): **23/24 byte-identical**, only AAVE H1 changes — the two whipsaw pairs
collapse to one honest CHoCH (06-23 15:00 vs 72.25, sustained hold, failed
honestly 06-24 10:00) and the breakout BOS confirms 06-24 22:00 on the first
close above 77.70 (~30h earlier), staircase 77.70 → 85.12 → 87.99. Real-data
regression fixture: `tests/liquidity/detectors/data/aaveusdt_1h_2026_05_10_07_04.json`
(1315 candles — the **full** production internal-detector slice from the
structural anchor; the release-gap/min-pullback ATR guards use the series-wide
mean true range, so a truncated window does not reproduce production state).

**Close-confirmed reported staircase floor** (`InternalStructureDetector`, as
of 2026-07-04, companion to the above): `bos_floor_require_close_break`
(constructor default `False`; wired **`True`** in `load_dashboard_data`). The
*reported* floor tracker (`prev_<side>_bos_extreme`, the level the next
continuation BOS plots against) does **not** ratchet on an advance that only
*wick-swept* it — pivot extreme beyond the current floor with no candle closing
beyond it. Such a wick never established a new formed level (its own mark is
dropped by `_reanchor_bos_close_break`), so the next continuation keeps
referencing the last close-confirmed extreme instead of the wick. Narrow by
design: an advance whose pivot never even *reached* the floor (a trailing-level
break far short of it, e.g. range breakdowns inside a post-crash range months
above the crash fundo) still ratchets — freezing there leaves later BOS
reporting a level their leg never broke, and the composition re-anchor then
drops the whole staircase (measured: AAVE D1 lost its 176→142→91 post-crash
staircase under the broad freeze variant, which was rejected). Companion stash:
the failed-CHoCH staircase restore previously set the reported tracker from the
*gate* (`last_<side>_bos_<extreme>`), which legitimately ratchets on wick-only
breaks — laundering the wick back into the reported floor across a provisional
CHoCH. Under the flag, the reported tracker is stashed at each CHoCH alongside
the gate (`pre_choch_<side>_bos_extreme`) and restored from its *own* stash
(max/min'd with the CHoCH origin, whose break the failure itself
close-confirmed). The state machine — gate, trend, CHoCH promotion — is
untouched: only what an emitted BOS *reports* (and thus where the re-anchor
confirms it) changes. Motivating case (measured, AAVEUSDT H1 `limit=1200`,
same wick as the leg-origin case above): the 06-26 breakout BOS (px 87.99)
reported the swept 77.94 wick instead of the close-confirmed 77.70 first top —
the 06-17 wick advance ratcheted the tracker, and the 06-24 `CHOCH_FAILED`
restore reinjected it from the gate. On, it reports 77.70. Measured
(BTC/ETH/SOL/AAVE × 5m/15m/30m/1h/4h/1d): **zero marks lost**; 10 of 24 combos
change and every change is a reference correction to the earlier
close-confirmed level (BTC 30m 06-13: 64362.60 wick → 64250.00, the documented
pre-break-drop wick; BTC 15m/30m 07-03: 62386.70 → 62180.00; ETH 1h/4h 06-02
converge on 1965.48; ETH 1d 02-02: 3302.20 → 3100.00) or a mark *gain* (AAVE
1d: the May-2026 crash BOS appears, ref the 05-08 91.85 retest low — off, it
referenced the pre-break 85.67 and `_drop_pre_break_reference_bos` killed it).
Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_05_27.json` (552-candle
self-contained window; off → 06-26 BOS ref 77.94, on → 77.70). Off =
byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**Provisional live-edge BOS** (`InternalStructureDetector`, as of 2026-07-05):
`emit_provisional_bos` (constructor default `False`; wired **`True`** in
`load_dashboard_data`). At the right edge a continuation can *close* beyond the
staircase floor (`last_bear_bos_low`/`last_bull_bos_high`) while its confirming
swing pivots have not formed yet (the swing-lookback lag), so the state machine
emits no BOS even though structure broke by close — the "why is there no BOS at
the June low" case (BTC D1: price closed below the 59800 floor on 06-25 → new
lows to 57758, but 57758 is not a confirmed pivot yet). When set, at the end of
`detect` a single `MarketStructure` with `provisional=True` is emitted at the
first candle that closed beyond the floor (`reference_price_level` = the floor,
`reference_timestamp` = the floor's origin, `price_level` = the live leg
extreme), computed from authoritative final state (trend + floor), **never
re-derived outside the detector**. Purely additive (appended, excluded from the
staged-BOS dedup; with the flag off the output is byte-for-byte identical) and
**skipped by** `_reanchor_bos_close_break` / `_drop_pre_break_reference_bos`. The
frontend renders it dimmed + `SparseDotted` with a `?` suffix (`BOS? ▼`), like a
weak CHoCH; it is superseded by the confirmed BOS once pivots form, or vanishes
if the trend flips first (an intentional live-edge repaint, honestly signaled by
the dimmed style — the payoff is *only* the live edge, so a static backtest shows
nothing; it is measured by **walk-forward replay**).
*Continuation gate*: `bull_floor_from_bos` / `bear_floor_from_bos` track whether
the current floor is a genuine BOS extreme (set `True` on a BOS advance, or a
`CHOCH_FAILED` restore of the resumed trend's real floor) or merely *seeded* at a
fresh CHoCH with that CHoCH's own level (`False`). The provisional emits only when
the flag is `True`: a fresh CHoCH's seed level has necessarily already been
closed beyond (that is what confirmed the CHoCH), so a provisional there just
doubles the CHoCH line (the NEAR M15 clutter — a provisional bullish BOS on the
1.965 bullish-CHoCH level). The `CHOCH_FAILED`-restore = `True` keeps the BTC D1
59800 case (no new BOS since the flip, but the floor is the genuine 31/01 BOS
low). Measured (walk-forward, BTC/ETH/SOL × 1h/4h/1d): **85% of resolved
provisional marks confirm** (up from 67% without the gate — it removed the
CHoCH-seed lead≈0 redundants and several repaints, keeping every genuine
continuation), ~11-candle median lead. Not mirrored into `SwingStructureDetector`
(not drawn).

**Provisional live-edge CHoCH** (`InternalStructureDetector`, as of 2026-07-06):
`emit_provisional_choch` (constructor default `False`; wired **`True`** in
`load_dashboard_data`). The mirror of the provisional BOS for the *reversal*: at
the right edge a counter-trend move can *close*-break the standing **structural**
CHoCH reference (`validated_choch_<side>` with `_structural=True`, e.g. a BOS
leg-origin) for `persistence_candles` consecutive closes — the same sustained
break the confirmed CHoCH demands — while its confirming swing-low/high pivot has
not formed yet (the swing-lookback lag). The state machine emits no CHoCH, so a
genuine forming reversal is invisible until the pivot confirms ~`swing_lookback`
candles later — the SOL M15 case (price sustained a close-break below the 80.72
leg-origin reference but the fundo was too fresh to be a confirmed pivot, so the
bearish CHoCH did not render, only a `LIQUIDITY_SWEEP`). When set, at the end of
`detect` a single `MarketStructure` with `provisional=True`,
`event=CHANGE_OF_CHARACTER`, `reference_structural=True` is emitted at the first
candle that started the sustained close-break (`reference_price_level` = the
structural reference, `reference_timestamp` = its pivot, `price_level` = the live
leg extreme), computed from authoritative final state — never re-derived outside
the detector. Gates: **only a structural reference** qualifies (mirror of the BOS
`floor_from_bos` gate — a weak re-anchor/fallback level would repaint as chop;
since 2026-07-11 `emit_provisional_choch_weak` relaxes this, at the weak-ref
barrier persistence — see "Provisional CHoCH against weak references" below),
and a poke that closes beyond for *fewer* than `persistence_candles` and reclaims
is (correctly) just a sweep and emits nothing. A live-edge reversal **supersedes**
a same-tail provisional BOS (`prov_event = None` — the two references are on
opposite sides of price, so a double would draw a contradictory `BOS?`/`CHoCH?`
pair). Purely additive (appended, off = byte-for-byte identical) and **skipped
by** `_reanchor_bos_close_break` / `_drop_pre_break_reference_bos` (non-BOS) and
by the frontend line-termination logic (a provisional mark never truncates a
confirmed line — `!other.provisional` in `structureLineEndTime`). The frontend
renders it dimmed + `SparseDotted` with a `?` suffix (`CHoCH? ▼`), like a
provisional BOS; it is superseded by the confirmed CHoCH once the pivot forms, or
vanishes if price reclaims the level (an intentional live-edge repaint). Measured
(walk-forward, BTC/ETH/SOL/AAVE × 15m/30m/1h/4h, 350 steps): **~50% of resolved
provisional marks confirm** (a lower bound — some "repaints" are ref re-anchors
the exact-match missed, or confirmations just past the replay window), ~8-candle
median lead. Reversals at the live edge are inherently more sweep-prone than
continuations (hence lower than the BOS's 85%); the dimmed style is what
communicates that. Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_30_07_06.json` (500-candle
self-contained window; off → no provisional, on → one bearish `CHoCH?` @ 80.72).
Not mirrored into `SwingStructureDetector` (not drawn).

**Fast-fizzle CHoCH invalidation marker** (`InternalStructureDetector`, as of
2026-07-07): `choch_fizzle_reclaim_candles` (constructor default `None` = off;
wired **`30`** in `load_dashboard_data` via `_CHOCH_FIZZLE_RECLAIM_CANDLES`). The
normal CHOCH_FAILED only invalidates a CHoCH once price *closes* back through the
far **leg origin** (the swing the reversal launched from). A CHoCH whose reversal
fizzled — price reclaims the very level the CHoCH *broke* and then ranges above
it — can therefore hang unfailed for a long time while its line runs to the chart
edge, because the closes never clear the distant origin (the SOL M15 case: a
bearish CHoCH at 80.72 whose confirming continuation BOS was wick-only, so
dropped from the chart, leaving the line looking unbroken; price reclaimed 80.72
in 14 candles yet the closes never cleared the 82.3 origin, so it stood for over
a day). When set, at the end of `detect` the **standing** CHoCH (the most recent
trend-defining CHANGE_OF_CHARACTER whose line is still open, tracked as
`standing_choch_ref`/`_index`/`_dir`, set at every CHoCH emission and cleared at
every CHOCH_FAILED) gets an additive CHOCH_FAILED **marker** at the first candle
that starts a sustained (`persistence_candles`) close-reclaim of its own broken
level, **if that reclaim starts within `choch_fizzle_reclaim_candles` of the
CHoCH**. A reclaim *after* the window is genuine follow-through (the reversal
held) and left alone — so the number separating a fizzle from a held reversal is
a **wide plateau** (the NEAR M5 genuine reversal held its level 133 candles
before reclaiming; the SOL M15 fizzle 14 — any K in `[~20, ~100]` splits them).
The marker is **purely additive** and does **not** flip the state-machine trend:
the frontend's `failedChochTime` pairs it (same direction, no intervening
same-direction CHoCH) to terminate the stale line at the reclaim, and it renders
as a normal solid failure mark. It is flagged `provisional=True` **only** so the
replay consumers (`LiquidityHuntEngine`, `NarrativeEngine`) skip it — the
detector trend never flipped, so the hunt/narrative reading must not either (the
hunt stays inócuo). Two rejected alternatives, both ruled out by measurement: a
*closer origin decided at emission* (from leg geometry) cannot separate the cases
— NEAR's leg is 2.23 ATR tall, SOL's cut point 2.20 ATR, they collide — and a
*real trend-flip CHOCH_FAILED* cascades the whole downstream CHoCH sequence
(+206/-220 events across BTC/ETH/SOL/AAVE/NEAR × 5m..1d), the additive-over-
state-machine failure mode. The additive marker is surgical: **+8/-0 across the
same 30 combos** (one marker per chart whose standing CHoCH fizzled, zero
removals, zero CHoCH-count changes). Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_24_07_07.json` (1243-candle
self-contained window; off → no CHOCH_FAILED, on → one bearish marker @ 80.72,
07-06 15:15; the reclaim lands 9 candles after the CHoCH, so a window of 8 leaves
it unmarked). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

*Resumed-fizzle cancel (composition level, as of 2026-07-11)*:
`_drop_resumed_fizzle_markers` in `dashboard_data` (after the two BOS passes)
drops a fizzle marker followed by a **chart-surviving** same-direction BOS: the
reclaim was a deep pullback the reversal recovered from, not a fizzle, and the
marker would falsely invalidate a standing CHoCH (the ETHUSDT H1 case: the
06-29 bullish CHoCH's 1583 reference was reclaimed for a day — the marker fired
— then the reversal printed a bullish BOS staircase to 1833, and on the chart
the false ✕ let the 06-19 bearish CHoCH line run to the edge via the
failed-CHoCH transparency rule). The cancel lives at composition, not in the
detector, because only composition knows which BOS survive
`_reanchor_bos_close_break` — the SOL M15 motivating fizzle also has a
same-direction BOS after its reclaim at detector level, but it is wick-only and
dropped from the chart, so the line there is genuinely stale and its marker
stands. Measured (BTC/ETH/SOL/AAVE/NEAR × 15m..1d): drops exactly 3 markers,
all the false pattern (ETH 15m/30m/1h — the same June-bottom reversal); the
genuine ones (BTC 1d, SOL 15m) are untouched. Real-data regression fixture:
`tests/liquidity/detectors/data/ethusdt_1h_2026_05_10_07_11.json` (1500-candle
production H1 slice; the raw detector emits the fizzle, `_run_internal_structure`
drops it). The frontend companion: a fizzle (provisional `choch_failed`) is
excluded from `isFailedChoch` (the line-termination *transparency* rule) — the
state-machine trend never flipped back, so a fizzle-marked CHoCH still cuts the
prior opposite CHoCH/BOS lines; only its *own* line stops at the reclaim
(`failedChochTime` keeps counting fizzles for that, via `includeFizzle`).

**Failed-CHoCH whipsaw fixes** (`InternalStructureDetector`, as of 2026-07-11,
two companion flags): the BTC H1 18–25/06 crash printed a single bearish BOS
(62232) and then only sweeps because two weak bullish CHoCHs flipped the trend
mid-crash — with the trend flipped, every new low was counter-trend (sweep,
never BOS), the `CHOCH_FAILED` prints late (at the next confirmed pivot) and
never retro-reclassifies, and the second flip (06-25 04:00, a cold-start
fallback CHoCH at the 61256 trailing LH firing on a 4-candle bounce one day
after the previous failure) happened because a failed-CHoCH flip arms no origin
(one-shot), so the unconfirmed-window fallback suppression lapses at the
failure. (1) `choch_failed_fallback_suppress_candles` (constructor default
`None` = off; wired **`20`** flat via
`_CHOCH_FAILED_FALLBACK_SUPPRESS_CANDLES`): the cold-start `active_<side>`
fallback stays suppressed for this many candles after a *same-direction*
`CHOCH_FAILED`; structural/validated references are untouched, so a genuine
reversal (which promotes a leg origin via BOS) still fires — the motivating
whipsaw fired 15 candles after the failure. (2) `stage_choch_failed_window_bos`
(constructor default `False`; wired **`True`** via
`_STAGE_CHOCH_FAILED_WINDOW_BOS`): while a CHoCH is provisional, counter-trend
staircase breaks (new extremes beyond the previous recorded one, seeded from
the pre-CHoCH reported-floor stash) are recorded (`_EatenBreak`); at the
`CHOCH_FAILED` each is staged as an additive BOS of the resumed trend (merged/
deduped like the impulse staging, close-break re-anchored at composition, so
wick-only ones drop) and the eaten extremes fold into the restored staircase
floors (gate: most extreme pivot; reported floor: close-confirmed only) so the
next real continuation references the true prior formed extreme. Recorded
breaks are discarded if the CHoCH confirms; a fresh CHoCH clears the window.
Measured (BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`): 17/30 combos
identical; the rest are staircase splits (one gap-jumping BOS becomes two
steps, e.g. NEAR 1h `3.08 ref=2.58` → `2.76 ref=2.58` + `3.08 ref=2.76`); zero
new `CHOCH_FAILED` anywhere. BTC 1h (motivating): the crash gains the
62232 → 61870 → 59060 → 58030 staircase, the 06-25 whipsaw CHoCH becomes a
sweep, the phantom 06-30 `CHOCH_FAILED` disappears; cost: the recovery CHoCH
fires 07-02 09:00 (ref 60758) instead of 07-01 13:00 (ref 59444 fallback),
~20h later against a better reference. Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_1h_2026_05_18_07_04.json` (1136-candle
production H1 slice). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

**Displacement release for spent cycles** (`InternalStructureDetector`, as of
2026-07-11): `stale_reanchor_displacement_atr` /
`stale_reanchor_displacement_candles` (constructor defaults `None` = off, must
be set together; wired **16.0 / 15** in `load_dashboard_data` via
`_STALE_REANCHOR_DISPLACEMENT_ATR`/`_CANDLES`). The staleness re-anchor's
candle timer is blind to how far a leg *stretched*: after a violent move the
reversal reference stays pinned at the pre-move leg origin for the full
`stale_reanchor_candles` window (H4 = 60 candles = 10 days), so the strongest
bounce of the whole cycle is consumed as a `LIQUIDITY_SWEEP` against a level
many ATRs overhead — the ETHUSDT H4 case: the 06-05 crash BOS (1503, breaking
1712) promoted its pre-crash leg origin **2046** (structural) as the bullish
CHoCH reference; the +23% bounce to 1848 nine days later printed as a sweep,
and the chart sat on the mid-crash BOS for **35+ days**. When the gap between
the effective reversal reference (`validated or origin or active`) and the
leg's running extreme (`bear_leg_low`/`bull_leg_high`) reaches N × the series'
mean true-range% (same volatility normalization as the release gap — per-TF
adaptivity for free), the cycle is *spent*: the staleness threshold shrinks to
the displacement candle count and the re-anchor **window starts at the last
advance** (the post-move range) instead of a fixed trailing window, so the
reference lands on the new range's first pullback extreme (ETH: 1722, the
06-07 top). Everything else is the existing staleness machinery
(`reanchor_opposite` with all its guards). Measured (BTC/ETH/SOL/AAVE/NEAR ×
15m/30m/1h/4h/1d, `limit=1200`): **N=16 changes exactly 6/25 combos, all the
motivating pattern** — ETH 4h resolves into CHoCH↑ 06-15 (ref 1722, weak) →
CHoCH↓ 06-23 → BOS↓ 06-24 (1510) → CHoCH↑ 07-02 (ref 1692, structural), trend
ends bullish; BTC 4h gains the honest 06-14 CHoCH↑ + 06-27 `CHOCH_FAILED` pair
around the June crash; AAVE 4h gains the entirely-missing Feb→Mar −30% bearish
cycle (CHoCH↓ 03-07 + BOS staircase 104→92); NEAR 1h / SOL 4h flip the June
bottom ~6–17 days earlier with a confirming BOS staircase. N=8 fires on
routine legs (25/25 combos — a leg's ref-to-extreme gap *is* its height); N=14
starts reshaping BTC 30m/1d; N=18 loses AAVE 4h / NEAR 1h; N=20 loses the ETH
4h target itself (~19 ATR). M is a wide plateau (10/15/20/25 byte-identical on
the whole matrix). A companion **weak-ref sweep ratchet** (a sweep beyond a
weak validated ref moves it to the swept extreme) was prototyped alongside and
**rejected by measurement**: on a grinding decline each lower sweep ratcheted
the reference down just ahead of the confirming closes, so the reversal CHoCH
could never fire (it erased the genuine ETH H4 March 2385→1936 bearish cycle);
the candidate pipeline's sweep re-anchor requires trend *resumption* before a
swept level goes live, and skipping that gate is what broke. Real-data
regression fixture:
`tests/liquidity/detectors/data/ethusdt_4h_2025_11_21_2026_07_11.json`
(1395-candle production H4 slice). Off = byte-for-byte identical. Not mirrored
into `SwingStructureDetector` (not drawn).

**Provisional CHoCH against weak references** (`InternalStructureDetector`, as
of 2026-07-11): `emit_provisional_choch_weak` (constructor default `False`,
requires `emit_provisional_choch`; wired **`True`** in `load_dashboard_data`).
The provisional live-edge CHoCH originally required a *structural* reference —
but after any re-anchor the standing reference is weak, so exactly in the
released/reset cycles the displacement release creates, the forming reversal
was invisible (the ETH H4 case: price closed above the weak reference with
nothing on screen). When set, a weak reference also qualifies, sustaining the
weak-ref barrier persistence (`choch_weak_ref_persistence_candles`) where
wired instead of the base; the emitted mark carries
`reference_structural=False`, so the frontend renders it dimmed with a `?*`
suffix (forming *and* weak — `?` leads, since a full repaint is the stronger
caveat). On coarse timeframes it is near-inert (the pivot lag ~5 is shorter
than the base persistence 12, so the confirmed CHoCH arrives with the
provisional); the lead materializes intraday where the weak barrier (4) is
shorter than the pivot lag. Measured (walk-forward over the last 400 candles,
BTC/ETH/SOL/AAVE/NEAR × 15m/30m/1h): 10 weak provisional marks, **10/10
followed by a confirmed same-direction CHoCH** (among them the BTC 1h 07-02
recovery CHoCH from the whipsaw-fix cost, now visible at the live edge).
Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_26_07_11.json`
(1474-candle window ending at the live edge; off → no provisional CHoCH, on →
one bullish `CHoCH?*` @ 78.34). Off = byte-for-byte identical.

**Weak-ref CHoCH failure at the broken level** (`InternalStructureDetector`, as
of 2026-07-12): `choch_weak_ref_fail_at_broken_level` (constructor default
`False`; wired **`True`** in `load_dashboard_data` via
`_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL`). A CHoCH fired against a **weak**
reference (a synthetic re-anchor level or the cold-start fallback) arms its own
*broken level* as an additional invalidation reference alongside the far leg
origin: the synthetic level's break was the reversal's only evidence, so a
sustained close (base persistence) back through it emits a real `CHOCH_FAILED`
(trend flips back) at the *tighter* of the two levels — structural CHoCHs keep
the origin-only failure. Motivating case (BTCUSDT D1): the 2026-04-30 bullish
CHoCH against the weak 75998.9 re-anchor collapsed within days, but the 59800
origin was never sustained-broken — the trend sat bullish through the entire
82.8k→57.7k crash (−30%), every new low printed as a counter-trend sweep, and
the chart showed no bearish BOS at the bottom, unlike ETH D1 (whose rally never
fired a CHoCH and whose June break of 1736 printed the continuation BOS at the
fundão). On: `CHOCH_FAILED` 05-26 at 75998.9 + BOS↓ 06-25 ref 59800 + trend
bearish — the ETH analogue. **Weak-level failures re-seed the resumed
staircase at the failure level** (gate + reported floor, like a CHoCH seeds its
cycle; `*_floor_from_bos = False` so a provisional BOS never doubles the
failure line) instead of restoring the pre-CHoCH stash: the weak reference
existed precisely because the old cycle was spent, and the plain restore was
measured to pin the resumed trend's whole next leg on an ancient floor (AAVE 4h
lost its entire Feb→Mar −30% staircase, NEAR 1h its June one). The reported
floor folds the deepest eaten-window extreme when `stage_choch_failed_window_bos`
recorded breaks (else the next continuation re-reports the failure level and,
with no matching opposite pivot, its line origin never resolves — a stretched
duplicate of the ✕ line). Origin-triggered failures restore exactly as before.
Measured (BTC/ETH/SOL/AAVE/NEAR × 15m..1d, `limit=1200`): 12/25 identical;
every change is the weak-CHoCH lifecycle — whipsaw CHoCH pairs become honest
`CHoCH + CHOCH_FAILED` sequences with the resumed trend's staircase intact
(BTC 4h gains the June-crash BOS staircase and a richer March; NEAR 1h keeps
its June staircase with an honest 06-14/06-19 failure pair; SOL M15's live-edge
fizzle becomes a real failure; ETH 4h June reads `✕ @ 1721.57` + BOS↓ 1510 with
the 07-02 recovery CHoCH untouched). Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_1d_2022_06_03_2026_07_11.json`
(1500-candle production D1 slice; off → fizzle marker only + trend bullish +
no bearish BOS after January, on → real failure + fundão BOS + trend bearish).
Off = byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**Staircase rollback on a discarded phantom advance**
(`InternalStructureDetector`, as of 2026-07-12): `rollback_staircase_on_discard`
(constructor default `False`; wired **`True`** in `load_dashboard_data`). A high
pivot beyond the BOS staircase gate (`last_bull_bos_high`/`last_bear_bos_low`)
advances the state machine (creating a pending BOS) and ratchets the gate up to
that pivot — but the pivot can be a long upper-wick spike to a new high that
*closed* far lower (a failed push). If the pending BOS is then **discarded
without emitting** — its confirming pullback comes in below the prior BOS's
confirming pullback (`last_bullish_bos_origin`) yet stays above the leg origin,
so it is neither an emitted BOS nor a reversal (the CHoCH path) — the gate stays
pinned at that wick top. A later *genuine* continuation to a slightly lower high
can then never advance (it sits below the pinned gate) and prints only
`LOWER_HIGH`/`HIGHER_LOW` labels, so the chart hangs on a stale BOS while price
makes a full new leg. Motivating case (measured, ETHUSDT M30 `limit=1200`): a
07-06 candle wicked to 1833.00 but closed 1812.43, pinning the gate at 1833; its
pending BOS was discarded when the 1756.62 pullback came in below the prior
1772.84 confirming low; the 07-11 rally topping at **1829.52 < 1833** printed no
BOS and the last one hung from 07-04. When set, discarding such a pending BOS
restores the gate to its pre-advance value (`_PendingBOS.prev_staircase`, snapshotted
before the advance ratcheted it), so the 07-11 continuation advances and emits a
BOS against the 1812.85 swing high it broke. It fires **only** on the discard
path (never on an emitted BOS or a genuine continuation) and touches **nothing but
the gate value** — not the confirming-pullback gate, `candidate_choch_<side>`, or
any CHoCH state — so it cannot cascade the reversal sequence (the
additive-over-state-machine discipline). Measured (BTC/ETH/SOL × 15m/30m/1h/4h/1d,
`limit=1200`): **5/15 combos change**, each `+1` BOS (ETH M30 the target, ETH 15m,
ETH 4h, a BTC 4h live-edge provisional) or a single reference correction (SOL 30m
`+1/−1`, a deeper/earlier reference); the other 10 identical, zero structure
removed. The relaxed-confirm-gate alternative (`bos_confirm_ignore_origin_staircase`,
emitting the failed 1833 push itself) was **rejected by measurement**: it seeds
`candidate_choch_<side>` and cascaded the CHoCH sequence (ETH 15m +4/−7, BTC 1d
+1/−3). Real-data regression fixture:
`tests/liquidity/detectors/data/ethusdt_30m_2026_06_15_07_12.json` (1309-candle
production M30 slice from the structural anchor; off → last bullish BOS 07-04, on →
adds the 07-10 08:00 BOS ref 1812.85, re-timed to 07-11 15:00 by
`_reanchor_bos_close_break`). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

**Displacement-success CHoCH-origin retirement**
(`InternalStructureDetector`, as of 2026-07-13): `choch_success_displacement_atr`
(constructor default `None` = off; wired **`4.5`** in `load_dashboard_data` via
`_CHOCH_SUCCESS_DISPLACEMENT_ATR`). A CHoCH stays *provisional* — its origin
armed, a sustained reclaim of it a `CHOCH_FAILED` — until a confirming BOS
retires the origin. But an **impulsive reversal leg emits no BOS**: no pullback
pivot forms in the impulse, so the state machine confirms none (worse for the
*first* leg after a CHoCH, which has no prior staircase floor for the
impulse-BOS staging to fill either). The origin then lingers indefinitely and
the eventual mean-reversion fires a **false `CHOCH_FAILED` on a move that
plainly succeeded**. Motivating case (measured, NEARUSDT H1 `limit=1200`): two
bullish CHoCHs — 2026-06-08 (origin 2.045) and 2026-06-14 (origin 2.173) —
rallied to 2.264 (~5.0 ATR above origin) and 2.562 (~7.6 ATR), emitted zero
bullish BOS the whole June window, and both got marked `CHOCH_FAILED` on the
pullback (the two grey `CHoCH ✕ ▲` at 2.05 and 2.18 the user flagged). When
set, once the reversal leg's extreme (`bull_leg_high`/`bear_leg_low`) has
displaced `>= N x ATR%` (`mean_tr_pct`) beyond the fail level, the origin is
**retired right at the failure check** (`bull_choch_origin`/`bear_choch_origin`
+ the `*_choch_fail_ref` stash set to `None`, `*_fail_pivot` nulled so the
`if` falls through) exactly as a confirming BOS would — the reversal is
established, and any *later* reversal is a fresh opposite CHoCH, not a failure
of this one. Mirrored on both sides; purely a retirement guard (no new events,
no trend mutation of its own). Threshold **4.5**: the shallower NEAR case is
~5.0 ATR, so 4.5 leaves ~0.5 ATR of margin against live drift while staying
well clear of a shallow pop-then-fail (a genuine failed reversal rarely runs
4.5 ATR). Measured (BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`,
`confluence_filter=False`, production wiring): non-provisional `CHOCH_FAILED`
**30 → 23**, `CHANGE_OF_CHARACTER` **171 → 182** (genuine reversals surfaced
where a false failure had masked them), and — the key safety property — the
standing `final_trend` is **unchanged on every one of the 30 combos** at *every*
threshold swept (4.0/4.5/5.0/6.0): the retirement only rewrites intermediate
narration, never the trend state. Real-data regression fixture:
`tests/liquidity/detectors/data/nearusdt_1h_2026_05_11_07_13.json` (1500-candle
production H1 slice; off → both bullish `CHOCH_FAILED` at 2.045 and 2.173, on →
neither, the 06-08 bullish CHoCH stands). Not mirrored into
`SwingStructureDetector` (impulsive-leg BOS gaps are an internal-scope concern;
the major detector's freeze semantics differ).

**`CHOCH_FAILED` scan bounded to after the CHoCH formed**
(`InternalStructureDetector`, as of 2026-07-13; **not flag-gated** — a
correctness fix, not a tunable). A `CHOCH_FAILED` fires when price *reclaims*
the fail level (the origin, or a weak CHoCH's own broken level) before a
confirming BOS, and its timestamp comes from a **backward scan** for the candle
that reclaimed it (`find_sustained_break_index`), gated by `confirms_break`.
Both scanned from `prev_<kind>_pivot_index + 1` — the previous same-kind pivot,
which **can precede the CHoCH itself**. A reversal can only be invalidated by a
reclaim that comes *after* it forms, but the unbounded scan would grab the
**pre-CHoCH leg** — often the very move the CHoCH reversed — as the "reclaim".
Motivating case (measured, NEARUSDT H1 `limit=1200`, surfaced by the
displacement-retirement change above keeping the bullish trend alive into the
06-15 top): a weak bearish CHoCH formed 06-16 14:00 (fail level 2.339, its own
broken level under `choch_weak_ref_fail_at_broken_level`), but the failure scan
ran back to the 06-15 **rally up** through 2.339 (price was at ~2.50 heading to
the 2.56 peak) and stamped a phantom bearish `CHoCH ✕ ▼` at **06-15 16:00 — a
failure timestamped *before* the 06-16 14:00 CHoCH it invalidates**, drawn
mid-rally. Fix: track `bull_choch_arm_index` / `bear_choch_arm_index` (the
pivot-loop index where each origin is armed, i.e. the confirming pivot of the
CHoCH) and clamp the failure scan start to `max(prev_pivot + 1, arm_index + 1)`
for **both** the `confirms_break` guard and the `find_sustained_break_index`
attribution. The clamp only ever **narrows** the window, so it is strictly a
correctness improvement: a genuine post-CHoCH reclaim still satisfies
`confirms_break` (and now gets the correct, later timestamp); a failure vanishes
**only** when its *sole* sustained break lay before the CHoCH (entirely
spurious). Removing the NEAR phantom also cleared the downstream whipsaw it had
triggered (a bullish re-flip → re-CHoCH → second failure at 06-16 21:00): with
the phantom gone the 06-16 14:00 bearish CHoCH stands and leads a clean bearish
BOS staircase (06-17 19:00 / 06-19 03:00 / 06-22 22:00). Measured
(BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`, `confluence_filter=False`):
non-provisional `CHOCH_FAILED` **23 → 20**, **zero** temporally-inverted
failures remain (an orphan failure with no preceding same-direction CHoCH), and
the standing `final_trend` is **identical to pristine `HEAD`** (before *either*
2026-07-13 fix) on all 30 combos. Regression on the same NEAR fixture
(`test_near_1h_choch_failed_never_predates_its_choch`): every non-provisional
failure has a preceding same-direction CHoCH, no bearish failure survives the
06-15..06-16 rally/top window, and the 06-17 19:00 bearish BOS prints. Mirrored
on both directions; not applied to `SwingStructureDetector` (same rationale as
above).

**CHoCH origin = deepest leg extreme** (`InternalStructureDetector`, as of
2026-07-05): `choch_origin_leg_extreme` (constructor default `False`; wired
**`True`** in `load_dashboard_data`). A CHoCH's *origin* — the level whose
sustained break back through it (before a confirming BOS) invalidates the
unconfirmed reversal as a `CHOCH_FAILED` — is now the **deepest extreme of the
reversed leg** (`_extreme(active_<side>, pending_<side>)`), not the trailing
`active_<side>` alone. The trailing reference ratchets toward the new high/low
through the reversal leg's intermediate pivots, so by the time the CHoCH
confirms it can sit right next to the new extreme, arming an *instant* failure
on the first minor pullback and ping-ponging the trend into weak
CHoCH/`CHOCH_FAILED` pairs — and because a failed CHoCH never emits an opposite
CHoCH, the genuine reversal line never terminates and stretches across the
chart. Motivating case (measured, NEARUSDT M5 `limit=1200`): a bullish CHoCH
07-04 14:10 (a genuine +4% reversal to 2.039) whose true fundo was 1.967 but
whose `active_low` had ratcheted up to a 2.004 higher-low near the top — off,
it failed immediately at 2.004 and spawned a weak `CHoCH* ▲` and a second
failure (the line ran to the chart edge); on, the origin is 1.967, the CHoCH
holds through the shallow pullbacks and fails once honestly when price breaks
the true fundo (03:40, ref 1.967), so its line pairs with that failure and
terminates. Neither `active_<side>` nor `pending_<side>` alone is the fundo —
`active_low` ratchets up through higher-lows, `pending_low` can retain a
shallower early-leg low — so the *deeper* of the two is taken (mirror: the
higher of `active_high`/`pending_high` for a bearish origin). Measured
(BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`): **`CHOCH_FAILED` drops ~33%**
(63 → 42), converting whipsaw CHoCH/fail pairs into sweeps or holding CHoCHs
(BOS count neutral, +17 sweeps); the few timeframes that gain CHoCHs (BTC 15m)
are genuine chop where the added CHoCHs are `struct=True`. Real-data regression
fixture: `tests/liquidity/detectors/data/nearusdt_5m_2026_07_04_05.json`
(373-candle self-contained window; off → whipsaw with a `CHOCH_FAILED` @ 2.004,
on → one `CHOCH_FAILED` @ 1.967). Off = byte-for-byte identical. Not mirrored
into `SwingStructureDetector` (not drawn).

**CHoCH confirmation** (`InternalStructureDetector`): the CHoCH reference is
the **pullback (origin) of the most recent continuation-confirmed BOS**. A
BOS's pullback (the confirming LH for bearish, HL for bullish) starts as a
provisional `candidate_choch_<side>`; it is promoted to
`validated_choch_<side>` only when a subsequent move makes a **new leg
extreme** (below `bear_leg_low` for bearish, above `bull_leg_high` for
bullish) — a genuine continuation. A pullback-BOS formed during a retrace
that does not extend the leg cannot ratchet the reference down. Each
continuation pullback must also stay on the correct side of the previous
pullback (LH staircase / HL staircase) via a dedup gate. A sweep that pokes
beyond the current `candidate_choch_<side>` re-anchors that candidate to the
swept extreme (more-extreme only — the "sweep then expand" origin), but a
sweep never moves the *validated* reference directly; a sweep with no
continuation never promotes. A bullish CHoCH fires on a sustained break above
`validated_choch_high or choch_origin_high or active_high`; any break that
doesn't clear the reference, or doesn't hold, is a `LIQUIDITY_SWEEP`. The
`active_<side>` cold-start fallback ensures the detector can flip trend
during the bootstrap phase (before any validated/origin reference has been
built), preventing the trend from getting stuck if the initial direction was
wrong. The `choch_origin` one-shot blind-spot fallback prevents the trend
from getting stuck after a CHoCH whose reversal fails before a fresh
validated reference can be rebuilt.

**Failed CHoCH (`CHOCH_FAILED`, both detectors)**: a CHoCH is provisional
until a same-direction BOS confirms it. While unconfirmed it carries an origin
(`bull_choch_origin`/`bear_choch_origin` — the active low at a bullish CHoCH /
active high at a bearish CHoCH, the swing the CHoCH move launched from). A
sustained break back through that origin before a confirming BOS emits a
`CHOCH_FAILED` event (direction = the failed CHoCH's direction,
`reference_price_level` = the broken origin) and flips the trend back; it
supersedes the `choch_origin` recovery for the unconfirmed window at a tighter
level. The origin is retired on the confirming BOS or at the next trend flip,
and a failed-CHoCH flip arms no opposite origin (one-shot, no ping-pong).
Because a failed CHoCH means the original trend never ended, the resumed
trend's BOS staircase continues from its *genuine* last BOS extreme, not the
(often higher-low / lower-high) CHoCH origin — otherwise a non-extending BOS
could print past the previous same-direction BOS. The reversing trend's
staircase floor is stashed (`pre_choch_bear_bos_low`/`pre_choch_bull_bos_high`)
when the CHoCH fires and restored on failure (more extreme of it and the
origin); a confirming BOS discards the stash.

**CHoCH lines across a failed CHoCH (frontend)**: in
`MainChart.structureLineEndTime`, a provisional CHoCH that is later invalidated
(a same-direction `choch_failed` fires before another same-direction CHoCH
intervenes — paired via `isFailedChoch`) is transparent to line termination:
the prior BOS/CHoCH line it appeared to cut keeps running through it until a
*genuine* opposite-direction CHoCH (or the chart edge), matching the resumed
structure. A **fizzle marker** (a *provisional* `choch_failed`) does **not**
grant this transparency (as of 2026-07-11, `isFailedChoch` passes
`includeFizzle: false` to `failedChochTime`): the state-machine trend never
flipped back, so the fizzle-marked CHoCH still genuinely reversed structure and
keeps cutting the prior opposite lines — only its *own* line stops at the
reclaim (own-line termination keeps counting fizzles). Before this, a fizzle
let the prior opposite CHoCH line run to the chart edge (the ETH H1 stretched
bearish CHoCH).

**CHoCH confirmation** (`SwingStructureDetector`): uses the older
candidate/baseline model. A candidate LH/HL is promoted to validated when a
subsequent BOS beats `candidate_choch_<side>_baseline`. `SwingStructureDetector`
**always sets** origin (every CHoCH sets
`choch_origin_<opposite> = active_<side>`): with `persistence_candles=10`
ping-pong risk is negligible, while the higher lookback makes the blind-spot
window long enough that one-shot would re-introduce the stuck-trend bug.

**`MarketStructure.reference_timestamp`**: CHoCH events carry the timestamp of
the `validated_choch_<side>` pivot (the promoted LH/HL), allowing the frontend
to anchor CHoCH lines at their true origin rather than at the break candle.

**POI (Order Block) module** (rewritten 2026-07-10, made Pine-faithful
2026-07-11): `POIDetector` implements the MSB-OB logic (EmreKb's "Market
Structure Break & Order Block" TradingView indicator, minus the zigzag
drawing) as a **faithful batch port**: `barssince`-window pivots (local
extremes since the previous opposite *signal*, not leg extremes),
value-compared same-pivot guard, and running anchor scans with the
indicator's `[pivot_len]`-lagged window bound. Fidelity was verified against
the indicator on TradingView with real BTCUSDT 15m data — a user-reported
divergence (a missing 07-09 Bu-OB) traced to an earlier leg-extreme pivot
variant that flipped less often; the port reproduces the on-chart boxes
exactly (regression fixture
`tests/liquidity/detectors/data/btcusdt_15m_2026_06_25_07_11.json`, 44
zones). Flips require a fib-extension break (`fib_factor=0.33`); each MSB
marks the last opposite-direction candle of the impulse-origin leg as the
order block and the last same-direction candle of the broken-pivot leg as
the `BREAKER_BLOCK` (prior extreme swept) / `MITIGATION_BLOCK` (`kind`
field), full candle range, frozen. Lifecycle: ACTIVE → INVALIDATED on a
single close beyond the far boundary; touches never retire a zone. The
MITIGATED state, `RTOSweepEvent`, and `poi_sweep_events` were removed
everywhere (domain, `DashboardData`, API schema, `ManipulationCycleDetector`
input, `NarrativeEngine` timeline, frontend RTO markers). The React frontend
renders active zones via `POIBoxesPrimitive`, starting each box at the
anchor candle, labeled `OB`/`BB`/`MB` + direction arrow.

**Manipulation cycle detection**: `ManipulationCycleDetector` connects
existing observations into three-phase Wyckoff/SMC cycles (accumulation →
sweep → expansion). Works retrospectively (matching sweeps to prior
accumulation and subsequent expansion BOS) and prospectively (identifying
active accumulation zones where stops are building). Minimum accumulation
candles are timeframe-adaptive (`_TIMEFRAME_MIN_ACCUMULATION`: M5=15,
M15=10, M30=7, H1=7, H4=3). Prospective accumulations are clustered per
side within `proximity_pct` to avoid duplicate cycles for overlapping zones,
and zones already targeted by sweep-based cycles are excluded via proximity
matching. The React frontend renders cycles in a sidebar panel
(`ManipulationCyclesPanel`, max 5 cards) and as chart overlay boxes (max 3,
togglable via CHART ON/OFF button).

**Behavior divergence detection**: `BehaviorDivergenceAnalyzer` cross-references
volume delta with zone proximity and structure events to detect when institutional
flow opposes visible price direction. Four types: distribution (price rising +
negative VD near buy-side zone), accumulation (price falling + positive VD near
sell-side zone), exhaustion (VD declining after BOS), absorption (high volume +
small price movement near zone). Window size is timeframe-adaptive. Wired into
`DashboardData` and the API schema; frontend TypeScript types are defined but
no sidebar panel or chart overlay yet.

**React frontend panes**: the main chart has three synced panes — candlestick
(main), volume delta histogram, and RSI(14) with regular divergence detection
(bullish LL+HL, bearish HH+LH). All three share synchronized time scales and
crosshairs.

**Narrative engine**: `NarrativeEngine` (in `app/narrative.py`) synthesizes all
detection layer outputs into a `MarketNarrative` — a chronological timeline of
institutional events, pattern anomalies, phase-dependent summary, and
confluence count. Wired into `DashboardData`, API schema, and frontend
TypeScript types. Five anomaly detectors: expansion+exhaustion, accumulation+
distribution, concentrated liquidity, unconfirmed CHoCH, BOS without VD.
Summary tone adapts per manipulation phase (neutral, accumulation, manipulation,
expansion, failed) with retail bias and HTF alignment context. Frontend
`NarrativePanel` sidebar component is not yet implemented.

**Leverage liquidation estimator** (psychology-evolution roadmap idea 3,
completed): the data layer gained a second provider port,
`FuturesDataProvider` (`BinanceFuturesDataProvider` via ccxt `binanceusdm`),
sourcing open interest / funding / long-short ratio.
`LeverageLiquidationEstimator` infers the over-leveraged side and projects
`LeverageLiquidationMap` liquidation bands at tiers 10x/25x/50x/100x around
liquidity-zone entries, each time-bounded from entry formation to the
liquidation-hit candle (or still live). Wired into
`DashboardData.liquidation_map` (degrades to `None` for spot-only symbols) +
API schema. Frontend renders the bands via `LiquidationBandsPrimitive`
(time-bounded boxes, warm color per leverage tier: 10x amber → 100x crimson)
behind a `⊟ Liq` toolbar toggle, decluttered to a near-price subset (live pools
+ recent hits, ±8%, ≤12) while the full set stays in the API for backtesting.

**OI regime analysis** (as of 2026-07-02): `OIRegimeAnalyzer` (psychology)
joins open interest with price to read *who is behind the move*: the
price × OI matrix as a rolling current regime (long/short buildup = new money,
short covering / long liquidation = unwinding) plus per-event qualification of
the internal BOS/CHoCH/SWEEP stream (`NEW_MONEY`/`COVERING`/`FLUSH`/`FLAT`),
measured through one candle *after* the event so a sweep's liquidation flush
(which lands on the next OI sample) is caught.
`BinanceFuturesDataProvider.get_open_interest_history` paginates past the
500-row cap (clamped to Binance's ~30-day retention +1h margin — a `startTime`
at exactly −30d gets error -1130), so OI covers the visible window on intraday
timeframes; on D1 coverage is ~30 points and most structure events fall
outside it (regime still shown, events unqualified — intended degradation).
Wired into `DashboardData.oi_analysis` + API. Frontend: "OI Regime" KPI card
(with HTF-trend confluence badge) and `⊕`/`⊖`/`⚡` suffixes on structure
labels. Historical continuation-frequency stats were deliberately deferred.

**Liquidity hunt state** (as of 2026-07-06): `LiquidityHuntEngine` (app-level
synthesizer) answers "who is the resting liquidity of the current move, and
has it been captured yet" — the counter-trend trap question (an LTF CHoCH
against the HTF trend makes its entrants the fuel; e.g. SOL H1 bearish CHoCH
inside an H4 uptrend → shorts get swept before the correction can proceed).
Combines the internal-structure trend vs HTF direction, nearby equal-level
zones and liquidation bands (intact vs captured-since-the-flip), OI flush
events, and the OI regime into a `LiquidityHuntState` with a strict `CAPTURED`
gate (all mapped pools consumed **and** OI no longer unwinding). Wired into
`DashboardData.liquidity_hunt` + API schema. Frontend: the KPI row's Price
card was replaced by the **Liquidity Hunt** conclusion card (rightmost).
Purely observational language throughout (who is the liquidity / when it was
captured), per the research-platform constraint.

**Multi-timeframe overview / Structure Ladder** (as of 2026-07-11):
`app/overview.py` + `GET /api/overview` + the `MultiTimeframePanel` sidebar
panel — a per-timeframe state ladder (M5→W1: internal trend, last structural
event, forming provisional marks, hunt phase), sharing the exact production
detection pipeline via `dashboard_data._run_internal_structure` so the ladder
always matches what the chart renders per timeframe. Per-timeframe API
caching with proportional TTLs. This is **phase 1** of the user's multi-TF
score plan (see memory: the composite confluence score over OB/Sweep/EQL/
VOL/RSI-div/Hunt comes next; decision/execution logic stays out of this
repo). Alongside it, **narrative/anomaly synthesis was turned off by
default** at the API (`narrative=false` query param; `compute_narrative`
flag in `load_dashboard_data`) — the `NarrativePanel` exists and returns
untouched whenever the param is re-enabled.

**BOS line-origin anchor robustness** (as of 2026-07-13): a BOS line is drawn
from the origin of the level it broke (`reference_timestamp`) to where it broke
it. Both the composition re-anchor (`_reanchor_bos_close_break`) and the
detector's provisional-BOS path resolved that origin by scanning back for a
candle whose *own-side* extreme (`low` for a bearish floor, `high` for bullish)
*exactly* equalled the level. Two failure modes surfaced (both **cosmetic** —
`reference_timestamp` never feeds detector state or trend; **not** regressions,
they reproduce on pristine `main`):

1. **`reference_timestamp=None`** (ETH H4 first bearish BOS at 1721.57): the
   *first* BOS of a leg reports the CHoCH-seeded floor, whose origin is the
   reversal's *opposite*-polarity extreme (a bearish leg's floor is the reversal
   *top*, a **high**). The own-side (low) scan never matched → `None` → the
   frontend drew the line from the chart edge.
2. **Far-back spurious anchor** (SOL H1 provisional bearish BOS at 75.60): the
   provisional scan truncated the window at `last_advance_index`, which excluded
   the real origin (a floor whose pivot low formed *after* the state advance),
   so an older candle that merely touched the same price (11 days back) won.

Fix: a shared `_common.resolve_break_origin_timestamp(candles, break_index,
level, *, bearish)` — scans the pre-break window most-recent-first: own-side
exact → opposite-side exact (the first-BOS case) → range-straddle (excluding the
break candle) → `None`. `_reanchor_bos_close_break` calls it **only when its own
own-side scan left `None`** (strictly additive: existing good anchors untouched);
the provisional path calls it over the full pre-break window (dropping the
`last_advance_index` truncation). Measured across BTC/ETH/SOL/AAVE/NEAR ×
M15/H1/H4/D1 (20 combos, live 1500-candle slices): **11 `reference_timestamp`
corrections** (10 `None`→resolved + 1 far-back→recent), **0 BOS gained/lost**,
event set otherwise byte-identical to HEAD → trend replay unchanged.

Filling the `None`s *exposed a latent bug* in `_drop_pre_break_reference_bos`
(one AAVE H4 first bearish BOS at 122.72 was silently dropped): that pass reset
its "reference must form after the prior same-direction BOS" constraint only on
a `CHANGE_OF_CHARACTER`, not on a (non-provisional) `CHOCH_FAILED` — but a failed
CHoCH also flips the trend (to the *opposite* of its direction) and starts a new
leg, whose first BOS references the CHoCH-seeded level (formed before the flip).
ETH's analogous first bearish BOS survived only by luck (its 06-07 level origin
happened to post-date the prior bearish BOS close; AAVE's 122.72 origin fell a
few candles before it). Added the mirror `CHOCH_FAILED` reset (provisional fizzle
markers excluded — they don't move the trend), restoring the dropped BOS (total
back to HEAD's 311). All three fixes are cosmetic/composition-level with pure-
function cores; covered by synthetic unit tests
(`resolve_break_origin_timestamp` tiers, the `CHOCH_FAILED` reset, and a
`_reanchor` opposite-polarity fill) rather than heavy real-data fixtures.

**Consolidation (lateral range) detection — phase 1, observation only** (as of
2026-07-14): inside a broad range the detector goes *correctly* silent — both
references end up pinned outside the box (the BOS staircase at a pre-range
extreme above, the CHoCH reference at the leg origin below), so nothing inside
can trigger, and none of the anti-lock tools apply (the staleness re-anchor's
CHoCH still needs 12 sustained closes a range never gives; the displacement
release needs a stretched leg, and a range is compression; the staircase only
relaxes at a CHoCH). Motivating locks (fixtures
`btcusdt/ethusdt_1h_2026_05_13_07_14.json`, captured live before resolution):
BTC H1 10 days after the 07-04 BOS @63450 — the 07-07 rally to 64691.9
advanced state but its pending BOS died unconfirmed (deep range pullback), the
64692 wick became the staircase bar the later 64288/64680 rallies missed, and
the 61297/61520 drops printed only sweeps against a leg-origin CHoCH ref far
below; ETH H1 mirrored it after the 07-06 BOS @1833 (a −6.6% drop to 1712 = 3
sweeps; the 1829.52 recovery missed the 1833 staircase by 3.5 dollars).

Phase 1 makes the silence explicit instead of touching the state machine:
a `ConsolidationRange` domain entity + `detect_consolidation_ranges`
(`liquidity/detectors/consolidation.py`), a **pure post-pass run at the
composition level** (`dashboard_data._detect_consolidations`, inside
`_run_internal_structure`) over the **surviving** event stream. Segment
boundaries are the post-composition-pass non-provisional
BOS/CHoCH/`CHOCH_FAILED` (a `CHOCH_FAILED` contributes the *opposite* of its
direction — the trend it reverts to). **In-detector integration was built
first and reverted by measurement**: using the detector's internal advances
(collected in `emit`) split BTC's July box in two at a 07-10 advance whose BOS
`_reanchor_bos_close_break` later dropped as wick-only — a visible range split
at an invisible point. With chart-event boundaries it is one box, 07-04 18:00
→ live.

Definition: per quiet segment, the longest trailing window whose high-low box
stays within `_CONSOLIDATION_MAX_HEIGHT_ATR` (8.0) × the detection series'
mean true-range% (the displacement release's normalization), holding at least
`_CONSOLIDATION_MIN_CANDLES` (60) candles, with alternating edge-zone touches
(compressed top/bottom sequence ≥ 3, outer 25% zones — filters one-way drifts
inside the cap). Once confirmed the box absorbs candles while total height
stays within the cap; an unabsorbable poke either **resolves** the range
(close beyond the boundary holding `_CONSOLIDATION_RESOLVE_PERSISTENCE` = 4
further closes, `is_sustained_break`) or stays outside the frozen box (a
boundary sweep — K=8 chosen over 10 because K=10 absorbed ETH's 07-12 1848
spike into the box top, hiding the sweep). A structure advance ending the
segment resolves an open range in the advance's direction; open at series end
= `ACTIVE`. N=60 confirms both motivating locks; N=40 added sub-2.5-day boxes
reading as routine pauses; N=80 only delayed confirmation (~3.3 days on H1).
Live matrix (BTC/ETH/SOL/AAVE/NEAR × 15m/1h/4h/1d): 2–8 ranges per
1200-candle combo, BTC H1/H4 independently finding the same July box
[61297–64692], ETH June bottom basing (06-25→06-29 [1511–1610]) resolving
bullish into the July rally — honest accumulation reads. Zero impact on
events/trend by construction (`detect()` untouched).

Surfaces: `DashboardData.consolidation_ranges` (+ API), a `▭ RANGE` box on
the chart (third `POIBoxesPrimitive`, neutral slate, live ranges to the right
edge via the sentinel clamp, `▭ Range` toolbar toggle default **on**), a
ladder chip `▭ RANGE ·Nc` (`TimeframeOverview.in_consolidation` /
`consolidation_candles`, from the ACTIVE range). **Range line termination
was built and reverted on user visual review (2026-07-14, same day)**: the
initial cut truncated a BOS/CHoCH line whose level sits inside a confirmed
box at the range start (`consolidationTruncationTime`) — on the SOL H1 chart
this cut reference lines dead at every box edge, which read as lost
structure rather than decluttering. Reference lines now run through the box
untouched; the "stale line to the edge" problem is instead solved by the
phase-2 staged breakout event, which terminates the old line at the range's
*resolution* (a structural fact) rather than at its *start* (a cosmetic
cut).

**Consolidation breakout staging — phase 2, additive events** (as of
2026-07-14, same day): a range's boundary is the structural level its
breakout actually broke — often breakable while the state machine's own
references stay out of reach (ETH's next close above the 1829.5 range top
would still sit under the 1833 staircase bar). `stage_breakout_events`
(`detectors/consolidation.py`, pure; run by `_run_internal_structure` under
`_CONSOLIDATION_STAGE_BREAKOUT_EVENTS`, merged + timestamp-sorted like the
detector's staged-BOS merge) stages one event per range resolved by a
sustained boundary break, at the breakout candle: **with** the segment's
standing trend (the direction established by the advance that opened the
quiet segment) → a real `BREAK_OF_STRUCTURE` referencing the boundary (safe
for replay — it re-asserts the trend the replay already holds, and the
ladder's `last_event` picks it up); **against** it → a `CHANGE_OF_CHARACTER`
with `provisional=True` (the additive contract: the state-machine trend never
flipped, so hunt/narrative replay skip it while the chart shows the dimmed
`CHoCH?` mark — same rationale as the fizzle marker). `reference_timestamp`
= the first candle that formed the boundary, so the line spans the defended
level. Nothing is staged for advance-resolved ranges (the real event already
marks the candle), bootstrap segments (no trend context), or when a real
same-direction BOS/CHoCH sits within `_CONSOLIDATION_STAGE_DEDUP_CANDLES` =
12 of the breakout (one confirmation window — the real event and the staged
one are the same break read twice; e.g. the BTC H1 June-bottom range resolves
bullish on the same candle as the real 07-02 weak-ref CHoCH, staged mark
dropped). Measured on the live 5×4 matrix: **+7 events / 0 removed /
`final_trend` unchanged in all 20 combos** — SOL 4h gains the March + May
bounce reversals (provisional CHoCH at range tops 91.19/90.71; the May one
lands on a candle the machine had read as a mere sweep) and the April
continuation (BOS at the 82.08 range floor), AAVE 4h/1d and BTC 4h one
honest mark each, NEAR 15m one. BTC/ETH H1 gain nothing *yet* — their July
ranges are still ACTIVE; the staged mark is what will appear at the eventual
breakout. Fixture `solusdt_4h_2025_11_06_2026_07_14.json` locks the three
SOL marks. Phase 3 (flag, only if phase 2 proves out in use): resolution
re-seeds staircase + CHoCH refs at the boundaries ("cycle reset").

**Not yet implemented**:
- Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
  `invalidated_at` for the swept zone.
- Composite multi-timeframe confluence score (phase 2 of the score plan):
  per-TF signed sub-scores from OB/Sweep/EQL/VOL/RSI-div/Hunt with exposed
  components; requires porting RSI(14) + divergence detection (today
  frontend-only, `MainChart.tsx`) into `indicators/`.
- React frontend behavior divergence sidebar panel and chart overlay.
- React frontend liquidity targets, retail trap, and market structure
  sidebar panels.
