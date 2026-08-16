# Ported changes

Five of the six strategies predate the platform. This is every place the port
changed what the original code *did*, as opposed to how it got its data.

The list exists because a silently corrected bug is worse than an uncorrected
one: someone comparing the new output against the old finds a discrepancy,
cannot explain it, and stops trusting both. Where the difference is
interesting, the old behaviour is kept runnable behind a flag rather than
described.

Changes that are **not** listed, because they apply to every port: data access
moved to the hub, cash and positions moved to the portfolio, fill prices moved
to the cost model, and hardcoded constants became declared `Param`s.

---

## `mean_reversion` — the stop-loss comparison had the wrong sign

**Original:** `paper-broker/algos/meanReversionClass.py`, and identically in
`paper/meanReversionBacktestClass.py`.

```python
sell = False
if change < stop_loss:   change is open P&L in dollars
    sell = True
if change > take_profit:
    sell = True
```

`stop_loss` was passed as a positive dollar figure — the sweep used 200, 500
and 800. So `change < stop_loss` is true whenever open P&L is below **plus**
$200, which includes every losing position, every flat position, and every
winning position up to $200. The stop fired on almost every bar a position was
open, and it fired on winners.

The intent is not ambiguous: a variable named `stop_loss` swept over 200 / 500
/ 800 alongside a `take_profit` swept over the same values is meant to be "exit
if I am down more than this much". The port compares against `-stop_loss`.

**Effect:** the ported strategy holds positions far longer and trades far less
than the original. It is not a small difference, and results from the original
sweep are not comparable to results from this one.

**Not kept behind a flag**, unlike the `bw_cross_sectional` case below: the old
behaviour is not an interesting alternative hypothesis, it is a typo whose only
effect is to close positions at random.

---

## `mean_reversion` — position sizing

**Original:** `shares_to_trade = int(cash // price)` — the entire account into
one name.

That was correct for what the original was: a script that looked at one symbol.
Run across a universe it is not sizing, it is a race, and the first symbol in
the list gets everything.

**Port:** a `position_fraction` parameter (default 0.9) split evenly across the
symbols in the universe. On a single-symbol universe with `position_fraction=1.0`
this reduces to the original.

---

## `stat_arb` — the hedge ratio window

**Originals:** `paper-broker/algos/statArbClass.py` (live) fitted β over a
freshly downloaded window that **included the current price**.
`paper/backTestStat.py` (backtest) fitted it over `data.iloc[i-lookback:i]`.

These disagree, and only one of them is implementable: you cannot fit a
regression on a window that contains the price you are about to trade at.

**Port:** the backtest version. β, the spread mean and the spread standard
deviation all come from the trailing window, re-estimated every bar.

---

## `stat_arb` — OLS by closed form

The original called `statsmodels.api.OLS(y, sm.add_constant(x)).fit()` and read
`model.params[sym2]`. The port computes the slope directly as
`cov(x, y) / var(x)`.

**This is not a behaviour change** — the values agree to floating point. It is
here for completeness because the diff looks like one. The reason is speed:
this runs on every pair on every bar, and `.fit()` builds a full results object
to return one number.

---

## `stat_arb` — a stop on the z-score

**New, not ported.** The original had no exit other than mean reversion, so a
spread that stopped reverting was held indefinitely, which is how a pairs book
takes its worst losses.

Added as `stop_z`, default 4.0, and **settable to 0 to disable** — so the
original's behaviour is one parameter away and the cost of the addition is
measurable.

---

## `stat_arb_ev` — costs come from the run, not from a config file

**Original:** `stat-arb-v2/core/ev_filter.py` read `trading_fee_pct` and
`slippage_pct` from its own `config/settings.py`.

That let the filter's assumptions drift away from the backtester's. A filter
that rejects trades using 5bp costs, inside a backtest that fills at 1bp, is
answering a question nobody asked.

**Port:** the filter reads `ctx.costs.round_trip_bps()` — the same cost model
that will actually price its fills. Changing the scenario in the GUI now
changes what the filter rejects, which is the correct coupling and makes the
"how much does the filter depend on the cost assumption" experiment a
one-dropdown change.

The four gates themselves — half-life, breakeven z, reward/risk, minimum EV —
are preserved with their original thresholds as defaults.

---

## `stat_arb_ev` — half-life estimation

**Original:** half-life came from the partial-cointegration Kalman filter in
`cointegration/partial_coint.py`, which depended on the C++ `stat_engine`
compiled through `ctypes`.

**Port:** a direct Ornstein-Uhlenbeck fit — regress Δspread on lagged spread,
`half_life = −ln2 / ln(1 + b)`. Pure NumPy, no build step.

The two agree closely on well-behaved spreads and diverge on spreads with a
large random-walk component, where the Kalman version is the better estimate.
This is a real capability loss and it is the one change here made for
portability rather than correctness. If the C++ engine is reintroduced, this is
the first thing that should switch back.

---

## `bw_valuation` — one anchor table from two files

**Originals:** `fundamentals-v1/quality/weighting.py` held `highs_and_lows`;
`fundamentals-v1/valuation/metrics.py` held `range_settings`. They covered
different metric sets and used **different normalisers** — quality clipped at
1.2 and divided by 1.2 to leave headroom, valuation clipped hard to the range.

The port reassembles both into one table over the eight metrics the
point-in-time dataset carries, and **keeps both normalisers**, applying each to
the metrics it originally governed. The `style` column in `ANCHORS` records
which is which. Collapsing them to one normaliser would have been tidier and
would have changed every score.

The category weights are exposed as parameters. The original averaged its six
category scores equally, which `1/1/1` reproduces.

---

## `bw_cross_sectional` — the lookahead bug cannot be reproduced

**Original:** `fundamentals-v2/score.py` built each metric's distribution from
every ticker in every quarter at once, then ranked each company-quarter against
that pool.

There is no flag for this. `ctx.cross_section()` returns only what was knowable
at the current timestamp, so the ported strategy is structurally incapable of
pooling across dates. This is the one place where the platform did not preserve
an option — see `docs/architecture.md` for why that was the right call.

---

## `bw_cross_sectional` — the inverted directions *are* reproducible

**Original:** the percentile helper took `higher_is_better` and was called with
the default `True` for all eight metrics. Lower is better for `Net Debt/EBITDA`,
`EV/EBITDA` and `P/E`. Net Debt/EBITDA is the only input to the risk term, so
the risk component ran fully backwards: the model preferred leveraged,
expensive companies and scored that as prudence.

**Kept, behind `legacy_directions=True`.** Unlike the mean-reversion typo, this
one is worth being able to run: the corrected model looks good, and a corrected
model that looks good needs its counterfactual executable rather than described.
Select the strategy twice in the console with the flag set differently and the
two curves come back on the same page.

All three scoring methods from the original — `percentile`, `zscore` (median
centred), `minmax` — are preserved as a `method` parameter, including `minmax`,
which the original author moved away from because two extreme companies decide
everybody's score. Keeping it means that conclusion is demonstrable rather than
folklore.

---

## `bw_cross_sectional` — winsorisation

**New.** A `winsorise` parameter (default 2% per tail) clips each metric's
cross-section before scoring.

The original had no outlier handling, which is defensible for `percentile` —
rank statistics are already robust — and is not defensible for `zscore` or
`minmax`, where a single company with a P/E of 4,000 moves everybody. Set it to
0 to reproduce the original exactly.
