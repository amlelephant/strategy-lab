"""
strategy-lab
============
A testing platform for systematic trading strategies.

The whole surface, in two lines:

    from lab import backtest, sweep

    backtest("stat_arb", symbols="KO,PEP")
    sweep("mean_reversion", symbols="KO,PEP", sma_window=[10, 20, 30])

That is `lab.api`, and it is the only place a parameter is chosen — the GUI
has no control for one, by design. Underneath it, the framework proper:

    from lab import Hub, RunConfig, load_prices, build

    dataset = load_prices("data/prices.pkl").for_universe(["GS", "MS"])
    result  = Hub(dataset, build("stat_arb", {"entry_z": 2.0})).run()
    print(result.table())

`lab.core` holds the framework, `lab.data` the loaders, `lab.strategies` the
algorithms. Importing this package registers every strategy.

See AGENTS.md for the contract an automated contributor needs, and
docs/architecture.md for why it is shaped this way.
"""

from .core import (HOLD, SCENARIOS, CostModel, FillTiming, Hub, LatencyModel,
                   MarketContext, Order, Param, ParamKind, Performance,
                   Portfolio, Position, RunConfig, RunResult, Side, Strategy,
                   Universe, all_strategies, build, describe_all, get,
                   register, run_sweep, summarise)
from .data import (Dataset, attach_fundamentals, catalog, fetch_yfinance,
                   load_fundamentals, load_prices)
from .api import backtest, sweep

# Importing the package must register the strategies, or `build("stat_arb")`
# fails for anyone who has not separately imported lab.strategies.
from . import strategies as strategies  # noqa: E402,F401

__version__ = "0.1.0"

__all__ = [
    "HOLD", "SCENARIOS", "CostModel", "FillTiming", "Hub", "LatencyModel",
    "MarketContext",
    "Order", "Param", "ParamKind", "Performance", "Portfolio", "Position",
    "RunConfig", "RunResult", "Side", "Strategy", "Universe",
    "all_strategies", "build", "describe_all", "get", "register", "run_sweep",
    "summarise", "Dataset", "attach_fundamentals", "catalog", "fetch_yfinance",
    "load_fundamentals", "load_prices", "strategies",
    "backtest", "sweep",
]
