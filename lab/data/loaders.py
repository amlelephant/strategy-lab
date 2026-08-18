"""
lab/data/loaders.py
===================
Getting common market data into the canonical shape.

The platform's data contract is deliberately boring, because the point of a
contract is that outside data can meet it. The canonical form is **tidy OHLCV**:

    timestamp,symbol,open,high,low,close,volume
    2021-01-04,AAPL,133.52,133.61,126.76,129.41,143301900
    2021-01-04,MSFT,222.53,223.00,214.81,217.69,37130100

Anything with a timestamp, a symbol and a close can be loaded. Wide frames
(one column per symbol) and per-symbol CSV directories are recognised too, and
`load_prices()` sniffs which of the three it is looking at.

`fetch_yfinance` is the one loader that touches the network, and it caches to
disk on the way past. Nothing inside a backtest ever downloads: a run that
refetches its own data is a run that cannot be replayed, and every strategy in
this repository used to do exactly that.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .dataset import CLOSE, OHLCV, Dataset, FundamentalRecord

DATA_DIR = Path(os.environ.get("LAB_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
CACHE_DIR = DATA_DIR / "cache"

_TIME_KEYS = ("timestamp", "date", "datetime", "time", "Date", "Datetime")
_SYMBOL_KEYS = ("symbol", "ticker", "Symbol", "Ticker")

# ── the market benchmark ─────────────────────────────────────────────────
# Every result is measured against the market, and the market here means the
# S&P 500 as tradeable by anyone: SPY, auto-adjusted, back to 2005. It lives
# in its own file rather than inside a price dump because it is not part of
# any universe — it is the yardstick, and a yardstick in the price file is a
# yardstick some strategy will end up holding.
MARKET_FILE = "market_spy.csv"
MARKET_SYMBOL = "SPY"
MARKET_LABEL = "the S&P 500"

# ── the risk-free rate ───────────────────────────────────────────────────
# Sharpe and Jensen's alpha are both defined on returns *in excess of* the
# risk-free rate. Assuming that rate is zero is not a small simplification: it
# hands every strategy rf·(1−β) of free alpha, which is largest for exactly the
# strategies that hold cash — a market-timing rule's whole apparent edge can be
# the interest it was never charged for not earning.
# `^IRX` is the 13-week Treasury bill yield, stored as an annualised decimal.
RISK_FREE_FILE = "riskfree_3m.csv"
RISK_FREE_SYMBOL = "^IRX"
RISK_FREE_LABEL = "3-month T-bill"


# ═══════════════════════════════════════════════════════════════════════════
# Prices
# ═══════════════════════════════════════════════════════════════════════════

def load_prices(source: str | Path, *, name: str | None = None,
                report_lag_days: int = 60, market: bool = True) -> Dataset:
    """Load a price dataset from a file or a directory of files.

    Recognised:
      * `.csv` / `.csv.gz`  — tidy or wide, sniffed
      * `.parquet`          — same
      * `.pkl` / `.pickle`  — a DataFrame, or a dict containing one
      * a directory         — one CSV per symbol, named `<SYMBOL>.csv`

    `market=True` attaches the S&P 500 benchmark and the risk-free rate if
    their files are present. Attached here rather than at each of the three
    places that build a run — the CLI, `lab.api` and the web app — so no seam
    can forget one and report an alpha against the wrong thing.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"no such data source: {path}")

    name = name or path.stem

    if path.is_dir():
        dataset = _from_symbol_directory(path, name=name,
                                         report_lag_days=report_lag_days)
    else:
        suffix = "".join(path.suffixes).lower()
        if suffix.endswith(".parquet"):
            frame = pd.read_parquet(path)
        elif suffix.endswith(".pkl") or suffix.endswith(".pickle"):
            frame = _unwrap_pickle(pd.read_pickle(path))
        else:
            frame = pd.read_csv(path)
        dataset = frame_to_dataset(frame, name=name,
                                   report_lag_days=report_lag_days)

    return attach_risk_free(attach_market(dataset)) if market else dataset


def frame_to_dataset(frame: pd.DataFrame, *, name: str = "dataset",
                     report_lag_days: int = 60) -> Dataset:
    """Turn a tidy or wide DataFrame into a Dataset, sniffing which it is."""
    if _looks_tidy(frame):
        return _from_tidy(frame, name=name, report_lag_days=report_lag_days)
    return _from_wide(frame, name=name, report_lag_days=report_lag_days)


def _unwrap_pickle(obj) -> pd.DataFrame:
    """Accept a bare DataFrame or a dict that contains one.

    The existing `data/prices.pkl` is a dict of
    `{prices, requested, missing, start, end}` — a cache envelope, not a
    frame. Rather than make the caller know that, look inside.
    """
    if isinstance(obj, pd.DataFrame):
        return obj
    if isinstance(obj, Mapping):
        for key in ("prices", "close", "data", "frame"):
            if key in obj and isinstance(obj[key], pd.DataFrame):
                return obj[key]
        frames = [v for v in obj.values() if isinstance(v, pd.DataFrame)]
        if len(frames) == 1:
            return frames[0]
    raise TypeError("pickle does not contain a recognisable price DataFrame")


def _looks_tidy(frame: pd.DataFrame) -> bool:
    cols = {str(c).lower() for c in frame.columns}
    return bool(cols & {k.lower() for k in _SYMBOL_KEYS}) and CLOSE in cols


def _find(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lower = {str(c).lower(): c for c in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _from_tidy(frame: pd.DataFrame, *, name: str,
               report_lag_days: int) -> Dataset:
    time_col = _find(frame, _TIME_KEYS)
    sym_col = _find(frame, _SYMBOL_KEYS)
    if time_col is None or sym_col is None:
        raise ValueError(
            "tidy price data needs a timestamp column (one of "
            f"{_TIME_KEYS}) and a symbol column (one of {_SYMBOL_KEYS}); "
            f"got {list(frame.columns)}")

    frame = frame.copy()
    frame[time_col] = pd.to_datetime(frame[time_col], utc=False, errors="coerce")
    frame = frame.dropna(subset=[time_col])

    fields: dict[str, pd.DataFrame] = {}
    for field in OHLCV:
        col = _find(frame, (field,))
        if col is None:
            continue
        pivot = frame.pivot_table(index=time_col, columns=sym_col,
                                  values=col, aggfunc="last")
        pivot.columns = [str(c) for c in pivot.columns]
        fields[field] = pivot.sort_index()

    if CLOSE not in fields:
        raise ValueError("tidy price data needs a 'close' column")
    return Dataset(fields, name=name, report_lag_days=report_lag_days)


def _from_wide(frame: pd.DataFrame, *, name: str,
               report_lag_days: int) -> Dataset:
    """A wide frame is timestamps × symbols, holding closes."""
    frame = frame.copy()
    time_col = _find(frame, _TIME_KEYS)
    if time_col is not None:
        frame = frame.set_index(time_col)
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[frame.index.notna()]
    frame.columns = [str(c) for c in frame.columns]

    if isinstance(frame.columns, pd.MultiIndex):
        # yfinance's group_by='ticker' layout: (symbol, field)
        return _from_multiindex(frame, name=name, report_lag_days=report_lag_days)

    numeric = frame.apply(pd.to_numeric, errors="coerce")
    numeric = numeric.dropna(axis=1, how="all")
    if numeric.empty:
        raise ValueError(f"{name}: no numeric price columns found")
    return Dataset({CLOSE: numeric.sort_index()}, name=name,
                   report_lag_days=report_lag_days)


def _from_multiindex(frame: pd.DataFrame, *, name: str,
                     report_lag_days: int) -> Dataset:
    level_names = [str(v).lower() for v in frame.columns.get_level_values(-1).unique()]
    field_level = -1 if any(f in level_names for f in OHLCV) else 0
    sym_level = 0 if field_level == -1 else -1

    fields: dict[str, pd.DataFrame] = {}
    for field in OHLCV:
        matches = [c for c in frame.columns
                   if str(c[field_level]).lower().replace(" ", "_") in
                   (field, f"adj_{field}")]
        if not matches:
            continue
        sub = frame[matches]
        sub.columns = [str(c[sym_level]) for c in matches]
        fields[field] = sub.sort_index()

    if CLOSE not in fields:
        raise ValueError(f"{name}: MultiIndex frame has no close column")
    return Dataset(fields, name=name, report_lag_days=report_lag_days)


def _from_symbol_directory(path: Path, *, name: str,
                           report_lag_days: int) -> Dataset:
    files = sorted(p for p in path.iterdir()
                   if p.suffix.lower() in {".csv", ".parquet"})
    if not files:
        raise FileNotFoundError(f"{path} contains no .csv or .parquet files")

    frames: list[pd.DataFrame] = []
    for file in files:
        sub = (pd.read_parquet(file) if file.suffix == ".parquet"
               else pd.read_csv(file))
        sub = sub.copy()
        if _find(sub, _SYMBOL_KEYS) is None:
            sub["symbol"] = file.stem.upper()
        frames.append(sub)
    return _from_tidy(pd.concat(frames, ignore_index=True), name=name,
                      report_lag_days=report_lag_days)


# ═══════════════════════════════════════════════════════════════════════════
# Fundamentals
# ═══════════════════════════════════════════════════════════════════════════

def load_fundamentals(source: str | Path, *, report_lag_days: int = 60
                      ) -> dict[str, list[FundamentalRecord]]:
    """Load `{ticker: {period_end: {metric: value}}}` JSON.

    This is the shape the SimFin pipeline emits, and the keys are fiscal
    quarter-ends. Each record's `known_from` is set to `period_end +
    report_lag_days`, which is what makes it safe to look up chronologically.
    Sixty days is deliberately conservative: US filers have 40 days (large
    accelerated) to 45 days (everyone else) after quarter-end for a 10-Q, so
    60 clears the deadline rather than assuming everyone files on it.
    """
    path = Path(source)
    with open(path) as handle:
        raw = json.load(handle)

    lag = pd.Timedelta(days=report_lag_days)
    out: dict[str, list[FundamentalRecord]] = {}
    for ticker, periods in raw.items():
        records: list[FundamentalRecord] = []
        for period_end, values in periods.items():
            try:
                end = pd.Timestamp(period_end)
            except (ValueError, TypeError):
                continue
            clean = {k: float(v) for k, v in values.items()
                     if isinstance(v, (int, float)) and np.isfinite(v)}
            if not clean:
                continue
            records.append(FundamentalRecord(end, end + lag, clean))
        if records:
            records.sort(key=lambda r: r.known_from)
            out[str(ticker)] = records
    return out


def attach_fundamentals(dataset: Dataset, source: str | Path, *,
                        report_lag_days: int | None = None) -> Dataset:
    """Return a copy of `dataset` carrying the fundamentals in `source`."""
    lag = report_lag_days if report_lag_days is not None else dataset.report_lag_days
    fundamentals = load_fundamentals(source, report_lag_days=lag)
    return Dataset({f: dataset._fields[f] for f in dataset.fields},
                   symbols=dataset.symbols, fundamentals=fundamentals,
                   name=dataset.name, report_lag_days=lag,
                   benchmark=dataset.benchmark,
                   benchmark_label=dataset.benchmark_label,
                   risk_free=dataset.risk_free)


# ═══════════════════════════════════════════════════════════════════════════
# The market benchmark
# ═══════════════════════════════════════════════════════════════════════════

def load_market_series(source: str | Path | None = None, *,
                       symbol: str = MARKET_SYMBOL) -> pd.Series | None:
    """The benchmark close series, or None if its file is not there.

    Missing rather than fatal on purpose: a clone with an empty `data/` should
    still run backtests. What it must not do is report an alpha as though it
    had a market to compare against — `benchmark_equity` falls back to the
    run's own universe and relabels itself, and the hub warns.
    """
    path = Path(source) if source is not None else DATA_DIR / MARKET_FILE
    if not path.exists():
        return None

    dataset = load_prices(path, name=path.stem, market=False)
    column = symbol if symbol in dataset.symbols else dataset.symbols[0]
    series = dataset._fields[CLOSE][column].dropna()
    return series if len(series) else None


def attach_market(dataset: Dataset, source: str | Path | None = None, *,
                  label: str = MARKET_LABEL) -> Dataset:
    """Return `dataset` with the market benchmark on its clock.

    A no-op when the benchmark file is absent, and when `dataset` already
    carries one — so re-loading a dataset does not stack benchmarks.
    """
    if dataset.benchmark is not None:
        return dataset
    series = load_market_series(source)
    if series is None:
        return dataset
    return Dataset({f: dataset._fields[f] for f in dataset.fields},
                   symbols=dataset.symbols, fundamentals=dataset.fundamentals,
                   name=dataset.name, report_lag_days=dataset.report_lag_days,
                   benchmark=series, benchmark_label=label,
                   risk_free=dataset.risk_free)


def load_risk_free_series(source: str | Path | None = None) -> pd.Series | None:
    """Annualised risk-free rate as a decimal, or None if the file is absent.

    Two columns, `timestamp` and `rate`, the rate already divided by 100 — the
    source quotes percent and a factor of 100 loose in a metrics pipeline is a
    hundredfold error nobody notices until a Sharpe looks strange.
    """
    path = Path(source) if source is not None else DATA_DIR / RISK_FREE_FILE
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    time_col = _find(frame, _TIME_KEYS) or frame.columns[0]
    rate_col = _find(frame, ("rate", "yield", CLOSE)) or frame.columns[-1]
    series = pd.Series(
        pd.to_numeric(frame[rate_col], errors="coerce").to_numpy(dtype=float),
        index=pd.to_datetime(frame[time_col], errors="coerce")).dropna()
    series = series[series.index.notna()].sort_index()
    return series if len(series) else None


def attach_risk_free(dataset: Dataset, source: str | Path | None = None
                     ) -> Dataset:
    """Return `dataset` carrying the risk-free rate on its clock."""
    if dataset.risk_free is not None:
        return dataset
    series = load_risk_free_series(source)
    if series is None:
        return dataset
    return Dataset({f: dataset._fields[f] for f in dataset.fields},
                   symbols=dataset.symbols, fundamentals=dataset.fundamentals,
                   name=dataset.name, report_lag_days=dataset.report_lag_days,
                   benchmark=dataset.benchmark,
                   benchmark_label=dataset.benchmark_label,
                   risk_free=series)


# ═══════════════════════════════════════════════════════════════════════════
# Network fetch (cached — never called from inside a run)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_yfinance(symbols: Sequence[str], start: str, end: str, *,
                   interval: str = "1d", cache: bool = True,
                   name: str | None = None) -> Dataset:
    """Download OHLCV and cache it as tidy CSV under `data/cache/`.

    Deliberately separate from everything a backtest touches. Fetch once,
    commit the cache path into the run config, and every later run of that
    config is replayable — including on a machine with no network.
    """
    symbols = [s.upper() for s in symbols]
    key = hashlib.sha1(
        f"{','.join(sorted(symbols))}|{start}|{end}|{interval}".encode()
    ).hexdigest()[:12]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"yf_{interval}_{key}.csv"

    if cache and cached.exists():
        return load_prices(cached, name=name or f"yfinance {interval}")

    import yfinance as yf  # imported lazily — the core never needs it

    raw = yf.download(symbols, start=start, end=end, interval=interval,
                      auto_adjust=True, progress=False, group_by="ticker")
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance returned nothing for {symbols}")

    rows: list[pd.DataFrame] = []
    for symbol in symbols:
        try:
            sub = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        sub = sub.rename(columns=str.lower).reset_index()
        time_col = _find(sub, _TIME_KEYS)
        sub = sub.rename(columns={time_col: "timestamp"})
        sub["symbol"] = symbol
        keep = ["timestamp", "symbol"] + [f for f in OHLCV if f in sub.columns]
        rows.append(sub[keep])

    if not rows:
        raise RuntimeError(f"yfinance returned no usable columns for {symbols}")

    tidy = pd.concat(rows, ignore_index=True).dropna(subset=[CLOSE])
    if cache:
        tidy.to_csv(cached, index=False)
    return _from_tidy(tidy, name=name or f"yfinance {interval}",
                      report_lag_days=60)


# ═══════════════════════════════════════════════════════════════════════════
# Catalog — what the GUI offers in its dataset dropdown
# ═══════════════════════════════════════════════════════════════════════════

def catalog(data_dir: Path | None = None) -> list[dict]:
    """Every loadable price source under `data/`, newest first."""
    root = Path(data_dir or DATA_DIR)
    if not root.exists():
        return []
    entries: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or "cache" in path.parts:
            continue
        if path.suffix.lower() not in {".csv", ".parquet", ".pkl", ".pickle"}:
            continue
        if "fundamental" in path.stem.lower() or "valuation" in path.stem.lower():
            continue
        # Not a price file: a rate series in the prices dropdown is a backtest
        # waiting to trade a percentage as though it were a share price.
        if path.name == RISK_FREE_FILE:
            continue
        entries.append({
            "id": str(path.relative_to(root)).replace("\\", "/"),
            "label": path.stem.replace("_", " "),
            "path": str(path),
            "size_mb": round(path.stat().st_size / 1e6, 1),
        })
    return entries


def fundamentals_catalog(data_dir: Path | None = None) -> list[dict]:
    root = Path(data_dir or DATA_DIR)
    if not root.exists():
        return []
    return [{
        "id": str(p.relative_to(root)).replace("\\", "/"),
        "label": p.stem.replace("_", " "),
        "path": str(p),
        "size_mb": round(p.stat().st_size / 1e6, 1),
    } for p in sorted(root.rglob("*.json"))
        if "fundamental" in p.stem.lower() or "valuation" in p.stem.lower()]

