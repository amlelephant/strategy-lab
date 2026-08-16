"""
lab/core/hub.py
===============
The hub. One pass over the dataset; every strategy gets asked the same
question at the same instant, with the same data, against the same costs.

    for each bar t:
        fill the orders decided at t-1        (you cannot trade a close you
                                               have only just seen)
        mark every sleeve to market
        ask every strategy for orders at t
        queue them for t+1

Each strategy runs in its own **sleeve** — its own cash, positions and trade
log. Sleeves never compete for capital, so a run of six strategies produces
six independently interpretable results plus a combined curve, rather than one
result that depends on which strategy happened to spend the cash first.

Three things live here and nowhere else, on purpose:

  * **The clock.** Strategies cannot advance it, skip it, or look past it.
  * **Fills.** A strategy names a size and a direction, never a price.
  * **Cash.** A strategy that cannot afford a trade is refused and told why;
    it does not get to overdraw and find out later.

The rejection log is a first-class output, not a debugging aid. "The strategy
wanted 214 trades and could afford 190" is a finding about position sizing,
and it is invisible in any framework that silently clips.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from ..data.dataset import Dataset
from .contract import (HOLD, Intent, MarketContext, Order, Side, Strategy)
from .costs import CostModel, FillTiming
from .metrics import Performance, benchmark_equity, summarise
from .portfolio import Fill, InsufficientCash, Portfolio


# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RunConfig:
    """Everything about a run that is not the data or the strategy."""

    starting_cash: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    #: Hard cap on any single position as a fraction of sleeve equity. A
    #: strategy asking for more is clipped and told so in the log — silently
    #: honouring a 40x request is how a backtest reports a 900% return.
    max_position_weight: float = 1.0
    #: Extra bars to skip beyond each strategy's declared warmup.
    warmup_padding: int = 0
    #: Flatten every sleeve on the final bar so the closing equity is realised
    #: cash rather than a mark.
    flatten_at_end: bool = True
    label: str = ""
    #: Trailing bars used to estimate volatility for the latency model.
    volatility_lookback: int = 60

    def describe(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "max_position_weight": self.max_position_weight,
            "warmup_padding": self.warmup_padding,
            "flatten_at_end": self.flatten_at_end,
            "costs": self.costs.describe(),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SleeveResult:
    key: str
    title: str
    params: dict[str, Any]
    equity: pd.Series
    exposure: pd.Series
    trade_log: pd.DataFrame
    performance: Performance
    messages: list[tuple[pd.Timestamp, str]]
    rejections: list[tuple[pd.Timestamp, str]]
    orders_requested: int = 0
    orders_filled: int = 0

    @property
    def fill_rate(self) -> float:
        return self.orders_filled / self.orders_requested if self.orders_requested else 0.0

    def to_dict(self, curve_points: int = 400) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "params": self.params,
            "performance": self.performance.as_dict(),
            "headline": self.performance.headline(),
            "equity": _downsample(self.equity, curve_points),
            "drawdown": _downsample(
                self.equity / self.equity.cummax() - 1.0, curve_points),
            "orders_requested": self.orders_requested,
            "orders_filled": self.orders_filled,
            "fill_rate": self.fill_rate,
            "rejections": [(str(t), m) for t, m in self.rejections[:200]],
            "messages": [(str(t), m) for t, m in self.messages[:200]],
            "trades": _trade_rows(self.trade_log),
        }


@dataclass
class RunResult:
    dataset: dict[str, Any]
    config: dict[str, Any]
    sleeves: list[SleeveResult]
    benchmark: pd.Series
    benchmark_performance: Performance
    combined: pd.Series
    combined_performance: Performance
    elapsed_seconds: float
    warnings: list[str] = field(default_factory=list)

    def best(self) -> SleeveResult | None:
        finished = [s for s in self.sleeves if s.performance.observations]
        return max(finished, key=lambda s: s.performance.sharpe, default=None)

    def table(self) -> pd.DataFrame:
        """One row per sleeve — the summary the CLI prints."""
        rows = []
        for sleeve in self.sleeves:
            p = sleeve.performance
            rows.append({
                "strategy": sleeve.title,
                "return": p.total_return, "cagr": p.cagr, "sharpe": p.sharpe,
                "t": p.sharpe_t, "max_dd": p.max_drawdown,
                "trades": p.round_trips, "hit": p.hit_rate,
                "costs": p.total_commission + p.total_slippage,
            })
        bench = self.benchmark_performance
        rows.append({
            "strategy": "— equal-weight universe —",
            "return": bench.total_return, "cagr": bench.cagr,
            "sharpe": bench.sharpe, "t": bench.sharpe_t,
            "max_dd": bench.max_drawdown, "trades": 0,
            "hit": float("nan"), "costs": 0.0,
        })
        return pd.DataFrame(rows)

    def to_dict(self, curve_points: int = 400) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "config": self.config,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "warnings": self.warnings,
            "sleeves": [s.to_dict(curve_points) for s in self.sleeves],
            "benchmark": {
                "label": "Equal-weight universe",
                "equity": _downsample(self.benchmark, curve_points),
                "performance": self.benchmark_performance.as_dict(),
            },
            "combined": {
                "equity": _downsample(self.combined, curve_points),
                "performance": self.combined_performance.as_dict(),
            },
        }


def _downsample(series: pd.Series, points: int) -> list[list[Any]]:
    series = series.dropna()
    if series.empty:
        return []
    if len(series) > points:
        step = max(1, len(series) // points)
        series = pd.concat([series.iloc[::step], series.iloc[[-1]]])
        series = series[~series.index.duplicated(keep="last")]
    return [[ts.isoformat(), round(float(v), 2)] for ts, v in series.items()]


def _trade_rows(log: pd.DataFrame, limit: int = 500) -> list[dict[str, Any]]:
    if log.empty:
        return []
    frame = log.head(limit).copy()
    frame["timestamp"] = frame["timestamp"].astype(str)
    return frame.replace({np.nan: None}).to_dict("records")


# ═══════════════════════════════════════════════════════════════════════════
# Sleeve — one strategy's private world
# ═══════════════════════════════════════════════════════════════════════════

class _Sleeve:
    __slots__ = ("strategy", "portfolio", "pending", "equity", "exposure",
                 "messages", "rejections", "warmup", "requested", "filled",
                 "started")

    def __init__(self, strategy: Strategy, config: RunConfig) -> None:
        self.strategy = strategy
        self.portfolio = Portfolio(config.starting_cash)
        self.pending: list[Order] = []
        self.equity: list[float] = []
        self.exposure: list[float] = []
        self.messages: list[tuple[pd.Timestamp, str]] = []
        self.rejections: list[tuple[pd.Timestamp, str]] = []
        self.warmup = max(0, strategy.warmup) + config.warmup_padding
        self.requested = 0
        self.filled = 0
        self.started = False

    def note(self, ts: pd.Timestamp, message: str) -> None:
        if len(self.messages) < 5000:
            self.messages.append((ts, message))

    def reject(self, ts: pd.Timestamp, message: str) -> None:
        if len(self.rejections) < 5000:
            self.rejections.append((ts, message))


# ═══════════════════════════════════════════════════════════════════════════
# The hub
# ═══════════════════════════════════════════════════════════════════════════

class Hub:
    """Runs strategies over a dataset.

        hub = Hub(dataset, [MeanReversion(sma_window=20), StatArb()])
        result = hub.run()
        print(result.table())
    """

    def __init__(self, dataset: Dataset,
                 strategies: Strategy | Sequence[Strategy],
                 config: RunConfig | None = None) -> None:
        if isinstance(strategies, Strategy):
            strategies = [strategies]
        if not strategies:
            raise ValueError("Hub needs at least one strategy")

        self.dataset = dataset
        self.config = config or RunConfig()
        self.strategies = list(strategies)
        self.warnings: list[str] = []

        # The latency model needs to know how long a bar is.
        self.config.costs.bar_seconds = dataset.bar_seconds

    # ── the loop ───────────────────────────────────────────────────────
    def run(self, progress: Callable[[float, str], None] | None = None
            ) -> RunResult:
        started = time.perf_counter()
        dataset, config = self.dataset, self.config
        config.costs.reset()

        if len(dataset) < 2:
            raise ValueError(f"{dataset.name}: need at least 2 bars to run")

        if dataset.dropped:
            self.warnings.append(
                f"{len(dataset.dropped)} requested symbol(s) have no price "
                f"data and were dropped from the universe: "
                f"{', '.join(dataset.dropped[:12])}"
                f"{'…' if len(dataset.dropped) > 12 else ''}")

        sleeves = [_Sleeve(s, config) for s in self.strategies]
        for sleeve in sleeves:
            if sleeve.warmup >= len(dataset):
                self.warnings.append(
                    f"{sleeve.strategy.title or sleeve.strategy.key}: warmup "
                    f"of {sleeve.warmup} bars exceeds the {len(dataset)}-bar "
                    f"dataset — it will never trade.")

        index = dataset.index
        n = len(index)
        report_every = max(1, n // 100)
        immediate = config.costs.timing is FillTiming.CLOSE

        for i in range(n):
            prices = dataset.prices_at(i)
            timestamp = index[i]

            for sleeve in sleeves:
                # 1 ── fill what was decided last bar
                if sleeve.pending and not immediate:
                    self._execute(sleeve, sleeve.pending, i, reference="open")
                    sleeve.pending = []

                # 2 ── mark to market
                sleeve.equity.append(sleeve.portfolio.value(prices))
                sleeve.exposure.append(sleeve.portfolio.exposure(prices))

                # 3 ── ask for orders
                if i < sleeve.warmup or i == n - 1:
                    continue

                ctx = MarketContext(dataset, sleeve.portfolio, i,
                                    costs=config.costs, log=sleeve.note)
                if not sleeve.started:
                    sleeve.strategy.on_start(ctx)
                    sleeve.started = True

                orders = self._ask(sleeve, ctx)
                if not orders:
                    continue
                sleeve.requested += len(orders)
                if immediate:
                    self._execute(sleeve, orders, i, reference="close")
                else:
                    sleeve.pending = list(orders)

            if progress is not None and i % report_every == 0:
                progress(i / n, f"{timestamp:%Y-%m-%d}")

        # ── wind down ──────────────────────────────────────────────────
        last = n - 1
        final_prices = dataset.prices_at(last)
        for sleeve in sleeves:
            ctx = MarketContext(dataset, sleeve.portfolio, last,
                                costs=config.costs, log=sleeve.note)
            if sleeve.started:
                try:
                    sleeve.strategy.on_finish(ctx)
                except Exception as exc:                     # noqa: BLE001
                    sleeve.reject(index[last], f"on_finish raised: {exc!r}")
            if config.flatten_at_end:
                sleeve.portfolio.flatten(final_prices, index[last])
            sleeve.equity[-1] = sleeve.portfolio.value(final_prices)

        if progress is not None:
            progress(1.0, "done")

        return self._assemble(sleeves, time.perf_counter() - started)

    # ── asking a strategy, safely ──────────────────────────────────────
    def _ask(self, sleeve: _Sleeve, ctx: MarketContext) -> list[Order]:
        """Call `on_bar` and validate what comes back.

        A strategy that raises is disabled for the rest of the run rather than
        killing it. One broken algorithm should not cost you the other five
        results in the same pass.
        """
        try:
            orders = sleeve.strategy.on_bar(ctx)
        except Exception as exc:                             # noqa: BLE001
            sleeve.reject(ctx.timestamp, f"on_bar raised: {exc!r} — disabled")
            sleeve.warmup = len(self.dataset) + 1            # stop asking
            return []

        if orders is None or orders is HOLD:
            return []
        if isinstance(orders, Order):
            orders = [orders]
        clean: list[Order] = []
        for order in orders:
            if not isinstance(order, Order):
                sleeve.reject(ctx.timestamp,
                              f"on_bar returned {type(order).__name__}, "
                              f"expected Order — ignored")
                continue
            clean.append(order)
        return clean

    # ── execution ──────────────────────────────────────────────────────
    def _execute(self, sleeve: _Sleeve, orders: Iterable[Order], i: int,
                 reference: str) -> None:
        dataset, config = self.dataset, self.config
        timestamp = dataset.index[i]
        portfolio = sleeve.portfolio

        groups: dict[str, list[Order]] = defaultdict(list)
        for k, order in enumerate(orders):
            groups[order.group or f"_solo{k}"].append(order)

        mark = dataset.prices_at(i)
        equity = portfolio.value(mark)

        for group_id, group in groups.items():
            resolved: list[tuple[Order, float, float]] = []   # order, ref, qty
            skip = False

            for order in group:
                ref = self._reference_price(order.symbol, i, reference)
                if not np.isfinite(ref) or ref <= 0:
                    sleeve.reject(timestamp,
                                  f"{order.symbol}: no price at fill time — "
                                  f"group '{group_id}' skipped")
                    skip = True
                    break
                quantity = self._resolve_quantity(order, ref, equity,
                                                  portfolio, sleeve, timestamp)
                if quantity is None:
                    continue                      # nothing to do for this leg
                resolved.append((order, ref, quantity))

            if skip or not resolved:
                continue

            priced = [_with_quantity(o, q) for o, _, q in resolved]
            affordable, net = portfolio.can_afford(
                priced, {o.symbol: r for o, r, _ in resolved})
            if not affordable:
                sleeve.reject(
                    timestamp,
                    f"group '{group_id}' needs ${net:,.0f}, sleeve has "
                    f"${portfolio.cash:,.0f} — not taken "
                    f"({', '.join(o.symbol for o, _, _ in resolved)})")
                continue

            for order, ref, quantity in resolved:
                try:
                    self._fill(sleeve, order, ref, quantity, i, timestamp)
                except InsufficientCash as exc:
                    sleeve.reject(timestamp, f"{order.symbol}: {exc}")

    def _reference_price(self, symbol: str, i: int, reference: str) -> float:
        if reference == "open" and "open" in self.dataset.fields:
            value = self.dataset.bar_at(symbol, i).get("open", float("nan"))
            if np.isfinite(value) and value > 0:
                return float(value)
        return self.dataset.price_at(symbol, i)

    def _resolve_quantity(self, order: Order, ref: float, equity: float,
                          portfolio: Portfolio, sleeve: _Sleeve,
                          timestamp: pd.Timestamp) -> float | None:
        """Turn any intent into a share count, applying the position cap."""
        cap_shares = (self.config.max_position_weight * equity / ref
                      if equity > 0 else float("inf"))

        if order.intent is Intent.OPEN:
            quantity = float(order.quantity or 0.0)
            if quantity <= 0:
                return None
            if quantity > cap_shares:
                sleeve.reject(
                    timestamp,
                    f"{order.symbol}: asked for {quantity:,.0f} shares "
                    f"(${quantity * ref:,.0f}), capped at {cap_shares:,.0f} "
                    f"by max_position_weight={self.config.max_position_weight:.0%}")
                quantity = cap_shares
            return quantity if quantity > 0 else None

        if order.intent is Intent.CLOSE:
            side = order.side
            if side is None:
                position = portfolio.get(order.symbol)
                if position is None:
                    return None
                side = position.side
            position = portfolio.get(order.symbol, side)
            if position is None:
                return None
            return min(order.quantity or position.quantity, position.quantity)

        # TARGET — move to a signed weight of sleeve equity.
        #
        # Size against equity *net of what it costs to get in*. A 100% target
        # cannot buy 100% of equity: the fill lands above the reference price
        # and the commission is charged on top, so sizing off gross equity
        # makes every full-weight order fail its own cash check by a few basis
        # points. The buffer is twice the one-way cost plus 2bp, which also
        # absorbs a typical latency draw.
        one_way = (self.config.costs.commission_bps
                   + self.config.costs.slippage_bps) / 10_000.0
        deployable = equity * max(0.0, 1.0 - 2.0 * one_way - 0.0002)
        desired = (order.weight or 0.0) * deployable / ref
        held = 0.0
        for side in (Side.LONG, Side.SHORT):
            position = portfolio.get(order.symbol, side)
            if position is not None:
                held += position.signed_quantity
        delta = desired - held
        return delta if abs(delta) * ref > 1.0 else None   # ignore dust

    def _fill(self, sleeve: _Sleeve, order: Order, ref: float, quantity: float,
              i: int, timestamp: pd.Timestamp) -> None:
        portfolio = sleeve.portfolio
        costs = self.config.costs
        volatility = self._bar_volatility(order.symbol, i)

        if order.intent is Intent.CLOSE:
            side = order.side or (portfolio.get(order.symbol).side
                                  if portfolio.get(order.symbol) else None)
            if side is None:
                return
            price = costs.fill_price(ref, side, opening=False,
                                     volatility=volatility)
            fill = portfolio.close(
                order.symbol, side, price, timestamp=timestamp,
                quantity=quantity, reference_price=ref,
                commission=costs.commission(quantity * price),
                slippage=abs(price - ref) * quantity,
                reason=order.reason, group=order.group, meta=order.meta)
            if fill:
                sleeve.filled += 1
            return

        if order.intent is Intent.OPEN:
            side = order.side
            price = costs.fill_price(ref, side, opening=True,
                                     volatility=volatility)
            portfolio.open(order.symbol, side, quantity, price,
                           timestamp=timestamp, reference_price=ref,
                           commission=costs.commission(quantity * price),
                           slippage=abs(price - ref) * quantity,
                           reason=order.reason, group=order.group,
                           meta=order.meta)
            sleeve.filled += 1
            return

        # TARGET: `quantity` is a signed delta. Unwind the wrong side first,
        # then establish the right one, so a flip is two clean fills rather
        # than one incoherent position.
        remaining = quantity
        wrong = Side.SHORT if remaining > 0 else Side.LONG
        position = portfolio.get(order.symbol, wrong)
        if position is not None:
            unwind = min(position.quantity, abs(remaining))
            price = costs.fill_price(ref, wrong, opening=False,
                                     volatility=volatility)
            portfolio.close(order.symbol, wrong, price, timestamp=timestamp,
                            quantity=unwind, reference_price=ref,
                            commission=costs.commission(unwind * price),
                            slippage=abs(price - ref) * unwind,
                            reason=order.reason or "rebalance",
                            group=order.group, meta=order.meta)
            sleeve.filled += 1
            remaining -= unwind if remaining > 0 else -unwind

        if abs(remaining) * ref <= 1.0:
            return
        side = Side.LONG if remaining > 0 else Side.SHORT
        price = costs.fill_price(ref, side, opening=True, volatility=volatility)
        portfolio.open(order.symbol, side, abs(remaining), price,
                       timestamp=timestamp, reference_price=ref,
                       commission=costs.commission(abs(remaining) * price),
                       slippage=abs(price - ref) * abs(remaining),
                       reason=order.reason or "rebalance", group=order.group,
                       meta=order.meta)
        sleeve.filled += 1

    def _bar_volatility(self, symbol: str, i: int) -> float:
        """Trailing per-bar return standard deviation, for the latency model.

        Trailing, not full-sample: a cost model that peeks is still a model
        that peeks, even though it is not generating the signal.
        """
        history = self.dataset.history(symbol, i, self.config.volatility_lookback + 1)
        if len(history) < 3:
            return 0.0
        returns = np.diff(history) / history[:-1]
        returns = returns[np.isfinite(returns)]
        return float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0

    # ── output ─────────────────────────────────────────────────────────
    def _assemble(self, sleeves: list[_Sleeve], elapsed: float) -> RunResult:
        dataset, config = self.dataset, self.config
        index = dataset.index
        results: list[SleeveResult] = []

        for sleeve in sleeves:
            equity = pd.Series(sleeve.equity, index=index[:len(sleeve.equity)])
            exposure = pd.Series(sleeve.exposure, index=equity.index)
            log = sleeve.portfolio.trade_log()
            results.append(SleeveResult(
                key=sleeve.strategy.key,
                title=sleeve.strategy.title or type(sleeve.strategy).__name__,
                params=dict(sleeve.strategy.settings),
                equity=equity, exposure=exposure, trade_log=log,
                performance=summarise(
                    equity, periods_per_year=dataset.periods_per_year,
                    trade_log=log, exposure=exposure),
                messages=sleeve.messages, rejections=sleeve.rejections,
                orders_requested=sleeve.requested, orders_filled=sleeve.filled))

        _disambiguate(results)

        benchmark = benchmark_equity(dataset, config.starting_cash)
        combined = sum((r.equity for r in results[1:]), results[0].equity) \
            if results else pd.Series(dtype=float)

        return RunResult(
            dataset=dataset.describe(),
            config=config.describe(),
            sleeves=results,
            benchmark=benchmark,
            benchmark_performance=summarise(
                benchmark, periods_per_year=dataset.periods_per_year),
            combined=combined,
            combined_performance=summarise(
                combined, periods_per_year=dataset.periods_per_year),
            elapsed_seconds=elapsed,
            warnings=self.warnings)


def _disambiguate(results: list[SleeveResult]) -> None:
    """Give same-strategy sleeves distinguishable titles.

    Running one strategy twice with different parameters is the platform's
    central comparison — corrected against buggy, screened against unscreened.
    Two tabs reading "BW Valuation (cross-sectional)" makes that comparison
    unusable, so a repeated title gains the parameters that actually differ.
    """
    by_title: dict[str, list[SleeveResult]] = defaultdict(list)
    for sleeve in results:
        by_title[sleeve.title].append(sleeve)

    for title, group in by_title.items():
        if len(group) < 2:
            continue
        differing = sorted({
            name for name in group[0].params
            if len({_hashable(s.params.get(name)) for s in group}) > 1
        })
        for sleeve in group:
            if differing:
                detail = ", ".join(f"{n}={sleeve.params[n]}" for n in differing)
            else:
                detail = f"run {group.index(sleeve) + 1}"
            sleeve.title = f"{title} [{detail}]"


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, (list, dict, set)) else value


def _with_quantity(order: Order, quantity: float) -> Order:
    """A copy of `order` carrying a concrete share count, for the cash check.

    TARGET deltas are signed; the affordability check wants an OPEN of the
    right side with a positive size.
    """
    if order.intent is Intent.TARGET:
        return Order(order.symbol, Intent.OPEN,
                     Side.LONG if quantity > 0 else Side.SHORT,
                     abs(quantity), None, order.reason, order.group, order.meta)
    return Order(order.symbol, order.intent, order.side, abs(quantity),
                 order.weight, order.reason, order.group, order.meta)
