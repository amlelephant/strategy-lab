"""
lab/core/metrics.py
===================
Turning an equity curve and a trade log into numbers, including the numbers
that say how much to trust the other numbers.

Most backtest reports stop at return, Sharpe and max drawdown. Those three
describe the sample; none of them describes the *sample size*. A Sharpe of
0.67 over 15 quarterly observations and a Sharpe of 0.67 over 15 years are
the same number and completely different claims, and the first is not
distinguishable from luck.

So `summarise()` also reports:

  * `sharpe_stderr` ≈ sqrt((1 + S²/2) / n) — the standard error of an
    estimated Sharpe ratio (Lo, 2002), for n independent observations.
  * `sharpe_t` — the Sharpe divided by that error. Below about 2, the result
    is not evidence.
  * `observations` — n, stated plainly so nobody has to infer it.

## Sharpe is the wrong question, and `alpha()` is the right one

A significant Sharpe says "this made money at a rate unlikely to be chance".
It does not say the strategy was worth running, because **owning the market and
doing nothing** also makes money at some rate, and over 2021-2025 it made
rather a lot. A long-only strategy in a rising market inherits the market's
Sharpe and can look skilful while having added nothing.

The benchmark is the S&P 500, not the run's own universe. Measuring a strategy
against the names it picked credits it for the picking — a rule applied to four
names that beat the market looks skilful even when the rule did nothing and the
names did all the work. The equal-weight hold of the run's own universe is
still available — as a curve the result page can draw, not as a yardstick.

The question a strategy has to answer is whether it beat the alternative of
not bothering. That is `alpha()`, and it is the number the result page leads
with:

  * **alpha** — the intercept of a regression of the strategy's returns on the
    benchmark's (Jensen, 1968), annualised. What the strategy earned that the
    benchmark's movement does not explain.
  * **beta** — the slope. How much of the strategy *is* the benchmark. A
    long-only book that tracks its universe has a beta near 1 and needs a
    positive alpha to have justified itself.
  * **alpha_t** — the regression t-statistic, with Newey-West standard errors
    so that autocorrelated daily returns do not inflate it. This is what makes
    a result conclusive or not.
  * **information_ratio** — active return over tracking error: the same
    comparison without the risk adjustment, which is the form most people mean
    by "did it beat the benchmark".

A strategy can have a fine Sharpe and a negative alpha. That combination is
common, it is the single most useful thing a backtest can tell you, and it is
invisible in any report that stops at the first three numbers.

## The headline is `active_return`, not `alpha`

`alpha` and `active_return` answer different questions and routinely disagree:

  * **active_return** — the plain annualised (strategy − benchmark). Whether it
    ended with more money than the index. **This is the headline.**
  * **alpha** — what it earned per unit of market risk taken. Secondary, and
    reported next to the beta that gives it meaning.

Alpha divided by a small beta is a big number for anything that made money
without market exposure. A market-neutral book returning 3% a year while the
index returned 16% has a beta of 0.00 and therefore an alpha of +3%, and
leading with that puts "+3% alpha" at the top of a result that lost to the
index by thirteen points a year. The regression is not wrong; it is answering
"per unit of risk", and a reader looking at an equity curve is asking "did it
make more money".

So the plain comparison leads, `has_alpha` requires actually beating the
benchmark as well as clearing its t-test, and no surface says "beats the
market" about a strategy that did not.

## The risk-free rate is not zero

Both Sharpe and alpha are defined on returns *in excess of* the risk-free rate.
Assuming that rate is zero is not a rounding error — regressing raw returns
instead of excess returns gives an intercept of `rf·(1 − β)`, which is free
alpha handed to exactly the strategies that hold cash. At β = 0.32 and a 1.6%
average bill rate that is +1.1% a year of alpha nobody earned, and it was
enough to move a t-statistic from 2.6 to 2.0 — from "significant" to not.

`data/riskfree_3m.csv` is the 13-week T-bill, and it is a *series*, not a
constant: it was 4.7% in 2006, 0.02% in 2014 and 5% in 2023, and a strategy
that holds cash is affected by which of those it held cash through.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0

#: What a result is measured against when the run has no market series — an
#: equal-weight, frictionless hold of the run's own universe. This is the
#: fallback, not the default: it answers "did this beat holding these names",
#: which flatters any strategy whose universe happened to lag the market and
#: punishes one whose universe happened to lead it. See `benchmark_equity`.
FALLBACK_BENCHMARK_LABEL = "the equal-weight universe"

#: Retained under its old name because it is imported in several places.
BENCHMARK_LABEL = FALLBACK_BENCHMARK_LABEL


@dataclass
class Performance:
    """A full performance summary. All rates are decimals, not percents."""

    starting_equity: float = 0.0
    ending_equity: float = 0.0
    total_return: float = 0.0
    cagr: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: float = 0.0
    calmar: float = 0.0

    observations: int = 0
    sharpe_stderr: float = float("nan")
    sharpe_t: float = float("nan")

    # ── versus the benchmark: whether this was worth running at all ─────
    #: Annualised Jensen's alpha. NaN when no benchmark was supplied.
    alpha: float = float("nan")
    alpha_t: float = float("nan")
    alpha_p: float = float("nan")
    beta: float = float("nan")
    #: Annualised mean of (strategy − benchmark) per-period returns.
    active_return: float = float("nan")
    tracking_error: float = float("nan")
    information_ratio: float = float("nan")
    information_ratio_t: float = float("nan")
    benchmark_label: str = ""
    #: Mean annualised risk-free rate the excess returns were taken over.
    #: 0.0 means cash was assumed to pay nothing.
    risk_free_rate: float = 0.0

    trades: int = 0
    round_trips: int = 0
    hit_rate: float = float("nan")
    profit_factor: float = float("nan")
    avg_trade_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0

    total_commission: float = 0.0
    total_slippage: float = 0.0
    turnover: float = 0.0
    time_in_market: float = 0.0

    years: float = 0.0
    periods_per_year: float = TRADING_DAYS

    def as_dict(self) -> dict[str, Any]:
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                for k, v in self.__dict__.items()}

    @property
    def is_significant(self) -> bool:
        """Whether the Sharpe clears the conventional |t| > 2 bar.

        This is a low bar and passing it is not a licence to trade. It is here
        so that failing it is visible rather than something a reader has to
        work out.
        """
        return math.isfinite(self.sharpe_t) and abs(self.sharpe_t) > 2.0

    @property
    def has_alpha(self) -> bool:
        """Whether the strategy beat the market, by more than chance.

        **Outperformance is required, not just a positive intercept.** Jensen's
        alpha divided by a small beta is a large number for any strategy that
        made money without market exposure, and a market-neutral book earning
        3% while the index earned 16% scores +3% alpha at beta 0.00. That is
        what the regression means and it is not what "has alpha" should be
        allowed to claim, because the alternative to running the strategy was
        owning the index and ending with far more money.

        So: ahead of the benchmark outright, *and* the risk-adjusted figure
        survives its own t-test.
        """
        return (self.outperformed and math.isfinite(self.alpha_t)
                and self.alpha_t > 2.0 and self.alpha > 0)

    @property
    def _against(self) -> str:
        # Used verbatim, never lower-cased: "s&p 500" is not a thing. Both
        # labels are written to read after "over" and inside "— … —".
        return self.benchmark_label or "the benchmark"

    @property
    def outperformed(self) -> bool:
        """Whether it simply ended with more money than the benchmark.

        Deliberately separate from `has_alpha`. Alpha is a *rate per unit of
        market risk taken*; this is the plain question, and the two can
        disagree — a rule holding cash two days in three can earn a large
        positive alpha while finishing far below the index, because it was
        never exposed to most of the index's rise.
        """
        return math.isfinite(self.active_return) and self.active_return > 0

    def verdict(self) -> str:
        """One sentence, leading with whether it beat the market.

        The plain comparison comes first because it is the one a reader can
        check against the chart. Leading with the risk-adjusted figure put
        "+3.09% alpha" at the top of a strategy that trailed the index by 13
        points a year, and no amount of accompanying detail undoes a headline
        that says the opposite of what happened.
        """
        if not math.isfinite(self.active_return):
            return "No benchmark to compare against."

        plain = f"{self.active_return:+.2%} a year vs {self._against}"

        if not math.isfinite(self.alpha_t):
            return f"{plain}."

        risk_adj = (f"{self.alpha:+.2%} against its {self.beta:.2f} beta "
                    f"(t = {self.alpha_t:.1f})")

        if not self.outperformed:
            tail = f"Risk-adjusted, {risk_adj}."
            # Only worth explaining where it is actually the explanation: a
            # low beta is what turns a losing return into a positive alpha.
            # Saying it about a beta of 0.99 would be nonsense.
            if math.isfinite(self.beta) and self.beta < 0.8 and self.alpha > 0:
                tail += (" That measures how little market exposure it"
                         " carried, not a better return.")
            return f"{plain} — it did not beat the market. {tail}"

        if self.alpha_t > 2.0 and self.alpha > 0:
            return (f"{plain} — beat the market, and {risk_adj} survives "
                    f"adjusting for risk.")

        return (f"{plain} — ahead of the market, but {risk_adj} is not "
                f"distinguishable from luck.")

    def headline(self) -> str:
        return (f"{self.total_return:+.2%} total · "
                f"{self.active_return:+.2%}/yr vs benchmark · "
                f"alpha {self.alpha:+.2%} (t={self.alpha_t:.1f}, "
                f"beta {self.beta:.2f}) · Sharpe {self.sharpe:.2f} "
                f"(t={self.sharpe_t:.1f}, n={self.observations}) · "
                f"max DD {self.max_drawdown:.2%} · {self.round_trips} trades")


def _per_period_rate(risk_free, index: pd.Index,
                     periods_per_year: float) -> np.ndarray:
    """De-annualise a risk-free rate onto `index`, scalar or series alike.

    A scalar is the constant-rate case; a Series is the honest one, because
    the rate is not a constant — it was 5% in 2007, 0.02% in 2014 and 5%
    again in 2023, and a strategy that sits in cash is affected by which of
    those it sat through.
    """
    if risk_free is None:
        return np.zeros(len(index), dtype=float)
    if isinstance(risk_free, pd.Series):
        aligned = risk_free.reindex(index).ffill().bfill()
        values = aligned.to_numpy(dtype=float)
        return np.nan_to_num(values, nan=0.0) / periods_per_year
    return np.full(len(index), float(risk_free) / periods_per_year)


def drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1.0


def summarise(equity: pd.Series, *, periods_per_year: float = TRADING_DAYS,
              trade_log: pd.DataFrame | None = None,
              exposure: pd.Series | None = None,
              risk_free=None) -> Performance:
    """Full summary from an equity curve, plus trade stats if a log is given.

    `risk_free` is an annual rate — a float, or a Series on the equity curve's
    own index — de-annualised before being subtracted from per-period returns.
    Sharpe and Sortino are both defined on excess returns; passing nothing
    means assuming cash pays zero, which it has not since 2021.
    """
    equity = pd.Series(equity).astype(float).dropna()
    perf = Performance(periods_per_year=periods_per_year)
    if len(equity) < 2 or equity.iloc[0] == 0:
        return perf

    perf.starting_equity = float(equity.iloc[0])
    perf.ending_equity = float(equity.iloc[-1])
    perf.total_return = perf.ending_equity / perf.starting_equity - 1.0

    returns = equity.pct_change().dropna()
    returns = returns[np.isfinite(returns)]
    perf.observations = int(len(returns))
    if perf.observations < 2:
        return perf

    perf.years = perf.observations / periods_per_year
    if perf.years > 0 and perf.ending_equity > 0:
        perf.cagr = (perf.ending_equity / perf.starting_equity) ** (1 / perf.years) - 1.0

    rf_per_period = _per_period_rate(risk_free, returns.index, periods_per_year)
    perf.risk_free_rate = float(np.mean(rf_per_period)) * periods_per_year
    excess = returns - rf_per_period

    sd = float(excess.std(ddof=1))
    perf.volatility = sd * math.sqrt(periods_per_year)
    if sd > 0:
        perf.sharpe = float(excess.mean()) / sd * math.sqrt(periods_per_year)

    downside = excess[excess < 0]
    dsd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    if dsd > 0:
        perf.sortino = float(excess.mean()) / dsd * math.sqrt(periods_per_year)

    # Lo (2002): SE(Ŝ) ≈ sqrt((1 + Ŝ²/2) / n) for iid returns, on the
    # per-period Sharpe. Annualising both keeps the ratio unchanged, so the
    # t-statistic is computed on the per-period figure.
    per_period_sharpe = perf.sharpe / math.sqrt(periods_per_year)
    perf.sharpe_stderr = math.sqrt(
        (1.0 + 0.5 * per_period_sharpe ** 2) / perf.observations)
    if perf.sharpe_stderr > 0:
        perf.sharpe_t = per_period_sharpe / perf.sharpe_stderr

    dd = drawdown_series(equity)
    perf.max_drawdown = float(dd.min())
    perf.max_drawdown_days = _longest_drawdown_days(equity, dd)
    if perf.max_drawdown < 0:
        perf.calmar = perf.cagr / abs(perf.max_drawdown)

    if exposure is not None and len(exposure):
        perf.time_in_market = float((pd.Series(exposure) > 0).mean())

    if trade_log is not None and not trade_log.empty:
        perf.trades = int(len(trade_log))
        closes = trade_log[trade_log["action"] == "close"] \
            if "action" in trade_log.columns else trade_log
        perf.round_trips = int(len(closes))
        if perf.round_trips:
            pnl = closes["realised_pnl"].astype(float)
            perf.hit_rate = float((pnl > 0).mean())
            wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
            perf.profit_factor = float(wins / losses) if losses > 0 else float("inf")
            perf.avg_trade_pnl = float(pnl.mean())
            perf.best_trade = float(pnl.max())
            perf.worst_trade = float(pnl.min())
        if "commission" in trade_log.columns:
            perf.total_commission = float(trade_log["commission"].sum())
        if "slippage" in trade_log.columns:
            perf.total_slippage = float(trade_log["slippage"].sum())
        if {"quantity", "price"} <= set(trade_log.columns):
            notional = (trade_log["quantity"] * trade_log["price"]).sum()
            avg_equity = float(equity.mean())
            if avg_equity > 0 and perf.years > 0:
                perf.turnover = float(notional / avg_equity / perf.years)

    return perf


def alpha(perf: Performance, equity: pd.Series, benchmark: pd.Series, *,
          periods_per_year: float = TRADING_DAYS, risk_free=None,
          label: str = "benchmark") -> Performance:
    """Fill in `perf`'s benchmark-relative fields, in place, and return it.

    Regresses the strategy's excess returns on the benchmark's:

        r_s − rf = α + β·(r_b − rf) + ε

    α is the part of the return the benchmark's movement does not explain —
    annualised here by multiplying by the period count, which is the standard
    convention and slightly overstates compounding.

    **Newey-West standard errors** (lag ≈ n^(1/4), the usual rule of thumb).
    Daily strategy returns are autocorrelated — a position held for weeks
    produces weeks of correlated returns — and plain OLS errors treat every
    day as independent evidence, which inflates the t-statistic in exactly the
    direction that flatters the strategy. This is the same discipline the
    Sharpe t-statistic gets from Lo (2002) elsewhere in this file.

    Both series are aligned on their shared dates first: a sleeve that started
    late or a benchmark with a different calendar must not silently regress
    mismatched rows against each other.
    """
    strat = pd.Series(equity).astype(float).dropna()
    bench = pd.Series(benchmark).astype(float).dropna()
    perf.benchmark_label = label

    joined = pd.concat([strat, bench], axis=1, join="inner").dropna()
    if len(joined) < 3:
        return perf

    returns = joined.pct_change().dropna()
    returns = returns[np.isfinite(returns).all(axis=1)]
    if len(returns) < 3:
        return perf

    rf = _per_period_rate(risk_free, returns.index, periods_per_year)
    perf.risk_free_rate = float(np.mean(rf)) * periods_per_year
    r_s = returns.iloc[:, 0].to_numpy(dtype=float) - rf
    r_b = returns.iloc[:, 1].to_numpy(dtype=float) - rf

    # Active return and information ratio — the un-risk-adjusted comparison.
    active = r_s - r_b
    perf.active_return = float(np.mean(active)) * periods_per_year
    sd = float(np.std(active, ddof=1))
    perf.tracking_error = sd * math.sqrt(periods_per_year)
    if sd > 0:
        perf.information_ratio = (float(np.mean(active)) / sd
                                  * math.sqrt(periods_per_year))
        # t of the mean active return: IR_annual · sqrt(years).
        perf.information_ratio_t = (perf.information_ratio
                                    * math.sqrt(len(active) / periods_per_year))

    if float(np.std(r_b, ddof=1)) <= 0:
        return perf

    try:
        import statsmodels.api as sm

        model = sm.OLS(r_s, sm.add_constant(r_b)).fit(
            cov_type="HAC",
            cov_kwds={"maxlags": max(1, int(len(r_s) ** 0.25)), "use_correction": True})
        perf.alpha = float(model.params[0]) * periods_per_year
        perf.beta = float(model.params[1])
        perf.alpha_t = float(model.tvalues[0])
        perf.alpha_p = float(model.pvalues[0])
    except Exception:                                        # noqa: BLE001
        # statsmodels is a declared dependency, but a metrics helper is not
        # where a run should die. Fall back to the plain OLS closed form and
        # leave the t-statistic missing rather than reporting an optimistic
        # one computed a different way than the docstring claims.
        cov = float(np.cov(r_s, r_b, ddof=1)[0, 1])
        var = float(np.var(r_b, ddof=1))
        perf.beta = cov / var if var > 0 else float("nan")
        if math.isfinite(perf.beta):
            perf.alpha = (float(np.mean(r_s)) - perf.beta * float(np.mean(r_b))
                          ) * periods_per_year
    return perf


def _longest_drawdown_days(equity: pd.Series, dd: pd.Series) -> float:
    """Calendar days of the longest stretch spent below a prior peak."""
    underwater = dd < -1e-12
    if not underwater.any():
        return 0.0
    longest = pd.Timedelta(0)
    start: pd.Timestamp | None = None
    for ts, under in underwater.items():
        if under and start is None:
            start = ts
        elif not under and start is not None:
            longest = max(longest, ts - start)
            start = None
    if start is not None:
        longest = max(longest, underwater.index[-1] - start)
    return float(longest.days) if isinstance(longest, pd.Timedelta) else 0.0


def benchmark_label(dataset) -> str:
    """What this dataset's benchmark should be called on a chart or in a table."""
    return getattr(dataset, "benchmark_label", "") or FALLBACK_BENCHMARK_LABEL


def has_market_benchmark(dataset) -> bool:
    return getattr(dataset, "benchmark", None) is not None


def benchmark_equity(dataset, starting_cash: float = 100_000.0) -> pd.Series:
    """The frictionless curve every sleeve's alpha is measured against.

    The market, when the dataset carries one — the S&P 500, held over the run's
    own bars. The alternative to running a strategy is not holding the same
    names it happened to pick; it is owning the market, which is what a reader
    could have done instead without any of this. A strategy whose universe
    simply happened to be four good names should not be credited with alpha for
    it, and measuring against the universe itself did exactly that.

    Falls back to an equal-weight hold of the universe when no market series is
    attached, and says so through `benchmark_label`, because a run on a clone
    with an empty `data/` should still produce numbers — just not numbers that
    quietly claim to be market-relative.

    This curve pays no costs, which overstates it slightly and in the
    strategy's disfavour. Owning the index is not free either.
    """
    series = getattr(dataset, "benchmark", None)
    if series is not None:
        # bfill covers a dataset that starts before the benchmark prints; the
        # leading stretch is then flat, which is visible on the chart rather
        # than a hole that silently shortens the regression.
        level = series.ffill().bfill()
        first = float(level.iloc[0])
        if math.isfinite(first) and first > 0:
            return level / first * starting_cash

    return universe_equity(dataset, starting_cash)


def universe_equity(dataset, starting_cash: float = 100_000.0) -> pd.Series:
    """Equal-weight, frictionless hold of the run's own universe.

    Not a benchmark and not a strategy — an *overlay*. It answers "how did the
    names this run traded do on their own", which is worth being able to see
    and is not worth being measured against: a rule applied to four names that
    beat the index is not skilful if those four names did all the work.

    This used to be a registered strategy (`buy_and_hold`) occupying a sleeve
    in every run. It is a curve, so it is drawn as one.
    """
    closes = pd.DataFrame(
        {s: dataset._fields["close"][s] for s in dataset.symbols},
        index=dataset.index).ffill()
    normalised = closes / closes.bfill().iloc[0]
    return normalised.mean(axis=1) * starting_cash
