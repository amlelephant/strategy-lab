"""
lab/api.py
==========
The code API: run a strategy from the file that defines it.

The GUI deliberately cannot tune a parameter. That is not an omission — a
`Param` value is a fact about a strategy's code, so it belongs in the strategy's
own file, edited in an editor, next to the rule it changes. What that leaves is
the need to *try* a value quickly, and this module is that: three functions,
importable from anywhere, designed to sit at the bottom of a strategy file.

    # lab/strategies/my_strategy.py
    if __name__ == "__main__":
        from ..api import backtest, sweep

        backtest(MyStrategy, symbols="KO,PEP")
        sweep(MyStrategy, symbols="KO,PEP", threshold=[1.5, 2.0, 2.5])

Run it with `python -m lab.strategies.my_strategy` — as a module, so the
relative imports the strategy already uses keep working.

Parameters are passed as keyword arguments: `backtest(MyStrategy, window=30)`
overrides one knob for one run without touching the file, and
`sweep(MyStrategy, window=[10, 20, 30])` searches a grid of them. Everything
else — the data, the universe, the frictions — is a named argument with the
same default the GUI shows, so a run here and a run there are the same run.

Prices are always real. There is no generated-data option anywhere in `lab/`:
a market is more complex than anything we could simulate, so a backtest
against a simulated series is a measurement of the simulation. The test suite
has its own generator (`tests/synthetic_prices.py`) because contract tests
need determinism, and it cannot be reached from here.

Nothing in this module is imported by the web layer. It exists to be called
from a script, a notebook or a strategy file, which is the only place a
parameter is allowed to be chosen.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence, Type

import pandas as pd

from .core.contract import Strategy
from .core.costs import SCENARIOS, CostModel, FillTiming
from .core.hub import Hub, RunConfig, RunResult
from .core.registry import build, get
from .core.sweep import SweepResult, run_sweep
from .data.dataset import Dataset
from .data.loaders import DATA_DIR, attach_fundamentals, load_prices

__all__ = ["dataset", "backtest", "sweep", "print_result", "print_sweep"]

#: The default price file, under `data/`. There is no generated-data option
#: and no fallback: a backtest against a simulated series measures the
#: simulation, and the whole point of this platform is to measure a strategy.
#: `python run.py fetch SYMBOLS --start … --end …` is how you get more.
DEFAULT_PRICES = "prices.pkl"


def _key_of(strategy: Strategy | Type[Strategy] | str) -> str:
    """Accept a class, an instance or a key string — all three read naturally
    at a call site, and which one you have depends on where you are calling
    from."""
    if isinstance(strategy, str):
        get(strategy)                       # raises with the known keys listed
        return strategy
    key = getattr(strategy, "key", "")
    if not key:
        raise TypeError(f"{strategy!r} is not a strategy class, instance or key")
    return key


def dataset(data: str | Path = DEFAULT_PRICES, *,
            symbols: str | Sequence[str] | None = None,
            fundamentals: str | Path | None = None,
            all_with_fundamentals: bool = False,
            start: str | None = None, end: str | None = None) -> Dataset:
    """Load and narrow a dataset the same way the CLI and the GUI do.

    `data` is a file under `data/` or an explicit path — real prices, always.
    `symbols` accepts `"KO,PEP"` or `["KO", "PEP"]`; pair strategies consume
    the list two at a time.
    """
    path = Path(data)
    if not path.exists():
        path = DATA_DIR / str(data)
    if not path.exists():
        raise FileNotFoundError(
            f"no price file at {data!r}. Put one in {DATA_DIR}, or download "
            f"one: python run.py fetch AAPL MSFT --start 2021-01-01 "
            f"--end 2025-01-01")
    out = load_prices(path)

    if fundamentals:
        fpath = Path(fundamentals)
        if not fpath.exists():
            fpath = DATA_DIR / str(fundamentals)
        out = attach_fundamentals(out, fpath)

    if all_with_fundamentals:
        names = [s for s in out.symbols if s in out.fundamentals]
        if not names:
            raise ValueError(f"{out.name} has no fundamentals attached")
        out = out.for_universe(names)
    elif symbols:
        if isinstance(symbols, str):
            symbols = symbols.replace("\n", ",").split(",")
        names = [str(s).strip().upper() for s in symbols if str(s).strip()]
        out = out.for_universe(names)

    if start or end:
        out = out.between(start, end)
    return out


def _config(scenario: str, timing: str, cash: float, seed: int) -> RunConfig:
    base = SCENARIOS[scenario]
    return RunConfig(
        starting_cash=cash,
        costs=CostModel(commission_bps=base.commission_bps,
                        slippage_bps=base.slippage_bps, latency=base.latency,
                        timing=FillTiming(timing), seed=seed))


def backtest(*strategies: Strategy | Type[Strategy] | str,
             data: str | Path = DEFAULT_PRICES,
             symbols: str | Sequence[str] | None = None,
             fundamentals: str | Path | None = None,
             all_with_fundamentals: bool = False,
             start: str | None = None, end: str | None = None,
             scenario: str = "realistic", timing: str = "next_open",
             cash: float = 100_000.0, seed: int = 7,
             show: bool = True, **params: Any) -> RunResult:
    """Run one or more strategies over one dataset and return the result.

        backtest(MyStrategy, symbols="KO,PEP")
        backtest(MyStrategy, symbols="KO,PEP")
        backtest(MyStrategy, symbols="KO,PEP", window=30)        # one knob, one run

    Keyword arguments that are not named above are strategy parameters, and
    are applied to every strategy in the call — which is what you want when
    a second named strategy has no such parameter to collide
    with. To give two strategies different parameters, build them yourself:
    `backtest(MyStrategy(window=10), MyStrategy(window=40), ...)`.
    """
    if not strategies:
        raise TypeError("backtest() needs at least one strategy")

    built = []
    for item in strategies:
        if isinstance(item, Strategy):
            built.append(item)              # already configured; leave it alone
        else:
            built.append(build(_key_of(item), dict(params)))

    ds = dataset(data, symbols=symbols, fundamentals=fundamentals,
                 all_with_fundamentals=all_with_fundamentals,
                 start=start, end=end)
    result = Hub(ds, built, _config(scenario, timing, cash, seed)).run(
        _progress if show else None)
    if show:
        print_result(result, dataset_line=str(ds))
    return result


def sweep(strategy: Strategy | Type[Strategy] | str, *,
          data: str | Path = DEFAULT_PRICES,
          symbols: str | Sequence[str] | None = None,
          fundamentals: str | Path | None = None,
          all_with_fundamentals: bool = False,
          start: str | None = None, end: str | None = None,
          scenario: str = "realistic", timing: str = "next_open",
          cash: float = 100_000.0, seed: int = 7,
          top: int = 15, show: bool = True, **grids: Sequence[Any]
          ) -> SweepResult:
    """Grid-search one strategy's parameters and return the ranked result.

        sweep(MyStrategy, symbols="KO,PEP", threshold=[1.5, 2.0, 2.5])

    Each keyword that is not named above is a parameter and a list of values
    to try. Parameters you do not name stay at the value declared in the
    strategy's file — or at its `Param.grid`, if it has one and you sweep
    nothing explicitly.

    The verdict matters more than the winner. With enough tries the best
    Sharpe in any grid is mostly a measurement of how many tries you had, so
    `SweepResult.verdict()` compares it against what pure noise would have
    produced. Read that line before believing the top row.
    """
    key = _key_of(strategy)
    overrides = {name: list(values) for name, values in grids.items()}
    ds = dataset(data, symbols=symbols, fundamentals=fundamentals,
                 all_with_fundamentals=all_with_fundamentals,
                 start=start, end=end)
    result = run_sweep(ds, key, overrides=overrides or None,
                       config=_config(scenario, timing, cash, seed),
                       progress=_progress if show else None)
    if show:
        print_sweep(result, top=top, dataset_line=str(ds))
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Console output — shared with run.py so both entry points read identically
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_utf8() -> None:
    """Windows consoles default to cp1252 and choke on the em-dashes and ±
    signs in a result table. Called before printing rather than at import,
    because a library that reconfigures `sys.stdout` just for being imported
    is a library that has overstepped."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _progress(fraction: float, stage: str) -> None:
    bar = "#" * int(fraction * 30)
    print(f"\r  [{bar:<30}] {fraction:5.0%}  {stage}", end="", flush=True)


def print_result(result: RunResult, dataset_line: str = "") -> None:
    """The per-sleeve table, then the verdict for each one.

    The verdict is not decoration. A Sharpe with |t| under 2 is not a weaker
    version of a result, it is the absence of one, and printing the number
    without saying so is how a backtest gets over-read by the person who ran
    it.
    """
    _ensure_utf8()
    print("\n")
    if dataset_line:
        print(f"  {dataset_line}\n")
    for warning in result.warnings:
        print(f"  note: {warning}")

    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 30)
    table = result.table().copy()
    for column in ("return", "cagr", "vs_bench", "alpha", "max_dd"):
        table[column] = table[column].map(
            lambda v: "—" if pd.isna(v) else f"{v:+.2%}")
    table["hit"] = table["hit"].map(lambda v: "—" if pd.isna(v) else f"{v:.0%}")
    for column in ("sharpe", "t", "alpha_t", "beta"):
        table[column] = table[column].map(
            lambda v: "—" if pd.isna(v) else f"{v:.2f}")
    table["costs"] = table["costs"].map(lambda v: f"${v:,.0f}")
    print(table.to_string(index=False))

    print()
    for sleeve in result.sleeves:
        perf = sleeve.performance
        print(f"  {sleeve.title}: {perf.headline()}")
        print(f"  {'':<{len(sleeve.title)}}  {perf.verdict()}")
        print(f"  {'':<{len(sleeve.title)}}  "
              f"{sleeve.orders_filled}/{sleeve.orders_requested} orders filled, "
              f"{len(sleeve.rejections)} rejections")
    print(f"\n  {result.elapsed_seconds:.1f}s\n")


def print_sweep(result: SweepResult, top: int = 15,
                dataset_line: str = "") -> None:
    _ensure_utf8()
    print("\n")
    if dataset_line:
        print(f"  {dataset_line}\n")
    pd.set_option("display.width", 220)
    print(result.rows.head(top).to_string(index=False))
    print(f"\n  {result.verdict()}\n")
