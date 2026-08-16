"""
lab/strategies/buy_and_hold.py
==============================
Buy the universe on the first bar, hold it, do nothing else.

This is the control, and it is also the shortest complete example of the
contract — if you are writing a new strategy, copy this file. Everything a
strategy needs is here and nothing else is:

  * declare `key`, `title`, `universe`, `summary`
  * declare parameters instead of hardcoding numbers
  * read the world from `ctx`
  * return orders

It exists because a strategy that does not beat it has not earned its
complexity. The hub already plots an equal-weight benchmark on every result,
but that benchmark is frictionless — this one pays the same commission and
slippage as everything else, which is the fair comparison.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Strategy, Universe)
from ..core.registry import register


@register
class BuyAndHold(Strategy):

    key = "buy_and_hold"
    title = "Buy and Hold"
    universe = Universe.SINGLE
    summary = "Equal-weight the universe once, then do nothing."
    notes = ("The control. Any strategy that does not clear this, after the "
             "same costs, is not a strategy.")

    params = (
        Param("invested_fraction", 0.98, ParamKind.FLOAT, low=0.05, high=1.0,
              step=0.01,
              help="Share of sleeve equity to deploy. Below 1.0 so slippage "
                   "on the opening fills cannot overdraw the account."),
        Param("rebalance_bars", 0, ParamKind.INT, low=0, high=2000, step=1,
              help="Rebalance back to equal weight every N bars. 0 never "
                   "rebalances, which is the honest buy-and-hold."),
    )

    def on_start(self, ctx: MarketContext) -> None:
        self._bought = False

    def on_bar(self, ctx: MarketContext):
        due = (not self._bought) or (
            self.rebalance_bars > 0 and ctx.i % self.rebalance_bars == 0)
        if not due:
            return HOLD

        tradeable = [s for s in ctx.symbols if np.isfinite(ctx.price(s))
                     and ctx.price(s) > 0]
        if not tradeable:
            return HOLD

        self._bought = True
        weight = self.invested_fraction / len(tradeable)
        return [Order.target(symbol, weight, reason="equal-weight the universe")
                for symbol in tradeable]
