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

## What it costs, measured

The round trip was the study's one open number, and it is now measured rather
than assumed. Not from the bars — that was tried and failed (below) — but from
the tape. Binance's `aggTrades` is public and carries `isBuyerMaker`, so inside
a short window the gap between the average price of trades that lifted the ask
and those that hit the bid **is** the effective spread a taker paid.
`research/spread_trades.py` counts it; the result is frozen in
`research/measured_spreads.json`.

The spread turns out not to be the story. At a base-tier taker fee the round
trip runs **0.103% to 0.185%** across the universe, of which 0.10% is the fee
and 0.3 to 8.5 basis points is the spread. ETH sits at 0.0029%, BTC 0.0036%,
SOL 0.0105%, ADA 0.0369%, RVN 0.0531%. **The fee is the cost and the spread is
noise on top of it** — so what decides whether this is operable is the reader's
fee tier, a number they already know, not their choice of symbol.

Charged per trade, at both ends of what the instrument can bound:

| bound | symbols priced | n | cost | block net R | t | placebo |
|---|---|---|---|---|---|---|
| floor | 37 / 69 | 342 | 0.123% | **+0.179** | +2.2 | −0.493 |
| ceiling | 67 / 69 | 636 | 0.131% | **+0.187** | +3.1 | −0.510 |

The two bounds exist because coverage and cleanliness trade off. A one-minute
window is immune to drift but cannot price a thin symbol at all, so its subset
is biased **cheap**; filling the rest at five minutes covers almost everything
but inflates those by 1.5-2×, biasing that half **dear**. The conclusion is the
same at both ends, so it does not depend on which bias is chosen — and the
ceiling reads slightly better only because near-full coverage nearly doubles n.

Against the flat 0.10% assumption the table above uses, the real cost is about
half an R-unit dearer (0.38-0.41R against 0.26R) and the edge shrinks with it:
+0.269 becomes +0.18, and `t` falls from +4.7 to between +2.2 and +3.1. **It
survives. It survives smaller than the flat table said.**

Two limits stand, and neither is small. This prices a fill **at the touch**:
depth beyond the top of book, latency, and a stop-market firing into a fast
move are all uncharged, and a stop fires on roughly half these trades — so it
is a floor on cost and a ceiling on the edge. And it is measured on **recent**
tape, because Binance refuses a time-ranged `aggTrades` search older than about
two days, while the trades priced span two years. Reading one onto the other
assumes a symbol's spread is stable in relative terms — that CRV has always
been dearer than BTC, not that either held a fixed number. That assumption is
untested and is the weakest joint in the chain.

## The cost cannot be read off the candles

The one number the study cannot supply is the reader's real round trip, and
the obvious shortcut — estimate the spread per symbol from the bars already
cached, then charge each trade its own symbol's cost — was tried and does not
work. `research/spread_cost.py` implements Abdi-Ranaldo and Corwin-Schultz and
carries the test that rejects them.

Both are calibrated on equity daily bars, where a bar's range is mostly
bid-ask bounce around a slow efficient price. A crypto perp on a 15-minute bar
is the opposite regime, and the estimators read the volatility instead. The
falsification is `--validate`: a spread belongs to the order book, so the same
symbol must estimate the same whatever bar length it is measured on. The
median comes out 0.107% on M15, 0.098% on H1 and 0.911% on D1, and ETHUSDT
reads 1.420% on the daily against a real quoted spread near half a basis
point. Coverage tells the same story from the other side — the estimator
returns no answer for 49 of 72 symbols on M15, and the ones it fails on are
the liquid ones, so what it does price is a biased sample of the wide tail.

Charged against the trades it prices, it reported the block arm falling from
+0.45 gross to −0.18 net. **That number is not a result and is not quoted
anywhere**: it rests on an estimator that fails its own sanity check, over a
biased subset. The sensitivity table above stands as the right instrument —
it says what the edge *needs* rather than claiming to know what it costs.

Getting the real number needs quotes, not bars: a book snapshot recorded at
each signal, or fills from an account. That is execution, which is out of
scope here, so it belongs in the consumer of the API.

## Position management: one variant is free, the popular one is negative

Every number in this study until now was take-and-stop: a fixed 2R target, a
fixed 1R stop, marked to market at the horizon. Management was never measured,
and "you can only win by protecting" is exactly the kind of claim that never
gets counted against a trial budget. Five variants are now walked candle by
candle alongside the plain one — the path matters, because a trade that arms
a breakeven, returns to entry and *then* runs to target is a winner in the
outcome grid and a scratch under the rule, which is why reading management off
the grid always makes it look free.

Block arm, n=636, each trade charged its own measured cost (the partial pays
1.5 round trips, since it exits twice):

| variant | net R | t | win | scratch | loss |
|---|---|---|---|---|---|
| plain 2R | **+0.187** | +3.1 | 53.3% | 0% | 46.7% |
| breakeven at 0.5R | +0.072 | +1.6 | 34.3% | 45.4% | 20.3% |
| breakeven at 1.0R | +0.097 | +1.9 | 39.9% | 31.0% | 29.1% |
| **breakeven at 1.5R** | **+0.187** | +3.3 | 49.2% | 12.3% | **38.5%** |
| partial half at 1R | **−0.150** | −3.5 | **70.9%** | 0% | 29.1% |
| trailing 1R | +0.150 | +3.0 | 66.0% | 2.4% | 31.6% |

**Early breakeven costs about half the expectation.** Arming at 0.5R or 1R
trades 46.7% full losses for 45% scratches — a much nicer curve to sit
through, at +0.072 instead of +0.187. That is a real trade and a legitimate
one to make knowingly; it is not free, which is how it is usually sold.

**Taking half off at 1R is the worst option available**, and it is the most
popular. It posts the highest win rate in the whole study — 70.9% — and loses
money. The half that exits at 1R pays a full round trip to collect half a
prize, and the exit count is why: 1.5 round trips against everyone else's 1.
This is the cleanest example in this project of a win rate pointing the
opposite way to the payoff.

**Breakeven at 1.5R is the one thing that is free.** Identical expectation to
plain, a marginally better `t`, and it converts 8 points of full losses into
scratches. The reason is arithmetic: at 1.5R the target is only 0.5R away, so
price that gets there usually finishes, and moving the stop rarely ejects a
winner. At 1R half the journey remains and it ejects plenty.

A caution on reading the walk-forward here. Its Sharpe is computed on **gross**
R, and Sharpe rewards low variance, so trailing (6.75) and the partial (6.63)
outrank plain (6.30) there while being worse and negative respectively once
cost is charged. Choosing by that column would pick the only variant that
loses money. The five variants are declared in the rule set so PBO counts them
(now 0.000 over 23 trials, deflated Sharpe 5.89 against an expected max of
1.96), but the ranking that matters is net R.

**Recommendation, such as it is: 2R fixed, optionally with the stop to entry
once 1.5R is reached. Never a partial at 1R.** And note that `be1.5` *ties*
plain rather than beating it — the choice between them is about tolerance for
full losses, not about return.

## Why the VWAP: a Schelling point, not a break-even

The layer's stated thesis is that the VWAP matters because it is a
*population's* break-even — whoever entered since the anchor is underwater
below it, and therefore supply. That thesis has an obvious weak spot: 00:00
UTC is not an event anyone entered on. A structural reversal is. So the rival
reading is that the session VWAP works because it is a **Schelling point** — a
level every participant computes the same way and can therefore expect the
others to be watching — and that what matters is shared observation rather
than shared positioning.

The two make opposite predictions, which makes them separable. Under the
break-even thesis a VWAP anchored at a trend flip should do at least as well;
under the Schelling reading it should do worse, because nobody outside this
system computes it. `--vwap-anchor event` restarts the accumulation at each
non-provisional `CHANGE_OF_CHARACTER` / `CHOCH_FAILED` and changes nothing
else — same detector, same entry, stop, target and placebo.

| anchor | block | placebo | lift | net R | t |
|---|---|---|---|---|---|
| session (calendar) | 52.4% | 29.4% | **+23.0 pp** | +0.269 | +4.7 |
| trend flip (event) | 39.0% | 26.2% | +12.8 pp | −0.128 | −2.3 |

The samples are the same size (676 against 659), so this is not a coverage
artefact. Anchored at a flip the setup loses half its lift and goes net
negative. **The Schelling reading is the one that survives**: this works
against a line the whole market is looking at, and stops working against a
line only this system can draw.

Two things follow, and they point in opposite directions for the roadmap.

The block half of the mechanism is not what changed — the lift is smaller but
still there (+12.8 pp), so an order block tested below *any* running average
contributes something. What collapses is the base rate: reclaiming a bespoke
line is simply a worse event than reclaiming the session VWAP, and the placebo
says so too (26.2% against 29.4%).

And the direction for anything built next is *toward* commonly-watched levels,
not away from them. A bespoke anchor is not a refinement of this setup; it is
a different and worse one. That also removes the last support for the
event-anchor hypothesis this project had been carrying since the H4 work.

*Unexplained, and left that way:* the H4 result — session anchor negative,
weekly anchor strong — was originally explained by accumulation, and that
explanation is dead (see the retraction below). A Schelling reading offers a
candidate — a six-candle session VWAP on H4 hugs price closely enough that
nobody would be watching it — but that is a story, not a measurement, and it
is recorded here as one.

## Liquidity is not an axis

The earlier claim — that the strength
sat in the alts, where the cost assumption was weakest — rested on a
hand-picked list of majors, a researcher's choice sitting exactly where the
conclusion was. With the spread measured per symbol there is an objective
axis, so the split is by spread tercile and each trade is charged its own
cost:

| tercile | median spread | n | hit 2R | placebo | lift | net R | t |
|---|---|---|---|---|---|---|---|
| tight | 0.011% | 164 | 51.8% | 31.0% | +20.8 pp | +0.144 | +1.2 |
| middle | 0.023% | 217 | 55.3% | 29.6% | +25.7 pp | +0.311 | +3.1 |
| wide | 0.044% | 255 | 51.4% | 28.0% | +23.4 pp | +0.109 | +1.2 |

The lift is uniform — 21 to 26 points against a placebo steady at 28-31% — so
the mechanism is not an artefact of thin books. The wide tercile is not the
strong one: same hit rate, *lowest* net, because it pays more cost. The
prediction recorded before the run (present in all three with overlapping
magnitudes, if real) is what happened.

Walk-forward says the same from the other side. Declared as three more rules,
so PBO counts them, each tercile is positive out of sample and **none beats
the undivided rule** — 3.75, 4.46 and 4.61 against 6.30. Splitting by
liquidity subtracts sample and adds nothing, which is what a split with no
information in it looks like. PBO is unchanged at 0.067; the deflated Sharpe
absorbed the extra trials (expected max 1.77 → 1.85, observed 5.53).

**Do not filter this setup by liquidity.**

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
* Deflated Sharpe: observed 5.53 against an expected max of 1.85 from the
  eighteen trials declared (the liquidity terciles below are three of them).

## The EMA(9): a second route into the same observation

Measured 2026-08-22/23 on the M15 sweep, 72 symbols. The question came from a
reader's charts: the fast line is on them too, and a pinbar that rejects *it*
looks like the same event. Four variants were declared and measured; one is now
wired, three are recorded as negatives.

**What the rule became.** Same block, same stop, same target. The trigger widens
from "a pinbar that reclaims the VWAP" to "a pinbar that rejects **either**
shared line", gated on the EMA(9) having crossed the VWAP in the reclaim's
favour. `BlockReclaim.trigger_line` records which fired.

| | n | hit | net R | t |
|---|---|---|---|---|
| VWAP-only trigger | 636 | 52.8% | +0.187 | +3.1 |
| **either line** | 622 | **54.0%** | **+0.225** | **+3.7** |

Better in both halves (search +0.315 against +0.264, holdout +0.038 against
+0.020). Modest — about 20% more per trade — and it earns it **without
discarding trades**: 622 against 636. That is why it is trusted more than the
filters below. A gain from selecting a subset is always suspect; a gain at the
same sample size is a re-timing of the entry, not a survivorship effect.

Walk-forward, 22 folds, the widened rule and both of its sub-routes declared:
**pooled out-of-sample Sharpe 6.72, the best of 26 candidates** (the VWAP-only
trigger pools 6.15), picked on train in three late folds, PBO 0.000, deflated
Sharpe 6.10 against an expected max of 2.01.

### The subset that must not be taken

The gain concentrates in the `both` route — one candle rejecting both lines:
63.5% hit and +0.521 net overall, **68.2% in the search half**. Filtering to it
is the obvious move and it is wrong. Out of sample that subset pools **3.87**
against the whole rule's 6.72, and its holdout is 53.3% on n=30. Declaring it
alongside the rule being defended is what let the procedure price it; leaving it
out of the trial count would have flattered the choice.

Observe `trigger_line`; never filter on it. It is deliberately not drawn on the
chart for the same reason.

### The route that motivated it is rare

A pinbar on the 9 with the VWAP never touched — the charted case — is **28 of
622 trades, 4.5%**, and pools 1.02 out of sample. Not wrong, just uncommon, and
the reason is geometric: inside the tight-stop population the 9 sits *beyond*
the VWAP 97% of the time, so price recovering from the block crosses the average
first and the old trigger already caught it. Over *every* reclaim, with no stop
filter, the 9 is the far level only 61% of the time.

### Three negatives

* **Strict confluence as a filter.** Requiring both lines on the reclaim candle:
  59.8% hit and +0.415 net, but n=34 in the holdout and no advantage there
  (+0.070 against a +0.020 baseline). Not established. Note also that "closed
  beyond the 9" and "reclaimed the 9" are the *same test* on this population
  (Jaccard 0.98), not two — with the 9 beyond the VWAP, clearing one clears the
  other.
* **The pullback hook.** Taking the EMA(9) pullback *after* a VWAP reclaim,
  paired against the reclaim entry on the same 113 episodes: **57.5% → 25.7%**,
  net +0.353 → −0.655. Placebo-grade. The block's advantage is spent at the
  reclaim; waiting for the give-back discards it. The arm was also structurally
  incapable of finding the charted case, since it required a VWAP reclaim pinbar
  to have fired first.
* **The 9 replacing the VWAP.** Block tested and reclaimed against the EMA(9)
  alone: the block still lifts over its own placebo (+8.8pp, and it replicates
  — +8.6 search, +9.1 holdout), but gross is +0.008 and net −0.308. The failure
  is hit rate, not cost: this arm's stop is *wider* (0.48% against 0.38%) and so
  its cost is *lower* (0.316R against 0.408R). **The level does about three
  quarters of the work.**

That last one sharpens the Schelling reading rather than contradicting it.
Shared observation is not sufficient — the line has to **accumulate**. The 9 is
on every chart in the world and does a third of the job; the event-anchored VWAP
likewise lost half its lift by re-basing. Three measurements, one direction:
what matters is how much history the level carries.

### A broken instrument, caught by its own arm

The first `ob-either` implementation re-implemented the surrounding rule instead
of importing it, and took every POI zone kind while emitting one trade per visit
rather than collapsing several blocks resolving on one candle down to the
nearest test. Its VWAP route then measured 33% where the production arm measures
53% **on the same trigger** — impossible as a finding, diagnostic as a bug. The
corrected arm mirrors `detect_block_reclaims` condition for condition and widens
exactly one thing. `docs/` already prescribes one implementation for detector
and study; this is what the drift looks like when it happens.

The period is fixed at **9** because a reader named it. Sweeping 5/9/21 would
turn a pre-registered choice into a search, and the result would then owe a
correction it has not been charged.

```
poetry run python research/vwap_ob_pinbar.py \
    --symbols $(python research/_symbols.py all) --timeframes 15m \
    --limit 25000 --deep --export trades.json
poetry run python research/vwap_walkforward.py --trades trades.json
```

## The window, corrected

Every span this document quoted in days was wrong, in two compounding ways, and
the numbers are restated here rather than edited away.

**The quoted figure was a global range, not a window.** "666 days" and "715
days" are the distance from the earliest trade of *any* symbol to the latest,
and one symbol (EOSUSDT, carrying deeper cached history than the rest) stretched
both. The window a symbol actually contributes is far shorter.

**And the 5m fetch was silently clamped.** `PaginatedFuturesProvider.max_fetch_limit`
is 60 000; the study asked for 75 000. Sixty thousand 5m candles is 208 days,
which is exactly the 204-day median span the trades show.

| | asked | got | median span per symbol |
|---|---|---|---|
| M15 | 25 000 | 25 000 | **253 days** (241–259) |
| M5 | 75 000 | 60 000 | **204 days** (199–207) |

So the M5 study's central design — *same calendar window, so the difference is
the timeframe* — was not achieved. Restricting M15 to each symbol's own M5 date
range puts them on the same dates:

| | n | hit | cost R | net | t |
|---|---|---|---|---|---|
| M15, full window | 622 | 54.0% | 0.405 | +0.225 | +3.7 |
| **M15, on the M5 window** | 417 | 55.4% | 0.397 | **+0.275** | +3.8 |
| M5 | 1351 | 47.1% | 0.687 | **−0.268** | −6.4 |

The conclusion survives — M15 is if anything slightly better on the shorter
dates, so the gap was the timeframe rather than the period. But that is known
*now*; it was asserted before it was checked, which is the part worth recording.
A silent clamp is the failure mode to watch for here: the provider returned
fewer candles than asked and said nothing, and nothing downstream compared the
span it got against the span it wanted.

## Two ideas from a visual backtest, both measured, both negative

Both came from a reader watching charts rather than from the data, which is the
useful direction for a hypothesis to arrive from. Neither survived.

### A VSA climax does not make a better target

The thought: a climax bar is where the tape already changed hands violently, so
a move might travel *to* it and stop. `research/climax_target.py` measures two
readings — a **static** target (the nearest climax level already printed beyond
the entry, known before the trade starts) and a **dynamic** exit (leave on the
close of the first candle that prints an opposing climax). Both are free of
lookahead, and the comparison runs on the subset that *has* a target with the 2R
baseline recomputed there, since dropping the targetless trades would select for
charts that happened to be violent lately.

| M15, n=374 (those with a target) | gross | net | t |
|---|---|---|---|
| **fixed 2R** | +0.583 | **+0.188** | **+2.4** |
| static climax level | +0.503 | +0.108 | +1.1 |
| dynamic climax exit | +0.455 | +0.060 | +0.4 |

M5 repeats it (−0.281 / −0.343 / −0.448 on n=771), and so does each half of both.
Notably the median climax target sits at **2.40R**, so the level is not absurd —
what costs is its spread: p10 at 0.79R gives the edge away, p90 at 7.00R hands
it back. A fixed 2R is the better compromise. That last sentence is a story;
what is measured is that it loses.

### Restricting to the majors is worse, and the reason is the denominator again

| | n | hit | cost R | net |
|---|---|---|---|---|
| M15, all 70 symbols | 622 | 54.0% | 0.405 | **+0.225** |
| M15, 7 majors only | 42 | 50.0% | **0.536** | −0.036 |
| M5, all 70 | 1351 | 47.1% | 0.687 | −0.268 |
| M5, 7 majors only | 82 | 52.4% | **1.041** | −0.468 |

The majors cost **more** in R, not less: less volatility means a smaller stop as
a fraction of price, so the same fee takes a larger share of it — on M5 it
passes a whole R per trade. The setup also barely fires there, 42 trades over
roughly eight months per symbol.

Per symbol the operable population is 3 to 19 trades, which is not a sample.
BTC shows 71.4% on M15 with a 95% interval of **35.9–91.8%**; ETH shows 33.3% on
M15 and 78.6% on M5; BNB shows 80.0% on five trades. Those are the same symbols
swapping places, which is what noise looks like. Loosen the `r_atr` gate until
each symbol has 86–226 trades and all seven land between 12.5% and 25.3% on both
timeframes, net negative throughout — the same finding from the other side, that
where there is sample there is no setup.

Which carries a practical consequence worth stating plainly: **this setup is not
an asset's, it is a rarity that needs many symbols to accumulate.** 622 M15
entries come from 70 symbols — nine per symbol over eight months. Trading only
the majors means waiting weeks between signals and never accumulating enough of
them to tell working from unlucky. Watching the whole list is not statistical
fussiness; it is what makes the setup operable at all.

With n=42 the claim is not "majors are worse"; it is that **there is no evidence
they are better**, the point estimate is worse, and a mechanism explains why.
Note also the shape of the question: picking "the strong assets" by hand is the
same move as the retracted "the strength is in the alts" claim, which the
objective redo by measured-spread tercile showed to be uniform. Liquidity is not
an axis, and choosing the axis after seeing the charts is how it appears to be
one.

## The trigger candle: three definitions, and the union wins

A reader showed a 03 Aug M15 entry the layer had not taken and named why: "a
level-2 pinbar, bigger body but a good tail". The candle measures 43% body, 53%
tail, 4% nose — it fails the shipped rule on the **body** by eight points while
passing on the tail, and fifteen minutes earlier that same rule fired a *losing*
trade in the opposite direction on the same chart.

Unlike the five attempts above, this is not a new variable. It is a **constant
somebody chose**, and it decides whether a trade exists at all rather than
whether it is good.

Three definitions, kept apart rather than collapsed into one threshold, because
they are different candles wearing one name:

| grade | tail | body | nose |
|---|---|---|---|
| `legacy` (what shipped) | ≥ 50% | ≤ 35% | **unconstrained** |
| `l1` (the golden rule) | ≥ 65% | — | ≤ 15% |
| `l2` | ≥ 20% | ≥ 1/3 | ≤ 15% |

Worth noticing what that table says about the shipped rule: it is **neither** of
the other two. Looser than the golden rule on the tail, and silent about the
nose — the far wick, which is the part saying price was pushed back the other
way before the close.

### The union, measured

| M15, r_atr ≤ 1.0 | n | hit | net | t |
|---|---|---|---|---|
| legacy trigger (shipped) | 624 | 54.0% | +0.226 | +3.8 |
| **union of the three** | **891** | 54.9% | **+0.238** | **+4.7** |

**43% more trades at the same hit rate**, which matters more than the net here:
this setup's binding constraint has always been sparsity, nine entries per
symbol over eight months.

Walk-forward over 22 folds with each grade declared beside the union:

| rule | pooled OOS Sharpe |
|---|---|
| **`ob-pin2\|r<=1.0`** | **7.90** |
| `ob-pin2\|r<=1.0\|legacy` | 7.24 |
| `ob-either\|r<=1.0` (shipped) | 6.75 |
| `ob-pin2\|r<=1.0\|l2only` | 4.56 |
| `ob-pin2\|r<=1.0\|hasl1` | 4.25 |

Best of 30 declared candidates, PBO 0.000, deflated Sharpe 7.25 against an
expected max of 2.07. **The union beats every one of its own subsets**, including
the legacy-only one — which is the cleanest form this answer could take, and the
third time in this document that the whole rule outperforms the piece of it that
looked best in the search half. `l2` alone measures +0.142 with a holdout of
−0.017; taking it on its own is exactly the mistake the subsets are declared to
price.

`BlockReclaim.pinbar_grade` records which definitions fired. Observe it; do not
filter on it. M5 improves too (−0.270 to −0.197) and stays negative, so nothing
there changes.

## Where the stop goes, and five attempts to recover the discarded 95%

The stop is the extreme of the whole test window — the lowest low (highest high)
between the visit's first candle and the reclaim candle inclusive — with the
entry at the reclaim's close and R the distance between them. No buffer: it sits
on the tip of the wick.

A reader marking that trade by hand put the stop at the same price the detector
did, to a tenth of a point: 77 423.8 against a hand-drawn ~77 425, on an entry
of 77 254.3 against ~77 250. **The placement is not in question**; what follows
is whether anything around it can be improved.

### Nothing about the stop can be improved

Priced on the operable population, the same trades under five stop placements:

| stop | median R% | net | t |
|---|---|---|---|
| **the test extreme (current)** | 0.39% | **+0.226** | **+3.8** |
| extreme + 0.25 ATR | 0.52% | +0.187 | +3.1 |
| extreme + 0.5 ATR | 0.65% | +0.163 | +2.8 |
| extreme + 1.0 ATR | 0.91% | +0.075 | +1.4 |
| the block's far edge | 0.99% | −0.097 | −1.7 |

Every tick of buffer costs, and the degradation is **monotonic**, which is what
makes it clean: not one bad setting but a bad direction. The motivating worry —
that a stop parked on a visible extreme sits exactly where this project's own
sweep layer says liquidity rests — does not survive. What the table establishes
precisely is that the trade-off does not pay: a buffer both avoids false stops
*and* pushes 2R further away, and the second effect dominates. It does not show
that stops are never hunted.

### And nothing recovers the discarded trades

`r_atr ≤ 1.0` discards 95% of reclaims — 159 of BTC's 168 on 5m. Three
alternative measures were emitted and cut, chosen because each has a mechanism
rather than because it looked good:

* **distance to the block edge** rather than to the wick, since the layer's
  thesis is about the two *levels* being close while `r_atr` measures the tip of
  a spike. Over 1198 M15 reclaims the two run at a median ratio of 0.69 and
  under 0.17 a tenth of the time, and 27% of the discarded sit within one ATR of
  the block — so the difference is real and there was room for it to matter.
* **verticality of the approach** (ATR travelled per candle into the test), from
  the reader's example arriving on a near-vertical run.
* **rejection fraction** of the extreme candle.

| filter, whole population | n | hit | net | t |
|---|---|---|---|---|
| **`r_atr ≤ 1.0` (standing rule)** | 624 | **54.0%** | **+0.226** | +3.8 |
| `block_atr ≤ 1.0` | 2547 | 31.8% | +0.023 | +0.9 |
| verticality ≥ 1.0 ATR/candle | 1234 | 19.9% | −0.037 | −1.2 |
| rejection ≥ 0.7 | 3544 | 21.3% | −0.081 | −4.1 |

Inside the discarded set the block distance is the only one that moves anything
(25.5% against the discarded baseline's 19.1%) and it stays negative.
**Verticality measures *worse* than the baseline** (17.4%), so a vertical run
into a block continues, if anything, rather than reverses — wrong in direction,
not merely in size.

Five new trials, all declared before running, five failures, and nothing in
production changed. The standing rule has now outlasted twenty-three measured
alternatives across exits, management, triggers, filters and stops; it is a
defended choice rather than an inherited one.

What remains open is the example itself. The reader's 9.83R trade is separated
by none of these — `r_atr` 1.87 and `block_atr` 1.44 against a 1.0 gate — so
whatever distinguished it is not a distance, not the approach's shape, and not
the rejection. That is a map of what is missing, not a conclusion about the
trade.

## Letting the winners run: the excursion is there and unreachable

The question came from a chart rather than from the data, which is why it was
worth answering. A reader showed a 5m reclaim that ran **9.83R** and never came
near its stop, and asked whether the measurement was right. Two things followed.

**The detector found that trade exactly** — same minute, bearish, `both` route,
fresh block, entry 77254.3 against a 77423.8 test extreme. But its `r_atr` was
**1.87**, and every table in this document caps that at 1.0, so it had never
been counted in anything reported here. That was a real gap in what was being
shown, and closing it is what the rest of this section does.

### The band the example belongs to loses

M5, `ob-either`, each trade charged its own measured cost:

| r_atr band | n | hit | cost R | gross @2R |
|---|---|---|---|---|
| ≤ 1.0 | 1351 | **47.1%** | 0.687 | **+0.418** |
| 1.0–1.5 | 2127 | 32.6% | 0.393 | +0.039 |
| **1.5–2.0** | **2519** | **26.8%** | 0.283 | **−0.020** |
| 2.0–3.0 | 4443 | 18.7% | 0.206 | −0.017 |
| > 3.0 | 5852 | 8.4% | 0.128 | −0.026 |

A 2:1 payoff breaks even at 33.3% before any cost. The wider bands sit under it
and their gross is negative, in both halves. The cost *does* fall as the stop
widens — the geometry works exactly as predicted, 0.283R against 0.687R — but
the hit rate falls faster. **The `r_atr` gate is not hiding an edge; it is
separating the only band that has one.**

### No exit family rescues either timeframe

`research/runner_exits.py` prices every dated entry under eleven rules — fixed
targets out to 10R, and four uncapped trailing stops — plus the MFE
distribution, which is not an exit anyone can take and is reported only as the
ceiling every real rule is measured against. Adverse extremes are credited
first within a candle and the trailing level is the one *previous* candles
earned, so both choices bias the runner columns down.

**The fat tail is in the tight band, not the wide one.** On M5 at 40 candles the
`r_atr ≤ 1.0` band offers a median MFE of **3.89R** (p90 10.75R); the 1.5–2.0
band offers **1.37R** (p90 4.04R), and it falls to 1.00R and 0.57R above that.
Most trades in the wide bands never offer even 1.5R. The reader's 9.83R was not
a typical member of its band; it was its extreme.

Every one of the eleven rules is net negative in every M5 band, and in the wide
bands the runners make it **worse** (fixed 10R at −0.094 against fixed 2R's
−0.013): more trades that were ahead give it all back.

The cleanest demonstration is what the horizon does. Stretching it from 40
candles to 200 raises the tight band's median MFE from **3.89R to 8.38R** while
no rule's payoff improves at all. **The excursion is there and it is
unreachable** — price goes that far, but it passes through the stop on the way.
More time collects nothing; it only offers more chances to be stopped.

### A Chandelier trail does not help, and the reason is the gate

An ATR-anchored trail is a genuinely different family from the R-unit trails
above: it hangs `N × ATR` under the running high with the **ATR recomputed every
candle**, so it widens when a leg turns violent instead of freezing its distance
at entry.

| M15, n=622 | net | t |
|---|---|---|
| **fixed 2R** | **+0.225** | **+3.7** |
| chandelier 22 / 1.5×ATR | +0.180 | +2.3 |
| chandelier 22 / 2.0×ATR | +0.094 | +1.0 |
| chandelier 22 / 3.0×ATR | +0.068 | +0.6 |

M5's best is 22/2.0 at −0.239, against the fixed 3R's −0.216. Nothing is
rescued.

Note the ordering: **the wider the multiple, the worse** — the reverse of the
motivating intuition. It follows from the gate. `r_atr ≤ 1.0` means R is at most
one ATR *by construction*, so a 3×ATR chandelier sits 3R or more under the peak
and the trade must travel past 3R merely to bring the stop to breakeven. That is
why `chand22/3.0` (+0.068) measures almost exactly like `trail3.0/2.0` (+0.057):
the same looseness reached by two different routes. The mechanism the idea rests
on is real; it just cannot apply to a setup that has already selected for tight
stops.

### On M15 the existing 2R survives a much wider family

| rule | gross | net | t |
|---|---|---|---|
| **fixed 2R** | +0.630 | **+0.225** | **+3.7** |
| fixed 3R | +0.570 | +0.164 | +2.1 |
| fixed 6R | +0.599 | +0.193 | +1.8 |
| fixed 10R | +0.566 | +0.161 | +1.3 |
| trail 2R/1R | +0.600 | +0.194 | +3.0 |
| trail 3R/2R | +0.462 | +0.057 | +0.7 |

M15's median MFE is 4.00R too, and 2R still wins — holding past it converts
winners into scratches more often than it collects tails. The exit was
previously tested only against 1R–3R; it now stands against targets to 10R and
uncapped trailing, and the `t` separates it clearly from its neighbours.

### What cannot be concluded

That the reader's read was luck. A 9.83R trade in a band that loses on average
may well have carried something the band does not capture — the block's
freshness, the hour, the displacement behind it. What the measurement
establishes is narrower and worth stating exactly: **`r_atr` alone does not
separate it, and no variable currently emitted does.** Hunting for one by
collecting the examples that worked is how a false finding gets built, so any
such search owes a control matched the way every claim here is.

## M5: the mechanism confirms, the arithmetic refuses

Measured 2026-08-22, 72 symbols on 5m, asking for 75 000 candles so as to cover
the same calendar window as the 15m run and read a difference as the timeframe
rather than as the period. **That is not what happened**, and the correction is
below under "The window, corrected": the request was silently clamped to the
provider's 60 000-candle cap, leaving 5m on ~204 days per symbol against 15m's
~253. Re-run with the windows actually matched, the comparison holds — 15m
returns **+0.275** on the 5m's own dates against 5m's −0.268 — so the conclusion
stands and the stated design did not. Same rule, same
`app.block_reclaim` detector, not one parameter retuned. 70 of 72 symbols
entered (`LRCUSDT` took its whole timeframe down with a degenerate
`ConsolidationRange`, the `EGLD H1` failure mode again; `MKRUSDT` produced no
setup).

The two halves of the result point opposite ways, and separating them is the
finding.

**The mechanism passes, more cleanly than at M15.** Block 44.5% against a
placebo's 24.4% — a +20.0pp lift against M15's +23.0pp — and it replicates
across the frozen hash split: +20.3pp on search (n=1104), **+19.1pp on holdout
(n=463)**. That is stronger evidence than M15 gave, where the holdout's `t` of
+1.2 could not reject zero on its own. The gross exit grid is positive across
every target with the same broad plateau: +0.262 / +0.281 / +0.341 / +0.341 /
+0.376 at 1.0 → 3.0R.

**The trade fails, at every target and every management variant.** With each
trade charged its own measured cost (n=1504, 0.132% of price):

| target | hit | gross | cost R | net | t |
|---|---|---|---|---|---|
| 1.0R | 63.1% | +0.262 | 0.670 | **−0.408** | −15.1 |
| 1.5R | 51.2% | +0.281 | 0.670 | **−0.389** | −11.8 |
| 2.0R | 44.5% | +0.341 | 0.670 | **−0.329** | −8.4 |
| 2.5R | 38.0% | +0.341 | 0.670 | **−0.329** | −7.5 |
| 3.0R | 33.9% | +0.376 | 0.670 | **−0.294** | −6.0 |

**The cost column does not vary, and that is definitional, not rounding.** R is
the entry→stop distance, fixed at entry; the target decides where the trade goes,
never what the round trip costs relative to the risk taken. So the target is not
a lever on cost — it moves only the gross, which would have to nearly double.

The clearest framing is the hit rate the payoff demands:

| target | actual | needed, no cost | needed, with cost | short by |
|---|---|---|---|---|
| 2.0R | 44.5% | 33.3% | 55.7% | 11.2pp |
| 2.5R | 38.0% | 28.6% | 47.7% | 9.7pp |
| 3.0R | 33.9% | 25.0% | 41.8% | 7.9pp |

Without cost the setup clears the bar comfortably at every target. The 0.670R
lifts the bar across the line, and the trade lands 8-20pp under it.

### Why: the stop shrinks, the fee does not

The round trip costs 0.131% of price at M15 and **0.132% at M5** — the same. But
M5's stop is far tighter, so the identical fee eats **1.7× more of the risk
unit**: 0.670R against 0.40R. A tight stop looks like an advantage, and is one in
absolute risk; but someone reasoning in R never sees the fee change size, because
as a fraction of price it does not. The shrinking happens on the other side of the
division and is invisible on the chart. **Dropping a timeframe is not free, and the
price is in the denominator.**

To break even the round trip would have to cost 0.067% — 2.2bp of fee per side
after the measured median spread, against Binance USDT-M's standard 5bp. That is
an account tier, not an adjustment.

### Management does not rescue it either

Measured, not deduced (the first draft of this section argued it arithmetically,
which in a project where a 1R partial once fooled us with a 70.9% hit rate was
exactly the wrong habit). Block arm, n=1504, each variant charged its own cost:

| variant | win | scratch | loss | gross | net | t |
|---|---|---|---|---|---|---|
| **plain 2R** | 44.8% | 0.1% | 55.1% | +0.341 | **−0.329** | −8.4 |
| breakeven 0.5R | 24.5% | 44.3% | 31.1% | +0.180 | −0.490 | −16.5 |
| breakeven 1.0R | 28.9% | 34.2% | 36.9% | +0.208 | −0.462 | −14.4 |
| breakeven 1.5R | 41.0% | 10.2% | 48.7% | +0.331 | −0.339 | −9.1 |
| partial ½ at 1R | 63.1% | 0.0% | 36.9% | +0.235 | **−0.770** | −25.6 |
| trailing 1R | 53.5% | 2.7% | 43.8% | +0.307 | −0.363 | −10.7 |

The plain fixed target is the best of the six. At M15 breakeven-at-1.5R *tied*
it; here it already loses, because a dearer round trip makes each scratch exit
relatively more expensive. And the partial repeats the M15 trap at worse scale —
the table's highest hit rate and its worst result, paying 1.5 round trips on a
cost already worth 0.670R, over 1R of cost to collect half a prize.

### What it changes

The finding is **not** "it does not work at M5", and the distinction sets the
roadmap. Looking for more events by dropping timeframe is looking in the wrong
place: every step down multiplies the trades and shrinks the R that pays for
them. The open direction is the opposite one — the same mechanism where R is
wide, which is where the unexplained H4 result already pointed.

One asymmetry is left open and not claimed: at M5 the short side lifts +22.6pp
against the long side's +16.8pp, and unlike M15 the placebo does *not* reproduce
it. One sample, in a setup that loses money regardless. Recorded as a loose
thread.

```
poetry run python research/vwap_ob_pinbar.py \
    --symbols $(python research/_symbols.py all) --timeframes 5m \
    --limit 75000 --deep --export trades5m.json
poetry run python research/vwap_exit_grid.py --trades trades5m.json \
    --max-r-atr 1.0 --spreads research/measured_spreads.json
```

The visual record is `block_reclaim_m5_artifact.html`, published at
<https://claude.ai/code/artifact/9666b70f-79f7-409e-a017-228e9def4897>.

## The r_atr gate is the setup, and the timeframe ladder (2026-08-23)

The gate discards ~93% of triggers, and the natural complaint — it must be
throwing away good trades — was measured. It is not. On the `ob-pin2`
population, deciles of `r_atr` show a **cliff, not a slope**: decile 1
(r_atr ≤ ~1.1) nets +0.140 on M15 while every other decile is negative, at
2R, 1.5R and 3R alike. Nothing recovers the discarded population: within
`r_atr > 1.0`, the pinbar grades measure identical (42.3–42.6% hit, all
negative net), the trigger line doesn't separate, and the 1.0–1.5 band with a
golden-tail pinbar is still −0.072. A cutoff sweep shows the shipped `≤ 1.0`
near-optimal on total net R for M15 (939 × +0.233 ≈ +219R; widening to 1.25
doubles the sample and drops the total). The 42% of discarded trades that do
reach 2R are what a visual backtest sees; the 58% that stop out pay for them.

The same grid run at M30, H1 and H4 (71 symbols, `--limit 25000`, spans
512d / 1031d / 2124d median per symbol) turns the cost finding into a ladder —
the mechanism's gross edge is roughly constant (decile 1 gross ≈ +0.2 to
+0.5 everywhere) while the cost falls with the timeframe, so the net rises
monotonically:

| TF | gated n | hit 2R | cost/trade | net/trade |
|---|---|---|---|---|
| M5 | 2058 | 51.8% | 0.75 | −0.194 |
| M15 | 939 | 55.1% | 0.41 | +0.233 |
| M30 | 1124 | 44.3% | 0.25 | +0.075 |
| H1 | 1850 | 39.8% | 0.15 | +0.039 |
| H4 | 419 | 54.4% | 0.07 | +0.565 |

**H4 is where the gate can be widened.** Full-sample it is net-positive even
ungated (+0.074); walk-forward over 58 folds (2019→2026, purge 7d) gives PBO
0.133 with `ob-pin2|r<=1.0` pooled OOS SR 2.34 and the pre-declared wider
tiers holding (`r<=1.5` 1.96, `r<=2.0` 1.93, ungated 1.96 with the family's
highest mean daily R). But the matched recent window (last 253 days, the
M15 study's span) corrects the ungated claim: **no gate at all is −0.029
there — the ungated positive was 2020–21 regime.** What survives both samples
is the widened cap: `r_atr ≤ 1.5` nets +0.128 recent / +0.264 full, ~17
trades/month across the universe, cost 0.05–0.07R. (Frequency is per
timeframe's own clock: the gated intraday tiers all fire ~36–39/month
universe-wide — M15 39, M30 36, H1 38 — about 0.5/month per symbol, which is
why a watchlist of a few symbols reads as "almost never"; gated H4 is ~5.)
`≤ 2.0` matches its net with double the exposure; `≤ 1.5` is the recorded
choice. M15 `≤ 1.0` remains the best per trade; the two are the same rule on
two clocks. M30 and H1 are thin-positive inside the gate only — alive, not
worth a dedicated push.

Walk-forward Sharpe here is gross (the known trap); on H4 the cost is
0.03–0.07R, which flips no sign. The wider tiers were declared in
`research/vwap_walkforward.py` before the run, so PBO prices them.

### The accumulation filter: the line has to be somebody's break-even

The fourth confirmation of one thesis, and the first that becomes an
operable cut. The thesis has three prior sightings: the H4 confluence lift
lived in the *weekly* VWAP, not the session one; the EMA(9) route refined the
Schelling reading to "the line must accumulate"; and `vwap_candles` is
documented on the domain model as a monotone lift. The cut (declared in
`vwap_walkforward.py` before the confirming run, 2026-08-23, alongside three
siblings so PBO priced the family):

**M15, inside the gate: require `vwap_candles >= 15`** — do not take a
reclaim against a session VWAP younger than ~15 candles (~4h), because a
barely-started average is nobody's break-even yet.

- Walk-forward: pooled OOS Sharpe **8.47 vs 7.90** for the plain gate — the
  best of 40 declared rules, PBO 0.000 — and mean daily R *rises* (0.371 vs
  0.352) while dropping a third of the trades: the kept trades more than pay
  for the cut ones.
- Symbol holdout: 62.4% search / 53.6% holdout against the base's
  57.5%/50.6% — the lift shrinks but survives both halves.
- Operationally: gated M15 hit ~55% → ~59%, ~39 → ~26 trades/month
  universe-wide, monthly R unchanged or better.

The three siblings are negative findings and are recorded as such:
`ema_slope_with` (63/53 in the in-sample slice) collapsed to OOS 5.74 —
another pretty cut that died in the rite; the `vwap>=15 + slope` combination
is the `both`-route subset trap again (5.40); `revisit` pools at 7.40, at the
base — consistent with the freshness reversal staying an observation, not a
filter. **On H4 none of the four improves the plain gate** (all pool below
2.34): the timeframe is already scarce, and any further cut thins the daily
series past what it returns. H4 stays as it is.

Like every threshold in this layer, `vwap_candles` is emitted, not enforced:
the field rides on every `BlockReclaim`, and the 15-candle floor is the
reader's cut with the measurement above behind it.

### The H4 target grid, and why the target stays 2R

The full 1R–3R grid at both caps, both windows (net of measured cost):

| gate ≤ 1.0 | hit full | net full | hit 253d | net 253d |
|---|---|---|---|---|
| 1R | 72.6% | +0.382 | 79.5% | +0.485 |
| 2R | 54.4% | +0.565 | 46.2% | +0.280 |
| 2.5R | 49.2% | +0.643 | 38.5% | +0.241 |
| 3R | 43.7% | +0.655 | 33.3% | +0.229 |

The two windows disagree about the target: full-sample the net rises with
the target (3R best), while the recent 253-day window (n=39) falls with it
(1R best). A disagreement where one side has 39 trades is sample noise, not
regime — and picking the target off the better cell of this table is the
target-overfit the climax study already priced. Everything from 1.5R to 3R
is net-positive in every slice; nothing dominates both windows. **The target
stays 2R**: never the worst cell anywhere, and the only one that has been
through the walk-forward and the eleven exit rules. 2.5R was considered and
declined for exactly this reason (recorded 2026-08-23); it becomes a
question again only if it wins in *both* windows on a larger recent sample.
At the ≤1.5 cap the same grid is flatter (3R best in both windows, +0.288 /
+0.200) but the spread is within noise of 2R there too.

The frequency/hit-rate pairing is one or the other, not both: **~5
trades/month at ~54% (gate ≤ 1.0), or ~17/month at ~40% (cap ≤ 1.5)** —
total monthly net R is comparable (~1.4R vs ~2.2R recent, at 2R); the choice
is about cadence and drawdown tolerance, not strength.


## What this does not establish

**That a reader's own fill matches the measured one.** The round trip is no
longer assumed — it is counted off the tape, and the setup survives it at both
bounds (see "What it costs, measured"). But that measurement prices a fill at
the touch on recent tape, so depth, latency and a stop-market in a fast move
are still uncharged, and the reader's fee tier — which dominates the cost — is
theirs, not the study's. Nothing in this layer knows any of that, which is one
reason it names no entry, size, or target.

*Superseded:* this section previously said the edge died at 0.15% on majors
and that its strength sat in the alts where cost was least knowable. Both
halves were wrong. The cost is dominated by the flat fee rather than by the
spread, and the lift is uniform across spread terciles.

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
