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

**Displacement-success percentage cap**
(`InternalStructureDetector`, as of 2026-07-17):
`choch_success_displacement_max_pct` (constructor default `None` = uncapped;
wired **`0.20`** in `load_dashboard_data` via
`_CHOCH_SUCCESS_DISPLACEMENT_MAX_PCT`). The ATR unit above self-adapts to each
series' volatility — that is what makes the threshold config-free per asset —
but on an extremely volatile series it **degenerates**: surveyed on the
production slices (2026-07-17), 4.5 ATR as a percentage move is BTC 1D 16%,
ETH 23%, SOL 32%, AAVE 33%, NEAR 36%, AERO 44%, ENA 49% (vs a sane 3–9% on
every H1). A volatile alt daily thus demands an unreachable 30–50% move to
credit an unconfirmed reversal, so a plainly successful impulse still gets its
CHoCH retroactively cancelled on the give-back. Motivating case (AEROUSDT 1D):
the 2026-05-16 bearish CHoCH (broke the 0.43900 HL) fell **−31%** to 0.30440
with a real close-break of its 0.38430 fundo — but the V-recovery to 0.58
formed **no pullback pivot** (no high pivot between 06-06 and 06-23), so no
confirming BOS ever emitted, the origin stayed armed, ~2.6 in-detector ATRs
fell short of the 4.5 threshold, and the sustained reclaim of 0.439 fired a
retroactive `CHOCH_FAILED` (+ the recovery absorbed into the flip, no fresh
bullish CHoCH). When set, the effective threshold becomes
`min(atr_mult × mean_tr_pct, max_pct)` (`_displacement_success_threshold`, all
four check sites — both pivot-loop sides + both live-edge sides). **20%** ≈
2.05 ATR on AERO 1D (the cleanest of the sweep: 3.0 changed nothing, 2.5
left a live-edge ✕ ping-pong, 2.0 read best); every intraday combo sits far
below the cap, so the NEAR H1 calibration above is untouched. Inert without
`choch_success_displacement_atr` (nothing to cap — wiring that toggles the
base flag off need not clear it). Measured (BTC/ETH/SOL/NEAR/AAVE/AERO/ENA ×
15m/30m/1h/4h/1d, `limit=1200`, production wiring, raw + composed streams):
**4/35 combos changed — all dailies** (NEAR/AAVE/AERO/ENA 1D), every intraday
combo byte-identical, **`final_trend` unchanged on all 35**. The daily diffs
consistently read better: AAVE 1D's four-✕ ping-pong across mid-2024 becomes
the real 76→185 rally credited as CHoCH ▲ 05-23 + a clean BOS ▲ staircase
(118 → 148.89 → 159 → 185.24); AERO 1D's Oct-2025 crash (0.96 → 0.23) becomes
a credited bearish CHoCH chain instead of three failed bullish CHoCHs, and its
Nov rally to 1.30 — which fully retraced — correctly reads as a sweep; NEAR
and ENA 1D gain BOS staircases on their big legs. Real-data regression
fixture: `tests/liquidity/detectors/data/aerousdt_1d_2024_12_2026_07.json`
(592-candle production D1 slice from the structural anchor; uncapped → the
06-16 bearish `CHOCH_FAILED` at 0.439 and no bullish CHoCH, capped → the
May bearish CHoCH stands and the recovery is a fresh bullish CHoCH at the
0.4809 LH, `final_trend` bullish either way). Not mirrored into
`SwingStructureDetector` (the base flag isn't either).

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

**Consolidation cycle reset — phase 3, scoped re-seed** (flag
`_CONSOLIDATION_RANGE_RESET_CYCLE`, default **OFF**; as of 2026-07-14): the
state machine can look stuck inside a range because both its references sit
*outside* the box (staircase at a pre-range wick above, CHoCH reference at the
leg origin below), so nothing inside the box triggers. Phase 3 re-seeds those
references onto the box boundaries and replays. The mechanism: the
consolidation scanner emits `RangeReset` directives
(`detect_consolidation_ranges_with_resets`) at a range's confirmation and each
boundary expansion — the box *as known at that candle* (the final box would be
lookahead); a second `InternalStructureDetector.detect(range_resets=...)` pass
applies them at the top of the pivot loop, collapsing the counter-trend CHoCH
reference to the opposite boundary (structural) and the with-trend
staircase + reported floor to the near boundary, clearing the stale pullback
candidate and restarting the staleness clock. Empty/absent directives are
byte-identical to a plain `detect` (the safety invariant, unit-tested).

**Blanket re-seed rejected by measurement.** Applying the directives of *every*
historical range was measured on the live 5×4 matrix: **20/20 combos changed,
heavy structural churn (−11 to +13 events), 1 trend flip.** It genuinely
unsticks real cases (SOL 4H surfaces a whole month of May structure phase 2
was blind to), but it also **rewrites settled history** — the ETH 4H case
turned the 06-23 `CHOCH_FAILED` (which lets July's +12% recovery register,
ending bullish) into a real bearish CHoCH that bypasses the failed-CHoCH net
and ends bearish against a rising market. That is the "cascades trend state"
failure the house culture warns about: a reference change at candle N reclassi-
fies everything after N.

**Scoped to the single ACTIVE range** (`_scope_resets_to_live_range`, the
shipped form): only the range still open at the edge — the one that looks stuck
*now* — is re-seeded; resolved ranges keep their additive phase-2 staged marks.
Because ranges are non-overlapping segments, every reset at/after the active
range's start index belongs to it and no resolved range's does. Measured
**0/20 structural changes, 0 trend flips** — the ETH regression is gone (its
June range is *resolved*, never re-seeded) and settled history is untouched;
the only current-snapshot effect is BTC 4H dropping a spurious mid-box `BOS?`
provisional (63990 inside [61297, 64950]). **Conservative by construction**: a
range un-scopes the moment it resolves (4 sustained closes past a boundary), so
phase 3 (a) suppresses premature mid-box provisional clutter while price ranges
inside, and (b) anchors the forming mark at the real boundary during the first
breakout candles — but does **not** by itself turn a range-exit reversal into a
real trend flip (the exit resolves the range and hands off to phase-2 staging).
Tests: detector safety invariant + reset-re-seeds-reference (suppression
contrast) in `test_internal_structure.py`; scanner reset emission in
`test_consolidation.py`; active-only scoping in `test_dashboard_data.py`.

**Deferred (Option B, the real cycle-reset).** Making a range-exit reversal a
*real* trend flip needs the re-seed to persist through resolution (scoped to
the most-recent range so the cascade stays bounded to the tail) **and** the
`CHOCH_FAILED` net preserved through the re-seed (so a fake-out back into the
box still fails rather than locking the flipped trend — the ETH failure mode).
That is the deep state-machine work; it must be built scoped + measured
step-by-step, not blanket. Not started.

**Consolidation box no longer trails a breakout — the "arm on close" gate**
(as of 2026-07-15). Reported symptom: a confirmed BTC H4 July 2026 range
(`61297`–`64692`) *grew its top to `65590`* as price broke out and made new
highs, instead of resolving at the defended top. Root cause: once a range is
`active`, absorption was checked **before** resolution, and absorption has no
strictness — any candle whose inclusion keeps the box under the (generous,
`8×ATR ≈ 8%` on BTC H4) height cap widens the box. A genuine breakout that
stayed within that envelope was swallowed candle-by-candle: the top ratcheted
up with price, so `is_sustained_break` (which needs *all* of the next
`resolve_persistence`+1 closes beyond the boundary) never caught up against a
moving target, and the breakout chop (closes dipping back inside) kept leaking
the top upward via retest **wicks**.

Fix, in `_scan_segment`'s active branch — classify each candle resolution-first
with a per-side **arm** flag (`top_armed`/`bottom_armed`, cleared when a range
ends):

1. sustained close beyond a boundary → **resolve** there (checked first);
2. close beyond a boundary that has *not* held → **arm** that side (a breakout
   test; the boundary is frozen thereafter — retest wicks can no longer widen
   it);
3. close inside the box → widen to include a wick beyond an **un-armed**
   boundary within the height cap (an armed side stays frozen; a wick beyond
   the cap is a boundary sweep).

The arm gate is the crux: it distinguishes a directional push (a *close*
breaches the boundary → freeze) from two-sided oscillation (a wick *leads* the
closes, closes stay inside → keep widening). It fixes BTC H4 (top stays 64692,
price trades above the frozen box) **and** preserves ETH H1 July's live range
(`1712.45`–`1829.52`, whose 1829.52 top is a wick with closes ~1825 below it,
and whose 07-13 spike to 1848 exceeds the cap as a sweep) — the two fixtures
`test_eth_1h_july_range_lock_is_a_live_consolidation` /
`test_sol_4h_range_breakouts_stage_additive_events` still pass unchanged. A
full box *freeze at confirmation* was tried first and **rejected by
measurement**: it threw away legitimate oscillation-widening, so ETH July's
top came in too tight (1816) and normal price action above it produced a false
bullish resolution. Live matrix (BTC/ETH/SOL/AAVE × M30..D1): the reported BTC
H4 top corrected 65590→64692; every other boundary is equal-or-tighter (no
post-confirmation wick-trailing), **zero** resolved-direction flips, `final_trend`
unchanged on all combos, one additive ETH H4 range from a tighter segment
split. 497 tests pass.

### Superseded provisional `CHoCH?` are dropped from history

`_drop_superseded_provisional_choch` (`app/dashboard_data.py`, composition
pass; as of 2026-07-15). Reported symptom: two dimmed `CHoCH?` marks stuck in
ETHBTC H4 history (2026-06-01 and 2026-06-16) that "tried to turn bullish but
failed" and never cleared, cluttering the chart. Root cause: a *provisional*
`CHANGE_OF_CHARACTER` — whether the detector's live-edge forming mark
(`emit_provisional_choch`) or a **range-breakout reversal staged against the
segment trend** (`stage_breakout_events`, the phase-2 additive contract) — is
drawn as a dimmed `CHoCH?` and skipped by replay, and the `?` glyph promises a
*forming* mark that resolves (confirms or vanishes). But a staged reversal is
**fire-and-forget**: it has no lifecycle, so it lingers forever regardless of
what price did next. The ETHBTC H4 06-01 mark was a bullish `CHoCH?` the market
invalidated four candles later with a real bearish BOS through the range floor
(the reversal *failed*); the 06-16 mark was superseded 96 candles later by the
real bullish CHoCH that finally flipped the trend on 07-02 (real structure
*confirmed* the reversal, making the stale mark redundant).

Fix: a provisional `CHANGE_OF_CHARACTER` is dropped if **any later
non-provisional BOS/CHoCH** exists — the state machine has spoken again, so the
mark's fate is settled either way (opposite advance = failed, same-direction
advance = confirmed by real structure). A genuine live-edge forming mark has no
later real advance and survives, honoring the `?`. A later *provisional* advance
does not count (it may itself vanish). Runs after the phase-2 staging merge,
before the visible filter; mirrors `_drop_resumed_fizzle_markers`.

Provably trend-safe: the pass removes only `provisional` events, which never
participate in replay, so `final_trend` (computed upstream from the detector) is
unchanged — verified across the live 5×4 matrix (BTC/ETH/SOL/AAVE/ETHBTC ×
M30/H1/H4/D1), `final_trend` identical on all 20 combos. Scope decision (user,
AskUserQuestion): **drop all superseded** (rule B) over drop-only-failed (rule
A) — measured, *all 8* staged `CHoCH?` currently in the matrix windows were
already superseded (6 by an opposite real advance = failed, 2 by a same-direction
real advance = confirmed-elsewhere), i.e. none were genuinely live-edge, so the
historical staged reversal mark was never useful; rule A would have left the two
same-direction-vindicated ones (incl. ETHBTC 06-16) still cluttering the chart.
The phase-2 fixture `test_sol_4h_range_breakouts_stage_additive_events` now locks
the *inverse*: its March + May staged `CHoCH?` (both later contradicted by a real
bearish advance) are dropped, while the April staged continuation BOS
(non-provisional) survives. Four unit tests lock the pass directly (later
opposite advance drops, later same advance drops, no later advance keeps,
later provisional keeps). 502 tests pass.

### Reversal-eaten BOS staging (`stage_reversal_eaten_bos`, 2026-07-15)

A BOS is only *emitted* once a confirming opposite pullback pivot forms after
the close-break (a state-advance alone leaves a still-pending BOS). On an
**impulsive final leg that reverses immediately** — the classic SMC "last lower
low that closes below the prior fundo, then a CHoCH the other way" — the
reversal reclaims the leg origin *before* that pullback forms, and the same
pivot that reclaims fires the CHoCH. The still-pending BOS is discarded without
ever emitting (`internal_structure.py`, the emit-check `else` branch where
`price > pb.price`, **not** the CHoCH branch — there `pending_bos` is already
`None`), so the final continuation is invisible even though the trader watched
its candle *close* below the fundo. This is the break that "permits" the
reversal, so its absence is exactly what looks wrong.

Motivating case (**ENAUSDT M30**, bearish leg CHoCH ▼ 2026-07-11 22:00 → CHoCH
▲ 2026-07-14 04:00): the leg's final low 0.07679 closes 0.07754 — below its
0.07760 fundo — then the bullish CHoCH. Without staging the leg showed only one
shallow BOS (ref 0.07954) and nothing for the 0.07760 break.

**Fix** (`stage_reversal_eaten_bos`, wired `True` via `_STAGE_REVERSAL_EATEN_BOS`;
default `False` in the detector): when a pending BOS is discarded at the
reclaim/reversal (both the bearish-pending and bullish-pending emit-check
discards, plus the two CHoCH branches defensively), stage an **additive** mark
for it *iff* its staircase floor already **closed**-broke (`floor_closed`) —
keyed on the *close* through the floor (the trader's validation), **not** the
impulse stager's displacement threshold (here 1.04% vs the 1.5% gate, too
marginal to rely on). Only close-confirmed *continuation* floors qualify (never
the first-of-leg `ref_price` fallback). Deduped against real BOS and re-timed to
the close-break by `_reanchor_bos_close_break` like the other staged marks
(`impulse_bos_displacement_pct`, `stage_wick_rejected_bos`,
`stage_choch_failed_window_bos`). This is the additive-over-state-machine
discipline: the state machine, trend, and CHoCH promotion are untouched.

Measured (BTC/ETH/SOL/AAVE/NEAR/ENA × 5m..1d, `limit=1200`,
`confluence_filter=False`): **0 trend flips** on all 36 combos, **purely
additive** — every delta is a BOS *count increase* (0 removed, 0 CHoCH change);
23/36 combos gain +1..+3 marks, each a close-confirmed continuation preceding a
reversal. Fixture `enausdt_30m_2026_06_20_07_15` + on/off regression tests
(`test_reversal_eaten_bos_off_misses_final_continuation`,
`test_reversal_eaten_bos_marks_final_continuation_before_choch`, the latter
asserting the staged mark is the only new event and the trend is unchanged).

Left as-is at the time: the sibling **Gap #1** — the *deeper* first-fundo BOS
(ENA M30 ref 0.07908) the RAW detector *does* emit but `_reanchor_bos_close_break`
drops, because within its active window (up to the next same-direction BOS) no
candle closes below 0.07908 (the close-below lands at 07-13 03:00, already inside
the next BOS's territory). Recovering it means loosening the conservative
close-in-window rule; deferred then, **resolved 2026-07-18** by the leg-launch
rescue below.

### Base persistence 12 → 2 + confirmed-trend barrier (hysteresis, 2026-07-16)

Two paired changes. First, the internal detector's base `persistence_candles`
dropped from the uniform 12 to a uniform **2** on every timeframe
(`_INTERNAL_STRUCTURE_PARAMS`), a deliberate recalibration toward faster trend
identification: a CHoCH now confirms on 2 sustained closes instead of 12.
That alone re-derives several fixture locks (5 regression tests recalibrated —
notably BTC D1's flag-off crash pathology *self-resolves* at base 2, the 74868
origin break now confirms, so `choch_weak_ref_fail_at_broken_level`'s value on
that window shrinks to "one day sooner at the tighter level"; the NEAR H1
displacement-off window drops from two false failures to one, the 06-14 leg's
BOS now confirming and retiring its origin normally; BTC H1's July ACTIVE
range start moves to 07-05 23:00 behind a newly-surviving BOS — box identical).
Note the weak-ref new-cycle barrier (4) covers only M5–H1: at base 12 the
coarse timeframes were protected by the base itself, at base 2 they rely on
the confirmed-trend barrier below.

Second, the compensating **confirmed-trend barrier**
(`InternalStructureDetector.choch_confirmed_trend_persistence_candles`, wired
**4** on all timeframes via `_CHOCH_CONFIRMED_TREND_PERSISTENCE`): hysteresis
on trend flips, so a *confirmed* structure is harder to invalidate than a
*pending* one. A trend is pending from the flip that set it (CHoCH,
`CHOCH_FAILED` revert, or silent advance flip) until an **emitted** BOS in its
direction confirms it — exactly the moment the CHoCH origin retires (a
displacement-success retirement counts, standing in for the BOS an impulse
never printed). While pending, reverse CHoCHs keep base persistence and
`CHOCH_FAILED` stays the cheap escape valve. Once confirmed, a counter-trend
CHoCH must sustain `max(barrier, weak-ref barrier)` closes: a stop-hunt poke
through the reversal reference is reported as a `LIQUIDITY_SWEEP` by the
existing non-sustained branch (no new event semantics), or the CHoCH confirms
a few candles later when the break is real. The provisional live-edge `CHoCH?`
path is untouched (it keeps showing the *attempt* forming while the confirmed
event waits out the barrier).

Measured (BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`, production wiring,
barrier 4/6/8 vs off at base 2): the diff signature at every level is the
intended one — whipsaw CHoCH+`CHOCH_FAILED` pairs reclassified to sweeps with
the continuation preserved, genuine CHoCHs re-timed a few candles later at the
same reference. Barrier 4: −154/+163 events, CHoCH −68/+36, `CHOCH_FAILED`
7→4, +58 sweeps, **1** standing-trend change (BTC 15m — a live-edge
`CHOCH_FAILED` whipsaw killed while price flushed to new lows, the corrected
reading). Barrier 6 doubles the churn (−293/+293) and changes a second
standing conclusion (AAVE 1h, needs visual review); 8 rewrites settled coarse
history (4 flips). **4 chosen** (2× base — the same value the weak-ref
barrier measured best); raise after visual review if stop hunts still flip
confirmed structures. Unit tests on the AAVE H1 wick-window fixture: the
confirmed-phase whipsaw (bearish CHoCH 06-18 + ✕ 06-20) reclassified with the
genuine 06-23 CHoCH surviving at the same 72.61 reference, and the
pending-phase whipsaw (bullish CHoCH 06-08 / ✕ 06-09 / re-fire 06-11)
byte-identical with the barrier on
(`test_confirmed_trend_barrier_reclassifies_stop_hunt_reversal`,
`test_confirmed_trend_barrier_spares_pending_trend_flips`).

### Pending-CHoCH invalidation at the broken level (`choch_pending_fail_at_broken_level`, 2026-07-16)

The pending half of the PENDING/CONFIRMED hysteresis, motivated by a user
report on AAVEUSDT H1: the 2026-07-08 12:00 bearish CHoCH broke the
*structural* 87.90 leg origin, no bearish BOS ever confirmed it (impulsive
drop, no pullback pivot), and both exits sat at the reversed leg's 97.4
extreme — the origin `CHOCH_FAILED` needed 97.4 reclaimed, the reverse-CHoCH
reference (`choch_origin_high`) *was* 97.4, and the weak-fail flag did not
apply (the reference was structural). Result: a +14% recovery rally printed
as three bullish sweeps (88.85 → 93.1 → 98.28) under a stale bearish trend
for three days. (The additive fizzle marker did fire *live* on 07-09 but is
live-edge-only by construction — `standing_choch_ref` tracks only the current
standing CHoCH — so batch reruns show no `✕` in history, and it never flips
the trend anyway.)

With the flag on, a *pending* CHoCH (no emitted BOS yet; the same
`trend_confirmed` boundary as the confirmed-trend barrier, and
displacement-success retires the level exactly like it retires the origin)
also arms **its own broken level** as an invalidation reference even when
structural. The structural-level failure demands its own persistence,
`choch_pending_fail_persistence_candles` (wired **6**), stronger than base so
an ordinary retest does not kill a genuine reversal; weak references keep the
existing `choch_weak_ref_fail_at_broken_level` behavior (base persistence,
weak-level staircase re-seed), while a structural-level failure restores the
pre-CHoCH staircase stash (the interrupted cycle was alive — on AAVE that
makes the resumed rally's first BOS reference the genuine 97.4 top).

Calibration (AAVE window): persistence 5 (and below) also kills the ordinary
07-04 retest against the *correct* 07-03 bullish CHoCH (the dip to 86.81 held
2–4 closes below 87.83) and cascades the prior sequence; 6 spares it and
catches the real 07-09 fizzle reclaim (held far longer); 8 ≈ 6 — the plateau
between "ordinary retest" and "real fizzle" is wide. Matrix measurement
(BTC/ETH/SOL/AAVE/NEAR × 5m..1d, persistence 5/6/8 vs off): at 6,
−346/+311 events, +89 real `CHOCH_FAILED` (each a pending CHoCH properly
disregarded on its reclaim), net sweeps down (112 removed / 78 added), and
**2 standing-trend corrections — AAVE 30m and AAVE 1h bearish → bullish, the
motivating case**; 5 adds a SOL 15m flip (the over-eager retest kill). Fixture
`aaveusdt_1h_2026_05_15_07_16.json` locks the production reading (CHoCH
07-08 → real `✕` 07-09 05:00 at 87.9 → rally BOS 07-10 against 97.4) and the
off-variant locks the old pathology. Knock-on recalibrations: the BTC D1
crash window now resolves upstream (January bullish CHoCH pending-fails
01-20, April flips at the structural 71999.9 instead of the weak 75998.9
re-anchor, May crash is a plain bearish CHoCH at 74868 — the weak-fail flag
is belt-and-suspenders on that fixture now, still load-bearing for weak refs
generally); the NEAR H1 displacement-off window gains two level-reclaim false
failures (2.083, 2.173) that the displacement retirement (production on)
correctly prevents; the ETH H1 resumed-fizzle composition test pins the flag
off (on that window the pending-fail preempts the fizzle marker with a real
failure — the marker keeps its niche for reclaims sustaining fewer closes
than the pending-fail persistence).

### Resumed-fizzle cancel by new extreme (2026-07-16)

User report (SOLUSDT M15): the 2026-07-16 00:15 bearish CHoCH (ref 77.21, a
correct reversal — price crashed to 75.6 after) carried a `✕` fizzle marker at
05:30, stacked next to the CHoCH label. A shallow bounce had sustained exactly
six closes back above 77.21 (topping at 77.59, ~1.2 ATR) before collapsing
through the CHoCH's own 76.64 fundo two hours later. The state-machine trend
never flipped (the pending-fail did *not* fire: its reclaim window must start
at or before a swing-high pivot, and by the next qualifying pivot the
displacement-success retirement had already cleared the level) — the bug was
the *additive marker* alone.

A reclaim-depth gate at emission was measured and ruled out: the **genuine**
June fizzle's reclaim was *shallower* (0.98 ATR) than this false one
(1.18 ATR) — depth cannot separate them. What separates them is what happens
*after* the reclaim: the false marker's reversal **resumed** (new extreme
beyond the CHoCH's own pivot), the genuine fizzle's never did. So the existing
`_drop_resumed_fizzle_markers` composition pass gained a second resumption
proof alongside the chart-surviving same-direction BOS: **a candle closing
beyond the marked CHoCH's `price_level`** (the triggering pivot's fundo/topo)
after the marker — covering a resumed leg whose BOS hasn't confirmed a
pullback yet. Close-based (a wick through the extreme is a sweep, not
resumption); the standing CHoCH is the last same-direction non-provisional
CHoCH before the marker (the fizzle only ever marks the stream's last CHoCH).
Live-edge semantics preserved: the marker still shows honestly while only the
reclaim is known, and repaints away within a candle or two of the resumption
close (verified by truncation at 06:45 vs 08:00).

Matrix impact (BTC/ETH/SOL/AAVE/NEAR × 5m..1d): exactly two markers dropped —
the motivating SOL M15 05:30 and its M30 sibling of the same event; zero other
event changes, `final_trend` untouched by construction (markers never feed
replay). Fixture `solusdt_15m_2026_07_01_07_16.json` locks both the batch
cancel and the truncated live-edge marker; synthetic tests lock the close-vs-
wick semantics.

### Failed-CHoCH re-activation (`choch_failed_rearm`, 2026-07-16)

**Motivating case (MUUSDT H4, a new listing):** two bearish CHoCHs cancelled
and the trend stuck bullish through a −19% collapse. The 06-23 CHoCH
(structural ref 1120.6) failed legitimately — a one-candle 1050→1215 reclaim
that held days at 1120–1255 (the user's own read: "sweep forte, poderia até
falhar"). But the second bearish CHoCH (07-02, weak sweep-re-anchor ref
1026.86) died on a **flat drift hugging the level** — twelve closes 0.1–1.9%
above 1026.86 (well under half an ATR of displacement), killed at the weak-ref
base persistence 2 — and then the machine had no way back:

1. The failure nulls all reversal references (one-shot, anti-ping-pong) and
   `choch_failed_fallback_suppress_candles=20` keeps the trailing fallback
   off — so the crash's first break (**eleven** consecutive closes below the
   951.42 extreme, 07-07) evaluated with `choch_low_ref=None` → sweep.
2. The dead-cat bounce (875→1035) armed a pending bullish BOS whose leg
   origin — **875.67, the crash low itself** — became the bearish CHoCH
   reference, so the next break (7 closes below 954.36, 07-13) demanded a
   close below the bottom of the very crash it was trying to read → sweep.

**Mechanism** (`choch_failed_rearm`, wired True): a `CHOCH_FAILED` arms the
failed CHoCH's *broken reference* as a **re-arm** level. A later sustained
break back beyond it — scanned only from the failure onward (the arm-index
clamp, mirroring `*_choch_arm_index`), at the original reference's
weak/structural persistence class — **re-emits the `CHANGE_OF_CHARACTER`**
and flips the trend again: the "reclaim" that failed the CHoCH was the old
trend's last gasp, not a recovery. Placement and lifecycle:

- Ranks **just above the trailing fallback** in the reference chain
  (`validated / pending-leg / origin` keep authority; a first-priority
  variant was built and demoted — while armed it *blocked* lower refs, e.g.
  BTC D1's April 2026 fallback CHoCH). Exempt from the post-failure fallback
  suppression: the level is a proven structural fact, not a hair-trigger
  trailing pivot, so the anti-ping-pong window doesn't apply.
- **One-shot per failure chain**: a re-fired CHoCH's own failure does not
  re-arm (`*_choch_from_rearm`), so a level being chopped cannot ping-pong
  CHoCH→✕→CHoCH→✕ indefinitely.
- The memory drops at any CHoCH emission (a fresh cycle owns the narrative)
  and when the opposite trend is **confirmed** (emitted BOS or
  displacement-success): once the reclaim built real structure, a much-later
  break of the old level is a fresh reversal, not a re-activation.

**MUUSDT H4 outcome:** the 06-23 CHoCH still fails 06-24 (genuine), the
re-arm re-fires the bearish CHoCH 07-01 at the same 1120.6 once price
sustains back below, the false 1026.86 CHoCH/✕ pair never exists, and the
collapse prints a real bearish BOS (07-07, ref 951.42). The +18% bounce off
875 reads as a bullish CHoCH that fails 07-13, re-fires 07-14 (a real +11%
push), and fizzle-marks on the final flush — honest narration of violent
chop.

**Historical validation:** the first divergence on the BTC D1 fixture is the
**2024-08-05 yen-carry flush** — the old reading kept a bullish trend through
70k→49k (the 07-04 bearish CHoCH had failed on the 07-14 bounce, and the
crash printed as a counter-trend *sweep* at 48888); the re-arm re-fires the
bearish CHoCH on 08-04. Exactly the MUUSDT pathology, on the most famous
liquidation day of that year.

**Rendering (visual review 2026-07-16):** the first review flagged stacked
marks — a re-arm cycle put up to four events at one level (CHoCH, ✕,
re-fired CHoCH, ✕/fizzle), each drawing a full-span line from the *same*
`reference_timestamp`, so lines and labels sat on top of each other
("dobrando os CHoCH ✕"). Three-part fix, per user choice (sequential
segments + distinct label):

- the re-arm pivot keeps the level's *price* but carries the **failure's
  timestamp**, so the re-fired CHoCH's line starts where the `✕` ended —
  the cycle reads as consecutive segments along the level (`✕` line
  origin→failure, re-fire line failure→its own end) instead of overlays;
- the frontend renders the re-fired CHoCH with a **`↻` suffix**
  (`CHoCH ↻ ▼`), identified by a prior same-direction real `✕` sitting
  exactly at the CHoCH's `reference_timestamp` (exact by construction —
  both come from the same sustained-break candle);
- a **fizzle marker draws no line of its own** (label only, anchored at the
  reclaim candle): the fizzled CHoCH still renders normally and its line
  already stops at the reclaim, so the marker's line traced the exact same
  segment twice (a pre-existing duplication the re-arm made denser).

**Second visual review (same day):** the tail cluster remained — at 962.15
the +18% bounce fired a bullish CHoCH (07-09, legitimate: five closes above a
structural leg origin, past the confirmed-trend barrier), failed 07-13,
re-fired 07-14 (+11% real push), and the final flush left **three marks on
one level with the machine trend still bullish 19% above price**. Two root
causes, two fixes:

- **Live-edge failure emission** (`choch_fail_live_edge`, wired True): the
  `CHOCH_FAILED` checks are pivot-gated, and a relentless one-way move forms
  *no* swing pivot (every candle a new extreme — lows 875→859→847→841→828→816
  with never five candles of respite), so the re-fire's failure sat
  condition-complete (six closes past the pending-fail persistence) but
  unemitted for days; only the additive fizzle marker showed, which never
  flips the trend, so the ladder/hunt read bullish through the crash. The
  same failure check (same reference arbitration, persistence class, and
  displacement-success retirement) now runs once more over the final state at
  the end of `detect` and emits the real failure at the sustained-break
  candle. Deterministic across runs; the in-loop path emits the identical
  event once a pivot finally forms (its skipped bookkeeping only feeds
  subsequent pivots, of which there are none). The provisional/fizzle
  live-edge marks are suppressed on a run that emits one (their floor state
  reflects the pre-flip trend).
- **Failed-re-fire collapse** (`_drop_failed_refire_cycles`, composition,
  user choice via review): a re-fired CHoCH that itself failed (a later
  same-direction real `CHOCH_FAILED` with no intervening same-direction
  CHoCH) added no standing structure — the level's story is already told by
  the original failure — so the pair is dropped and the ✕ → ↻ → ✕ stack
  becomes a single ✕. Trend-replay safe (the pair flips away and back;
  one-shot re-arm means no third re-fire references the dropped failure). A
  surviving or merely fizzle-marked re-fire is kept (hiding a CHoCH whose
  trend still stands would desync the chart from `final_trend`).
  **2026-07-17 extension**: a re-fire is matched by the failure's timestamp
  at its `reference_timestamp` *or* by a prior same-direction failure at the
  exact same `reference_price_level` — a CHoCH can re-attempt the level
  through a *structural* reference instead of the re-arm memory (ENAUSDT 4H:
  after the ✕ at 0.07463, the pending leg origin *is* the same pivot, so the
  07-08 re-attempt fired `reference_structural=True` with the level
  formation's own `ref_ts` and escaped the timestamp match, stacking a second
  ✕ + CHoCH on the same line as the first ✕ and the rebuild BOS). Measured
  on the live 6×5 matrix: exactly 1/30 combos changed (the motivating ENA 4H
  pair dropped; its dip reads as the `LIQUIDITY_SWEEP` already printed
  there), zero trend changes.

Measured against the reviewed state (same matrix): **purely subtractive** —
−62 events (31 failed-re-fire CHoCH + their 31 failure marks, across 22/36
combos), zero added, and exactly one standing-trend change: MUUSDT 4h
bullish→bearish, the motivating live-edge case.

**Measurement** (BTC/ETH/SOL/AAVE/NEAR/MU × 5m..1d, limit=1200, HEAD vs
wired): 31/36 combos change with the intended signature — sweep chains under
stale trends become re-fired CHoCH + BOS staircases (sweeps −69/+46, CHoCH
−57/+80, BOS −43/+55, `CHOCH_FAILED` −24/+39; the extra failures are honest
re-failures of re-fires). Two standing-trend changes, both NEAR
(15m/30m bearish→bullish): the 30m corrects a false `CHOCH_FAILED` against a
+7% rally that then made higher lows; the 15m is live-edge chop either way.
Two production fixture locks recalibrated with conclusions preserved: BTC D1
still resolves the 2026 crash bearish with the bottom BOS (narration shifts:
the April flip lands at the 75998.9 sweep extreme and dies by pending-fail
05-26); NEAR H1 displacement-off shows the re-arm *mitigating* that
pathology (the third false failure is replaced by a re-fire the 06-14 rally
BOS confirms). Fixture `muusdt_4h_2026_04_07_07_16.json` locks the
motivating window both ways (re-fire + BOS with the flag, sweeps +
stuck-bullish trend without).

### Persistent re-arm memory (`choch_failed_rearm_persistent`, 2026-07-17)

**BTCUSDT D1 2025-08..11**: a bearish CHoCH broke 111850 (ref formed
2025-08-03) and failed on 2025-09-10 (sustained reclaim). The user's read:
the failure was real at the time, but the October rally that "confirmed" it
— one continuation BOS at a marginal new high (126208 over the 124546 prior
high) — was a **liquidity sweep of the top**, fully given back in the
November crash. The original CHoCH deserved re-activation on the sustained
break back through 111850. Under the one-shot `choch_failed_rearm` nothing
could re-fire: a late-September dip had already consumed the re-arm (a
re-fire that then failed on the October rally, collapsed to the original ✕
by `_drop_failed_refire_cycles`), and a re-fire's own failure never re-arms.
The crash's reversal waited for the late, weak trailing reference at 98888.8
(11-14, eleven candles after price left 111850).

`choch_failed_rearm_persistent` (wired True) makes the re-arm memory
persistent while the level stays contested:

- **Every failure re-arms** — the one-shot-per-chain guard is lifted, so a
  failed re-fire arms the level again (only the most recent failure's level
  is remembered per side; any CHoCH emission still replaces the memory with
  its own cycle).
- **Opposite-trend confirmation demotes instead of retiring**: a demoted
  re-arm coexisting with a live fallback is arbitrated by **whichever level
  price crosses first in the break direction** (bearish: the higher level;
  bullish: the lower). Both halves measured load-bearing: at full rank a far
  armed level shadows every nearer live reference (a bullish re-arm at 94760
  from the January 2026 failure swallowed the frozen fixture's April CHoCH
  at the 75998.9 re-anchor); strictly below the fallback, the October
  re-fire loses to the farther 103470 trailing low and the target case
  degrades back to HEAD. Structural references keep their authority (the
  re-arm still ranks below validated/pending/origin).
- **Collapse-pass re-anchor**: with the chain no longer one-shot, a
  surviving re-fire can reference a failure `_drop_failed_refire_cycles`
  just dropped; its `reference_timestamp` is remapped to the nearest earlier
  surviving same-direction real failure, so the frontend's `↻` suffix still
  matches and the line starts at the visible ✕ (BTC D1: the 10-15 re-fire
  anchors at the 09-10 failure, not the collapsed September cycle's
  invisible 09-28 mark).

Result on the target: `CHoCH ↻ ▼` on 2025-10-15 at the proven 111850 (five
days after the 10-10 flash crash, a month earlier than HEAD's weak 11-14
CHoCH), and the crash prints a bearish BOS staircase (103470 → 98888 →
80600) instead of sweep + weak CHoCH.

**Measurement** (BTC/ETH/SOL/AAVE/NEAR/ENA × 15m..1d, limit=1200, HEAD vs
wired): 11/30 combos change, **0 standing-trend flips**. ETH 1D's 2024-08
carry-trade crash flips bearish on 08-01 (CHoCH ref 3205) instead of 08-27
(ref 2533) — the same signature as the target. The noisy 15m combos net
*fewer* events (whipsaw CHoCH pairs read as sweeps); ENA 1h differs only at
the live edge. **Known window sensitivity**: on the frozen D1 fixture (same
candles, window shifted 6 days earlier vs live) the January 2026 cascade
leaves a different trailing `active_high`, the staleness re-anchor's
tighten-only guard then never establishes the frozen 75998.9 reference, and
the April 2026 bullish cycle reads as sweeps — the live production window
keeps the April CHoCH → BOS → pending-fail cycle byte-identical. The fixture
lock (`test_btc_1d_crash_resolves_bearish_with_bottom_bos`) was recalibrated
into the real-data lock for the re-fire itself (✕ 09-10 → `↻` 10-15 with the
remapped anchor → January death → June bottom BOS → trend bearish), with an
off-lock (`test_btc_1d_rearm_persistent_off_refire_lost_to_late_weak_reference`)
pinning the pathology.

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

### Leg-launch BOS rescue (`_RESCUE_LEG_LAUNCH_BOS`, 2026-07-18)

Closes **Gap #1** of the reversal-eaten BOS investigation above.

The **first BOS of a leg** reports the CHoCH-seeded launch level — the
fundo/topo the reversal itself formed (see "First BOS of leg reference"). But
`_reanchor_bos_close_break` confirms every BOS inside *its own* window, which
ends at the **next same-direction BOS**. On a leg that **retests the CHoCH
before breaking down**, the launch level's first confirming close can land a few
candles *inside that successor's territory*: the launch BOS is dropped as
"wick-only" and the chart promotes a shallow, late fundo to first-of-leg
reference. The two roles the window plays were conflated — it is right for
*re-timing*, too strict as a *validity gate*, since a leg does not end at its
next continuation.

Motivating case (**ENAUSDT M30**, the same bearish leg as Gap #3 above): the leg
launches from the 0.07908 fundo the CHoCH formed (2026-07-12 00:30), price
retests the CHoCH, and the first close through 0.07908 comes at 07-13 03:00 —
three candles past the successor's stamp (07-13 01:30, ref 0.07954, a shallow
fundo formed **22 hours after** the launch level). The user read the chart as
"the BOS after the CHoCH should reference the fundo that formed and went back to
test the CHoCH, not the later shallow one" — exactly the launch level.

**Fix** (`_RESCUE_LEG_LAUNCH_BOS`, wired `True`; the pass takes
`rescue_leg_launch`, default `False`): when a **leg-launch** BOS (no
same-direction BOS between the flip that started the leg — a same-direction
CHoCH or opposite-direction `CHOCH_FAILED` — and the event) finds no close in
its own window, extend the search instead of dropping it. A close through the
floor there confirms the break and re-times the BOS to it; the shallower
same-direction continuations it passed over are **suppressed** (variant 1 of the
two the user chose between): they are premature clutter next to the launch
break, *and* their confirming close would otherwise re-kill the rescued mark in
`_drop_pre_break_reference_bos`, whose leg reset the rescued BOS now owns. A leg
that reverses without ever closing through still drops — the wick-only
protection is untouched.

**The bound came from measurement.** An unbounded search (to the leg's death:
next opposite CHoCH / same-direction `CHOCH_FAILED`) over-reached badly on
**AAVEUSDT D1**: a launch BOS whose floor (80.01) sat far beyond the leg
(trading at 145) scanned **seven months** and suppressed the real bearish
staircase it passed — 176.46 → 145.0 → 91.85 → 91.85 replaced by a single mark
(−5/+2). So the extended search runs through the **next same-direction BOS's
window only**: the launch break may confirm at most *one continuation late*. If
the level has not closed through by the second continuation, it is no longer the
leg's launch break. AAVE D1 then reduces to a clean −1/+1.

Measured (BTC/ETH/SOL/AAVE/NEAR/ENA × 5m..1d, `limit=1200`,
`confluence_filter=False`): **6/36 combos changed, 0 trend flips**, and every
delta is a **1:1 swap** (+7/−7) of a shallow late reference for the deeper
launch reference — never a net gain or loss of marks. AAVE 30m/1h (90.17 →
91.05), AAVE 1d (106.75 → 114.73), NEAR 15m (1.97 → 1.976), ENA 15m (two swaps),
ENA 30m (the motivating 0.07954 → 0.07908). Far narrower than the sibling
eaten-BOS staging (23/36). The rescued reference is the more extreme level in
every case, which is the point: the launch fundo/topo is structurally more
significant than the shallow retrace pivot that displaced it.

Tests: synthetic on/off + bound + bullish-mirror + leg-dies coverage in
`test_dashboard_data.py`, plus fixture locks on `enausdt_30m_2026_06_20_07_15`
(`test_run_internal_structure_rescues_ena_30m_leg_launch_bos` and its off-lock,
which asserts the RAW detector emits the 0.07908 BOS and the pass is what
decides its fate).

### Superseded-continuation BOS staging (`stage_superseded_continuation_bos`, 2026-07-18)

Sibling of the reversal-eaten staging above, found during the visual review of
the leg-launch rescue branch. A BOS only *emits* once a confirming
opposite-direction pullback pivot forms. In an **impulsive leg of consecutive
same-side pivots**, the next advance overwrites the still-pending BOS before
that pivot appears — and the reported floor (`prev_bull_bos_extreme` /
`prev_bear_bos_extreme`) has meanwhile ratcheted to the new pivot. So only the
**last** pending of such a run ever emits, the tops/bottoms that genuinely
formed and were broken in between get no mark, and the surviving BOS references
a later level than the leg actually broke first.

The user framed the symptom precisely: "it works after a CHoCH, but not after a
BOS." A CHoCH *seeds* the reported floor with the reversal's extreme, so the
first BOS of a leg references it correctly (the leg-launch fix above); a
*continuation* pending has no such seeding — superseded, its floor is simply
lost.

Motivating case (**NEARUSDT M15** 2026-07-14, pivots in UTC):

```
07:15  HIGH 2.0120   <- formed, then broken: deserves a BOS
10:15  HIGH 1.9960   (lower high, trails active_high down)
11:00  LOW  1.9670   (the pullback)
12:30  HIGH 2.0400   <- advance; floor_at_advance = 2.0120
15:30  HIGH 2.0660   <- next advance; NO low pivot in between
17:00  LOW  2.0180   (confirms only the 15:30 pending)
```

The 12:30 pending (floor 2.0120, `reference_timestamp` 07:15) was superseded
silently, so the leg's only continuation referenced 2.0400 with its line
starting at 12:30. Staged, the leg reads BOS 2.0120 → BOS 2.0400.

**Fix** (`stage_superseded_continuation_bos`, wired `True` via
`_STAGE_SUPERSEDED_CONTINUATION_BOS`; default `False` in the detector): at both
advance sites, before `pending_bos` is overwritten by a same-direction
`_PendingBOS`, stage the outgoing one. Shares `stage_pending_bos` with
`stage_eaten_bos` — same **close-through-the-floor** key (`floor_closed`), so a
floor the leg only *wicked* stays unmarked, and the same dedup + re-anchor
re-timing as every other staged mark. Additive-over-state-machine discipline:
trend, staircase gate, and CHoCH promotion untouched.

Measured (BTC/ETH/SOL/AAVE/NEAR/ENA × 5m..1d, `limit=1200`,
`confluence_filter=False`): **9/36 combos changed, 0 trend flips, +13 marks**.
Effectively additive — the one apparent removal (NEAR 5m) is the *same* BOS at
the *same* timestamp whose reference moved 1.955 → 1.957, the documented
same-timestamp arbitration in `_drop_pre_break_reference_bos` picking the
earlier-formed reference. No leg loses a mark.

Fixture `nearusdt_15m_2026_07_02_07_18` + on/off locks
(`test_run_internal_structure_stages_near_15m_superseded_continuation`,
`..._superseded_lost_without_staging`).

### Chart timezone: local intraday, exchange time on D1/W1 (2026-07-18)

Not a detector decision, but it belongs here because it twice corrupted a
structure review. Lightweight Charts has no timezone support: it renders every
`UTCTimestamp` in UTC. The chart therefore spoke UTC while the user read it as
local time (UTC−3), so "the BOS at 05:15" was really 02:15 local, and "the fundo
at 12/07 00:30" was 11/07 21:30. Both investigations above (ENA M30 Gap #1 and
NEAR M15) opened with three hours of misalignment between what the user saw and
what the data said — each cost a round trip to notice.

**Fix** (`frontend/src/utils/chartTime.ts`): `toChartTime(iso)` shifts each
timestamp by its own local UTC offset before handing it to the library — the
library's documented workaround for displaying another timezone. Per-timestamp
(not a single cached offset) so candles either side of a DST transition each get
the right one; verified against `America/New_York` across the 2026-03-08
transition (01:00 EST / 04:00 EDT).

**Daily and weekly are exempt.** Their timestamp *is* the exchange day (00:00
UTC); shifting would relabel the 14 Jul daily bar as "13 Jul 21:00". Those
timeframes keep exchange time, like every other platform.

Why the shift lives in the data rather than in `localization.timeFormatter` /
`tickMarkFormatter`: formatting alone leaves the library placing tick marks on
*UTC* boundaries, so the day-change tick would land at 21:00 local and the axis
would change date mid-evening. Shifting the data puts day boundaries where the
viewer expects them. The cost — chart times are no longer real epoch values — is
contained: nothing converts back (`param.time` is only forwarded between the
synced panes), and the helpers that compare event times to candle times are
shift-invariant because every time flows through the one converter. The function
is named `toChartTime`, not `toUtcTimestamp`, so that stays obvious.

A toolbar chip next to the OHLC readout shows the active clock (`UTC-3`
intraday, `UTC` on D1/W1) — the actual guard against a repeat, since the
ambiguity, not the offset, is what cost the time.
