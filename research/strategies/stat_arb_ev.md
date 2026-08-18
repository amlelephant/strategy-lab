## The claim

Same signal as `stat_arb` — same hedge ratio, same z-score — but it won't
enter unless the arithmetic works. The thesis, stated once so the four gates
make sense: a retail account cannot compete with anyone on sub-day
timescales, so the only spreads worth trading are those that revert slowly
enough that execution latency doesn't matter, *and* whose expected capture is
large relative to both the cost of trading and the volatility of holding
through the reversion.

## The four gates

Each rejects for a different reason, and each rejection is written to the run
log — so "how many trades did the half-life gate refuse" is a question with
an answer, not an assumption.

1. **Half-life.** Too fast (below `min_half_life`) is somebody else's game —
   the domain of participants with better fills and lower latency than a
   backtest can claim for a retail account. Too slow (above `max_half_life`)
   ties up capital in a position that may not revert this quarter. This gate
   does most of the work.
2. **Breakeven z.** The minimum |z| at which expected capture covers
   commission and slippage *at this position size*, derived from the run's
   own cost model — never a hardcoded number, so changing the cost scenario
   in the console changes what this gate rejects.
3. **Reward/risk.** Net expected value against one standard deviation of
   hold-period spread movement. A positive EV that's 3% of the noise around
   it isn't a trade, it's a coin flip with a small tilt.
4. **Minimum EV.** An absolute dollar floor, so tiny positions where fees
   dominate never get taken.

An earlier version of this filter gated on `net_ev > $10` and
`sharpe_contribution > 0.5`, and rejected nothing in any backtest run against
it — a $12k position at z = 2.2 clears a $10 floor trivially. A filter that
never fires is worse than no filter, because it looks like risk management
without doing any.

## What it does to the trade count

Four names (KO/PEP, XOM/CVX), same period, same costs as the unfiltered
version:

| | Return | Sharpe | t | Max DD | Trades |
|---|---|---|---|---|---|
| Statistical Arbitrage (z-score) | −27.84% | −0.98 | −2.1 | −29.98% | 96 |
| **Statistical Arbitrage (EV-filtered)** | **−24.16%** | **−0.92** | −1.9 | **−26.37%** | **92** |
| Buy and Hold | +88.20% | 0.88 | 1.9 | −14.37% | 4 |

The filter rejected 25 of 71 signals — 15 of them on a half-life under two
days, the regime where execution quality decides the outcome and this isn't
the strategy deciding it. It improved the result. **Both versions still
lose**, and lose to holding the same names. Run this beside `stat_arb` on the
same universe; the interesting output isn't which one earns more, it's the
rejection log, which says how many of the baseline's trades were never worth
taking and which gate caught them.

## Where the half-life estimate changed

The original filter's half-life came from a Kalman-filtered partial-
cointegration model, running through a compiled C++ engine
(`stat-arb-v2/core/stat_engine.cpp`) via `ctypes`. The port replaces it with a
direct Ornstein-Uhlenbeck fit in NumPy — regress Δspread on lagged spread,
`half_life = −ln2 / ln(1 + b)`. The two agree closely on well-behaved spreads
and diverge on spreads with a large random-walk component, where the Kalman
version is the better estimate. **This is a real capability loss**, accepted
for portability — no build step, no compiled dependency — and it's the one
change in this codebase made for that reason rather than for correctness. See
[`research/ported-changes.md`](../ported-changes.md).

## What this doesn't establish

A filter that rejects the worst of a losing strategy's trades and still loses
is doing its job, not failing at it — the honest reading is that the fast end
of daily-bar pairs trading on liquid names belongs to participants this
backtest cannot represent, which is exactly what the filter's thesis
predicted before any of this was run. That prediction being consistent with
the result is not the same as the result being large; both Sharpe ratios sit
below |t| = 2.
