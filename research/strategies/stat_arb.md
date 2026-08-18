## The claim

Two related names' spread reverts to its mean. Estimate a hedge ratio by OLS,
build the spread `y − β·x`, and trade its z-score: short the spread when it is
stretched rich, long it when it is stretched cheap, close at the mean.

That is the entire decision rule, and on its own it says nothing about
whether `y` and `x` are actually related. Two stocks that drift upward
together for three years produce a confident β, a tight-looking spread and a
clean z-score whether or not anything connects them — running OLS on two
independent random walks produces exactly that pattern. This is the
spurious-regression problem, and it sits inside the strategy's live decision
rule unless something screens for it.

## What screens for it: cointegration, done properly

`require_cointegration=True` gates every entry on Engle-Granger, re-tested
every `retest_every` bars and cached by pair (`lab/analysis/cointegration.py`).
Getting that test right turned out to be most of the work:

**The residuals need MacKinnon's table, not the standard one.** Step one
regresses `y` on `x` and asks whether the residuals are stationary — but the
residuals come from a regression fit to minimise their own variance, so they
look more stationary than observed data does. The standard Dickey-Fuller
table doesn't know that, and over-rejects. Measured on 528 pairs from this
repository's own data, changing nothing but the reference table:

| Test | Pairs called cointegrated at 5% |
|---|---|
| `adfuller()` on residuals (wrong table) | 90 (17.0%) |
| `coint()`, MacKinnon residual-based table | **42 (8.0%)** |

A 2.14× over-rejection, from the table alone.

**Step two is not optional.** Engle-Granger has a second step that a purely
generated implementation of it dropped: fit the error-correction model and
estimate α, the speed the spread reverts at. Without it, a pair can be
cointegrated at p = 0.01 with α ≈ 0 — a real long-run relationship that never
actually corrects on any timescale you can hold. Requiring `alpha < 0` and a
half-life under 60 days removes seven more of the 42 survivors: one in six,
statistically significant and untradeable at the same time.

## What the screen does to the strategy

Eight consumer and energy names, 2021–2025, 6bp round trip:

| | Return | Sharpe | t | Max DD | Trades |
|---|---|---|---|---|---|
| Unscreened | −16.76% | −0.84 | −1.8 | −16.77% | 258 |
| **Cointegration-screened** | **−4.68%** | **−0.68** | −1.4 | **−6.76%** | **28** |
| Buy and hold, same universe | +62.49% | 0.85 | 1.8 | −11.93% | 8 |

The screen refused 584 signals — a 91% rejection rate — cut the trade count
by 89%, and removed roughly three quarters of the loss and more than half the
drawdown.

**It did not make the strategy work.** Both versions lose, and both lose to
buying the same eight names and doing nothing. The finding is not
"screening fixes pairs trading" — it's that most of what the unscreened
version was trading was never a relationship at all, and the cost of finding
that out was one statistical test it had never been running.

## What this doesn't establish

Daily-bar pairs trading on liquid US large caps reads, from this sample, like
a crowded space where a 6bp retail round trip is enough to erase what's left
after the spurious relationships are screened out. That's a real result, but
it's one dataset and one cost assumption. Short availability and borrow cost
aren't modelled — every result here assumes shorting is free and always
possible, which it isn't. And a screen tuned to remove losing trades on this
sample is a screen that has seen this sample; `stat_arb_ev` is the harder
version of the same idea, gating on whether a trade clears its own costs
rather than on whether it would have worked in hindsight.

## Where the code came from

Ported from `paper-broker/algos/statArbClass.py`, with the rolling-window
mechanics of `paper/backTestStat.py` — the decision rule is unchanged. The
cointegration machinery is ported from the cointegration-explorer research
app, where the MacKinnon-table finding and the missing error-correction step
were first caught by checking the implementation against Engle & Granger
(1987) directly. See [`research/findings.md`](../findings.md) for the
full derivation and [`research/ported-changes.md`](../ported-changes.md) for
every place the port changed behaviour.
