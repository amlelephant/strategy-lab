#!/usr/bin/env python
"""
research/reproduce.py — regenerate every number in this directory.

    python research/reproduce.py                # all of it
    python research/reproduce.py critical-values
    python research/reproduce.py screen
    python research/reproduce.py directions

Each claim in `findings.md` and `results.md` is produced by one of these
functions. A write-up whose numbers cannot be regenerated on demand is a
write-up nobody can check, including its author six months later.
"""

from __future__ import annotations

import itertools
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from lab import Hub, attach_fundamentals, build, load_prices  # noqa: E402
from lab.analysis import engle_granger, prefilter             # noqa: E402
# stat_arb and bw_valuation are retired from the showcase (see
# lab/strategies/__init__.py) and so are not in the registry `build()` reads
# from. They are unchanged on disk and importing the class directly, instead
# of by key, still works — that is what the two functions below do.
from lab.strategies.bw_valuation import BWValuation             # noqa: E402
from lab.strategies.stat_arb import StatArb                     # noqa: E402

PRICES = ROOT / "data" / "prices.pkl"
FUNDAMENTALS = ROOT / "data" / "fundamentals_simfin.json"

#: 34 large caps across sectors. Chosen for liquidity and coverage, before any
#: test was run — picking the universe after seeing results is how a study
#: talks itself into a conclusion.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "KO", "PEP", "XOM",
    "CVX", "MCD", "YUM", "PG", "CL", "WMT", "TGT", "HD", "LOW", "UNH",
    "JNJ", "PFE", "MRK", "CAT", "DE", "UPS", "FDX", "NKE", "SBUX", "ORCL",
    "CRM", "ADBE", "INTC", "AMD", "TXN",
]

PAIRS = ["KO", "PEP", "XOM", "CVX", "MCD", "YUM", "CL", "PG"]


def _need(path: Path) -> None:
    if not path.exists():
        raise SystemExit(
            f"{path.name} is not in data/ — bulk data is gitignored. "
            f"See the README's Data section.")


def _table(result) -> str:
    frame = result.table()
    for column in ("return", "cagr", "max_dd"):
        frame[column] = frame[column].map(lambda v: f"{v:+.2%}")
    frame["hit"] = frame["hit"].map(
        lambda v: "—" if pd.isna(v) else f"{v:.0%}")
    for column in ("sharpe", "t"):
        frame[column] = frame[column].map(lambda v: f"{v:.2f}")
    frame["costs"] = frame["costs"].map(lambda v: f"${v:,.0f}")
    return frame.to_string(index=False)


# ═══════════════════════════════════════════════════════════════════════════

def critical_values() -> None:
    """Finding 1 — the residual-based table, on 528 real pairs."""
    _need(PRICES)
    print("\n── Engle-Granger critical values ────────────────────────────\n")

    dataset = load_prices(PRICES)
    have = [s for s in UNIVERSE if s in dataset.symbols]
    subset = dataset.for_universe(have).between("2021-01-04", "2024-12-31")
    closes = {s: subset.history(s, len(subset.index) - 1) for s in subset.symbols}

    pairs = list(itertools.combinations(subset.symbols, 2))
    mackinnon = wrong = tradeable = screened = 0

    for a, b in pairs:
        y, x = closes[a], closes[b]
        n = min(len(y), len(x))
        y, x = y[-n:], x[-n:]
        correct = engle_granger(y, x)
        naive = engle_granger(y, x, use_residual_critical_values=False)
        mackinnon += correct.is_cointegrated()
        wrong += naive.is_cointegrated()
        tradeable += correct.is_tradeable(max_half_life=60)
        screened += prefilter(y, x)[0]

    total = len(pairs)
    print(f"  {len(have)} tickers, {total} pairs, "
          f"{len(subset.index)} trading days\n")
    print(f"  adfuller() on residuals (wrong table)   "
          f"{wrong:4d}  {wrong / total:6.1%}")
    print(f"  coint() with MacKinnon values           "
          f"{mackinnon:4d}  {mackinnon / total:6.1%}")
    print(f"  ...and alpha < 0 with half-life <= 60   "
          f"{tradeable:4d}  {tradeable / total:6.1%}")
    print(f"  cheap prefilter passes                  "
          f"{screened:4d}  {screened / total:6.1%}")
    print(f"\n  over-rejection factor: {wrong / max(mackinnon, 1):.2f}x")


def screen() -> None:
    """Finding 2 — what the cointegration screen is worth to the strategy."""
    _need(PRICES)
    print("\n── Cointegration screen on stat_arb ─────────────────────────\n")

    dataset = load_prices(PRICES).for_universe(PAIRS)
    unscreened = StatArb()
    screened = StatArb(require_cointegration=True)
    screened.title = "Statistical Arbitrage (cointegration-screened)"

    result = Hub(dataset, [unscreened, screened]).run()
    print(_table(result))

    rejected = len(result.sleeves[1].messages)
    print(f"\n  the screen refused {rejected} signals")


def directions() -> None:
    """Finding 3 — what one wrongly-defaulted keyword argument cost."""
    _need(PRICES)
    _need(FUNDAMENTALS)
    print("\n── BW scorer: corrected vs original directions ──────────────\n")

    dataset = attach_fundamentals(load_prices(PRICES), FUNDAMENTALS)
    universe = [s for s in dataset.symbols if s in dataset.fundamentals]
    dataset = dataset.for_universe(universe)

    corrected = build("bw_cross_sectional")
    legacy = build("bw_cross_sectional", {"legacy_directions": True})
    legacy.title = "BW cross-sectional (original, inverted directions)"
    anchors = BWValuation()

    result = Hub(dataset, [corrected, legacy, anchors]).run()
    print(_table(result))


COMMANDS = {"critical-values": critical_values, "screen": screen,
            "directions": directions}

if __name__ == "__main__":
    requested = sys.argv[1:] or list(COMMANDS)
    for name in requested:
        if name not in COMMANDS:
            raise SystemExit(f"unknown: {name}. Try: {', '.join(COMMANDS)}")
        COMMANDS[name]()
    print()
