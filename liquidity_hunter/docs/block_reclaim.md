# Block reclaim — the measurement that admits the layer

`app.block_reclaim` is a decision-adjacent reading, and the project only
allows one *with the measurement that earned it*. This is that measurement:
what was tested, what it showed, and — at least as importantly — what it does
not show.

Everything here is reproducible from `research/`; the numbers below name the
command that regenerates them.

A visual edition of this record — the rule, the evidence, the graveyard of
rejected hypotheses, and the retractions — is published from
`block_reclaim_artifact.html`, kept next to this file so the page and the doc
it mirrors change together:
<https://claude.ai/code/artifact/16a5f942-8064-42dd-8917-ed478d0aae7d>.
It is the same content read from the other end: this file is what a person
changing the layer needs, that page is what a person deciding whether to trade
it needs. Republishing it means passing that URL, or it becomes a second
artifact.

## What is observed

Price works below the session VWAP, trades into an order block that is sitting
down there, and then prints a candle whose wick pierces the VWAP and whose body
closes back across it. Two populations are involved: whoever bought the block
is positioned in it, and whoever entered since the VWAP's anchor has that
average as their break-even. The reclaim is the candle where the second group
stops being underwater — right after the first group's level was tested.

The layer emits every such candle and records `r_atr` on each: the distance
from the reclaim to the tested extreme, in the local mean true range. It does
**not** filter on it. The threshold belongs to the reader, and keeping it out
of the detector is what stops the layer and the study from drifting apart.

## What was measured

M15, 72 symbols, 25 000 candles each, entries dated at the candle they occur
on, forward outcome read as the whole payoff — reaching a 2:1 excursion, being
stopped at 1R, or marked to market at 40 candles — with the round trip charged
per trade in that trade's own R.

The control is a **placebo**: the same pinbar reclaim of the same VWAP with no
block behind it, at the same `r_atr`. A random control only shows the setup
beats noise; the placebo shows whether the block contributes.

At `r_atr <= 1.0`, cost 0.10% per trade:

| sample | n | with block | placebo | lift | net R | t |
|---|---|---|---|---|---|---|
| 72 symbols (all) | 676 | 52.4% | 29.4% | +23.0 pp | +0.269 | +4.7 |
| 47 symbols (search) | 444 | 55.0% | 29.1% | +25.9 pp | +0.349 | +4.9 |
| 25 symbols (holdout) | 232 | 47.4% | 30.2% | +17.2 pp | +0.114 | +1.2 |

A 2:1 payoff breaks even at a 33.3% hit rate. The placebo sits at the random
walk in every sample; the tight-stop geometry earns nothing by itself.

The holdout's `t` is the number to keep in view. The lift is there and points
the same way, but at 0.10% the half that was not searched cannot on its own
reject a mean of zero. It is the weaker half in every cut.

```
poetry run python research/vwap_ob_pinbar.py \
    --symbols $(python research/_symbols.py all) --timeframes 15m \
    --limit 25000 --deep --export trades.json
poetry run python research/vwap_exit_grid.py --trades trades.json \
    --max-r-atr 1.0 --sample all|search|holdout
```

The search/holdout split is a hash of the symbol name, recorded in
`research/_symbols.py`. It was not recorded for the earlier run, which is why
these numbers are not on the same halves as the ones they replace.

### The exit is not a calibration

Net R at 0.10%, block arm, n=676: **+0.091** at 1.0R, **+0.209** at 1.5R,
**+0.269** at 2.0R, **+0.241** at 2.5R, **+0.261** at 3.0R. Positive across the
grid with a broad plateau rather than a spike, which is what separates an
effect from a tuned exit.

### Where it dies

R is 0.39% of price, so a 0.10% round trip costs 0.26R. The block arm survives
0.15% at 2.0R (+0.113, t=+2.0) and 0.06% comfortably (+0.393, t=+6.8); at 1.0R
it is already gone by 0.15%. What a reader's real round trip is remains the one
number this study cannot supply.

## Walk-forward: the edge is there across time, not only across symbols

Symbols held out do not answer "is this one regime?", because the symbols are
the same market. `research/vwap_walkforward.py` buckets the dated trades into a
daily return series per candidate rule and runs rolling walk-forward with purge
and embargo, selecting on train and scoring on test — validating the procedure,
not just the rule that survived it.

Fifteen rules were declared, the aggression family from the earlier passes plus
the `r_atr` threshold family, since the threshold was one of the things tried
and leaving it out of the trial count would flatter the choice being defended.

* **`ob|r<=1.0` is picked on train in most later folds**, and has the best
  pooled out-of-sample Sharpe of the fifteen: **6.30** against 3.64 at 1.5,
  4.04 at 0.5, 3.11 at 2.0, and −0.92 for the same threshold on the placebo.
  The threshold curve reproduces out of sample with the peak in the same place.
* 22 folds, mean train SR 6.19 against mean test SR 4.09 — degradation of
  −2.10, and **16 of 22 folds positive** out of sample.
* **PBO 0.067**, well under the 0.5 line.
* Deflated Sharpe: observed 5.53 against an expected max of 1.77 from fifteen
  trials on noise alone.

## What this does not establish

**That it pays.** R is 0.42% of price on M15, so a 0.10% round trip costs
0.275R and the arms break even near a 0.20% round trip. On liquid majors, where
that cost is knowable, n=167 and the edge dies at 0.15%. The strength sits in
the alts, where the cost assumption is the weakest thing in the study. Nothing
in this layer knows a reader's execution costs, which is one reason it names no
entry, size, or target.

**That the block works better short.** It looked that way, and it does not
survive the placebo. Both arms are split by side, and the *placebo* carries the
same asymmetry (27.4% bullish against 31.5% bearish; on the holdout, 25.8%
against 34.5%). Netted against it the block contributes **+23.2 pp on the long
side and +22.9 pp on the short** — the same amount. The gap is the window's own
drift showing through both arms, not the mechanism preferring a side. Read the
raw sides and the long trade looks like the poor relation; read the lift and
there is nothing to choose between them.

**A validated threshold.** `1.0 × ATR` came from reading the threshold curve on
the search set. The holdout used the same number and replicated, which is the
best defence available and is not the same as having chosen it in advance.

## Rejected along the way

Recorded because re-reading this is cheaper than re-running it: a higher
timeframe trend filter (helps the placebo equally), equal-level pools as the
trigger (negative everywhere), block freshness (helps a little, is not the
ingredient), the entry candle's own delta (22% against 22%), a CVD-aggression
veto (inverted — the slice reaches the target more often *and* is stopped more
often, which is both tails widening), open interest as a second axis (shrank
from −8 pp to −3 pp out of sample), a session-anchored VWAP on H4 (negative in
both samples), and a floor on R meant to bound the cost (improved the sample it
was tuned on, hurt the holdout).

## The block's lifetime: corrected after the measurement

Both the detector and the study originally read `POIZone.invalidated_at` as
the moment the block stopped resting. It is not. The POI queue retires the
**oldest** zone of a side whenever any zone of that queue breaks (the
indicator's `array.shift`), so the stamp records when the queue got around to
a box rather than when price took it — measured on BTCUSDT 1h, a block closed
through on 10 Aug carried a 19 Aug stamp, nine days later. Read as the break,
it keeps a spent block in the population for days, and a "test" of a spent
block has nobody positioned in it, which is the premise the entire reading
rests on. The mirror case is a box the queue retired though price never broke
it: read as the break, its remaining life is discarded.

`_rests_until` now searches for the break directly — the first candle to
*close* beyond the far boundary, from the candle that confirmed the box
forward — bounded by the retirement stamp, since a retired box is off the
board. It lives in both `app/block_reclaim.py` and
`research/vwap_ob_pinbar.py`, because the two are the same rule on purpose.

**The tables above predate this correction and describe the mis-dated
population.** The direction of the change is a guess, not a result: the
correction removes tests of blocks nobody was positioned in, which should
help, and shortens some blocks' lives, which removes entries of unknown
quality. Re-baselining them is the first thing the next measurement pass
does, before any new hypothesis is tested against them.

## The live edge

A reclaim on the series' last candle carries `provisional`. On a live feed that
candle is still forming and neither half of the reading is settled — the wick
may not end up crossing the VWAP, the close may not end up back across it — so
the mark can vanish on the next refresh. It is emitted rather than withheld,
because a reader watching the live edge wants to see it, and flagged so that
nothing replaying history counts a candle that may still become something else.
The chart suffixes it `?`, the same convention the provisional structure marks
use.

None of the numbers here cover these: the measurement reads a forward outcome
and therefore drops the tail of the series outright, so a live-edge reclaim was
never in the measured population and its confirmation rate is unknown.

## Detector and study: one implementation

The `ob` arm of `research/vwap_ob_pinbar.py` **is** `detect_block_reclaims`,
imported. For most of this investigation the two were the same rule written
twice, and they did not return the same set — the detector was a superset,
reproducing every measured entry and finding about 9% more. Two causes were
found, and both are now closed:

* **scan order.** The study merged the candles that touched *any* block into
  one stream and took one entry per visit, so a visit to one block could mask
  a visit to another; the detector scans per block, which is the more faithful
  reading of the rule. Importing it settles this in the detector's favour.
* **the floor on R.** The study dropped a visit whose R fell under
  `--min-r-atr` (default 0.25) measured against the **series-wide** mean true
  range, while the detector drops one under `MIN_DISTANCE_ATR` (0.05) measured
  against the **local** ATR(14). Both the value and the unit differed, so which
  visits survived differed — in a calm stretch the detector kept what the study
  discarded, in a volatile one the reverse — and a visit dropped in the scan is
  gone rather than filterable afterwards. The two now share the unit and the
  value.

What the arm still owns is everything downstream of the observation: the entry,
the stop, the forward outcome, the control. The detector names none of those,
by design.

The standing risk this removes is worth naming: `POIDetector` is under active
development, and while these were two implementations, a change to it would
have moved production without moving the measured object — silently. Now a
change to either shows up in both.

Because the `ob` arm's parameters are the detector's compiled-in constants, the
study **refuses to run** if `--max-wait`, `--merge-gap`, `--wick-frac` or
`--body-frac` are moved off them. Sweeping those means editing the detector,
which is a change to the rule and needs the whole re-measurement, not a flag.

**Every table above predates this and the lifetime correction.** Re-baselining
them is the next measurement pass.

## Two questions the re-baseline answered

**Does the VWAP's accumulation carry it? No.** The lift was seen to rise with
how much the average had accumulated — six candles negative, twenty-four weak,
forty-two strong, ninety-six strongest — and the mechanism predicted it: a
six-candle average is nobody's break-even, a ninety-candle one is a session of
positions. Asked **within** M15, where nothing but the accumulation moves, the
prediction fails. Block hit rate by candles of accumulation at the entry:
**49.5%** (0-31, n=329), **58.5%** (32-63, n=171), **51.7%** (64+, n=176) —
not monotonic, and the middle bucket is not a finding at that n. The placebo is
flat across the same buckets (29.3 / 30.3 / 28.8), so it is not that late
session hours behave differently either.

The earlier gradient varied timeframe, anchor and accumulation at once. Read
now, the accumulation is not the axis that carried it, and which of the other
two did is unmeasured. `BlockReclaim.vwap_candles` stays on the entity as a
description; **nothing should read it as a strength.**

**Is the direction asymmetry the mechanism or the period?** The period — see
"What this does not establish".

## A reversal worth recording: the fresh block is the weaker one

Freshness was reported as helping a little. On the corrected population it is
the other way round, and not by a little. Net R at 2.0R, cost 0.10%:

| slice | n | net R | t |
|---|---|---|---|
| first visit to the block | 308 | +0.103 | +1.2 |
| a later return | 368 | +0.407 | +5.3 |

The SMC claim is about the untouched zone, and the untouched zone is the half
that cannot carry the reading on its own. No filter is drawn from this — it is
one sample, and it is the sort of slice this study has been fooled by before.
It is recorded because it points the opposite way to the story the setup is
usually told with, and because the honest version of "freshness helps" is that
it does not.

## Changing the rule

The study's `ob` arm imports the detector, so a change to the detector *is* a
change to the measured object and needs the study run again: export the dated entries, compare against the placebo at the
threshold in question, and confirm on symbols held out of the change. A
modification that improves the search set and not the holdout is the shape
this study has already produced twice.

One correction worth keeping: several blocks, or several visits to one block,
often resolve at the same reclaim candle. They are one observation. The
detector keeps the **nearest** test, since that is the level the reclaim was
measured against; collapsing them leaves the reading unchanged (51.9% against
51.9%) while dropping the staler, farther tests, which measure worse.
