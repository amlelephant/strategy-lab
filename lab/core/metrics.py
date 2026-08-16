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

These are the first three fields the GUI shows on a result, above the return.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0


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

    def headline(self) -> str:
        return (f"{self.total_return:+.2%} total · Sharpe {self.sharpe:.2f} "
                f"(t={self.sharpe_t:.1f}, n={self.observations}) · "
                f"max DD {self.max_drawdown:.2%} · {self.round_trips} trades")


def drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return equity / peak - 1.0


def summarise(equity: pd.Series, *, periods_per_year: float = TRADING_DAYS,
              trade_log: pd.DataFrame | None = None,
              exposure: pd.Series | None = None,
              risk_free: float = 0.0) -> Performance:
    """Full summary from an equity curve, plus trade stats if a log is given.

    `risk_free` is an annual rate; it is de-annualised before being subtracted
    from per-period returns.
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

    rf_per_period = risk_free / periods_per_year
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


def benchmark_equity(dataset, starting_cash: float = 100_000.0) -> pd.Series:
    """Equal-weight buy-and-hold over the run's universe.

    Every result in the GUI is shown against this. A strategy that does not
    beat owning its own universe has not demonstrated anything, and that
    comparison is easy to omit by accident.
    """
    closes = pd.DataFrame(
        {s: dataset._fields["close"][s] for s in dataset.symbols},
        index=dataset.index).ffill()
    normalised = closes / closes.bfill().iloc[0]
    return normalised.mean(axis=1) * starting_cash
