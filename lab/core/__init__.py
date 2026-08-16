"""Core framework: the contract, the hub, and everything that measures a run."""

from .contract import (HOLD, Intent, MarketContext, Order, Param, ParamKind,
                       Position, Side, Strategy, Universe)
from .costs import SCENARIOS, CostModel, FillTiming, LatencyModel
from .hub import Hub, RunConfig, RunResult, SleeveResult
from .metrics import Performance, benchmark_equity, drawdown_series, summarise
from .portfolio import Fill, InsufficientCash, Portfolio
from .registry import all_strategies, build, describe_all, get, register
from .sweep import SweepResult, grid, run_sweep

__all__ = [
    "HOLD", "Intent", "MarketContext", "Order", "Param", "ParamKind",
    "Position", "Side", "Strategy", "Universe",
    "SCENARIOS", "CostModel", "FillTiming", "LatencyModel",
    "Hub", "RunConfig", "RunResult", "SleeveResult",
    "Performance", "benchmark_equity", "drawdown_series", "summarise",
    "Fill", "InsufficientCash", "Portfolio",
    "all_strategies", "build", "describe_all", "get", "register",
    "SweepResult", "grid", "run_sweep",
]
