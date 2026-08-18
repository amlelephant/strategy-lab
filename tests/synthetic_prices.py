"""
tests/synthetic_prices.py
=========================
A deterministic fake market. **Test scaffolding only — this lives in `tests/`
and not in `lab/` on purpose.**

The contract tests need a price series that every strategy can be run against
reproducibly, on a fresh clone, with no data downloaded: two identical runs
must produce identical equity curves, and every registered strategy must be
exercised even if it has never seen real prices. Generated data is the only
thing that satisfies that.

What it must never do is reach a result anybody reads. Simulated prices cannot
tell you whether a strategy works — a market is more complex than any process
we could write down here, so a backtest against this file measures the file.
The platform therefore has no way to load it: `lab/` cannot generate prices at
all, the dataset dropdown lists only what is in `data/`, and `lab.api` takes a
real file or nothing. If you find yourself wanting to import this from `lab/`,
the thing you actually want is `python run.py fetch`.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from lab.data.dataset import Dataset

CLOSE = "close"


def synthetic(symbols: Sequence[str] = ("AAA", "BBB", "CCC", "DDD"),
              bars: int = 750, seed: int = 3, *,
              cointegrated_pairs: bool = True) -> Dataset:
    """A deterministic fake market, for tests.

    Symbols are generated in pairs: the second of each pair is the first plus
    a mean-reverting spread, so a pairs strategy has something to find and a
    cointegration screen has something to pass.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2021-01-04", periods=bars)
    out: dict[str, np.ndarray] = {}

    for i, symbol in enumerate(symbols):
        if cointegrated_pairs and i % 2 == 1 and i > 0:
            base = out[symbols[i - 1]]
            spread = np.zeros(bars)
            for t in range(1, bars):          # Ornstein-Uhlenbeck
                spread[t] = 0.94 * spread[t - 1] + rng.normal(0, 0.9)
            out[symbol] = base * 0.8 + 20.0 + spread
        else:
            steps = rng.normal(0.0003, 0.014, bars)
            out[symbol] = 100.0 * np.exp(np.cumsum(steps))

    close = pd.DataFrame(out, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    return Dataset({"open": open_, CLOSE: close}, name="synthetic (test fixture)")
