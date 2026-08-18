"""
lab/strategies/mean_reversion.py
================================
Bollinger-band mean reversion on single names.

Ported from `paper-broker/algos/meanReversionClass.py`. The decision rule is
unchanged from the original:

    bands   = SMA(n) ± k · σ(n)
    flat and price < lower  →  go long
    flat and price > upper  →  go short
    long  and price ≥ SMA   →  close        (the mean is the target, not the
    short and price ≤ SMA   →  close         opposite band)
    stop-loss / take-profit on open P&L in dollars

What changed in the port, and why:

  * **The price series comes from the hub.** The original called
    `yf.download()` inside the class and recomputed the bands from whatever
    window happened to be current, which is why the same code gave different
    answers on different days and could not be backtested at all.

  * **The stop-loss comparison is fixed.** The original read
    `sell = change < stop_loss` with `stop_loss` passed as a positive dollar
    figure (200, 500, 800 in the sweep). That fires whenever open P&L is below
    +$200 — which is almost always, including on a position that is winning.
    The intent was plainly "exit if I am down more than this much", so the
    port compares against `-stop_loss`. This is the one substantive change to
    the original logic and it is the reason the ported strategy trades far
    less than the original did. See `research/ported-changes.md`.

  * **Position sizing is a declared fraction, not `cash // price`.** The
    original committed the entire account to one name because it only ever
    looked at one name. Across a universe that is not sizing, it is a race.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Side, Strategy, Universe)
from ..core.registry import register


@register
class MeanReversion(Strategy):

    key = "mean_reversion"
    title = "Bollinger Mean Reversion"
    universe = Universe.SINGLE
    summary = ("Fade moves outside a Bollinger band and take profit back at "
               "the moving average.")
    provenance = ("paper-broker/algos/meanReversionClass.py — written before "
                  "the platform existed; decision rule preserved, data access "
                  "and the stop-loss sign corrected.")
    notes = (
        "The thesis is that a liquid name pushed two standard deviations off "
        "its own short-run mean is more likely to come back than to keep "
        "going. It is the simplest possible statement of mean reversion and "
        "it is mostly a test of whether transaction costs eat the edge — "
        "which, at a 20-bar window on daily bars, they usually do. Run it at "
        "`frictionless` and then at `realistic` to see the whole story."
    )

    params = (
        Param("sma_window", 20, ParamKind.INT, low=2, high=500, step=1,
              help="Bars in the moving average and the standard deviation.",
              grid=(10, 20, 30, 40)),
        Param("band_multiplier", 2.0, ParamKind.FLOAT, low=0.25, high=6.0,
              step=0.25, help="Standard deviations from the mean to the band.",
              grid=(1.5, 2.0, 2.5)),
        Param("take_profit", 800.0, ParamKind.FLOAT, low=0.0, high=1e7,
              help="Close when open P&L exceeds this many dollars. 0 disables.",
              grid=(200.0, 500.0, 800.0)),
        Param("stop_loss", 500.0, ParamKind.FLOAT, low=0.0, high=1e7,
              help="Close when open P&L falls below minus this many dollars. "
                   "0 disables.",
              grid=(200.0, 500.0, 800.0)),
        Param("position_fraction", 0.9, ParamKind.FLOAT, low=0.01, high=1.0,
              step=0.05,
              help="Share of sleeve equity deployed across all names at once."),
        Param("allow_short", True, ParamKind.BOOL,
              help="Take the short side when price breaks the upper band."),
    )

    @property
    def warmup(self) -> int:
        return int(self.sma_window) + 1

    def on_bar(self, ctx: MarketContext):
        orders: list[Order] = []
        per_name = (ctx.equity * self.position_fraction) / max(1, len(ctx.symbols))

        for symbol in ctx.symbols:
            history = ctx.history(symbol, self.sma_window)
            if len(history) < self.sma_window:
                continue

            price = ctx.price(symbol)
            if not np.isfinite(price) or price <= 0:
                continue

            sma = float(np.mean(history))
            sd = float(np.std(history, ddof=1))
            if sd <= 0:
                continue
            upper = sma + self.band_multiplier * sd
            lower = sma - self.band_multiplier * sd

            position = ctx.position(symbol)

            # ── holding: exit at the mean, or on a P&L trigger ──────────
            if position is not None:
                pnl = position.unrealised(price)
                hit_stop = self.stop_loss > 0 and pnl <= -self.stop_loss
                hit_target = self.take_profit > 0 and pnl >= self.take_profit
                reverted = (position.side is Side.LONG and price >= sma) or \
                           (position.side is Side.SHORT and price <= sma)

                if reverted or hit_stop or hit_target:
                    why = ("reverted to mean" if reverted else
                           "stop loss" if hit_stop else "take profit")
                    orders.append(Order.close(
                        symbol, position.side,
                        reason=f"{why} (P&L ${pnl:,.0f})",
                        z=round((price - sma) / sd, 3), sma=round(sma, 4)))
                continue

            # ── flat: enter outside a band ──────────────────────────────
            quantity = int(per_name // price)
            if quantity < 1:
                continue

            if price < lower:
                orders.append(Order.open(
                    symbol, Side.LONG, quantity,
                    reason=f"{price:.2f} below lower band {lower:.2f}",
                    z=round((price - sma) / sd, 3), sma=round(sma, 4)))
            elif price > upper and self.allow_short:
                orders.append(Order.open(
                    symbol, Side.SHORT, quantity,
                    reason=f"{price:.2f} above upper band {upper:.2f}",
                    z=round((price - sma) / sd, 3), sma=round(sma, 4)))

        return orders or HOLD


# Run this file directly to test it — `python -m lab.strategies.mean_reversion`.
# The only place a parameter value is chosen: the GUI has no control for one.
if __name__ == "__main__":
    from ..api import backtest, sweep                          # noqa: F401

    backtest(MeanReversion, symbols="KO,PEP,XOM,CVX")

    # sweep(MeanReversion, symbols="KO,PEP,XOM,CVX",
    #       sma_window=[10, 20, 30, 40])

    # Needs data/prices.pkl. `python run.py fetch KO PEP XOM CVX
    # --start 2021-01-01 --end 2025-01-01` downloads one.
