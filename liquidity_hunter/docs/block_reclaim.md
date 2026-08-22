# Block reclaim — the measurement that admits the layer

`app.block_reclaim` is a decision-adjacent reading, and the project only
allows one *with the measurement that earned it*. This is that measurement:
what was tested, what it showed, and — at least as importantly — what it does
not show.

Everything here is reproducible from `research/`; the numbers below name the
command that regenerates them.

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

M15, 69 symbols, entries dated at the candle they occur on, forward outcome
read as the whole payoff — reaching a 2:1 excursion, being stopped at 1R, or
marked to market at 40 candles — with the round trip charged per trade in that
trade's own R.

The control is a **placebo**: the same pinbar reclaim of the same VWAP with no
block behind it, at the same `r_atr`. A random control only shows the setup
beats noise; the placebo shows whether the block contributes.

| sample | n | with block | placebo | lift |
|---|---|---|---|---|
| 43 symbols (search) | 370 | 51.9% | 30.8% | +21.1 pp |
| 30 symbols (held out) | 303 | 50.2% | 27.7% | +22.5 pp |

A 2:1 payoff breaks even at a 33.3% hit rate. The placebo sits at the random
walk; the tight-stop geometry earns nothing by itself.

```
poetry run python research/vwap_ob_pinbar.py --symbols … --timeframes 15m \
    --limit 25000 --deep --export trades.json
poetry run python research/vwap_exit_grid.py --trades trades.json --max-r-atr 1.0
```

The lift tracks how much the VWAP had accumulated at the reclaim, monotonically
across four measurements — six candles (a session VWAP on H4) is negative,
twenty-four is weak and does not replicate, forty-two is strong, ninety-six is
strongest. `BlockReclaim.vwap_candles` carries that count so a consumer can see
which end of the gradient a given reading sits at.

## What this does not establish

**That it pays.** R is 0.42% of price on M15, so a 0.10% round trip costs
0.275R and the arms break even near a 0.20% round trip. On liquid majors, where
that cost is knowable, n=167 and the edge dies at 0.15%. The strength sits in
the alts, where the cost assumption is the weakest thing in the study. Nothing
in this layer knows a reader's execution costs, which is one reason it names no
entry, size, or target.

**Anything about direction.** The bullish side is the weaker one in both
samples (+0.169 against the bearish +0.386).

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

## Detector against study: a known, quantified gap

The two are the same rule but not yet the same code, and they do not return
exactly the same set. Checked on BTCUSDT, SOLUSDT and CRVUSDT over the study's
own 25 000-candle M15 window, at `r_atr <= 1.0`:

| symbol | study | detector | shared | study only | detector only |
|---|---|---|---|---|---|
| BTCUSDT | 6 | 8 | 6 | 0 | 2 |
| SOLUSDT | 7 | 7 | 7 | 0 | 0 |
| CRVUSDT | 32 | 34 | 32 | 0 | 2 |

The detector is a **superset**: it reproduces every entry the measurement was
built on and finds about 9% more. The cause is scan order — the study merges
the candles that touched *any* block into one stream and takes one entry per
visit, so a visit to one block can mask a visit to another; the detector scans
per block. Per block is the more faithful reading of the rule, which is why it
was kept.

What that costs is precision about the extras: they were not in the measured
population, so their contribution is unknown — the reported lift describes the
shared set, not the detector's full output. Closing this means having the study
import `detect_block_reclaims` instead of re-implementing it, and re-baselining
the numbers above. That is the next measurement pass, and until it happens this
paragraph is the honest statement of what the layer's numbers cover.

## Changing the rule

The detector and `research/vwap_ob_pinbar.py` implement the same rule on
purpose. A change to one is a change to the measured object, so it needs the
study run again: export the dated entries, compare against the placebo at the
threshold in question, and confirm on symbols held out of the change. A
modification that improves the search set and not the holdout is the shape
this study has already produced twice.

One correction worth keeping: several blocks, or several visits to one block,
often resolve at the same reclaim candle. They are one observation. The
detector keeps the **nearest** test, since that is the level the reclaim was
measured against; collapsing them leaves the reading unchanged (51.9% against
51.9%) while dropping the staler, farther tests, which measure worse.
