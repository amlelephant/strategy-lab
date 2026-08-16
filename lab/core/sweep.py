"""
lab/core/sweep.py
=================
Parameter grids, and the honest way to read one.

The original `meanReversionBacktestClass.py` swept 108 combinations of SMA
window, band multiplier, take-profit and stop-loss, then reported the single
best final portfolio value. That is the natural thing to do and it is the
oldest trap in quantitative research: with 108 tries on one price series, the
best result is mostly a measurement of how many tries you had.

This module keeps the sweep — it is genuinely useful for seeing whether a
strategy has a broad plateau of decent parameters or one lucky spike — and
adds the three things that make the output readable:

  * **`best_is_probably_luck`** — the expected maximum Sharpe from `n`
    independent draws of pure noise. If the best result does not clear it,
    the sweep found nothing.
  * **`deflated_sharpe`** — the best Sharpe with a multiple-testing haircut
    applied (Bailey & López de Prado, 2014, simplified).
  * **`plateau`** — the fraction of the grid within 20% of the best. A robust
    parameter set has neighbours that also work; an overfit one is alone.

A sweep result that reports only the winner is not a result.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from ..data.dataset import Dataset
from .contract import Strategy
from .hub import Hub, RunConfig
from .registry import get

#: Euler–Mascheroni constant, used in the expected-maximum approximation.
_GAMMA = 0.5772156649


@dataclass
class SweepResult:
    strategy_key: str
    rows: pd.DataFrame                 # one row per parameter combination
    best: dict[str, Any]
    trials: int
    expected_max_noise_sharpe: float
    deflated_sharpe: float
    plateau: float
    warnings: list[str] = field(default_factory=list)

    @property
    def best_is_probably_luck(self) -> bool:
        return self.best.get("sharpe", 0.0) <= self.expected_max_noise_sharpe

    def verdict(self) -> str:
        if self.trials < 2:
            return "Single run — nothing to over-fit to."
        if self.best_is_probably_luck:
            return (f"The best Sharpe ({self.best['sharpe']:.2f}) does not clear "
                    f"what {self.trials} random tries would produce on noise "
                    f"({self.expected_max_noise_sharpe:.2f}). Treat as nothing found.")
        if self.plateau < 0.15:
            return (f"Best Sharpe {self.best['sharpe']:.2f}, but only "
                    f"{self.plateau:.0%} of the grid comes close to it — a spike, "
                    f"not a plateau. Likely over-fit to this sample.")
        return (f"Best Sharpe {self.best['sharpe']:.2f} "
                f"(deflated {self.deflated_sharpe:.2f}); {self.plateau:.0%} of the "
                f"grid is within 20% of it, which is a real plateau.")

    def to_dict(self, limit: int = 300) -> dict[str, Any]:
        frame = self.rows.head(limit).replace({np.nan: None})
        return {
            "strategy": self.strategy_key,
            "trials": self.trials,
            "best": self.best,
            "expected_max_noise_sharpe": self.expected_max_noise_sharpe,
            "deflated_sharpe": self.deflated_sharpe,
            "plateau": self.plateau,
            "probably_luck": self.best_is_probably_luck,
            "verdict": self.verdict(),
            "rows": frame.to_dict("records"),
            "columns": list(frame.columns),
            "warnings": self.warnings,
        }


def grid(strategy_key: str, overrides: dict[str, Sequence[Any]] | None = None
         ) -> list[dict[str, Any]]:
    """Every parameter combination for a strategy.

    Uses each `Param.grid` unless overridden. Parameters with no declared grid
    stay at their default, which keeps a sweep from silently exploding to
    thousands of runs because someone added a knob.
    """
    cls = get(strategy_key)
    overrides = overrides or {}
    names, value_lists = [], []
    for param in cls.params:
        values = overrides.get(param.name, param.sweep_values())
        values = [param.coerce(v) for v in values]
        names.append(param.name)
        value_lists.append(list(dict.fromkeys(values)))   # de-dupe, keep order
    return [dict(zip(names, combo)) for combo in itertools.product(*value_lists)]


def run_sweep(dataset: Dataset, strategy_key: str, *,
              overrides: dict[str, Sequence[Any]] | None = None,
              config: RunConfig | None = None,
              max_trials: int = 400,
              progress: Callable[[float, str], None] | None = None
              ) -> SweepResult:
    """Run every combination and rank by Sharpe."""
    combos = grid(strategy_key, overrides)
    warnings: list[str] = []
    if len(combos) > max_trials:
        warnings.append(
            f"grid has {len(combos)} combinations; truncated to {max_trials}. "
            f"Narrow the parameter grids rather than raising the cap — a "
            f"bigger sweep makes the best result less meaningful, not more.")
        combos = combos[:max_trials]

    cls = get(strategy_key)
    rows: list[dict[str, Any]] = []

    for n, params in enumerate(combos):
        run_config = config or RunConfig()
        try:
            result = Hub(dataset, cls(**params), run_config).run()
        except Exception as exc:                              # noqa: BLE001
            warnings.append(f"{params}: {exc!r}")
            continue
        sleeve = result.sleeves[0]
        perf = sleeve.performance
        rows.append({
            **params,
            "sharpe": perf.sharpe, "sharpe_t": perf.sharpe_t,
            "total_return": perf.total_return, "cagr": perf.cagr,
            "max_drawdown": perf.max_drawdown, "trades": perf.round_trips,
            "hit_rate": perf.hit_rate,
            "final_equity": perf.ending_equity,
        })
        if progress is not None:
            progress((n + 1) / len(combos), f"{n + 1}/{len(combos)}")

    if not rows:
        raise RuntimeError(f"sweep of {strategy_key} produced no completed runs")

    frame = pd.DataFrame(rows).sort_values("sharpe", ascending=False,
                                           ignore_index=True)
    best = frame.iloc[0].to_dict()
    trials = len(frame)

    sharpes = frame["sharpe"].to_numpy(dtype=float)
    sharpes = sharpes[np.isfinite(sharpes)]
    expected_max = expected_max_of_noise(trials, float(np.std(sharpes, ddof=1))
                                         if len(sharpes) > 1 else 0.0)
    deflated = max(0.0, float(best["sharpe"]) - expected_max)

    threshold = 0.8 * float(best["sharpe"]) if best["sharpe"] > 0 else float("inf")
    plateau = float((frame["sharpe"] >= threshold).mean()) if trials else 0.0

    return SweepResult(strategy_key, frame, best, trials, expected_max,
                       deflated, plateau, warnings)


def expected_max_of_noise(trials: int, dispersion: float) -> float:
    """Expected best Sharpe from `trials` draws of a zero-mean distribution.

    The standard approximation for the maximum of n independent normals:

        E[max] ≈ σ · [ (1-γ)·Φ⁻¹(1 - 1/n) + γ·Φ⁻¹(1 - 1/(n·e)) ]

    `dispersion` is the observed spread of Sharpes across the grid, which
    stands in for σ. This is the bar the best result has to clear before it
    counts as a finding rather than the luckiest of n coin flips.
    """
    if trials < 2 or dispersion <= 0:
        return 0.0
    from scipy.stats import norm            # local: scipy is optional at import
    a = norm.ppf(1.0 - 1.0 / trials)
    b = norm.ppf(1.0 - 1.0 / (trials * math.e))
    return float(dispersion * ((1.0 - _GAMMA) * a + _GAMMA * b))
