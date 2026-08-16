"""
lab/core/portfolio.py
=====================
Cash, positions and fills for one strategy sleeve.

This is the descendant of `paper-broker/algos/paperInterface.py` — the order
simulator that started this whole project, written because every off-the-shelf
platform hides its fill logic. Three pieces of it are load-bearing and are
preserved here deliberately:

  * **Short sales credit cash.** Selling borrowed shares brings money in;
    buying them back pays it out. A pairs trade is therefore far cheaper than
    the sum of its two notionals, which is the entire reason
    `can_afford(trade1, trade2)` existed rather than a naive `cash >= cost`.

  * **Share-weighted cost basis.** Adding to a position re-averages the entry
    price. Keeping the first entry price instead silently corrupts every
    later P&L number on that position.

  * **Long and short in the same name are separate positions.** They are not
    netted, because the strategies that opened them did not intend them to be.

What is new is that nothing here reaches for a live price. The hub passes a
price map in; the portfolio never looks anything up for itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import pandas as pd

from .contract import Intent, Order, Position, Side


@dataclass(frozen=True)
class Fill:
    """One executed leg. The trade log is a list of these."""

    timestamp: pd.Timestamp
    symbol: str
    action: str            # "open" | "close"
    side: Side
    quantity: float
    price: float           # price actually paid, after slippage
    reference_price: float  # the bar price the decision was made against
    commission: float
    slippage: float
    realised_pnl: float
    cash_after: float
    reason: str
    group: str
    meta: Mapping[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        d = {
            "timestamp": self.timestamp, "symbol": self.symbol,
            "action": self.action, "side": self.side.value,
            "quantity": round(self.quantity, 4), "price": round(self.price, 4),
            "reference_price": round(self.reference_price, 4),
            "commission": round(self.commission, 2),
            "slippage": round(self.slippage, 2),
            "realised_pnl": round(self.realised_pnl, 2),
            "cash_after": round(self.cash_after, 2),
            "reason": self.reason, "group": self.group,
        }
        d.update({f"meta.{k}": v for k, v in self.meta.items()})
        return d


class InsufficientCash(Exception):
    """Raised when a fill would take cash negative. The hub catches this,
    skips the order group, and records the rejection."""


class Portfolio:
    """A sleeve of capital with its own cash, positions and trade log."""

    def __init__(self, starting_cash: float = 100_000.0,
                 allow_negative_cash: bool = False) -> None:
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.allow_negative_cash = allow_negative_cash
        self._positions: dict[tuple[str, Side], Position] = {}
        self.fills: list[Fill] = []
        self.realised_pnl = 0.0
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self.turnover_notional = 0.0

    # ── inspection ─────────────────────────────────────────────────────
    def get(self, symbol: str, side: Side | None = None) -> Position | None:
        if side is not None:
            return self._positions.get((symbol, side))
        for s in (Side.LONG, Side.SHORT):
            pos = self._positions.get((symbol, s))
            if pos is not None:
                return pos
        return None

    def open_positions(self) -> tuple[Position, ...]:
        return tuple(self._positions.values())

    @property
    def symbols_held(self) -> set[str]:
        return {sym for sym, _ in self._positions}

    def value(self, prices: Mapping[str, float]) -> float:
        """Mark the book to market.

        A long adds its market value; a short subtracts it, because a short is
        a liability to buy the shares back. (The original had this method
        iterating `.items()` over a list, so it could never run — one of the
        first things the port fixed.)
        """
        total = self.cash
        for pos in self._positions.values():
            price = prices.get(pos.symbol)
            if price is None or price != price:  # missing or NaN
                price = pos.cost_basis           # stale mark beats no mark
            total += pos.signed_quantity * price
        return total

    def exposure(self, prices: Mapping[str, float]) -> float:
        """Gross notional as a fraction of equity."""
        gross = sum(pos.notional(prices.get(pos.symbol) or pos.cost_basis)
                    for pos in self._positions.values())
        equity = self.value(prices)
        return gross / equity if equity else 0.0

    # ── affordability ──────────────────────────────────────────────────
    def can_afford(self, orders: Iterable[Order],
                   prices: Mapping[str, float]) -> tuple[bool, float]:
        """Net cash impact of a group of orders, and whether it clears.

        Ported from `PaperBroker.can_afford`: longs cost cash, shorts provide
        it, and only the *net* has to be covered.
        """
        net = 0.0
        for order in orders:
            price = prices.get(order.symbol)
            if price is None or price != price:
                return False, 0.0
            if order.intent is Intent.OPEN:
                cost = (order.quantity or 0.0) * price
                net += cost if order.side is Side.LONG else -cost
            elif order.intent is Intent.CLOSE:
                pos = self.get(order.symbol, order.side)
                if pos is None:
                    continue
                qty = min(order.quantity or pos.quantity, pos.quantity)
                proceeds = qty * price
                net += -proceeds if pos.side is Side.LONG else proceeds
        if self.allow_negative_cash:
            return True, net
        return (self.cash >= net), net

    # ── mutation ───────────────────────────────────────────────────────
    def open(self, symbol: str, side: Side, quantity: float, price: float, *,
             timestamp: pd.Timestamp, reference_price: float | None = None,
             commission: float = 0.0, slippage: float = 0.0,
             reason: str = "", group: str = "",
             meta: Mapping[str, Any] | None = None) -> Fill:
        if quantity <= 0:
            raise ValueError(f"open {symbol}: quantity must be > 0")

        notional = quantity * price
        cash_delta = -notional if side is Side.LONG else notional
        cash_after = self.cash + cash_delta - commission

        if cash_after < 0 and not self.allow_negative_cash:
            raise InsufficientCash(
                f"{symbol} {side.value} {quantity:g} @ {price:.2f} needs "
                f"${-cash_delta + commission:,.2f}, have ${self.cash:,.2f}")

        self.cash = cash_after
        self.total_commission += commission
        self.total_slippage += slippage
        self.turnover_notional += notional

        existing = self._positions.get((symbol, side))
        if existing is not None:
            total = existing.quantity + quantity
            existing.cost_basis = (
                existing.cost_basis * existing.quantity + price * quantity
            ) / total
            existing.quantity = total
        else:
            self._positions[(symbol, side)] = Position(
                symbol=symbol, side=side, quantity=quantity,
                cost_basis=price, opened_at=timestamp)

        fill = Fill(timestamp, symbol, "open", side, quantity, price,
                    reference_price if reference_price is not None else price,
                    commission, slippage, 0.0, self.cash, reason, group,
                    dict(meta or {}))
        self.fills.append(fill)
        return fill

    def close(self, symbol: str, side: Side, price: float, *,
              timestamp: pd.Timestamp, quantity: float | None = None,
              reference_price: float | None = None, commission: float = 0.0,
              slippage: float = 0.0, reason: str = "", group: str = "",
              meta: Mapping[str, Any] | None = None) -> Fill | None:
        pos = self._positions.get((symbol, side))
        if pos is None:
            return None

        qty = pos.quantity if quantity is None else min(quantity, pos.quantity)
        if qty <= 0:
            return None

        proceeds = qty * price
        entry_cost = qty * pos.cost_basis

        if side is Side.LONG:
            self.cash += proceeds
            pnl = proceeds - entry_cost
        else:                                  # buy the borrowed shares back
            self.cash -= proceeds
            pnl = entry_cost - proceeds

        pnl -= commission
        self.cash -= commission
        self.realised_pnl += pnl
        self.total_commission += commission
        self.total_slippage += slippage
        self.turnover_notional += proceeds

        # `<= 0`, not `== 0`: quantities are floats.
        pos.quantity -= qty
        if pos.quantity <= 1e-9:
            del self._positions[(symbol, side)]

        fill = Fill(timestamp, symbol, "close", side, qty, price,
                    reference_price if reference_price is not None else price,
                    commission, slippage, pnl, self.cash, reason, group,
                    dict(meta or {}))
        self.fills.append(fill)
        return fill

    def flatten(self, prices: Mapping[str, float], timestamp: pd.Timestamp,
                reason: str = "end of backtest") -> list[Fill]:
        """Close everything. The hub calls this after the last bar so the
        final equity number is cash, not a mark."""
        out = []
        for (symbol, side) in list(self._positions):
            price = prices.get(symbol)
            if price is None or price != price:
                price = self._positions[(symbol, side)].cost_basis
            fill = self.close(symbol, side, price, timestamp=timestamp,
                              reason=reason)
            if fill:
                out.append(fill)
        return out

    # ── output ─────────────────────────────────────────────────────────
    def trade_log(self) -> pd.DataFrame:
        if not self.fills:
            return pd.DataFrame(columns=[
                "timestamp", "symbol", "action", "side", "quantity", "price",
                "reference_price", "commission", "slippage", "realised_pnl",
                "cash_after", "reason", "group"])
        return pd.DataFrame([f.as_row() for f in self.fills])

    def round_trips(self) -> pd.DataFrame:
        """Closing fills only — one row per realised trade, which is what
        hit-rate and profit-factor are computed over."""
        log = self.trade_log()
        if log.empty:
            return log
        return log[log["action"] == "close"].reset_index(drop=True)

    def __repr__(self) -> str:
        return (f"<Portfolio cash=${self.cash:,.0f} "
                f"positions={len(self._positions)} fills={len(self.fills)}>")
