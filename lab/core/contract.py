"""
lab/core/contract.py
====================
The contract between the hub and a strategy.

This is the only file a new strategy needs to understand. The hub owns the
clock, the data, the cash and the fills; a strategy owns exactly one thing —
deciding what to trade at the timestamp it is handed. It never downloads data,
never touches cash, and never marks its own book.

    hub  ──── MarketContext (what is knowable at time t) ────▶  Strategy
    hub  ◀─── list[Order]   (what to do about it)          ────  Strategy

The asymmetry is deliberate. Every strategy in this repository used to fetch
its own prices and keep its own cash, which meant no two of them were
comparable and none of them could be replayed. Moving both responsibilities
into the hub is what makes a result a measurement rather than an anecdote.

See AGENTS.md for the short version written for automated contributors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import Enum
from typing import Any, ClassVar, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════
# Trade actions — what a strategy returns
# ═══════════════════════════════════════════════════════════════════════════

class Side(str, Enum):
    """Direction of a position. Values match the strings the original
    paper-broker wrote to disk, so old state files still parse."""
    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


class Intent(str, Enum):
    """What an order is *for*. The hub executes each intent differently."""

    OPEN = "open"      # establish a new position (or add to one)
    CLOSE = "close"    # flatten an existing position, wholly or partly
    TARGET = "target"  # hold `weight` of sleeve equity in this symbol


@dataclass(frozen=True)
class Order:
    """One trade action at one timestamp.

    An order carries no price. The hub decides the fill price from the run's
    `FillTiming` and `CostModel` — a strategy that could name its own fill
    price could name a good one, and every backtest built that way flatters
    itself.

    Attributes
    ----------
    symbol
        Ticker the action applies to.
    intent
        OPEN, CLOSE or TARGET. See `Intent`.
    side
        Required for OPEN. Ignored by TARGET (sign of `weight` carries it).
        Optional for CLOSE — omit to flatten both directions.
    quantity
        Shares, for OPEN/CLOSE. `None` on a CLOSE means "all of it".
    weight
        Fraction of sleeve equity, for TARGET. Negative means short.
    reason
        Why. This lands in the trade log and the GUI, and it is the single
        highest-value field here: a trade log that says *why* is a research
        artefact, one that says only *what* is a receipt.
    group
        Legs of one logical trade share a group id. The hub fills a group
        atomically — either every leg or none — which is what keeps a pairs
        trade from ending up half on.
    meta
        Free-form diagnostics (z-score, hedge ratio, score percentile…).
        Carried into the trade log untouched.
    """

    symbol: str
    intent: Intent
    side: Side | None = None
    quantity: float | None = None
    weight: float | None = None
    reason: str = ""
    group: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    # ── constructors (use these; they validate) ─────────────────────────
    @staticmethod
    def open(symbol: str, side: Side | str, quantity: float, *,
             reason: str = "", group: str = "", **meta: Any) -> "Order":
        if quantity is None or quantity <= 0:
            raise ValueError(f"OPEN {symbol}: quantity must be > 0, got {quantity!r}")
        return Order(symbol, Intent.OPEN, Side(side), float(quantity),
                     None, reason, group, meta)

    @staticmethod
    def close(symbol: str, side: Side | str | None = None,
              quantity: float | None = None, *, reason: str = "",
              group: str = "", **meta: Any) -> "Order":
        return Order(symbol, Intent.CLOSE, Side(side) if side else None,
                     quantity, None, reason, group, meta)

    @staticmethod
    def target(symbol: str, weight: float, *, reason: str = "",
               group: str = "", **meta: Any) -> "Order":
        return Order(symbol, Intent.TARGET, None, None, float(weight),
                     reason, group, meta)

    def __post_init__(self) -> None:
        if self.intent is Intent.OPEN and self.side is None:
            raise ValueError(f"OPEN {self.symbol}: side is required")
        if self.intent is Intent.TARGET and self.weight is None:
            raise ValueError(f"TARGET {self.symbol}: weight is required")

    def __str__(self) -> str:
        if self.intent is Intent.TARGET:
            what = f"target {self.weight:+.2%}"
        elif self.intent is Intent.CLOSE:
            what = f"close {self.quantity if self.quantity else 'all'}"
        else:
            what = f"open {self.side.value} {self.quantity:g}"
        return f"{self.symbol} {what}" + (f"  ({self.reason})" if self.reason else "")


#: Returning this from `on_bar` is the same as returning `[]`. It exists
#: because the original strategy classes returned the string 'HOLD', and
#: reading `return HOLD` in a strategy is clearer than `return []`.
HOLD: list[Order] = []


# ═══════════════════════════════════════════════════════════════════════════
# Positions — what the hub holds on a strategy's behalf
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    """An open position. Cost basis is share-weighted across adds."""

    symbol: str
    side: Side
    quantity: float
    cost_basis: float
    opened_at: pd.Timestamp

    @property
    def signed_quantity(self) -> float:
        return self.quantity if self.side is Side.LONG else -self.quantity

    def unrealised(self, price: float) -> float:
        if self.side is Side.LONG:
            return (price - self.cost_basis) * self.quantity
        return (self.cost_basis - price) * self.quantity

    def notional(self, price: float) -> float:
        return abs(self.quantity) * price


# ═══════════════════════════════════════════════════════════════════════════
# Parameters — declared, not hardcoded
# ═══════════════════════════════════════════════════════════════════════════

class ParamKind(str, Enum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    CHOICE = "choice"


@dataclass(frozen=True)
class Param:
    """One tunable knob on a strategy.

    Declaring parameters instead of hardcoding them buys three things at once:
    the GUI renders a form with no per-strategy code, `sweep.py` can enumerate
    a grid without knowing what the strategy does, and a run's parameters are
    recorded with its result so it can be reproduced.
    """

    name: str
    default: Any
    kind: ParamKind = ParamKind.FLOAT
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    help: str = ""
    #: Values a sweep should try when none are given explicitly.
    grid: tuple[Any, ...] = ()

    def coerce(self, value: Any) -> Any:
        """Cast a value (typically a string from an HTML form) to this
        parameter's type, and range-check it."""
        if self.kind is ParamKind.INT:
            value = int(float(value))
        elif self.kind is ParamKind.FLOAT:
            value = float(value)
        elif self.kind is ParamKind.BOOL:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            value = bool(value)
        elif self.kind is ParamKind.CHOICE:
            if value not in self.choices:
                # HTML forms deliver everything as text
                match = [c for c in self.choices if str(c) == str(value)]
                if not match:
                    raise ValueError(
                        f"{self.name}: {value!r} not in {self.choices}")
                value = match[0]

        if self.low is not None and value < self.low:
            raise ValueError(f"{self.name}: {value} below minimum {self.low}")
        if self.high is not None and value > self.high:
            raise ValueError(f"{self.name}: {value} above maximum {self.high}")
        return value

    def sweep_values(self) -> tuple[Any, ...]:
        return self.grid if self.grid else (self.default,)

    # ── HTML-safe bounds ───────────────────────────────────────────────
    #
    # An `<input type="number">` takes its **step base from `min`**, so the
    # values it accepts are `min, min+step, min+2·step, …`. A triple like
    # `low=0.01, step=0.05, default=0.9` therefore describes a field that
    # rejects its own default: 0.9 is 17.8 steps above 0.01, and the browser
    # says "please enter a valid value" before the form is ever submitted.
    #
    # Rather than requiring every author to keep `low`, `step` and `default`
    # mutually consistent — a rule nobody will remember — the two properties
    # below re-anchor the grid on `default` and move the bounds inward to the
    # nearest grid points. The browser then only ever offers values the field
    # accepts, and `coerce()` still enforces the true `low`/`high` server-side.
    #
    # Decimal, not float: `0.9 - 17 * 0.05` is `0.04999999999999993`, and
    # emitting that as `min` reintroduces the same mismatch it is fixing.

    def _grid_bound(self, bound: float | None, upward: bool) -> float | None:
        if bound is None:
            return None
        if self.step is None or self.step <= 0 or self.kind is ParamKind.CHOICE:
            return bound
        try:
            default = Decimal(str(self.default))
            step = Decimal(str(self.step))
            span = (Decimal(str(bound)) - default) / step
        except (InvalidOperation, TypeError, ValueError):
            return bound
        # Floor the magnitude in both directions: the bound moves toward the
        # default, never away from it, so the browser can never offer a value
        # the server would reject.
        steps = span.to_integral_value(rounding=ROUND_CEILING if upward
                                       else ROUND_FLOOR)
        snapped = default + steps * step
        return float(snapped.normalize())

    @property
    def html_min(self) -> float | None:
        """`low`, raised to the first step-grid point at or above it."""
        return self._grid_bound(self.low, upward=True)

    @property
    def html_max(self) -> float | None:
        """`high`, lowered to the last step-grid point at or below it."""
        return self._grid_bound(self.high, upward=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "default": self.default, "kind": self.kind.value,
            "low": self.low, "high": self.high, "step": self.step,
            "html_min": self.html_min, "html_max": self.html_max,
            "choices": list(self.choices), "help": self.help,
            "grid": list(self.grid),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MarketContext — what is knowable at time t, and nothing more
# ═══════════════════════════════════════════════════════════════════════════

class MarketContext:
    """A read-only view of the world as of one timestamp.

    Every accessor slices the dataset at or before `self.i`. There is no
    method on this object that can return a future value, which is why
    lookahead bias is a property of the framework here rather than a thing
    each strategy has to remember not to do.

    The hub constructs one of these per bar per strategy and throws it away.
    Do not hold a reference to it across bars — hold state on `self` instead.
    """

    __slots__ = ("_ds", "_pf", "i", "timestamp", "_costs", "_log")

    def __init__(self, dataset, portfolio, i: int, costs=None, log=None):
        self._ds = dataset
        self._pf = portfolio
        self._costs = costs
        self._log = log
        self.i = i
        self.timestamp: pd.Timestamp = dataset.index[i]

    # ── universe ───────────────────────────────────────────────────────
    @property
    def symbols(self) -> tuple[str, ...]:
        """Symbols in this run's universe, in the order the run declared."""
        return self._ds.symbols

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """The universe consumed two at a time.

        `['GS','MS','KO','PEP']` becomes `(('GS','MS'), ('KO','PEP'))` — the
        same convention the original `backTestStat.py` used with its
        `all_symbols` list of lists. Pair strategies read this instead of
        `symbols`.
        """
        s = self.symbols
        return tuple(zip(s[0::2], s[1::2]))

    # ── prices ─────────────────────────────────────────────────────────
    def price(self, symbol: str) -> float:
        """Latest close for `symbol`. NaN if it does not trade at this bar."""
        return self._ds.price_at(symbol, self.i)

    def bar(self, symbol: str) -> dict[str, float]:
        """OHLCV for `symbol` at this timestamp, as far as the data has it."""
        return self._ds.bar_at(symbol, self.i)

    def history(self, symbol: str, n: int | None = None,
                field: str = "close") -> np.ndarray:
        """Last `n` observations for `symbol`, ending at and including now.

        Returns a plain ndarray, oldest first. `None` means all history.
        """
        return self._ds.history(symbol, self.i, n, field)

    def window(self, n: int, symbols: Sequence[str] | None = None,
               field: str = "close") -> pd.DataFrame:
        """Last `n` bars for several symbols as a DataFrame, NaNs dropped.

        This is what a pairs strategy regresses on.
        """
        return self._ds.window(self.i, n, symbols, field)

    def returns(self, symbol: str, n: int | None = None) -> np.ndarray:
        h = self.history(symbol, None if n is None else n + 1)
        return np.diff(h) / h[:-1] if len(h) > 1 else np.array([])

    def volatility(self, symbol: str, n: int = 60, annualised: bool = True) -> float:
        """Realised volatility from the last `n` returns."""
        r = self.returns(symbol, n)
        r = r[np.isfinite(r)]
        if len(r) < 2:
            return float("nan")
        sd = float(np.std(r, ddof=1))
        return sd * np.sqrt(self._ds.periods_per_year) if annualised else sd

    # ── fundamentals (point-in-time) ───────────────────────────────────
    def fundamentals(self, symbol: str) -> dict[str, float] | None:
        """The most recent fundamental record *publicly known* by now.

        The dataset applies a reporting lag when it indexes fundamentals, so
        a quarter ending 2021-06-30 is not visible here until the filing has
        plausibly landed. Quarter-end dates are not knowledge dates, and
        treating them as such is the single most common way a fundamentals
        backtest ends up reading the future.
        """
        return self._ds.fundamentals_at(symbol, self.timestamp)

    def cross_section(self) -> dict[str, dict[str, float]]:
        """`fundamentals()` for every symbol that has a record by now.

        Cross-sectional strategies rank within *this* dict — never against a
        distribution pooled over the whole backtest, which would let a 2021
        company be scored against 2024 peers.
        """
        return self._ds.cross_section_at(self.timestamp)

    # ── book ───────────────────────────────────────────────────────────
    def position(self, symbol: str, side: Side | None = None) -> Position | None:
        return self._pf.get(symbol, side)

    def positions(self) -> tuple[Position, ...]:
        return self._pf.open_positions()

    def in_market(self, *symbols: str) -> bool:
        """True if any of `symbols` currently has an open position.

        Kept from the original `PaperBroker.in_market(tkr1, tkr2)` — it is
        the guard every one of these strategies opens with.
        """
        return any(self._pf.get(s) is not None for s in symbols)

    @property
    def cash(self) -> float:
        return self._pf.cash

    @property
    def equity(self) -> float:
        return self._pf.value(self._ds.prices_at(self.i))

    @property
    def starting_equity(self) -> float:
        return self._pf.starting_cash

    def affordable(self, orders: Iterable[Order]) -> bool:
        """Would this group of orders leave cash non-negative?

        Ports `PaperBroker.can_afford` — short legs *credit* cash, so a pairs
        trade costs far less than the sum of its notionals.
        """
        return self._pf.can_afford(orders, self._ds.prices_at(self.i))[0]

    # ── diagnostics ────────────────────────────────────────────────────
    def log(self, message: str) -> None:
        """Record a line against this timestamp. Shown in the GUI's run log.

        Use it for rejections — a strategy that says why it *didn't* trade is
        far more useful in review than one that goes quiet.
        """
        if self._log is not None:
            self._log(self.timestamp, message)

    @property
    def costs(self):
        """The run's CostModel — read it if the decision depends on costs
        (see `stat_arb_ev`, which will not enter unless the edge clears them).
        """
        return self._costs

    @property
    def periods_per_year(self) -> float:
        return self._ds.periods_per_year

    def __repr__(self) -> str:
        return f"<MarketContext {self.timestamp:%Y-%m-%d} bar {self.i}>"


# ═══════════════════════════════════════════════════════════════════════════
# Strategy — the base class every algorithm implements
# ═══════════════════════════════════════════════════════════════════════════

class Universe(str, Enum):
    """What shape of universe a strategy expects. Metadata for the GUI and
    for run validation; the hub itself treats every strategy identically."""

    SINGLE = "single"          # acts on each symbol independently
    PAIR = "pair"              # consumes ctx.pairs — universe must be even
    CROSS_SECTION = "cross"    # ranks the whole universe against itself


class Strategy(ABC):
    """Base class for every algorithm in `lab/strategies/`.

    Implement `on_bar`. Everything else is optional.

    ┌─ what you declare ─────────────────────────────────────────────────┐
    │ key       stable id, used in URLs and saved results — never rename │
    │ title     human name for the GUI                                   │
    │ universe  SINGLE | PAIR | CROSS_SECTION                            │
    │ summary   one sentence: what edge is this claiming?                │
    │ params    tuple[Param] — no magic numbers in the body              │
    └────────────────────────────────────────────────────────────────────┘

    ┌─ what you implement ───────────────────────────────────────────────┐
    │ warmup    bars of history needed before the first call (property)  │
    │ on_start  called once, before the first bar                        │
    │ on_bar    called once per bar → list[Order]      ← required        │
    │ on_finish called once, after the last bar                          │
    └────────────────────────────────────────────────────────────────────┘

    Parameters arrive as keyword arguments, are validated against `params`,
    and are set as attributes. `MeanReversion(sma_window=30)` leaves
    `self.sma_window == 30` and every other declared param at its default.
    """

    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    universe: ClassVar[Universe] = Universe.SINGLE
    summary: ClassVar[str] = ""
    #: Longer prose shown on the strategy's GUI page. Markdown-ish, optional.
    notes: ClassVar[str] = ""
    #: Where this code came from, if it predates the platform. Shown in the
    #: GUI so a reader can tell ported work from work written against the hub.
    provenance: ClassVar[str] = ""
    params: ClassVar[tuple[Param, ...]] = ()

    def __init__(self, **kwargs: Any) -> None:
        declared = {p.name: p for p in self.params}
        unknown = set(kwargs) - set(declared)
        if unknown:
            raise TypeError(
                f"{type(self).__name__}: unknown parameter(s) "
                f"{sorted(unknown)}; declared: {sorted(declared)}")
        for name, param in declared.items():
            raw = kwargs.get(name, param.default)
            setattr(self, name, param.coerce(raw))
        self.settings: dict[str, Any] = {n: getattr(self, n) for n in declared}

    # ── lifecycle ──────────────────────────────────────────────────────
    @property
    def warmup(self) -> int:
        """Bars of history to skip before `on_bar` is first called.

        Return the longest lookback the strategy uses. The hub still *advances*
        through warmup bars, it just does not ask for orders — so anything you
        accumulate in `on_bar` will not see them. Read history from the
        context instead of accumulating it and this never matters.
        """
        return 0

    def on_start(self, ctx: MarketContext) -> None:
        """Called once before the first non-warmup bar. Initialise state here,
        not in `__init__` — the same instance may be reused across runs."""

    @abstractmethod
    def on_bar(self, ctx: MarketContext) -> Sequence[Order]:
        """Decide what to do at `ctx.timestamp`.

        Return a list of `Order` (possibly empty — or `HOLD`, which is the
        same thing and reads better). Return orders for as many symbols as
        you like; legs that must fill together share a `group`.

        This method must be pure with respect to the outside world: no
        network, no disk, no `datetime.now()`. Everything it needs is on
        `ctx`. That is what makes a run reproducible.
        """

    def on_finish(self, ctx: MarketContext) -> None:
        """Called once after the last bar. The hub flattens the book after
        this returns, so there is no need to close positions here."""

    # ── introspection (GUI + registry) ─────────────────────────────────
    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "key": cls.key,
            "title": cls.title or cls.__name__,
            "universe": cls.universe.value,
            "summary": cls.summary,
            "notes": cls.notes,
            "provenance": cls.provenance,
            "params": [p.as_dict() for p in cls.params],
        }

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.settings.items())
        return f"{type(self).__name__}({args})"
