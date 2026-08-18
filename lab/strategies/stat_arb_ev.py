"""
lab/strategies/stat_arb_ev.py
=============================
The pairs trade, but only entered on a relationship screened for being real
and an arithmetic that clears its own costs.

Ported from `stat-arb-v2/core/ev_filter.py` (the EV gates) and the
cointegration-explorer research app (the relatedness screen, originally
`stat_arb.require_cointegration` — see `research/strategies/stat_arb.md` for
the MacKinnon-table and missing-error-correction findings that came out of
building it). The two were shown side by side while each was being measured;
combined here because a production pairs strategy should not skip either
question. `stat_arb`, the unscreened, unfiltered baseline both were checked
against, is still in `lab/strategies/stat_arb.py` and still runs directly
(`python -m lab.strategies.stat_arb`) — it is just not registered, so it no
longer duplicates this one on the showcase.

The thesis, stated once so the gates make sense:

    Two series that drift upward together produce a confident hedge ratio
    whether or not anything connects them, and a retail account cannot
    compete with anyone on sub-day timescales. A spread is worth trading only
    if the relationship is real, it reverts slowly enough that execution
    latency is irrelevant, and the expected capture is large relative to both
    the cost of trading and the volatility of holding.

Gate 0, optional, screens what the other four take for granted:

    0  COINTEGRATION  Off by default so the unscreened signal stays visible.
                       `require_cointegration=True` gates entry on Engle-Granger,
                       re-tested every `retest_every` bars and cached by pair.
                       Two independent random walks can still pass gates 1-4
                       with a confident-looking hedge ratio; this is the gate
                       that asks whether the pair is related at all.

Four gates, in order, each rejecting for a different reason:

    1  HALF-LIFE   Too fast (< min) is somebody else's game; too slow (> max)
                   ties up capital in a position that may not revert this
                   quarter. This is the gate that does the real work.
    2  BREAKEVEN z The minimum |z| at which the expected capture covers
                   commission plus slippage at *this* position size. Derived
                   from the run's own cost model, never a hardcoded number.
    3  REWARD/RISK Net expected value over one-sigma of hold-period spread
                   movement. A positive EV that is 3% of the noise around it
                   is not a trade, it is a coin flip with a small tilt.
    4  MIN EV      An absolute dollar floor, so tiny positions where fees
                   dominate never get taken.

The earlier version of the EV filter gated on `net_ev > $10` and
`sharpe_contribution > 0.5`, and rejected nothing in any backtest — a $12k
position at z=2.2 clears a $10 floor trivially. A filter that never fires is
worse than no filter, because it looks like risk management. Every rejection
here is written to the run log with its reason, so "how often did gate N fire"
is a question you can answer instead of assume.
"""

from __future__ import annotations

import math

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


def ou_half_life(series: np.ndarray) -> float:
    """Half-life of mean reversion, in bars, from an OU fit.

        Δs_t = a + b·s_{t-1} + ε      half-life = −ln2 / ln(1 + b)

    Returns +inf when the series shows no reversion (b ≥ 0), which the
    half-life gate then rejects as "too slow" — the correct outcome, since a
    spread that does not revert has no half-life at all.
    """
    if len(series) < 12:
        return float("inf")
    lagged = series[:-1]
    delta = np.diff(series)
    centred = lagged - lagged.mean()
    variance = float((centred ** 2).sum())
    if variance <= 0:
        return float("inf")
    b = float((centred * (delta - delta.mean())).sum() / variance)
    if b >= 0 or b <= -1:
        return float("inf")
    return float(-math.log(2.0) / math.log(1.0 + b))


@register
class StatArbEV(Strategy):

    key = "stat_arb_ev"
    title = "Statistical Arbitrage"
    universe = Universe.PAIR
    default_symbols = "KO, PEP, XOM, CVX, MCD, YUM, CL, PG"
    summary = ("Pairs trading that screens for a real cointegrating "
               "relationship and will not enter unless expected capture "
               "clears costs, latency and hold-period risk.")
    provenance = ("stat-arb-v2/core/ev_filter.py and analysis/zscore_signal.py "
                  "for the EV gates, costs now read from the run's own model "
                  "rather than a private config; the cointegration screen is "
                  "ported from the cointegration-explorer research app.")
    notes = (
        "Run with `require_cointegration=True` and without it on the same "
        "universe. The interesting output is not which one earns more — it "
        "is the rejection log, which says how many signals were never a real "
        "relationship and how many cleared that screen but never cleared "
        "their own costs. See `research/strategies/stat_arb.md`."
    )

    params = (
        Param("lookback", 60, ParamKind.INT, low=20, high=750, step=5,
              help="Bars for the hedge ratio, spread moments and half-life.",
              grid=(60, 120)),
        Param("entry_z", 2.0, ParamKind.FLOAT, low=0.5, high=6.0, step=0.25,
              help="Signal threshold, before the gates.", grid=(1.75, 2.0, 2.5)),
        Param("exit_z", 0.5, ParamKind.FLOAT, low=0.0, high=3.0, step=0.25,
              help="Close when |z| falls below this. Also the assumed capture "
                   "target when computing expected value."),
        Param("stop_z", 4.0, ParamKind.FLOAT, low=0.0, high=12.0, step=0.5,
              help="Abandon at this |z|. 0 disables."),
        Param("min_half_life_days", 2.0, ParamKind.FLOAT, low=0.0, high=60.0,
              step=0.5,
              help="Gate 1 floor. Below this, reversion is fast enough that "
                   "execution quality decides the outcome.",
              grid=(1.0, 2.0, 5.0)),
        Param("max_half_life_days", 45.0, ParamKind.FLOAT, low=1.0, high=400.0,
              step=1.0, help="Gate 1 ceiling.", grid=(30.0, 45.0, 90.0)),
        Param("min_reward_risk", 0.15, ParamKind.FLOAT, low=0.0, high=3.0,
              step=0.05,
              help="Gate 3 floor: net EV as a multiple of one-sigma "
                   "hold-period spread movement.",
              grid=(0.05, 0.15, 0.30)),
        Param("min_ev_dollars", 25.0, ParamKind.FLOAT, low=0.0, high=1e6,
              help="Gate 4 floor, in dollars of net expected value."),
        Param("hold_multiple", 1.5, ParamKind.FLOAT, low=0.25, high=5.0,
              step=0.25,
              help="Expected hold as a multiple of the half-life."),
        Param("position_fraction", 0.9, ParamKind.FLOAT, low=0.01, high=1.0,
              step=0.05, help="Share of sleeve equity across all pairs."),
        Param("require_cointegration", False, ParamKind.BOOL,
              help="Refuse to enter unless the pair passes Engle-Granger on "
                   "the trailing window. Off by default so the unscreened "
                   "signal stays visible.",
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
        self.rejected: dict[str, int] = {
            "not_cointegrated": 0, "half_life_fast": 0, "half_life_slow": 0,
            "breakeven_z": 0, "reward_risk": 0, "min_ev": 0, "too_small": 0}
        self.approved = 0
        self._bars_per_day = max(1.0, ctx.periods_per_year / 252.0)
        self._screen: dict[str, tuple[int, bool, str]] = {}

    def _passes_screen(self, ctx: MarketContext, group: str,
                       y: np.ndarray, x: np.ndarray) -> tuple[bool, str]:
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

    def on_finish(self, ctx: MarketContext) -> None:
        total = self.approved + sum(self.rejected.values())
        if not total:
            return
        detail = ", ".join(f"{k}={v}" for k, v in self.rejected.items() if v)
        ctx.log(f"EV filter saw {total} signals: {self.approved} approved, "
                f"{total - self.approved} rejected ({detail or 'none'})")

    # ------------------------------------------------------------------
    def on_bar(self, ctx: MarketContext):
        pairs = ctx.pairs
        if not pairs:
            return HOLD

        orders: list[Order] = []
        capital = (ctx.equity * self.position_fraction) / len(pairs)
        cost_bps = ctx.costs.round_trip_bps() if ctx.costs else 6.0

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
            half_life = ou_half_life(spread) / self._bars_per_day

            # ── manage an open spread ───────────────────────────────────
            if ctx.in_market(y_sym, x_sym):
                stopped = self.stop_z > 0 and abs(z) >= self.stop_z
                if abs(z) < self.exit_z or stopped:
                    why = ("spread stopped reverting" if stopped
                           else "spread back at its mean")
                    for symbol in (y_sym, x_sym):
                        position = ctx.position(symbol)
                        if position is not None:
                            orders.append(Order.close(
                                symbol, position.side, group=group,
                                reason=f"{why} (z={z:+.2f})",
                                z=round(z, 3)))
                continue

            if abs(z) < self.entry_z:
                continue

            if self.require_cointegration:
                passed, note = self._passes_screen(ctx, group, y, x)
                if not passed:
                    self.rejected["not_cointegrated"] += 1
                    ctx.log(f"{group} z={z:+.2f} not taken - {note}")
                    continue

            # ── the four gates ──────────────────────────────────────────
            shares = capital / y_price
            if shares < 1:
                self.rejected["too_small"] += 1
                continue

            verdict = self._evaluate(
                z=z, spread_std=sd, half_life_days=half_life,
                y_price=y_price, x_price=x_price, beta=beta, shares=shares,
                y_vol=ctx.volatility(y_sym), x_vol=ctx.volatility(x_sym),
                cost_bps=cost_bps, capital=capital)

            if verdict["reason"]:
                self.rejected[verdict["gate"]] += 1
                ctx.log(f"{group} z={z:+.2f} rejected — {verdict['reason']}")
                continue

            self.approved += 1

            y_qty = int(shares)
            x_qty = int(y_qty * abs(beta))
            if y_qty < 1 or x_qty < 1:
                self.rejected["too_small"] += 1
                continue

            if z > 0:
                y_side, x_side = Side.SHORT, Side.LONG
                direction = "short spread"
            else:
                y_side, x_side = Side.LONG, Side.SHORT
                direction = "long spread"

            reason = (f"{direction} z={z:+.2f}, half-life {half_life:.1f}d, "
                      f"net EV ${verdict['net_ev']:,.0f} "
                      f"(r/r {verdict['reward_risk']:.2f})")
            meta = {"z": round(z, 3), "beta": round(beta, 4),
                    "half_life_days": round(half_life, 2),
                    "net_ev": round(verdict["net_ev"], 2),
                    "breakeven_z": round(verdict["breakeven_z"], 3)}
            orders.append(Order.open(y_sym, y_side, y_qty, group=group,
                                     reason=reason, **meta))
            orders.append(Order.open(x_sym, x_side, x_qty, group=group,
                                     reason=reason, **meta))

        return orders or HOLD

    # ------------------------------------------------------------------
    def _evaluate(self, *, z, spread_std, half_life_days, y_price, x_price,
                  beta, shares, y_vol, x_vol, cost_bps, capital) -> dict:
        """The four gates. Returns the first failure, or an empty reason."""

        # Expected capture: the spread travels from |z| back to the exit band.
        capture = (abs(z) - self.exit_z) * spread_std
        gross = capture * shares

        # Both legs, in and out. `cost_bps` is already a round trip per leg.
        notional = capital * (1.0 + abs(beta) * x_price / y_price)
        total_cost = notional * cost_bps / 10_000.0

        net_ev = gross - total_cost

        # One-sigma spread movement over the expected hold.
        y_daily = y_price * (y_vol / math.sqrt(252.0)) if np.isfinite(y_vol) else 0.0
        x_daily = x_price * (x_vol / math.sqrt(252.0)) if np.isfinite(x_vol) else 0.0
        spread_vol_daily = math.hypot(y_daily, beta * x_daily)
        hold_days = max(half_life_days * self.hold_multiple, 1.0) \
            if math.isfinite(half_life_days) else 1.0
        spread_vol_hold = spread_vol_daily * math.sqrt(hold_days) * shares

        reward_risk = net_ev / spread_vol_hold if spread_vol_hold > 0 else 0.0

        denominator = spread_std * shares
        breakeven_z = ((total_cost + self.min_ev_dollars) / denominator
                       + self.exit_z) if denominator > 0 else float("inf")

        out = {"net_ev": net_ev, "reward_risk": reward_risk,
               "breakeven_z": breakeven_z, "gate": "", "reason": ""}

        if half_life_days < self.min_half_life_days:
            out["gate"] = "half_life_fast"
            out["reason"] = (f"half-life {half_life_days:.1f}d below "
                             f"{self.min_half_life_days:.1f}d — too fast to be "
                             f"our game")
        elif half_life_days > self.max_half_life_days:
            out["gate"] = "half_life_slow"
            out["reason"] = (f"half-life {half_life_days:.1f}d above "
                             f"{self.max_half_life_days:.1f}d — may not revert")
        elif abs(z) < breakeven_z:
            out["gate"] = "breakeven_z"
            out["reason"] = (f"|z| {abs(z):.2f} below breakeven "
                             f"{breakeven_z:.2f} at this size — costs eat it")
        elif reward_risk < self.min_reward_risk:
            out["gate"] = "reward_risk"
            out["reason"] = (f"reward/risk {reward_risk:.3f} below "
                             f"{self.min_reward_risk:.2f} — EV is small next "
                             f"to hold-period noise")
        elif net_ev < self.min_ev_dollars:
            out["gate"] = "min_ev"
            out["reason"] = (f"net EV ${net_ev:,.0f} below "
                             f"${self.min_ev_dollars:,.0f}")
        return out


# Run this file directly to test it — `python -m lab.strategies.stat_arb_ev`.
# The only place a parameter value is chosen: the GUI has no control for one.
if __name__ == "__main__":
    from ..api import backtest, sweep                          # noqa: F401

    backtest(StatArbEV, symbols="KO,PEP,XOM,CVX,MCD,YUM,CL,PG")

    # sweep(StatArbEV, symbols="KO,PEP,XOM,CVX,MCD,YUM,CL,PG",
    #       min_reward_risk=[1.0, 1.5, 2.0])

    # Needs data/prices.pkl. `python run.py fetch KO PEP XOM CVX MCD YUM CL PG
    # --start 2021-01-01 --end 2025-01-01` downloads one.
