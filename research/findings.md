# Findings

Three defects, each found by checking an implementation against the source it
claimed to follow, and each **quantified rather than merely noted**. Every
number below regenerates with:

```bash
python research/reproduce.py
```

The pattern is the same in all three cases. The code ran. It produced plausible
output. It was wrong in a direction that made the results look better, and the
only way to notice was to go back to the paper.

---

## 1 — Engle-Granger tested residuals against the wrong table

**Where:** the cointegration implementation, step 2a, now
`lab/analysis/cointegration.py`.

The two-step procedure regresses `y` on `x` and asks whether the residuals `û`
are stationary. The implementation passed `û` straight to
`statsmodels.adfuller()`.

That is the wrong reference distribution. `û` is not observed data — it is the
residual of a regression **fitted to minimise its own variance**. That fitting
makes `û` look more stationary than an equivalent observed series would, so the
standard Dickey-Fuller table rejects the null too often. Residual-based
cointegration tests need MacKinnon's critical values, which
`statsmodels.coint()` implements and `adfuller()` does not.

Measured on this repository's own data — 33 large caps, 528 pairs, 1,005
trading days, 2021–2024 — changing **nothing but the table**:

| Test | Pairs called cointegrated at 5% |
|---|---|
| `adfuller()` on residuals | 90 (17.0%) |
| `coint()` with MacKinnon values | **42 (8.0%)** |

**A 2.14× over-rejection.** Same regression, same residuals, same lag
selection, different table. Roughly half of the "cointegrated" pairs were an
artefact of the test.

Reproduce by flipping `use_residual_critical_values` on `engle_granger()`. The
correct table is the default.

### And step two, which had been dropped entirely

Engle-Granger has two steps. Step one says a pair is *out* of equilibrium. Step
two fits the error-correction model and estimates **α**, the speed at which it
comes *back*:

```
Δy_t = φ₀ + Σφⱼ·Δy_{t-j} + Σθₕ·Δx_{t-h} + α·û_{t-1} + ε_t
half-life = −ln 2 / ln(1 + α)
```

A generated implementation of this procedure omitted step two. Without α you
can tell that a spread is stretched but not whether it will come back this
month or this decade, and **a pair can be cointegrated at p = 0.01 with α ≈ 0**
— a real long-run relationship that never actually corrects. Statistically
significant and completely untradeable, at the same time.

Adding it back removes more pairs:

| | Pairs |
|---|---|
| Cointegrated (correct table) | 42 (8.0%) |
| ...and α < 0 with a half-life under 60 days | **35 (6.6%)** |

Seven more pairs, one in six of the survivors, pass the test and do not revert.

---

## 2 — The pairs strategy was trading spurious relationships

**Where:** `lab/strategies/stat_arb.py`, and every version of it that came
before.

The strategy computed a hedge ratio by OLS and traded the spread's z-score. It
never asked whether the two series were related. Two stocks that drift upward
together for three years produce a confident hedge ratio, a tight-looking
spread and a clean z-score whether or not anything connects them — that is
precisely the spurious-regression problem the previous finding is about, sitting
inside a live decision rule.

Adding `require_cointegration=True` gates entries on the corrected
Engle-Granger test, re-run every 21 bars. On eight consumer and energy names,
2021–2025, at 6bp round trip:

| | Return | Sharpe | t | Max DD | Trades |
|---|---|---|---|---|---|
| Unscreened | −16.76% | −0.84 | −1.8 | −16.77% | 258 |
| **Cointegration-screened** | **−4.68%** | **−0.68** | −1.4 | **−6.76%** | **28** |
| Buy and hold | +62.49% | 0.85 | 1.8 | −11.93% | 8 |

The screen refused 584 signals and cut the trade count by 89%. It removed about
three quarters of the loss and more than half the drawdown.

**It did not make the strategy work.** Both versions lose, and both lose to
buying the same eight stocks and doing nothing. The finding is not "screening
fixes pairs trading" — it is that **most of what this strategy was trading was
never a relationship at all**, and that the cost of finding that out was one
statistical test it had never been running.

---

## 3 — The fundamental scorer looked ahead, and inverted three metrics

**Where:** `fundamentals-v2/score.py`, now `lab/strategies/bw_cross_sectional.py`.

**Lookahead.** Each metric's distribution was pooled across all tickers **and
all sixteen quarters**, then every company-quarter was ranked against that pool
— so a 2021 score was computed partly from 2024 data. The upstream pipeline had
carefully anchored each row to its filing date; the scoring layer discarded
that.

This one is not reproducible, and that is the interesting part. `ctx.cross_section()`
returns only what was knowable at the current bar, so the ported strategy
**cannot** pool across dates. The bug did not get caught, it stopped being
expressible. That is the entire argument for building a platform instead of
another script.

**Inverted directions.** The percentile helper took a `higher_is_better` flag
and was called with the default `True` for all eight metrics. Lower is better
for `Net Debt/EBITDA`, `EV/EBITDA` and `P/E`. Net Debt/EBITDA is the sole input
to the risk term, so the risk component ran **fully backwards**: the model
preferred leveraged, expensive companies and scored that as prudence.

That one is preserved behind `legacy_directions=True`, because a corrected
model that looks good needs its counterfactual runnable. On 1,747 companies,
2021–2025, quarterly rebalance at the filing date:

| | Return | CAGR | Sharpe | t | Max DD |
|---|---|---|---|---|---|
| **Corrected directions** | **+77.72%** | **13.75%** | **0.72** | 1.5 | −23.53% |
| Original, inverted | +26.50% | 5.41% | 0.36 | 0.8 | −32.87% |
| Absolute anchors (`bw_valuation`) | +52.41% | 9.90% | 0.58 | 1.2 | −25.21% |
| Buy and hold | +39.19% | 7.69% | 0.46 | 1.0 | −30.02% |

**One wrongly-defaulted keyword argument was worth 8.3 points of CAGR** and
nine points of drawdown. The buggy model barely beat holding the whole universe;
the corrected one beats it by six points a year.

### The older, dumber scorer never had either bug

`bw_valuation` — the *earlier* implementation — scores against hand-set
absolute anchors instead of a peer distribution. It needs no distribution, so
it could not pool across time. It encodes each metric's direction once in a
table, where a wrong entry is visible, instead of passing direction as an
argument that can be defaulted wrong in eight places.

It also earns less: 9.90% CAGR against the corrected model's 13.75%. Its
anchors are calibrated to US large caps and do not travel — they make every
grocer look bad and every software company look excellent.

The lesson is not "simpler is better". It is that **the sophisticated version
bought its extra return with two failure modes that the simple version was
structurally incapable of**, and only one of those two was ever noticed by
looking at output.

---

## The finding about the findings

The corrected fundamental model beats its control by six points of CAGR, which
is exactly the kind of number that should invite suspicion rather than a
victory lap. Its t-statistic is 1.5.

Every caveat is in [`results.md`](results.md). The short version: fixing these
bugs moved a model that was measurably wrong to one that is merely unproven.
Both of those statements are worth more than a Sharpe ratio.
