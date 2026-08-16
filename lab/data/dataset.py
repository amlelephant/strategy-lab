"""
lab/data/dataset.py
===================
The aligned, point-in-time view of everything a run can see.

A `Dataset` is two things glued to one clock:

  * **Prices** — one DataFrame per OHLCV field, indexed by timestamp, one
    column per symbol. Only `close` is required; strategies that ask for a
    field the data does not carry get `close` back rather than an exception,
    because most of the price sources here are close-only.

  * **Fundamentals** — quarterly records per symbol, indexed by the date they
    became *knowable* rather than the date they describe.

That second point is the one that matters. A record for the quarter ending
2021-06-30 did not exist on 2021-06-30; it appeared in a filing some weeks
later. Every fundamentals backtest that ranks companies on quarter-end dates
is reading the future, and the effect is not small — it is concentrated
precisely in the companies whose numbers moved, which is precisely the signal.
`report_lag_days` (60 by default) is how this dataset refuses to do that.

Once built, a Dataset is immutable and shared. `for_universe()` and
`between()` return narrowed views, they do not copy the price frames.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

CLOSE = "close"
OHLCV = ("open", "high", "low", "close", "volume")

#: Approximate bars per year, used to annualise Sharpe and to size the
#: latency model's per-bar interval.
_FREQ_TABLE: tuple[tuple[pd.Timedelta, float, float], ...] = (
    # (max bar spacing, periods per year, seconds of market time per bar)
    (pd.Timedelta(minutes=2),  252 * 390,     60.0),
    (pd.Timedelta(minutes=6),  252 * 78,     300.0),
    (pd.Timedelta(minutes=20), 252 * 26,     900.0),
    (pd.Timedelta(minutes=45), 252 * 13,    1800.0),
    (pd.Timedelta(hours=2),    252 * 6.5,   3600.0),
    (pd.Timedelta(days=3),     252.0,     23400.0),
    (pd.Timedelta(days=10),    52.0,     117000.0),
    (pd.Timedelta(days=45),    12.0,     507000.0),
    (pd.Timedelta(days=200),   4.0,     1521000.0),
)


@dataclass(frozen=True)
class FundamentalRecord:
    """One quarterly observation and the two dates that matter for it."""

    period_end: pd.Timestamp   # the quarter it describes
    known_from: pd.Timestamp   # when a trader could first have acted on it
    values: Mapping[str, float]


class Dataset:
    """Aligned prices and point-in-time fundamentals for one run."""

    def __init__(
        self,
        fields: Mapping[str, pd.DataFrame],
        *,
        symbols: Sequence[str] | None = None,
        fundamentals: Mapping[str, list[FundamentalRecord]] | None = None,
        name: str = "dataset",
        report_lag_days: int = 60,
    ) -> None:
        if CLOSE not in fields:
            raise ValueError("a Dataset needs at least a 'close' frame")

        close = fields[CLOSE].sort_index()
        if not isinstance(close.index, pd.DatetimeIndex):
            close.index = pd.to_datetime(close.index)

        self._fields: dict[str, pd.DataFrame] = {}
        for key, frame in fields.items():
            frame = frame.sort_index()
            if not isinstance(frame.index, pd.DatetimeIndex):
                frame.index = pd.to_datetime(frame.index)
            self._fields[key] = frame.reindex(close.index)

        self.name = name
        self.report_lag_days = int(report_lag_days)
        self.index: pd.DatetimeIndex = close.index
        available = tuple(close.columns)
        self.symbols: tuple[str, ...] = tuple(symbols) if symbols else available

        missing = [s for s in self.symbols if s not in available]
        if missing:
            raise KeyError(f"{name}: no price data for {missing}")

        #: Symbols asked for but absent from the price data — set by
        #: `for_universe`, surfaced by the hub.
        self.dropped: tuple[str, ...] = ()

        self.fundamentals: dict[str, list[FundamentalRecord]] = dict(fundamentals or {})
        # Sorted known_from lists, so a point-in-time lookup is a bisect.
        self._known: dict[str, list[pd.Timestamp]] = {
            sym: [r.known_from for r in recs]
            for sym, recs in self.fundamentals.items()
        }

        # Cache the close matrix as a plain array — `history()` runs once per
        # symbol per bar and .iloc is far too slow for that.
        self._close_values: dict[str, np.ndarray] = {
            sym: close[sym].to_numpy(dtype=float) for sym in self.symbols
        }
        self._field_values: dict[tuple[str, str], np.ndarray] = {}

        self.periods_per_year, self.bar_seconds = self._infer_frequency()

    # ── construction helpers ───────────────────────────────────────────
    @classmethod
    def from_close(cls, close: pd.DataFrame, **kwargs) -> "Dataset":
        return cls({CLOSE: close}, **kwargs)

    def _infer_frequency(self) -> tuple[float, float]:
        if len(self.index) < 3:
            return 252.0, 23400.0
        spacing = pd.Series(self.index).diff().dropna().median()
        for limit, ppy, secs in _FREQ_TABLE:
            if spacing <= limit:
                return ppy, secs
        return 1.0, 31_536_000.0

    # ── narrowing ──────────────────────────────────────────────────────
    def for_universe(self, symbols: Sequence[str]) -> "Dataset":
        """A view restricted to `symbols`, in the order given.

        Symbols with no price column are dropped rather than raising — a
        universe of 1,900 tickers assembled from a fundamentals file will
        always contain a few that never priced.
        """
        keep = [s for s in symbols if s in self._fields[CLOSE].columns]
        dropped = [s for s in symbols if s not in self._fields[CLOSE].columns]
        if not keep:
            raise KeyError(
                f"{self.name}: none of {list(symbols)[:8]} have prices")
        view = Dataset(
            {k: v[keep] for k, v in self._fields.items()},
            symbols=keep,
            fundamentals={s: self.fundamentals[s] for s in keep
                          if s in self.fundamentals},
            name=self.name, report_lag_days=self.report_lag_days)
        # Surfaced by the hub as a run warning. A universe that quietly
        # shrinks is how a backtest ends up reporting on a different set of
        # companies than the one it claims to test.
        view.dropped = tuple(dropped)
        return view

    def between(self, start=None, end=None) -> "Dataset":
        mask = np.ones(len(self.index), dtype=bool)
        if start is not None:
            mask &= self.index >= pd.Timestamp(start)
        if end is not None:
            mask &= self.index <= pd.Timestamp(end)
        if not mask.any():
            raise ValueError(f"{self.name}: no bars between {start} and {end}")
        return Dataset(
            {k: v.loc[mask] for k, v in self._fields.items()},
            symbols=self.symbols, fundamentals=self.fundamentals,
            name=self.name, report_lag_days=self.report_lag_days)

    def drop_incomplete(self, min_coverage: float = 0.95) -> "Dataset":
        """Drop symbols priced on less than `min_coverage` of the bars."""
        close = self._fields[CLOSE][list(self.symbols)]
        coverage = close.notna().mean()
        keep = [s for s in self.symbols if coverage[s] >= min_coverage]
        return self.for_universe(keep)

    # ── price access (hot path — called once per symbol per bar) ───────
    def _values(self, symbol: str, field: str) -> np.ndarray:
        if field == CLOSE:
            return self._close_values[symbol]
        key = (symbol, field)
        cached = self._field_values.get(key)
        if cached is None:
            frame = self._fields.get(field)
            if frame is None or symbol not in frame.columns:
                cached = self._close_values[symbol]
            else:
                cached = frame[symbol].to_numpy(dtype=float)
            self._field_values[key] = cached
        return cached

    def price_at(self, symbol: str, i: int) -> float:
        """Close at bar `i`. Falls back to the last known close so a symbol
        that stops printing does not silently mark the book to NaN."""
        values = self._close_values.get(symbol)
        if values is None:
            return float("nan")
        v = values[i]
        if v == v:
            return float(v)
        prior = values[:i + 1]
        finite = prior[np.isfinite(prior)]
        return float(finite[-1]) if len(finite) else float("nan")

    def prices_at(self, i: int) -> dict[str, float]:
        return {s: self.price_at(s, i) for s in self.symbols}

    def bar_at(self, symbol: str, i: int) -> dict[str, float]:
        return {f: float(self._values(symbol, f)[i]) for f in OHLCV
                if f in self._fields or f == CLOSE}

    def history(self, symbol: str, i: int, n: int | None = None,
                field: str = CLOSE) -> np.ndarray:
        """Observations up to *and including* bar `i`, oldest first.

        This is the only way a strategy reads the past, and it cannot reach
        past `i`. NaNs are dropped, so a strategy always receives a clean
        series and never has to guard for holes.
        """
        values = self._values(symbol, field)
        start = 0 if n is None else max(0, i + 1 - n)
        window = values[start:i + 1]
        return window[np.isfinite(window)]

    def window(self, i: int, n: int, symbols: Sequence[str] | None = None,
               field: str = CLOSE) -> pd.DataFrame:
        """Aligned last-`n`-bars frame for several symbols, rows with any NaN
        dropped — which is what an OLS hedge ratio needs."""
        cols = list(symbols) if symbols else list(self.symbols)
        frame = self._fields.get(field, self._fields[CLOSE])
        cols = [c for c in cols if c in frame.columns]
        start = max(0, i + 1 - n)
        return frame.iloc[start:i + 1][cols].dropna()

    # ── fundamentals (point-in-time) ───────────────────────────────────
    def fundamentals_at(self, symbol: str, when: pd.Timestamp
                        ) -> dict[str, float] | None:
        """Most recent record knowable at `when`, or None."""
        known = self._known.get(symbol)
        if not known:
            return None
        pos = bisect.bisect_right(known, when) - 1
        if pos < 0:
            return None
        return dict(self.fundamentals[symbol][pos].values)

    def cross_section_at(self, when: pd.Timestamp) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for symbol in self.symbols:
            record = self.fundamentals_at(symbol, when)
            if record is not None:
                out[symbol] = record
        return out

    def fundamental_dates(self) -> list[pd.Timestamp]:
        """Every distinct knowledge date in the fundamentals, sorted. Used by
        cross-sectional strategies to detect a rebalance bar."""
        seen: set[pd.Timestamp] = set()
        for known in self._known.values():
            seen.update(known)
        return sorted(seen)

    # ── introspection ──────────────────────────────────────────────────
    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(self._fields)

    def coverage(self) -> pd.Series:
        return self._fields[CLOSE][list(self.symbols)].notna().mean()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "symbols": len(self.symbols),
            "bars": len(self.index),
            "start": str(self.index[0].date()) if len(self.index) else None,
            "end": str(self.index[-1].date()) if len(self.index) else None,
            "fields": list(self.fields),
            "periods_per_year": self.periods_per_year,
            "has_fundamentals": bool(self.fundamentals),
            "fundamental_symbols": len(self.fundamentals),
            "report_lag_days": self.report_lag_days,
        }

    def __len__(self) -> int:
        return len(self.index)

    def __repr__(self) -> str:
        d = self.describe()
        return (f"<Dataset {d['name']}: {d['symbols']} symbols x {d['bars']} "
                f"bars {d['start']} to {d['end']}>")
