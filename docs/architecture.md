# Architecture

Why the framework is shaped the way it is. For *how to use it*, read
[`AGENTS.md`](../AGENTS.md); this document is the reasoning behind that
contract and is not needed to write a strategy.

---

## The problem it solves

Before this platform, each strategy was a self-contained script. Each one:

* downloaded its own prices, at its own frequency, over its own window
* kept its own cash in its own variables
* invented its own fill assumptions — usually "I get the price I saw"
* printed a number and exited without saving anything

The consequence is not that the numbers were wrong. It is that they were
**incomparable and irreproducible**. Two strategies reporting a 0.5 Sharpe on
different windows with different cost assumptions have not been compared, and
neither run could be repeated because both re-downloaded their inputs.

Every design decision below follows from wanting a number that means something.

---

## The inversion

The scripts pulled; the platform pushes.

```
before:   strategy → yfinance.download() → decide → mutate its own cash

after:    hub → MarketContext → strategy.on_bar() → list[Order] → hub fills
```

Moving data access and cash into the framework is what makes runs comparable.
It also means the framework can enforce properties the strategy would otherwise
have to remember:

| Property | How it is enforced |
|---|---|
| No lookahead | `MarketContext` has no accessor that can reach past its cursor |
| Reproducibility | No I/O in `on_bar`; the cost model's RNG is seeded per run |
| Honest fills | Orders carry no price; the hub applies timing and costs |
| Solvency | Cash checks happen in the portfolio, before the fill |
| Comparability | Every strategy in a run sees the same bars and the same costs |

The last column is the point. These are not conventions a contributor has to
observe. **They are properties of the objects.**

---

## Why the context is an object, not a DataFrame

The obvious alternative is handing the strategy a DataFrame and a row index.
That fails on the first property: `frame.iloc[i+1]` is one keystroke away, and
a lookahead bug written that way is invisible in review and invisible in the
output — the equity curve simply looks better.

`MarketContext` closes over the cursor. `ctx.history("AAA")` returns everything
up to and including now, as a plain array. There is no argument that makes it
return more. A contributor who wants to cheat has to reach for `ctx._ds`, which
is a deliberate act rather than an accident.

The cost is a slightly narrower API and one object allocated per bar per
strategy. Both are cheap; the property is not obtainable any other way.

---

## Sleeves, not a shared account

A run with six strategies gives each one its own `Portfolio` with the full
starting capital. They never compete for cash.

The alternative — one account, six strategies — produces one result that
depends on **which strategy happened to spend the money first**, which is an
artefact of the order they were listed in. Sleeves cost some realism (a real
book would net exposures and share margin) and buy six independently
interpretable results plus a combined curve. For a research tool that is the
right trade; for a production allocator it would not be.

---

## Fill timing

Default: an order decided on bar *t* fills at bar *t+1*'s open.

The alternative — filling at bar *t*'s close, which every one of the original
scripts did — hands the strategy a free option. It observes the closing price,
decides, and transacts at that same price, which nobody can do. On a
mean-reversion strategy the effect is large and always favourable, because the
strategy is entering precisely on the bars where the close was extreme.

`FillTiming.CLOSE` is still available, so the difference is measurable rather
than asserted. That is the general pattern here: a known-optimistic assumption
is kept as a named comparison instead of deleted, so its cost can be shown.

When the dataset has no open (most of the price sources here are close-only),
`NEXT_OPEN` falls back to the next bar's close — still a bar later, still not a
free option.

---

## The cost model, and why latency stays in it

Three terms: commission, spread, and latency drift.

Latency drift is ported from the original pairs engine. At swing-trade horizons
it contributes cents against dollars of expected profit, and it would be
reasonable to argue for deleting it.

It stays because **a cost model that only ever produces a small number is not
telling you anything.** The term's value is that it scales: at a 14-day hold it
is noise and says so; at a 30-second hold it dominates. `stat_arb_ev`'s first
gate — reject any pair whose mean-reversion half-life is under two days —
exists because of what this term does at that timescale. Deleting the term
would leave the gate as an unexplained constant.

The drift is random, so the model is seeded from the run config. Two runs of
the same configuration agree exactly; `tests/test_contract.py` asserts it for
every registered strategy.

---

## Atomic order groups

Orders sharing a `group` fill together or not at all.

A pairs trade has two legs. If the long leg fills and the short leg does not,
the result is not a hedge — it is a naked directional position that the
strategy did not ask for and will not manage correctly. The original broker
handled this with `can_afford(trade1, trade2)`, which checked the *net* cost of
both legs, because the short leg credits cash and the pair is therefore far
cheaper than the sum of its notionals.

The hub generalises that: resolve every leg in a group to a share count, check
affordability once for the whole group, and either fill all of it or reject all
of it with a logged reason.

---

## Rejections are output

When the hub refuses an order — insufficient cash, no price at fill time, a
position cap — it records the reason against the timestamp, and the GUI shows
it beside the trade log.

This started as debugging and turned out to be the more informative half of
some runs. "The strategy generated 71 signals, took 46, and rejected 15 of them
for a half-life under two days" is a finding about the strategy. In a framework
that silently clips, it is invisible: you see 46 trades and assume 46 signals.

The same applies inside strategies. `ctx.log()` exists so a strategy can say
why it declined, and `stat_arb_ev` uses it on every gate.

---

## Significance in the core, not in a notebook

`Performance` carries `observations`, `sharpe_stderr` and `sharpe_t` alongside
the usual figures, and `Performance.is_significant` tests |t| > 2.

Putting this in the metrics module rather than in analysis code is a deliberate
constraint: it means **no view of a result can accidentally omit it.** The GUI
prints the t-statistic directly under the hero number; the CLI prints it after
every summary line. A Sharpe of 0.72 over 15 quarterly rebalances is not a
weaker version of a real result, it is an unresolved measurement, and the
architecture makes saying so the default.

The estimator is Lo (2002): `SE(Ŝ) ≈ sqrt((1 + Ŝ²/2) / n)`, which assumes iid
returns. Real strategy returns are autocorrelated, so the true standard error
is usually larger and the reported t is, if anything, generous. That is the
right direction for the error to run.

---

## The sweeper reports its own unreliability

`run_sweep` runs a parameter grid and ranks by Sharpe, which is what the
original `meanReversionBacktestClass.py` did across 108 combinations. It also
reports:

* **`expected_max_noise_sharpe`** — the expected maximum of *n* draws from a
  zero-mean distribution with the grid's observed dispersion. If the best
  result does not clear this, the sweep found nothing.
* **`deflated_sharpe`** — the best, minus that haircut.
* **`plateau`** — the fraction of the grid within 20% of the best. A robust
  parameter set has neighbours that also work; an overfit one is alone.

`SweepResult.verdict()` turns those into a sentence, and the GUI leads with it
rather than with the winning row. A grid search that reports only its winner is
mostly measuring how many tries it had.

---

## Point-in-time fundamentals

`Dataset` indexes fundamental records by **knowledge date** — quarter end plus
`report_lag_days`, default 60 — not by the period they describe. A record for
the quarter ending 2021-06-30 first appears to `ctx.fundamentals()` on
2021-08-29.

Sixty days is deliberately conservative. US filers have 40 days (large
accelerated) to 45 days (everyone else) after quarter end to file a 10-Q, so 60
clears the deadline rather than assuming everyone files on it. The parameter is
exposed; shortening it is a research choice, and one that flatters the
backtest.

The lookup itself is a bisect over a sorted list of knowledge dates, so it
costs nothing to do it correctly.

---

## Performance

A run over 1,747 symbols × 1,126 bars with three cross-sectional strategies
completes in about twelve seconds. Two things make that possible:

* **Close prices are cached as flat NumPy arrays per symbol**, not read through
  `.iloc`. `history()` is called once per symbol per bar — hundreds of
  thousands of times — and pandas indexing is far too slow at that count.
* **Hedge ratios use the closed-form OLS slope** rather than
  `statsmodels.OLS(...).fit()`. It is one covariance over one variance, and it
  is roughly two orders of magnitude faster for a job that runs on every pair
  on every bar.

Neither changes a result. Both were necessary to make the GUI's run-and-look
loop fast enough that you actually use it.

---

## What is deliberately not here

* **A live trading path.** This measures strategies. Routing orders to a real
  broker is a different program with different failure modes.
* **A charting library.** See `docs/design-system.md`.
* **An optimiser.** The sweeper enumerates a declared grid and tells you how
  much of its winner is luck. Anything that searches harder makes that number
  worse, and the honest response to "the grid found nothing" is a better
  hypothesis, not a better search.
* **Portfolio-level risk management across sleeves.** Sleeves are independent
  by design (see above). Cross-strategy allocation is a real problem and it is
  a different one.
