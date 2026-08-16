"""
lab/strategies/stat_arb.py
==========================
Z-score pairs trading. The original.

Ported from `paper-broker/algos/statArbClass.py`, with the rolling-window
mechanics of `paper-broker/paper/backTestStat.py`. The rule is unchanged:

    hedge ratio β  = OLS(y ~ x) over the trailing window
    spread         = y − β·x
    z              = (spread − mean) / σ,  both from the same window
    z >  entry, flat  →  short the spread  (short y, long β·x)
    z < −entry, flat  →  long  the spread  (long  y, short β·x)
    |z| < exit, held  →  close both legs

The two legs share an order group, so the hub fills both or neither. That is
the ported form of `can_afford(trade1, trade2)` — the original checked the
*net* cost of the pair, because the short leg credits cash, and a pair that
half-fills is not a hedge, it is a naked directional bet.

The hedge ratio is re-estimated on every bar from the trailing window only.
The live version fitted β over a freshly downloaded window that included the
current price; the backtest version fitted it over `data.iloc[i-lookback:i]`.
The second is the correct one and is what this implements.

This is the honest, unfiltered baseline. It enters on the signal alone,
without asking whether the expected move is bigger than what it costs to
capture it. `stat_arb_ev` is the same strategy with that question added, and
the difference between the two is the point of having both.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Side, Strategy, Universe)
from ..core.registry import register


def hedge_ratio(y: np.ndarray, x: np.ndarray) -> float:
    """OLS slope of y on x with an intercept.

    Closed form rather than statsmodels: this runs once per pair per bar, and
    `sm.OLS(...).fit()` is roughly two orders of magnitude slower for a job
    that is one covariance divided by one variance.
    """
    x_mean, y_mean = x.mean(), y.mean()
    variance = float(((x - x_mean) ** 2).sum())
    if variance <= 0:
        return float("nan")
    return float(((x - x_mean) * (y - y_mean)).sum() / variance)


@register
class StatArb(Strategy):

    key = "stat_arb"
    title = "Statistical Arbitrage (z-score)"
    universe = Universe.PAIR
    summary = ("Trade the spread between two related names back to its mean, "
               "hedged by an OLS ratio.")
    provenance = ("paper-broker/algos/statArbClass.py + "
                  "paper/backTestStat.py — original decision rule preserved.")
    notes = (
        "The universe is consumed two symbols at a time, so "
        "`GS,MS,KO,PEP` trades the GS/MS and KO/PEP spreads independently. "
        "By default no cointegration test is applied: the strategy will "
        "happily trade a pair that merely looks related, because two series "
        "that drift upward together produce a confident hedge ratio whether "
        "or not anything connects them. Turn on `require_cointegration` and "
        "run it beside the default — the difference between the two is a "
        "direct measurement of what the spurious-regression problem costs."
    )

    params = (
        Param("lookback", 50, ParamKind.INT, low=10, high=750, step=5,
              help="Bars used to estimate the hedge ratio and spread moments.",
              grid=(30, 50, 100)),
        Param("entry_z", 2.0, ParamKind.FLOAT, low=0.5, high=6.0, step=0.25,
              help="Enter when |z| exceeds this.", grid=(1.5, 2.0, 2.5)),
        Param("exit_z", 0.5, ParamKind.FLOAT, low=0.0, high=3.0, step=0.25,
              help="Close when |z| falls below this.", grid=(0.25, 0.5, 1.0)),
        Param("stop_z", 4.0, ParamKind.FLOAT, low=0.0, high=12.0, step=0.5,
              help="Abandon the position if |z| reaches this — the spread has "
                   "stopped mean-reverting. 0 disables."),
        Param("position_fraction", 0.9, ParamKind.FLOAT, low=0.01, high=1.0,
              step=0.05, help="Share of sleeve equity across all pairs."),
        Param("require_cointegration", False, ParamKind.BOOL,
              help="Refuse to enter unless the pair passes Engle-Granger on "
                   "the trailing window. Off by default so the unscreened "
                   "baseline stays visible.",
              grid=(False, True)),
        Param("cointegration_level", 0.05, ParamKind.FLOAT, low=0.001, high=0.5,
              step=0.01, help="Significance level for that test."),
        Param("retest_every", 21, ParamKind.INT, low=1, high=252, step=1,
              help="Bars between cointegration re-tests. The test costs far "
                   "more than the signal does, and a relationship does not "
                   "change daily."),
    )

    @property
    def warmup(self) -> int:
        return int(self.lookback) + 1

    def on_start(self, ctx: MarketContext) -> None:
        self._screen: dict[str, tuple[int, bool, str]] = {}

    def _passes_screen(self, ctx: MarketContext, group: str,
                       y: "np.ndarray", x: "np.ndarray") -> tuple[bool, str]:
        """Cached Engle-Granger verdict for one pair."""
        cached = self._screen.get(group)
        if cached is not None and ctx.i - cached[0] < self.retest_every:
            return cached[1], cached[2]

        from ..analysis.cointegration import engle_granger
        result = engle_granger(y, x)
        ok = result.is_cointegrated(self.cointegration_level)
        note = (f"cointegrated at p={result.p_value:.3f}" if ok
                else f"not cointegrated (p={result.p_value:.3f})")
        self._screen[group] = (ctx.i, ok, note)
        return ok, note

    def on_bar(self, ctx: MarketContext):
        pairs = ctx.pairs
        if not pairs:
            return HOLD

        orders: list[Order] = []
        capital = (ctx.equity * self.position_fraction) / len(pairs)

        for y_sym, x_sym in pairs:
            frame = ctx.window(self.lookback, (y_sym, x_sym))
            if len(frame) < max(20, self.lookback // 2):
                continue

            y = frame[y_sym].to_numpy(dtype=float)
            x = frame[x_sym].to_numpy(dtype=float)
            beta = hedge_ratio(y, x)
            if not np.isfinite(beta):
                continue

            spread = y - beta * x
            mean, sd = float(spread.mean()), float(spread.std(ddof=1))
            if sd <= 0:
                continue

            y_price, x_price = ctx.price(y_sym), ctx.price(x_sym)
            if not (np.isfinite(y_price) and np.isfinite(x_price)) \
                    or y_price <= 0 or x_price <= 0:
                continue

            z = (y_price - beta * x_price - mean) / sd
            group = f"{y_sym}/{x_sym}"
            meta = {"z": round(z, 3), "beta": round(beta, 4)}
            held = ctx.in_market(y_sym, x_sym)

            # ── manage an open spread ───────────────────────────────────
            if held:
                stopped = self.stop_z > 0 and abs(z) >= self.stop_z
                if abs(z) < self.exit_z or stopped:
                    why = ("spread stopped reverting" if stopped
                           else "spread back at its mean")
                    for symbol in (y_sym, x_sym):
                        position = ctx.position(symbol)
                        if position is not None:
                            orders.append(Order.close(
                                symbol, position.side, group=group,
                                reason=f"{why} (z={z:+.2f})", **meta))
                continue

            # ── open a new spread ───────────────────────────────────────
            if abs(z) < self.entry_z:
                continue

            if self.require_cointegration:
                passed, note = self._passes_screen(ctx, group, y, x)
                if not passed:
                    ctx.log(f"{group} z={z:+.2f} not taken - {note}")
                    continue

            y_qty = int(capital / y_price)
            x_qty = int(y_qty * abs(beta))
            if y_qty < 1 or x_qty < 1:
                ctx.log(f"{group}: sized to {y_qty}/{x_qty} shares — too "
                        f"small to trade at ${capital:,.0f} per pair")
                continue

            if z > 0:                       # y rich relative to x
                y_side, x_side = Side.SHORT, Side.LONG
                direction = "short spread"
            else:                           # y cheap relative to x
                y_side, x_side = Side.LONG, Side.SHORT
                direction = "long spread"

            reason = f"{direction} at z={z:+.2f} (β={beta:.3f})"
            orders.append(Order.open(y_sym, y_side, y_qty, group=group,
                                     reason=reason, **meta))
            orders.append(Order.open(x_sym, x_side, x_qty, group=group,
                                     reason=reason, **meta))

        return orders or HOLD
