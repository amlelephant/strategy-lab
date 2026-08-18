"""
lab/strategies/hundred_day_mov_avg.py
=====================================
Hold the market while it closes above its 100-day moving average; hold cash
while it closes below.

The claim is that the sign of a long moving-average deviation carries
information about the *next* period's returns — that trends in an index
persist long enough to be traded net of costs. It is the oldest published
systematic rule there is, and the honest reason to run it here is that it is
the cheapest possible test of whether this platform's costs and timing are
being applied fairly: the rule is so simple that any surprising result is a
bug in the framework rather than an edge.

What would falsify it: a 100-day rule that fails to clear buy-and-hold on the
same series, after the same costs. Note that clearing it on *return* is not
enough and is not what this measures — being in cash for a third of the sample
mechanically lowers volatility, so a lower return at a much lower drawdown can
still be the better risk-adjusted outcome. Read the alpha and its t, not the
CAGR.

Written against the hub, not ported — there is no pre-platform version of this
one.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Strategy, Universe)
from ..core.registry import register


@register
class HundredDayMovAvg(Strategy):

    key = "hundred_day_mov_avg"
    title = "100 Day Moving Average"
    universe = Universe.SINGLE
    summary = ("Buy when the index closes above its 100-day moving average "
               "and sell when it closes below.")

    # This rule times *the market*, so its universe is the market. Run on a
    # handful of companies it measures something else entirely — whether four
    # particular firms happened to trend — and single-name moves swamp the
    # index momentum the rule is trying to read.
    default_data = "market_spy.csv"
    default_symbols = "SPY"
    notes = ("Intended for a broad index — `market_spy.csv` is SPY back to "
             "2005 — but it will run on any single name. Its weakness is "
             "whipsaw: in a range-bound market the price crosses the average "
             "repeatedly and the rule pays costs on every crossing for no "
             "directional gain. `band` exists to measure that, not to hide "
             "it. It is also a long/flat rule, so it cannot profit from a "
             "decline, only sit out of one.")

    params = (
        Param("window", 100, ParamKind.INT, low=5, high=400, step=5,
              help="Bars in the moving average. 100 is the rule as stated; "
                   "50 and 200 are the other two conventional choices.",
              grid=(50, 100, 200)),
        Param("band", 0.0, ParamKind.FLOAT, low=0.0, high=0.10, step=0.005,
              help="Fractional dead zone around the average. 0.0 is the "
                   "plain rule: cross by a hundredth of a cent and it "
                   "trades. Raising it demands the price clear the average "
                   "by that fraction, which cuts whipsaw and delays every "
                   "entry and exit.",
              grid=(0.0, 0.01, 0.02)),
        Param("invested_fraction", 0.98, ParamKind.FLOAT, low=0.05, high=1.0,
              step=0.01,
              help="Share of sleeve equity to deploy when long. Below 1.0 so "
                   "slippage on the entry fill cannot overdraw the account."),
    )

    @property
    def warmup(self) -> int:
        # The first signal needs a full window; the hub does not call on_bar
        # until this many bars exist, so on_bar never sees a short average.
        return self.window

    def on_bar(self, ctx: MarketContext):
        orders = []
        # Split the sleeve across whatever universe was chosen, so the rule is
        # well defined on more than one name even though it is meant for one.
        slice_weight = self.invested_fraction / max(1, len(ctx.symbols))

        # Names cross their averages on different days, so by the time the last
        # one signals the names already held have drifted above their share of
        # equity and the last slice is no longer funded. Cap every entry by the
        # cash actually on hand: on a one-name universe — what this is for —
        # cash equals equity when flat and this changes nothing.
        equity = ctx.equity
        headroom = (max(0.0, ctx.cash) * self.invested_fraction / equity
                    if equity > 0 else 0.0)

        for symbol in ctx.symbols:
            price = ctx.price(symbol)
            if not np.isfinite(price) or price <= 0:
                continue

            closes = ctx.history(symbol, self.window)
            if len(closes) < self.window:
                ctx.log(f"{symbol}: {len(closes)} of {self.window} bars — "
                        f"no average yet")
                continue

            average = float(np.mean(closes))
            if not np.isfinite(average) or average <= 0:
                continue

            deviation = price / average - 1.0
            held = ctx.position(symbol) is not None

            if deviation > self.band and not held:
                weight = min(slice_weight, headroom)
                if weight <= 0.001:
                    ctx.log(f"{symbol}: {deviation:+.2%} above average but "
                            f"only {headroom:.1%} of equity is funded — "
                            f"entry skipped")
                    continue
                orders.append(Order.target(
                    symbol, weight,
                    reason=f"close {deviation:+.2%} above its "
                           f"{self.window}-bar average",
                    average=average, deviation=deviation))
                # Two names can cross on the same day; the second must not be
                # sized against cash the first has already claimed.
                headroom -= weight
            elif deviation < -self.band and held:
                orders.append(Order.close(
                    symbol,
                    reason=f"close {deviation:+.2%} below its "
                           f"{self.window}-bar average",
                    average=average, deviation=deviation))
            elif self.band > 0 and abs(deviation) <= self.band:
                # Worth logging rather than passing silently: the whole point
                # of a band is how often it is the thing that stopped a trade.
                ctx.log(f"{symbol}: {deviation:+.2%} from average, inside the "
                        f"{self.band:.1%} band — holding "
                        f"{'long' if held else 'cash'}")

        return orders or HOLD


# Run this file directly to test it — `python -m lab.strategies.hundred_day_mov_avg`.
# This is where parameters get chosen: the GUI has no control for one, so a
# value you want to try goes here, or in the `params` block above.
if __name__ == "__main__":
    from ..api import backtest, sweep                          # noqa: F401

    # SPY back to 2005, so the rule is tested across 2008, 2020 and 2022
    # rather than only the sample in prices.pkl.
    backtest(HundredDayMovAvg,
             data="market_spy.csv", symbols="SPY")

    # sweep(HundredDayMovAvg, data="market_spy.csv", symbols="SPY",
    #       window=[50, 100, 200], band=[0.0, 0.01, 0.02])
