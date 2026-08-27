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

> **Lowered to 4 on 2026-08-25** (H4 raised from 1 to 2). The floor was right
> about *what* to drop and wrong about *how much*; see "The floor was aimed at
> the re-anchor candle" below.

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


## The paper journal: measuring the one cost the study assumes

Every net figure here is measured against an entry at the **trigger candle's
close** -- a price that has already happened by the time anything can act on
it. Live, the first available price is the next one, and the difference is the
only cost in this study that is assumed rather than observed. It matters most
exactly where the edge is thinnest: M15 nets +0.23R on a 0.41R cost, so a
persistent 0.05R of slippage is a fifth of the edge.

`app/paper_journal.py` + `app/paper_runner.py` record it. Two idempotent
passes, cron-able: `record_decisions` reads the screener, keeps rows passing
the operating gates (M15 `r_atr <= 1.0` and `vwap_candles >= 4`; H4 the gate
plus `vwap_candles >= 2`; M30/H1 the gate alone), and journals each with both prices -- the close the study assumed
and the tape's price at the moment of recording -- storing the gap in percent
and, the figure that decides, **in R**. `resolve_open` settles each against
later candles at 2R, the stop, or the 40-candle horizon, crediting the stop
when a candle spans both (the study's own conservative attribution), and
measures `realized_r` from the **observed** price, so the journal's R already
carries the slippage the study could not see.

It records and never orders: no credentials, no order placement, public data
only. Execution and position management stay out of scope; what is in scope is
the measurement.

One design error is worth recording, because the journal's first live pass
found it: reading the screener's whole recent window and pricing a row against
the tape *now* logged a 1.6R "slippage" on a signal that had fired three hours
earlier. That is the cost of chasing, not of slippage. `MAX_DECISION_AGE_CANDLES
= 1` bounds a decision to a trigger that has just closed.

### The request budget, learned by getting banned

The screener's first cron run got this IP banned by Binance (HTTP 418,
`-1003`). Three causes, all in the scan's design, all fixed:

1. **The retry made it worse.** ccxt's `DDoSProtection` subclasses
   `NetworkError`, which is exactly what `retry_with_backoff` was told to
   retry -- so 284 jobs each retried into a live ban. Bans and rate-limit
   rejections are now `DataProviderBannedError`, never retried
   (`retry._NEVER_RETRY`, whatever the caller passes), and one of them aborts
   the whole pass instead of letting the rest of the jobs spend a budget that
   is already gone.
2. **No cache on the library path.** `api/routes/screener.py` cached its
   units; `app.screener.load_screen`, which the journal calls, did not, so
   every pass refetched the universe. It now has its own lock-guarded
   per-(symbol, timeframe) cache with the same timeframe-proportional TTLs.
   (`app` may not import `api`, so the cache is local -- and the lock matters
   here, where the scan pool reads it from several threads.)
3. **The requests were priced wrong.** Binance charges klines by size: up to
   500 candles is weight 2, above that weight 5. At 600 candles a pass spent
   1420 of the 2400/min IP budget in one burst; at 300 it spends 568, and 300
   still covers the POI queue and several VWAP accumulations on every screened
   timeframe. Pool workers dropped 8 → 3, since ccxt's rate limiter is
   per-instance and not thread-aware -- the pool size *is* the burst size.

Worth stating plainly because it generalises past this feature: a scan that
fans out across a universe is a request budget first and an algorithm second,
and retrying a rate limit is how a rate limit becomes a ban.

### Departure: a block price never left is not a level (negative)

Read off a chart: a block tall enough to swallow the range is touched by every
candle, so price should have to *work clear* of the box after it forms and then
come back -- a test of a level, not a stretch of chop inside a region. The
instrument is in `research/vwap_ob_pinbar.py`: `departure_atr` (the furthest
price got wholly clear of the box between its creation and the test, in local
ATR), `departure_candles`, `block_age_candles`.

The mechanism is real and the filter is not. Inside the gate, the trades where
price **never** got clear are 1-4% of the population: 38/935 on M15, 26/1123 on
M30, 6/419 on H4. They measure worse on the coarse rungs (M30 −0.165, H4
−0.116) and identically to the rest on M15 (+0.219 against +0.230) -- and M15
is where the sample is largest. Out of sample nothing survives: against the
plain gate's pooled OOS Sharpe of 7.89, `dep > 0` is 7.77, `dep >= 0.5 ATR`
7.59, and combining it with the accumulation floor (7.98) is *worse* than that
floor alone (8.44).

Why it is already handled: with price stuck inside a tall box, the test extreme
sits far from the reclaim, so `r_atr` blows past 1.0 and the gate drops the
trade for a different reason. The filter that exists is doing this filter's
work without naming it, which is why only 1-4% remain to be caught.

Three related shapes measured in the same pass, all recorded and none admitted:
block **height** goes the *opposite* way to intuition (taller blocks measure
better on all four rungs -- thin ones are outright negative on M30), and a
**shorter approach** measures better on all three checked (M15 <3 ATR: +0.431
against +0.030). Both are in-sample slices of the kind this study has been
fooled by repeatedly, and neither has been through the rite.

## Live and replay: does a reclaim survive being read again?

The study runs the detector **once** over a finished series; a live reader runs
it on every closed candle. They need not agree, and a case found by the paper
journal showed they sometimes do not: two GALAUSDT M30 reclaims recorded live
on 2026-08-23 were gone from the same series read a few hours later. The cause
is visit merging -- a visit absorbs later touches across `MERGE_GAP_CANDLES`,
and the detector emits one reclaim per visit *after* the visit ends, so a visit
that keeps growing swallows a trigger that had already fired. The GALA block is
the pathological shape for this: 20.8% of price tall, so price stays "inside"
it for days and the visit never closes.

`research/reclaim_stability.py` measures how common that is by replaying the
detector over growing prefixes and comparing what was ever emitted live
(non-provisionally) against the final whole-series read. Across 12 symbols ×
M15/M30/H4, 2000 candles each:

| | count | share |
|---|---|---|
| emitted live | 725 | — |
| survive the final read | 678 | **93.5%** |
| vanish | 47 | 6.5% |
| **in the final read but never live** | **0** | **0.0%** |

The failure that would have invalidated the study did not happen: the "extra"
column is zero in all 36 combinations, so no measured trade is one a live
reader could not have taken. The error is omission only -- the measured
population is an honest, slightly smaller subset of the live one, which makes
every net figure mildly conservative rather than wrong.

M30 is the least stable rung (23 of the 47), and the most liquid majors barely
show it (ETH: zero across all three). The paper journal will therefore record
roughly one decision in fifteen that the study never measured -- which is the
kind of discrepancy it exists to expose, not a defect in it.

The fix belongs in the visit rule (a visit should end when price leaves the
block, and a box tall enough to swallow the range is not a level), and it
changes the measured object -- so it needs the study run again, per
**Changing the rule** below.

### Anchoring the trigger on the visit's start (negative, and instructive)

The obvious fix for the 6.5% instability: a visit's *end* moves as candles
arrive, its *start* does not, so search the trigger from the start. It is
implemented as `detect_block_reclaims(scan_from_visit_start=...)` and the
`ob-pin2s` research arm, and it does exactly what it promised on stability --
**100% of live reclaims survive the final read, zero vanish across all 36
symbol/timeframe combinations**, against 93.5% for the shipped anchor.

And it destroys the reading:

| | M15 shipped | M15 from-start | H4 shipped | H4 from-start |
|---|---|---|---|---|
| trades | 627 | 1473 | 419 | 870 |
| hit 2R | 59.3% | 29.1% | 54.4% | 35.6% |
| net | **+0.344** | **−0.628** | **+0.565** | **−0.015** |
| holdout hit | 53.6% | 27.7% | 60.0% | 36.9% |

The population **doubles** rather than shrinking, which names the cause: from
the visit's start the trigger is searched *while the test is still happening*,
so a pinbar fires with price still working into the block. The visit's end is
not an implementation detail -- it is the rule's semantics, "the test is
over", and the whole premise is a block tested **and then handed back**.

So the instability is intrinsic to the reading rather than a defect in it:
knowing the test finished requires waiting, and waiting is what lets the visit
keep growing. It is also harmless in the direction that matters -- extras are
zero, so the measured population is a conservative subset, never an invented
one. The flag stays off, kept for the record.

This also weakens the other candidate fix (capping block height): taller blocks
already measure *better* on all four rungs, so that cap would charge a price
too.

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


### The floor was aimed at the re-anchor candle (2026-08-25)

The accumulation floor was measured as a monotone lift and adopted at 15. What
it was actually removing only became visible by reading one stopped trade on a
chart: **AVAXUSDT M15, 2026-08-17 21:00 UTC-3**, entry 6.345, block 6.363-6.383.
Two things were wrong with it, and the second is general.

The "test" of the block was the 20:30 candle grazing 6.367 — four thousandths
into the bottom edge of a two-cent block, 2% penetration. And the entry candle
sat exactly on **00:00 UTC**, where the session VWAP re-anchors: the VWAP jumped
from 6.3316 to 6.3493 in one candle, `vwap_candles = 1`. Price did not lose the
VWAP; the VWAP moved across price. The trigger fired on the clock.

Measured, the defect is large and it replicates: entries with `vwap_candles = 1`
are **15% of the whole M15 population** and its worst subgroup — 34.2%/27.0% hit
rate, net −0.194/−0.477, the only negative rule among twelve declared in
`research/vwap_age_walkforward.py`, in both symbol halves.

So the floor was right. But 15 also discarded the **8-14 band, the best of all**
(65.5%/60.9% hit rate, +0.692/+0.584 net). Walk-forward over eight thresholds
shows a **plateau from 2 to 12** with a drop at 20 — the exact number does not
matter, and 15 sat just past the plateau's edge:

| | search SR | search R | holdout SR | holdout R |
|---|---|---|---|---|
| no floor | 6.37 | +180.0 | 2.93 | +70.0 |
| `>= 4` | **7.31** | **+206.4** | **4.85** | **+107.3** |
| `>= 15` | 7.08 | +197.6 | 4.67 | +99.1 |
| `>= 20` | 6.47 | +180.0 | 3.79 | +77.8 |
| `<= 3` (the defect) | −0.45 | −9.2 | −1.88 | −26.9 |

PBO 0.267/0.333 — higher than this project's usual 0.000, and expected: twelve
neighbouring thresholds are nearly the same rule, so they rank-swap easily. The
plateau is the evidence, not the peak.

**The floor is in candles, but what it measures is a fraction of the anchor
period** — and that period is per timeframe (`_VWAP_ANCHOR_PERIOD`: SESSION
intraday, WEEK on H4). Fifteen candles is ~4h of a 96-candle M15 day and **2.5
days of a 42-candle H4 week**. On H4 the defect is mild (its `vwap <= 3` bucket
measures *positive*) and any high floor is pure loss, so H4 drops only the
re-anchor candle itself. Expressing the floor as a fraction of the anchor period
rather than a candle count is the obvious follow-up, and is not done.

Method note: the case came from a reader looking at a chart and saying "it had
not even reached the OB yet". Four separate measurements had run over this same
population without isolating it — the defect is invisible in an aggregate
because it is a *minority* of trades with a *specific* mechanism. The block-test
criterion, the other half of the AVAX case, is still unmeasured inside the gate.

### A block the test crossed out the far side (2026-08-25)

The other half of the AVAX case, measured. The reading that prompted it -- "it
had not even reached the OB yet" -- does **not** survive: shallow tests are fine,
and the depth a test reaches inside the block does not separate. Nor does visit
length, once time is the axis: `visit_candles <= 2` gains a full Sharpe point on
holdout and **nothing** on search, and the combinations built on it lead the
in-sample table while their PBO climbs to 0.467.

One rule of sixteen survived, and it is the mechanical one. **Do not take a
reclaim whose test wicked clean through the block and out the far side**
(`test_extreme` beyond the far boundary). The detector retires a block only on a
*close* beyond it -- the `POIZone` lifecycle rule, where a wick back inside does
not break a zone -- so these blocks are still on the board with nothing holding.

| | search | holdout |
|---|---|---|
| base | SR 7.76, +206.2R | SR 5.48, +109.2R |
| did not cross out | **SR 8.26, +217.0R** | **SR 6.78, +127.5R** |
| crossed out (dropped) | SR −1.24, **−14.6R** | SR −2.59, **−21.0R** |

The discarded half is negative *on its own* rather than merely weaker, in both
halves -- the rare shape that makes a cut safe. It costs 8% of the gated
population and raises Sharpe and total R in both. Not a proxy for small blocks
either: piercing concentrates in sub-1-ATR blocks (median 0.78 vs 1.44 ATR), but
inside that bucket alone it still splits 34.9%/31.4% against 55.7%/57.1%.

Wired in `passes_gates` (`test_pierced_the_block`). Small: ~5% of result for 8%
of trades, an order of magnitude less than the re-anchor floor above.

### How deep the test dove, re-measured (2026-08-26)

The section above concludes that depth inside the block does not separate. That
run declared **one** depth rule, `pen_frac <= 0.25`, and 0.25 turns out to be the
one place in the range where the reading is absent. Swept end to end over the
gated population (`research/quality_features.py`, 1307 entries, 70 symbols,
~625 days, h120), the daily walk-forward reads:

| cut | R/day | SR | trades | days |
|---|---|---|---|---|
| ungated | +0.521 | 8.65 | 1307 | 488 |
| `pen < 0.20` | +0.467 | 8.14 | 761 | 379 |
| `pen < 0.25` | +0.500 | 8.58 | 848 | 406 |
| `pen < 0.30` | +0.535 | 9.24 | 923 | 416 |
| **`pen < 0.50`** | **+0.557** | **9.33** | 1093 | 454 |
| `pen < 0.80` | +0.537 | 9.07 | 1231 | 472 |
| `pen < 1.00` | +0.533 | 8.95 | 1279 | 480 |

A **plateau from 0.30 to 1.00**, and the prior study's declared threshold sits
just outside its lower edge. Both readings are right about what each tested.

Per trade the cut moves the 2R hit rate 62.1% -> 64.0%, R +0.561 -> +0.623, PF
2.15 -> 2.36, MAE 0.86R -> 0.80R, keeping 84% of the trades; it gains in all
four independent cuts and gains most in the symbol holdout (+0.086) and the
recent half (+0.100). Unlike the piercing rule, **what it discards is weak, not
negative** (+0.240R, 52.3% at 2R, +51R over the window): this one trades
absolute profit for a better average and a shallower drawdown, which is only
worth it while attention and capital bind before opportunity does.

Two limits, both real. The threshold was chosen **after** seeing the curve, so
its walk-forward is not a clean out-of-sample test -- the symbol holdout is the
nearest thing, and it passes there. And it is measured on **M15 only**;
`MAX_BLOCK_PENETRATION` therefore lists M15 alone, and a timeframe absent from
that dict is not gated on depth.

Wired in `passes_gates` (`test_penetrated_block_deeply`) on **M15, M30 and H1**,
and absent from H4 -- which is measured rather than merely unmeasured, since on
H4 the same rule does **not** clear the bar it cleared elsewhere.

| | trades | 2R | R/trade | daily SR | what it discards |
|---|---|---|---|---|---|
| M15 | 1307 | 62.1 -> 64.0% | +0.561 -> +0.623 | 8.65 -> 9.33 | +0.240R |
| M30 | 1747 | 54.9 -> 57.4% | +0.432 -> +0.506 | 6.56 -> 7.34 | +0.074R |
| H1 | 938 | 46.7 -> 48.9% | +0.278 -> +0.345 | 3.62 -> 4.17 | +0.005R |
| H4 | 234 | 56.8 -> 59.0% | +0.649 -> +0.714 | 8.06 -> **8.12** | +0.193R |

M30 is the strongest of the four (PBO 0.333, 35 of 37 folds positive,
train-to-test degradation −0.24) and peaks at 0.50, the same place M15 does. H1
improves at *every* threshold per day but returns PBO 0.533, so the choice among
them is a coin flip there; its own curve leans to 0.30-0.40 and it is wired at
0.50 anyway, taking the number the larger samples establish rather than fitting
a third one.

The discarded column is worth reading on its own: the cut gets **cheaper as the
timeframe thins**. On M15 it gives up trades still worth +0.240R each; on H1 it
gives up a group returning +0.005R, which is nothing at all. The weaker a
timeframe's edge, the more of what it holds is this group -- which is a claim
about where the rule earns its keep, not a reason to expect it everywhere: H4
is thin too and fails.

Per trade the direction replicates on H4 -- 234 entries over 2020-2026, `pen <
0.5` moving the 2R hit rate 56.8% -> 59.0% and R +0.649 -> +0.714, improving in
all four cuts, with the discarded group the weakest row of its table (n=29,
41.4%, +0.193R). Per **day** it does not: `pen < 0.5` reads +0.590 against the ungated
+0.607, and the walk-forward that would settle it returns PBO 0.867 over six
folds and 192 days carrying a trade -- that test does not disagree, it does not
know. Since "improves the daily series too" is the criterion that admitted this
rule on M15 and retired the four features measured beside it, changing rulers
for H4 would be changing them at the easiest place to fool oneself: 29 discarded
trades in six years.

The H4 curve also peaks at 0.30 rather than 0.50, which is a second reason to
stop: fitting a second threshold to 234 points is how a local reading gets
mistaken for a general one -- the same mistake the `pen <= 0.25` declaration
made in the other direction.

### The deep study's pinbar corrections do not transfer (2026-08-26)

Two fixes were made to the pinbar definitions while the deep-stop setup was
being built, and both stayed there: requiring the `l2` grade's colour to agree
(a red candle can satisfy the *bullish* `l2`, since the grade measures the body
as `abs(close - open)`), and raising `legacy`'s tail floor to 0.65 (it caps the
body but says nothing about the nose, so a doji passes). Both are right about
the candle. Measured here as their own arms -- each changes which triggers
exist, so each is its own scan rather than a cut -- neither transfers:

| arm | n | 2R | R/trade | R/day | SR |
|---|---|---|---|---|---|
| **shipped + depth gate** | 1093 | **64.0%** | **+0.623** | **+0.550** | **9.26** |
| shipped | 1307 | 62.1% | +0.561 | +0.514 | 8.58 |
| colour on `l2` + depth | 870 | 62.8% | +0.611 | +0.489 | 8.42 |
| tail 0.65 + depth | 850 | 63.3% | +0.597 | +0.457 | 7.72 |
| both + depth | 584 | 62.7% | +0.612 | +0.409 | 7.17 |

Decomposed, the colour rule's cost is not the re-timing it causes (only 29
trades enter in place of a refused trigger). It is that **the refused trades are
fine**: the 309 reclaims whose colour disagrees hit 2R at 62.5% against 62.0%
for the ones that agree, and inside the `l2`-only population they hit *more*
often (62.5% against 60.7%). What the filter buys is 0.1R on the average
(+0.486 against +0.584); what it costs is +150R of realized profit.

So the shape of the trigger candle is close to irrelevant in this setup, and the
location -- block plus VWAP -- is what carries the reading. That is consistent
with the union of the three grades beating every one of its subsets out of
sample, which only makes sense if the shape requirement is doing little work.

Why they earn their place in the deep setup and not here: there the stop is
deeper and there is no `r_atr` gate, so the population is far larger and can
afford an expensive filter. Here the gate has already selected, and a second
filter mostly removes sample. A correction that is right about the *mechanism*
can be wrong about the *population* -- do not carry a gate between setups
without re-measuring it.

`BlockReclaim.color_agrees` reports the colour so a reader can see it; nothing
filters on it. Its one supported use is choosing between two signals standing at
the same time -- the same narrow role the EMA9/VWAP alignment reading has.

The same run measured four other candidate features over this population and
**none survived**: the VWAP-to-block distance is already implied by the `r_atr`
gate (all 1307 entries sit within 1 ATR) and the VWAP sitting *inside* the block
measures worse; the displacement that created the block is not monotonic in any
threshold and its most vertical cut is the family's worst (-0.204R); a liquidity
sweep before entry helps only where it happens *inside* the block, and the
"closed back inside" half of that hypothesis measures **worse** than its absence
(+0.033 against +0.111) -- the trigger is already a rejection; and EMA9/VWAP
alignment is the strongest per-trade discriminator of all (66.5% against 56.0%)
yet **loses to the ungated series per day**, the third time that axis has
produced exactly that shape. Stacking all of them -- the "A++" hypothesis --
leaves 134 trades in 109 days across two years at +0.220R/day against +0.521.


## The setup on a broker's crypto list (2026-08-26)

A reader's question, and the shape of the answer is worth keeping: *does it pay
to trade only the 30 crypto CFDs FTMO lists, risking 0.25% per trade?*
`research/ftmo_universe.py` is the recut -- no new measurement, just the four
per-timeframe scans filtered to those symbols and re-priced under the broker's
cost sheet.

**The asset selection changes nothing.** 28 of the 30 are in the measured
universe (XMRUSD and BCHUSD are not USDT perpetuals on Binance and were never
measured), and their hit rate matches the full universe at every timeframe:
63.4% against 64.0% on M15, 57.9% against 57.4% on M30, 46.3% against 48.9% on
H1, 59.1% against 56.8% on H4. What changes is volume -- 39% of the trades
remain, about 42 a month across the four timeframes.

**The frightening number on the instrument sheet is the harmless one.** The
swap reads -30% a year, both sides. But the median position resolves in **4 to
5 candles** -- one hour on M15, sixteen on H4 -- so almost nothing crosses a
rollover and the swap costs 0.014R to 0.053R. Commission is what decides, and
it decides very differently per timeframe, because cost in R is
`commission / r_pct` and the stop distance is the denominator:

| | median R (% of price) | study 0.10% | 0.13% round turn |
|---|---|---|---|
| M15 | 0.383% | 0.328R | 0.426R |
| M30 | 0.514% | 0.237R | 0.309R |
| H1 | 0.870% | 0.135R | 0.176R |
| H4 | 1.833% | 0.061R | **0.080R** |

Which is the practical conclusion: **if costs bite, the answer is to move up a
timeframe, not to risk less.** The percentage risked cancels out of the cost
entirely -- notional and commission both scale with it -- so 0.25% or 1% per
trade changes the account arithmetic and not the R arithmetic. The M15 pays
five times the H4 for the same rule.

Over the window where all four timeframes coexist (Dec 2024 onward, 856 trades,
~42/month), under the pessimistic reading of the commission (0.065% *per side*):
**+16.6R/month**, max drawdown 12.6R, worst day -8.9R. At 0.25% per R that is
+4.2% a month against a 3.2% peak drawdown and a -2.2% worst day -- inside a
10% total / 5% daily limit with room. The study's own 0.10% assumption gives
+20.6R/month, so the broker's commission costs about a fifth of the result.

**What none of this covers:** the measurement is Binance USDT perpetuals and the
broker sells CFDs. The CFD *spread* appears nowhere in the arithmetic -- only
the published commission. If there is a spread on top, add it. That is the real
hole here, and no number above closes it.

## The setup outside crypto: equities and index CFDs

Two questions, measured 2026-08-26, both with the same code the crypto studies
use (`quality_features.scan` only changes provider):

1. Does the rule survive a different asset class at all?
2. Is the VWAP worth more where it is a stronger Schelling point?

### The hole above, closed

The section before this one ends by naming its own gap: the measurement was
Binance perpetuals while the broker sells CFDs, and the CFD spread appeared
nowhere. `research/mt5_export.py` exports the broker's own bars from the
MetaTrader 5 terminal, **including the per-bar spread in points**, so every
number below prices each entry at the spread of the bar it actually fired on.
That is the instrument, the feed and the cost, all first-hand.

### US equities: the rule transfers, the thesis does not

`research/equity_reclaim.py`, 100 liquid US names (Yahoo, real consolidated
volume, RTH only), H1, 159 entries:

| H1, 2R | US equities | crypto |
|---|---|---|
| hit 2R | 46.5% | 47.2% |
| gross | +0.396R | +0.407R |
| net (each class's own cost) | +0.339R | +0.283R |

The prediction was written into the script's docstring **before the run**: if
the VWAP earns its keep by being the institutional benchmark, equities -- real
tape, session anchor, the line execution desks are graded against -- had to
score higher. It did not. It tied, and tied across all four independent cuts
(search 46.7 / holdout 46.4, early 46.3 / late 46.9).

Read both halves. As robustness this is the hardest out-of-sample the setup
has ever passed: every parameter was fitted on crypto perpetuals and the gross
edge survived a move to a different tape, a different anchor, a different
session structure. As a hypothesis test it is a **negative** -- the
institutional-VWAP premium does not exist, which pushes the explanation toward
the plainer one: the VWAP marks where the recently-entered population sits at
break-even, arithmetic that any market with volume has. That agrees with the
event-anchor result (`project_vwap_schelling_point`): what matters is that the
line **accumulated**, not that it is prestigious.

The equity M15 arm is **undecided, not negative**: Yahoo caps 15m history at 60
days, which yielded 16 entries. The index ETFs that motivated the study gave
n=5 at H1 -- the setup barely fires on an index.

### The broker's index CFDs

`research/ftmo_index_reclaim.py`, 15 index/oil CFDs, per-bar spread as cost.
The VWAP here is weighted by **tick volume** (a CFD publishes no real volume) --
an approximation the equity result above licenses but does not verify.

| | n | hit 2R | gross | cost | net | /month | window |
|---|---|---|---|---|---|---|---|
| M5 | 323 | 58.8% | +0.750 | 0.671 | +0.079R | 10.4 | 31 months |
| M15 | 214 | 54.7% | +0.624 | 0.304 | +0.320R | 5.1 | 42 months |
| M30 | 335 | 51.9% | +0.558 | 0.255 | +0.304R | 4.6 | 73 months |
| H1 | 334 | 39.2% | +0.177 | 0.173 | +0.004R | 3.8 | 8 years |
| H4 | 24 | 58.3% | +0.750 | 0.030 | +0.720R | 1.2 | -- |

**M30 is the sturdiest reading**, not for its n but for six consecutive
positive years (+0.48 / +0.16 / +0.23 / +0.26 / +0.19 / +0.68) -- six
independent windows agreeing. M15 confirms (positive 2024, 2025, 2026) and
survives dropping GER40, whose 90%-on-10-trades carried 40% of the profit in
the first, 5-symbol pass.

**M5 has the best gross of the five and the worst cost, and unfiltered it is
flat.** 58.8% hit and 10.4 entries/month -- the only timeframe with real
frequency -- but the spread eats 0.671R of a 0.750R gross, leaving +0.079R and
+1.4R over 31 months. This is exactly the arithmetic that killed M5 in crypto
(`project_block_reclaim_m5_rejected`): cost is a % of price, what it consumes
is a % of R, and dropping a timeframe shrinks the denominator. Here the spread
is ~10x cheaper than Binance's taker fee, which is not enough on its own --
what makes M5 exist is **selecting the instrument**, and that is the walk-forward's
finding rather than a filter imposed on it (below).

**H1 is broken and unexplained.** Positive 2019-2022, then 21-33% hit across
2023, 2024 and 2025 (170 entries), recovering in 2026. A regime story cannot
carry it: M15 and M30 were positive in those same years on those same symbols.
Recorded as an open question rather than rationalised. H4 (n=24) decides
nothing.

### Instrument selection is a cost decision

Spread varies **tenfold across one broker's list**, and on M5 it decides the
sign of the result:

| M5 | n | hit | cost | net |
|---|---|---|---|---|
| all 15 | 186 | 62.4% | 0.525R | +0.325R |
| the cheap four (JP225, US100, US30, US500) | 54 | 57.4% | 0.203R | +0.487R |

N25 costs **1.417R** per M5 entry: it hits 60% and loses money. US2000 costs
1.132R. US100 costs 0.119R. Choosing instruments by spread is not picking
winners in hindsight -- spread is a property of the instrument, knowable before
trading, the same shape as the crypto fee-tier finding. The 0.20R cut-off,
though, was chosen after seeing the costs, and that is the degree of freedom in
it.

`research/index_cost.py` measures the cost side alone (ATR%, spread%, cost in
R, and spread by hour). Two things it settled: the broker's swap is irrelevant
next to the spread, and GER40's fat spread tail is **closed-session**, not news
-- 0.0155% overnight against 0.0047% between 09:00 and 22:00 UTC. Trading hours
are a filter the crypto work never needed.

### Walk-forward: M30 and M15 pass, M5 is undecided, H1 is dead

`research/ftmo_walkforward.py`, daily net series with the per-bar spread as
cost, seven competing rules (instrument selection by cost, gate tightening --
none of them touches the trigger).

| | folds | SR train -> test | positive folds | PBO | verdict |
|---|---|---|---|---|---|
| M30 | 11 | 3.71 -> 2.81 | 7/11 | **0.000** | passes |
| M15 | 5 | 5.82 -> 4.54 | 4/5 | **0.133** | passes |
| M5 | 8 | 3.66 -> 3.03 | 5/8 | **0.067** | passes, filtered |
| H1 | 10 | 1.80 -> **-0.54** | 6/10 | 0.533 | dead |

**M30 is the strongest reading this setup has produced in any market.** PBO
0.000 -- none of the apparent advantage is search -- and the rule the folds
picked most often is **"all"** (4 of 11): no filter beats the bare setup. When
the procedure's answer is *don't touch it*, that is worth more than the number,
because wanting to touch it is where overfitting comes from. M15 passes at the
same PBO as the H4 ladder already in production.

**M5 passes, but only with the instrument filter, and the filter is the
finding.** Unfiltered, the daily return is +0.006R -- 323 trades, +1.4R over 31
months, a flat line. Restricted to instruments whose median cost is under 0.30R
(GER40, JP225, UKOIL, US100, US30, US500) it returns +0.194R/day, 130 trades,
+45.1R, at 56.9% and +0.440R per trade -- roughly 6.5 entries/month, better
frequency *and* better R than M15 or M30.

What makes that credible is not the number but who chose it: **the folds picked
a cost filter in 7 of 8**, seeing only their own training window each time. A
filter I select after looking at a table is a degree of freedom; a filter the
procedure re-derives eight times from training data alone is a result. The
threshold family matters more than its value -- 0.30 was picked five times and
0.20 twice, while 0.10 leaves no trades at all.

The remaining caveat is calendar, not method: the eight folds come from ~500
trading days spanning 2025-2026, two years against M30's six. M5 is the
narrowest of the three that pass.

(An earlier pass of this measurement read PBO 0.467 on three folds and was
recorded here as undecided. It was scanning 60000 of the 100000 exported bars
-- a cap in the runner, not in the data. The conclusion was wrong in the
direction of caution, which is the cheaper direction, but it was wrong.)

The broker's M5 retention is the real ceiling and it has been probed: both
`copy_rates_from_pos` (capped at 100000 bars) and a date-ranged
`copy_rates_range` request reaching back to 2019 return the same start dates,
so ~100000 bars is what FTMO serves, not what the API allows. Note the spans
differ per instrument at equal bar counts -- a 24h index CFD spends 276 M5 bars
a day against a local-session index's ~108 -- so the American indices carry the
least calendar.

**H1 is confirmed dead** rather than noisy: ten folds, ample data, negative
test SR, and the base rule returns -0.0098R/day.

The instructive contrast is between the two ends. **M30 rejects every filter**
-- the folds chose "all" four times out of eleven, and nothing offered beats
the bare setup -- while **M5 requires one**. Cost explains it: at 0.255R per
entry M30 can carry any instrument on the list, at 0.671R M5 cannot. Same rule,
same instruments; the timeframe decides whether selection is optional.

### Not yet done

The tick-volume VWAP is unverified; the test is SPY with both volumes. Every
spread comes from a demo server. M5's ~100000-bar depth is the broker's own
retention, so its window stays at 18 months. The portfolio's 18-month common window
is short, and its five streams share one detection rule, so they are less
independent than five separate strategies would be.

### The crypto CFD spread, finally measured

This document has been carrying the same hole since the first broker study:
the crypto measurement is Binance perpetuals, the broker sells CFDs, and only
the announced commission ever entered the arithmetic. `research/ftmo_portfolio.py`
made it urgent by showing the sensitivity -- at +20bp of unmeasured spread the
account's month falls from +4.10% to +0.88%, at +40bp it goes negative -- and
`research/ftmo_crypto_spread.py` closes it the same way the index side was
closed: the spread comes off the broker's own bars, matched to each entry's
own bar.

The spread is real and it is enormous at the illiquid end. NEOUSD quotes $0.35
on an $8.4 instrument (**4.2%**), DASHUSD $0.40 on $32; BTCUSD quotes $6.62 on
$75,720 (**0.009%**), a factor of ~500 across one list. (A 0-point median is a
tick-resolution floor, not a free trade -- XLMUSD's tick is 0.048% of its price
-- so the table floors each bar at half a tick.)

**Crypto M15 survives it, but only once the expensive instruments are cut, and
the cut has to be on the spread rather than on total cost.** As a whole list it
reads -0.601R per entry; restricted to instruments whose median spread is under
0.1% it reads **+0.161R** across 221 entries, 10.5 a month.

Filtering on total cost -- the criterion that works on the index side -- was an
error worth recording, because it briefly retired this stream. On an index the
commission is zero, so cost *is* spread and a cost ceiling separates cheap from
expensive. In crypto the commission is a **floor**: 0.13% over a 0.2%-of-price
stop is 0.6R on its own, in **every** instrument, so a 0.30R cost ceiling
rejects the whole list on a number that distinguishes nobody. What distinguishes
is the spread, which varies 500x. Same threshold shape, opposite meaning, and
the difference only shows up where a fixed cost component exists.

**Crypto H4 is the stronger of the two**: +0.893R per entry at 67.3%, though
only 1.6 a month. The denominator explains the gap, for the third time in this
document -- an H4 stop measures 1.4-2.5% of price against M15's 0.2-0.4%, so the
same commission costs a fifth as much in R (BTCUSD: 0.623R at M15, 0.090R at
H4).

Which also names what actually kills a liquid crypto entry at M15, and it is not
the spread the terminal shows. BTCUSD quotes 0.009% of spread against a 0.13%
commission -- the commission is **seven times** the spread. The spread only
decides at the illiquid end, where it is catastrophic rather than marginal.

The corrected account -- three index streams plus both crypto streams, each
restricted to instruments whose spread allows them -- runs 28.1 entries a month
at +0.339R over the common window: +9.68R a month, 17.9R maximum drawdown, -7.1R
worst day. At 0.25% per trade that is **+2.42% a month against a 4.47% drawdown
and a 1.79% worst day**, the broker's 10% target in ~4.1 months, both limits
untouched, and the busiest day of the sample carries six entries worth 1.50% if
they all lose. The +4.10% it replaces was this same portfolio priced with a
spread of zero on half of it.

**The commission question is answered, and it was the optimistic reading.**
The instrument's commission table charges `0.0325 % em USD por lote` on the
entry and on the exit, closing the round trip at 0.065% -- so the 0.065% the
summary sheet announces is the *total*, not the per-side rate, and every
earlier crypto figure here was priced at twice the real cost. Corrected, the
account runs **+3.04% a month at a 3.88% drawdown and a -1.30% worst day**,
28.1 entries a month at +0.426R, the broker's 10% target in ~3.3 months. Crypto
M15 goes from +0.206R to +0.445R per entry and becomes the second-largest
stream.

The spread ceiling survives the change, which is the reassuring part: from
0.05% to 1.00% the crypto M15 stream returns between +3.16R and +4.58R a month,
a plateau, and only the unfiltered list collapses. The filter is not a tuned
threshold, it is the exclusion of two disasters -- NEOUSD at 4.8% of spread and
DASHUSD at 1.35%. The wired 0.1% was chosen before that curve was drawn and is
kept rather than moved to the 0.2% peak.

### Every cost measured, including the one that was assumed away

The index instrument sheet settles the two remaining assumptions, one in each
direction.

**Index commission is zero**, as assumed -- `0 % em USD por lote` on
US500.cash. That assumption held.

**Index swap did not exist in the arithmetic at all**, and it should have. It
is charged **in points** and it is strongly asymmetric across the list: US30
charges 1173 points a night on a long and *pays* 50 on a short, UK100 is the
reverse (+134 long, -369 short), and Friday counts as three nights. An average
of the two sides would erase exactly that, so `attach_costs` charges each entry
its own side, and resolves each position candle by candle to count the nights
it actually slept.

It costs almost nothing, and the reason is holding time rather than the rate:
the median index position lives 3-4 candles, and only 2% (M5) to 14% (M15) of
them cross a rollover. Mean swap runs +0.006R to +0.017R, and 4-6% of entries
*receive* it. The account goes from +3.04% to **+3.00% a month**, with the
drawdown widening from 3.88% to 4.21%.

The tail is worth knowing even though the mean is not. The worst single entry
in the sample is not a long -- it is an **AUS200 short held across a Friday**,
three nights at -165.93 points, costing 0.88-1.00R on its own. A trade whose
swap side is against it and which sleeps into a weekend can hand back its
entire expected edge.

So the full account, with commission, spread and swap all read off the broker's
own instrument sheets and bars: **+3.00% a month at 0.25% per trade, a 4.21%
drawdown, a -1.30% worst day**, 28.1 entries a month at +0.421R, the 10% target
in ~3.3 months. Nothing in that number is assumed any more except the two
things named below.

### The portfolio's own walk-forward

Each stream had cleared its own (`research/ftmo_walkforward.py`), which answers
"does this rule work". The portfolio asks something else: **was combining these
five a good choice, or the combination that happened to look best over the whole
period?** A stream can pass alone and still not deserve a seat -- if it loses on
the same days the others lose, it worsens the daily series, which is where the
broker's limits live, without paying for itself.

`research/ftmo_portfolio_walkforward.py` competes seven *compositions* (nothing
touches how an entry is detected) over the window common to all five, on a daily
series already net of commission, per-bar spread and per-side swap.

| | folds | SR train -> test | positive | PBO |
|---|---|---|---|---|
| daily **sum** (the portfolio question) | 11 | 6.72 -> 6.56 | **11/11** | **0.000** |
| daily **mean** (the rule question) | 11 | 5.72 -> 4.66 | 10/11 | 0.067 |

**The full portfolio wins, and the folds pick it more often than anything else**
(4 of 11). It also beats every subset, including each half alone -- indices at
SR 4.47, crypto at 4.42, together 6.08 -- which is diversification doing its job
rather than a bigger number from more trades. A degradation of -0.16 between
training and test is the smallest this project has measured.

No stream is dead weight either: on the days each one trades, both it *and* the
rest of the portfolio return positive, so none of them enters by dragging the
others down.

The aggregation is a real methodological choice and it changes the answer.
`daily_matrix` averaged each day's trades, which is right for comparing rules
because it normalises for how much a rule trades. But switching a stream on
means **taking more entries that day**, and a daily loss cap limits the sum, not
the average -- averaging would penalise the larger portfolio for dilution, which
is not what happens in the account. Both are reported; `sum` is the one that
answers the portfolio question, and both pass.

## Câmbio: a terceira classe de ativo (2026-08-27)

> **Os números desta seção foram medidos com o spread errado e estão
> SUPERADOS** pela seção *"A coluna de spread era o mínimo da barra"*, mais
> abaixo, escrita no mesmo dia. Ali o spread do câmbio subiu 29-45% (a coluna
> da barra era o mínimo do período) e isso **matou o M15 e o H1**, que aqui
> aparecem passando. A carteira final tem **só o H4 no câmbio**, +3,11%/mês.
>
> A seção fica como está de propósito: ela é o registro do que foi medido
> antes, e apagá-la esconderia que a conclusão mudou por causa do insumo e não
> do método. Leia as duas na ordem.

Os 28 pares da FTMO mais XAUUSD/XAGUSD, exportados do MT5 da corretora
(`research/ftmo_forex_reclaim.py`), com o custo lido das fichas: **comissão de
2,5 USD por lote por ponta** no câmbio, **0,0007% por ponta** nos metais,
spread da própria barra e swap com a virada tripla na **quarta-feira** (a
convenção do spot, contra a sexta dos índices).

A previsão foi escrita no cabeçalho do script antes de rodar: eu esperava
empate, porque câmbio não tem fechamento de sessão nem volume real e o
mecanismo de ponto de Schelling da VWAP não tinha onde se apoiar. Errei.

| TF | n | acerto | líquido/op | Sharpe OOS | folds | veredito |
|---|---|---|---|---|---|---|
| M5 | 504 | 59,5% | −0,188R | −3,24 | 2/6 | morto no custo |
| M15 | 428 | 53,3% | +0,186R | 1,56 | 7/11 | fraco, positivo |
| M30 | 688 | 50,9% | −0,002R | −0,03 | 11/22 | morto |
| H1 | 1162 | 46,8% | +0,147R | 1,78 | 28/41 | passa |
| H4 | 393 | 46,8% | +0,247R | 4,04 | 13/15 | passa, 1,8 ops/mês |

**O M5 é o caso mais limpo de custo mandando em todo o projeto**: acerta 59,5%,
o melhor de todos os timeframes, e perde dinheiro — bruto +0,782R contra custo
+0,970R. O stop mediano do câmbio é de 4,5 pontos-base, o menor de qualquer
ativo já medido aqui, então é onde um custo fixo pesa mais. Terceira vez que o
denominador decide.

**H4 e H1 melhoram fora da amostra** (degradação +0,77 e +0,25). O H4 é positivo
em cada década separada — +0,154R nos anos 2000, +0,351R nos 2010, +0,216R nos
2020 — e o spread histórico cai de 3,78bp para 0,40bp no mesmo intervalo, ou
seja, o feed antigo cobra *mais*, não menos.

Três coisas medidas e rejeitadas:

* **Busca por filtro: PBO 0,933.** As sete regras que servem em índice e cripto
  se revezam ganhando por acaso no câmbio. O que passou foi a regra sem filtro
  nenhum, e é assim que ela deve ser operada.
* **Teto de custo esperado** (calculável antes de entrar, pelo spread mediano do
  par sobre o stop): piora o M15 (3,30 → 3,14). As operações caras são caras
  porque o stop é apertado, e stop apertado é o que dá R bom quando acerta.
* **Metais como bloco:** +1,028R no M5 e +0,574R no M15, mas −0,340R no H1 e
  −0,048R no H4. O sinal inverte conforme a amostra cresce; é ruído.

O que isso diz sobre o mecanismo: o setup sobrevive na classe onde a VWAP é mais
fraca. Somado ao empate em ação americana, são duas classes dizendo que o que
carrega o resultado é a geometria — bloco de ordem, reclaim, stop no extremo
testado, gate de `r_atr` — e não o prêmio institucional da linha.

### A carteira com câmbio

`ftmo_portfolio_walkforward.py` compete 11 composições. **13 de 13 folds
positivos nas duas agregações, PBO 0,000 (soma) e 0,067 (média)**; a composição
completa vence pela média (SR 5,12 contra 4,70 sem câmbio) e empata no topo pela
soma. Não é volume: os *dias* melhoram.

O **M15 de câmbio foi cortado depois de medido na carteira** — +0,142R por dia
contra +0,217R do H1, subindo o total por operar muito. Sem ele a carteira sobe
de R/dia +0,734 para +0,736 com Sharpe 5,12 → 5,61.

Plano resultante, janela comum de 18 meses (presa pelo M5 de índice):

| | sem câmbio | com câmbio H1+H4 |
|---|---|---|
| ganho mensal a 0,25% | +3,00% | **+3,53%** |
| drawdown máximo | 4,21% | 5,04% (teto 10%) |
| pior dia | −1,30% | −1,50% (teto 5%) |
| entradas/mês | 28,1 | **38,6** |
| alvo de 10% em | 3,3 meses | **2,8 meses** |

Ressalvas que a tabela não mostra: a degradação por fold piorou (−1,44 contra os
−0,16 da carteira de cinco fluxos), efeito mecânico de competir 11 composições em
vez de 7 — o número de opções oferecidas já está no limite do honesto. A janela
de 18 meses apaga justamente o que o H4 de câmbio tem de melhor, seus 26 anos. E
os sete fluxos continuam compartilhando **uma só regra de detecção**, então são
menos independentes do que sete estratégias seriam.

## A coluna de spread era o mínimo da barra (2026-08-27)

Veio de uma pergunta do usuário — *"por que em M5 com quase 60% de acerto o
custo mata? Você tem certeza de todos esses custos?"* — e a resposta é que eu
não tinha.

Todo custo medido em instrumento da corretora sai da coluna `spread` do
candle exportado do MetaTrader. A documentação do terminal não diz de que
instante ela é. Exportei o bid/ask **tick a tick** (`mt5_export.py --ticks`,
`COPY_TICKS_INFO`) e comparei barra a barra (`research/spread_audit.py`):
**a coluna é o mínimo do período**, em 99,0-99,9% das barras, contra 0,3-10,6%
de concordância com a média.

### Mas só importa onde o spread flutua — e ele não flutua em todo lugar

Esta foi a segunda metade do achado, e ela desfez a primeira correção que eu
tinha aplicado. Medido por classe, em 19 símbolos:

| classe | evidência | fator |
|---|---|---|
| **índice** | 6 símbolos × ~2.800 barras M5: `min == max == coluna` (US500 60 pts, GER40 133, JP225 1000, USOIL 68, N25 60); só US100 varia | **1,0** |
| **cripto** | 4 dos 7 CFD não variam nada; mediana entre símbolos 1,00. BNBUSD é outlier (2,6×) sobre o menor spread da lista (0,0015%) | **por símbolo, ~1,0** |
| **câmbio** | flutua em todos; a coluna subestima sistematicamente | **1,29 (M5) → 1,45 (H4)** |

O fator **não é propriedade do terminal**. O mecanismo — a coluna é o mínimo —
é; a magnitude é de como a corretora cota aquele instrumento. Índice e a
maioria do cripto são cotados a spread **fixo**, e ali não há nada a corrigir.
Aplicar o número do câmbio ao índice foi exatamente o erro que a medição
seguinte desfez, e ele estava registrado como "provavelmente subestima" —
estava errado nos dois sentidos.

O fator do câmbio cresce com o timeframe pelo motivo mecânico esperado: barra
maior tem mais ticks, então o mínimo afunda mais.

### O que a correção matou

Ela pesa na proporção `spread/R`, exatamente onde o intradiário é fraco. No
walk-forward de regra única (sem filtro, para medir o setup e não a busca):

| fluxo | depois | veredito |
|---|---|---|
| câmbio M15 | SR 0,44 · 4/11 folds | morto |
| câmbio H1 | SR 0,23 · 23/41 folds | morto (cara ou coroa) |
| câmbio H4 | SR 2,64 · 13/15 · degradação **+0,94** | sobrevive |
| índice M5 (cru) | SR −2,11 · 3/8 | morto cru |
| índice M5 (custo ≤0,30R) | +0,427R · 130 ops | sobrevive filtrado |

O câmbio H1 chegou a entrar na carteira antes da correção; com ela rende
−0,019R por dia e derruba o Sharpe. Foi cortado, restando **só o H4 no
câmbio**. O filtro de custo do M5 de índice, que já existia por outro motivo,
faz exatamente o trabalho certo: seleciona as barras baratas.

### O M5 e a aritmética de sempre

A pergunta original tem resposta limpa, e é a quarta aparição da mesma conta.
O M5 tem o **melhor acerto da régua inteira** (59,5% no câmbio) e ainda assim
perde:

| TF | acerto | stop | spread | spread em R | precisa acertar |
|---|---|---|---|---|---|
| M5 | 59,5% | 2,18 bp | 0,75 bp | 0,668 | 65,7% |
| M15 | 53,3% | 4,49 bp | 0,72 bp | 0,263 | 46,9% |
| H1 | 46,8% | 10,72 bp | 0,71 bp | 0,194 | 41,9% |
| H4 | 46,8% | 27,94 bp | 1,62 bp | 0,100 | 38,4% |

O spread **não muda** de timeframe para timeframe — é o mesmo instrumento. O
stop encolhe 12,8 vezes do H4 para o M5, e está no denominador. O M5 acerta 6
pontos a mais que o M15 e precisaria acertar 19 a mais. No M5 o `spread/R`
mediano é 0,333 mas o p99 é **6,75**: um percentil das operações paga sete
vezes o risco em spread.

### Um bug menor, no lado otimista

O EURUSD exportado tem `spread = 0` em 65% das barras de M5 — valor faltando,
não spread. Cobrava 8 entradas de graça. Corrigido em `_mt5.py` (spread zero
recebe a mediana do próprio símbolo). Imaterial: o spread real do par é 1
ponto. Os outros 29 símbolos não têm o problema.

### A carteira depois de tudo

Seis fluxos: índice M5 (barato) + M15 + M30, cripto M15 + H4, câmbio H4.
**11/11 folds positivos, PBO 0,000, Sharpe 6,13, degradação −0,59** — a menor
já medida aqui. A 0,25% de risco: **+3,11% ao mês**, 29,1 entradas/mês,
drawdown 4,67% contra teto de 10%, pior dia −1,30% contra teto de 5%, alvo de
10% em 3,2 meses.

O número ficou **melhor** que o de antes do episódio (+3,00%) por três
motivos que se somam: o câmbio H1, que diluía, saiu; o índice voltou ao custo
certo; e o cripto passou a pagar o pouco que faltava. Não foi o custo que
melhorou — foi a composição, agora medida com o insumo certo.

### O que ainda não foi verificado

Todos os spreads vêm de **servidor demo**. Uma corretora pode cotar spread
fixo em demo e flutuante em conta real, e é justamente a hipótese que os
`min == max` de índice tornariam falsa. É a única fonte de custo do plano que
não tem verificação independente, e só execução real fecha.

## Rodando no feed da corretora (2026-08-27)

`research/ftmo_live.py` roda o plano contra os candles do terminal da FTMO, em
papel. É o mesmo `paper_journal` de sempre — **nenhuma linha de detecção muda**,
só o provider: em vez do perpétuo da Binance, `MT5CsvProvider` lê os CSV que
`mt5_export.py --refresh` mantém atualizados. O que se opera e o que se mede
passam a ser o mesmo instrumento, que era a última costura solta entre o estudo
e a conta.

Não manda ordem e não guarda credencial. O número que ele existe para produzir
é a **derrapagem em R**: todo resultado deste documento assume entrada no
fechamento da vela do gatilho, e só a fita ao vivo diz o que essa suposição
custa.

Os seis fluxos (`STREAMS`) são os do plano validado, cada um com a lista de
símbolos que passou no walk-forward e o gate do seu timeframe. O M5 roda com
`r_atr <= 1.0` e **sem piso de acumulação de VWAP** — `OPERATING_GATES` para no
M15 e escolher um número para o M5 seria ajustar sem medir; declarar que não há
só pode subestimar.

```
# Windows, com o terminal aberto (deixe a janela rodando)
powershell -ExecutionPolicy Bypass -File C:\mt5-export\refresh.ps1

# WSL, uma passada por vez (idempotente, bom para cron)
poetry run python -m research.ftmo_live
poetry run python -m research.ftmo_live --report-only
```

O `refresh.ps1` é **gerado** (`--write-refresh`) a partir das listas do módulo,
nunca editado à mão: um símbolo digitado a mais do lado Windows faria o que roda
divergir, em silêncio, do que foi medido.

**Verificação da primeira passada** (2026-08-27, dado exportado do dia
anterior): 98 símbolos encontrados nos seis fluxos, 12 linhas `FIRED`, 0
passando o gate — todas com `r_atr` entre 1,30 e 6,39. É a resposta certa: o
gate *é* o setup, e o penhasco no primeiro decil já estava medido. Zero
decisões registradas porque `MAX_DECISION_AGE_CANDLES = 1` exige que o gatilho
tenha acabado de fechar, e o dado era de ontem.

### O relógio do servidor, achado na primeira decisão ao vivo

O MetaTrader marca cada vela em **hora do servidor da corretora**, não em UTC —
a FTMO roda em GMT+3, para o candle diário fechar às 17h de Nova York — e o
exportador grava esse instante com sufixo `+00:00`. Comparar um timestamp de
vela com `datetime.now(UTC)` erra por três horas.

O deslocamento é **inferido do próprio dado** (`server_offset`: a vela de M5
mais nova não pode estar no futuro), não fixado numa constante — o servidor
muda de offset no horário de verão, e um número fixo quebraria em silêncio duas
vezes por ano.

Ele **não é aplicado aos candles**. O dia do servidor é a sessão correta para a
âncora da VWAP, e deslocá-lo mudaria `vwap_candles`, que é gate de produção.
Só a comparação com o relógio de parede precisa da correção.

### Idade do gatilho em tempo real, não em velas

A primeira decisão registrada ao vivo (NZDJPY H4) tinha derrapagem de −0,113R
sobre um gatilho que fechara **~1h20 antes**. `MAX_DECISION_AGE_CANDLES = 1`
limita a idade, mas em unidades de vela — e uma vela do H4 são quatro horas.
Precificar isso contra a fita de agora mede **deriva de preço**, não
derrapagem: a mesma classe do bug que registrou +1,6R na primeira passada do
diário de cripto, só que mais discreta e por isso mais perigosa.

`record_decisions` passou a aceitar `max_signal_age` (tempo real) e
`clock_offset`, ambos opcionais e neutros por padrão, então o caminho da
Binance não muda. O runner da corretora usa **5 minutos**: generoso para um
laço que roda a cada minuto, apertado o bastante para a linha medir o que
promete. A linha contaminada do arranque foi descartada em vez de deixada
poluindo a média.

### As duas coisas que precisam estar vivas

Nenhuma delas é o cron, e ambas falham em **silêncio** — o diário fica vazio,
que é indistinguível de "não houve sinal hoje":

1. **O terminal da corretora**, aberto e logado.
2. **O `refresh.ps1`** rodando no Windows. Se parar, os CSV congelam, o guard
   de idade descarta tudo, e a VM do WSL acaba desligando com o cron dentro.

Era três: a janela do WSL entrou no laço (`wsl.exe -e true` por volta), então
o `refresh.ps1` segura as duas pontas.

**Nada disso é o servidor da API** (`uvicorn liquidity_hunter.api.main:app`).
Aquele serve o gráfico React e não tem relação nenhuma com o diário — o
`ftmo_live` lê os CSV direto, sem HTTP no meio.

`research/ftmo_live_check.sh` verifica as três num comando. O script de cron usa
**caminho absoluto** para o poetry de propósito: o cron roda com PATH mínimo, e
um `command not found` num job de cron falha em silêncio.

## De R para lotes: o tamanho da ordem (2026-08-27)

O estudo inteiro vive em **R**, e isso é de propósito — o percentual arriscado
cancela na conta de custo, então medir em R deixa a conclusão independente do
tamanho da conta. Mas na hora de mandar a ordem alguém converte, e a conversão
tem duas armadilhas que o R esconde. `research/ftmo_sizing.py` faz a conta e,
mais útil, **mede quanto elas mordem** nas operações que o plano produziu.

O insumo é o `meta.json`. `trade_tick_value` já vem na moeda da **conta** —
verificado: GBPJPY tem 100 JPY por tick virando 0,6277 USD e EURGBP tem 1 GBP
virando 1,3590 — então não há conversão de moeda a fazer. Mas o valor foi lido
no dia da exportação e, para um par cruzado, anda com o câmbio: um erro de 2%
no `tick_value` é um erro de 2% no lote, menor que o próprio arredondamento e
maior que zero.

### O arredondamento custa pouco

Risco real médio contra o alvo de 0,25%, por conta:

| fluxo | $10k | $25k | $50k | $100k | $200k |
|---|---|---|---|---|---|
| índice M5 | 0,250% | 0,250% | 0,250% | 0,250% | 0,250% |
| índice M15 | 0,249% | 0,250% | 0,250% | 0,250% | 0,250% |
| índice M30 | 0,249% | 0,250% | 0,250% | 0,250% | 0,250% |
| cripto M15 | 0,243% | 0,248% | 0,249% | 0,249% | 0,250% |
| cripto H4 | **0,266%** | **0,242%** | 0,239% | 0,245% | 0,247% |
| câmbio H4 | 0,235% | 0,243% | 0,246% | 0,248% | 0,249% |

O lote é arredondado sempre para **baixo**: arredondar para cima estoura o
risco pretendido, e o limite diário da corretora é sobre a perda, não sobre a
intenção. O custo disso é de 0 a 6%, maior onde a conta é pequena.

### O lote mínimo, esse morde

O `0,266%` do cripto H4 em $10k está **acima** do alvo, e é o sintoma: em 12%
das operações o lote ideal fica abaixo de `volume_min`, e a ordem só existe
arriscando mais do que se pretendia.

O caso pior medido, verificado à mão:

```
GRTUSD  entrada 0,20384  stop 0,19229   (5,67%)
  contrato = 1.000.000 tokens   tick_value = 10 USD   volume_min = 0,01
  perda por lote cheio          = 11.550 USD
  lote ideal para arriscar 25   = 0,0022   ->  mínimo 0,01
  risco real                    = 115,50 USD = 1,155%
```

Um lote de GRT é **um milhão de tokens**. Com stop de H4 o mínimo já arrisca
4,6× o alvo numa conta de $10k. Os afetados são os CFD de contrato grande e
preço baixo (GRT, IMX, GALA, HBAR) no H4, onde o stop é largo: 7 de 56
operações em $10k, 3 em $25k, **nenhuma em $50k**.

Isso não invalida o plano — invalida **o cripto H4 numa conta pequena**. Em
$50k para cima a questão desaparece.

### O que ainda não é verificado: margem

Um stop muito curto produz um lote correto em risco e grande demais em
nocional — 83 lotes de US500 com stop de 0,03% são ~$444k de nocional numa
conta de $50k. Isso é risco certo e margem possivelmente insuficiente, e o
exportador não trazia `margin_initial`. Agora traz (junto com `volume_max`),
mas **o `meta.json` precisa ser reexportado** para os campos aparecerem, e o
limite ainda não foi medido. Até lá, um stop muito apertado pode virar recusa
da corretora — o pior lugar para descobrir.

## O risco por operação: 0,25% → 0,35% (2026-08-27)

Subir o risco multiplica o drawdown linearmente, e a tentação é comparar o
resultado com o teto e parar aí. Isso subestima o problema: o drawdown que
`ftmo_portfolio.py` reporta é o **do caminho que aconteceu**, uma amostra de
tamanho **um**, e o máximo de uma amostra de um não é limite superior de nada.

`research/ftmo_risk_budget.py` reamostra a série diária para estimar a
**probabilidade de estourar**, que é a grandeza que decide. Duas
reamostragens, de propósito, porque discordam pelo motivo certo: **iid**
(sorteia dias soltos, destrói o agrupamento de perdas e por isso subestima —
serve de piso) e **blocos de 5 dias** (preserva o agrupamento — a estimativa
honesta). 20.000 caminhos de 120 dias:

| risco | dd mediano | dd p95 | dd p99 | P(estoura 10%) | P(estoura 5%/dia) | P(+10%) |
|---|---|---|---|---|---|---|
| 0,25% | 2,66% | 4,81% | 6,14% | **0,0%** | 0,0% | 97,3% |
| **0,35%** | 3,73% | 6,73% | 8,60% | **0,3%** | 0,0% | 99,1% |
| 0,50% | 5,32% | 9,61% | 12,28% | **4,0%** | 0,0% | 99,7% |

O salto está entre 0,35% e 0,50%, não antes. Uma chance de 0,3% de queimar a
conta é um preço defensável por 40% mais retorno; 4,0% não é. O teto **diário**
de 5% nunca é ameaçado em nenhum dos três (P = 0,0%): o pior dia medido é de
5,2R e seriam precisos 14R.

A de blocos fica só um pouco pior que a iid (2,66% contra 2,46% na mediana), o
que diz que existe agrupamento de perdas nesta série mas ele é brando — o
método pegaria se fosse grave.

**A ressalva que nenhuma reamostragem remove:** ela sorteia do que foi medido,
então não contém regime que a amostra de 297 dias não conteve. O `P(+10%) =
99,1%` é condicional a a vantagem persistir como medida, e não é uma promessa.

### O plano a 0,35%

**+4,35% ao mês**, alvo de 10% em 2,3 meses, drawdown medido 6,54% (teto 10%),
pior dia −1,82% (teto 5%), pior caso aritmético 2,10% se as seis operações do
dia mais cheio perderem juntas.

Numa conta de **$100k**, nenhuma das 1.590 operações medidas fica abaixo do
lote mínimo: a conta cobre o plano inteiro e a questão do `volume_min` do
cripto H4 (que morde em $10k e $25k) não existe aqui.

## Manual de operação

### Já instalado, não repetir

O cron do WSL (`crontab -l` deve mostrar `ftmo_live_cron.sh`) e o `refresh.ps1`
gerado em `C:\mt5-export\`. Só refazer se trocar de máquina.

### Toda vez que ligar a máquina

1. Abrir o **terminal da FTMO** e esperar conectar.
2. No PowerShell, o laço que mantém os candles frescos — **deixar a janela
   aberta**:

   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\mt5-export\refresh.ps1
   ```

Nada mais. O `refresh.ps1` chama `wsl.exe -e true` a cada volta, o que mantém
a VM do WSL viva — ela desligaria quando não sobrasse processo e levaria o cron
junto. Não é preciso deixar uma janela do WSL aberta só para isso.

### No dia a dia

```bash
# está tudo vivo?
./research/ftmo_live_check.sh

# o que entrar, com lote calculado para a conta
poetry run python -m research.ftmo_sizing
```

O primeiro verifica as três coisas que falham em silêncio. O segundo imprime a
ordem pronta de cada decisão aberta — símbolo, lado, entrada, stop, alvo, lote
e o risco em dinheiro. Padrões: conta de $100.000, risco de 0,35%
(`--balance` e `--risk` mudam).

### De vez em quando

```bash
# o que o diário aprendeu (a derrapagem é o número que importa)
poetry run python -m research.ftmo_live --report-only

# quanto do risco alvo sobrevive ao lote discreto, por tamanho de conta
poetry run python -m research.ftmo_sizing --table

# a probabilidade de estourar os limites, por nível de risco
poetry run python -m research.ftmo_risk_budget
```

### Quando estranhar o silêncio

Diário vazio é o caso comum: o plano prevê ~29 entradas por mês nos seis
fluxos, cerca de **uma por dia**, e o gate `r_atr <= 1.0` reprova a maioria dos
disparos. Se `ftmo_live_check.sh` der dois OK, o pipeline está bem
independentemente do diário estar vazio.

O sinal de problema não é diário vazio — é o `check` reclamando, ou a coluna
`linhas` do funil zerada (os CSV congelaram).
