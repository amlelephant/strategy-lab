"""
lab/strategies/_rebalance.py
============================
Shared machinery for strategies that rank a universe on fundamentals and hold
the top slice until the next set of filings.

Not a strategy itself — the leading underscore marks it as infrastructure so
the registry never picks it up. Subclasses implement `score_universe()` and
get the rest: knowing when a rebalance is due, converting scores into target
weights, and closing what fell out of the basket.

The one thing worth reading here is `_rebalance_bars()`. A fundamentals
strategy must trade on the dates its information *arrives*, not on the dates
the accounting periods ended, and the two differ by roughly two months. The
dataset already indexes records by their knowable date; this maps those dates
onto the first trading bar at or after each one. That is the whole implementation
lag, and getting it wrong is worth several points of annual return in a
direction that always flatters the backtest.
"""

from __future__ import annotations

from abc import abstractmethod

import numpy as np
import pandas as pd

from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Strategy, Universe)


class FundamentalRebalance(Strategy):
    """Rank on fundamentals, hold the top slice, rebalance when filings land."""

    universe = Universe.CROSS_SECTION

    #: Subclasses append their own parameters to these.
    base_params = (
        Param("top_fraction", 0.20, ParamKind.FLOAT, low=0.01, high=1.0,
              step=0.01,
              help="Fraction of the scored universe to hold, best first. "
                   "0.20 reproduces the original backtest's top slice.",
              grid=(0.05, 0.10, 0.20)),
        Param("max_names", 60, ParamKind.INT, low=1, high=1000, step=1,
              help="Hard cap on holdings, whatever `top_fraction` implies."),
        Param("min_coverage", 6, ParamKind.INT, low=1, high=20, step=1,
              help="Metrics a company must report before it can be scored. "
                   "Companies with thin data score high by accident."),
    )

    def on_start(self, ctx: MarketContext) -> None:
        self._due = self._rebalance_bars(ctx)
        self._rebalances = 0

    def _rebalance_bars(self, ctx: MarketContext) -> set[pd.Timestamp]:
        """Timestamps on which to rebalance: the first bar at or after each
        date on which new fundamentals became knowable."""
        dataset = ctx._ds                       # deliberate: this is framework
        index = dataset.index
        due: set[pd.Timestamp] = set()
        for known in dataset.fundamental_dates():
            position = index.searchsorted(known, side="left")
            if position < len(index):
                due.add(index[position])
        return due

    # ------------------------------------------------------------------
    @abstractmethod
    def score_universe(self, ctx: MarketContext,
                       cross_section: dict[str, dict[str, float]]
                       ) -> dict[str, float]:
        """Score every eligible company using only `cross_section`.

        `cross_section` is what was publicly knowable at `ctx.timestamp` — one
        record per company that has filed by now. Rank *within* this dict.
        Ranking against a distribution pooled over the whole backtest is the
        single most expensive mistake available here, and it is invisible in
        the output: the equity curve just looks better.
        """

    # ------------------------------------------------------------------
    def on_bar(self, ctx: MarketContext):
        if ctx.timestamp not in self._due:
            return HOLD

        cross_section = ctx.cross_section()
        if not cross_section:
            return HOLD

        eligible = {
            symbol: record for symbol, record in cross_section.items()
            if sum(1 for v in record.values() if v is not None
                   and np.isfinite(v)) >= self.min_coverage
        }
        if len(eligible) < 5:
            ctx.log(f"only {len(eligible)} companies have {self.min_coverage}+ "
                    f"metrics — no rebalance")
            return HOLD

        scores = self.score_universe(ctx, eligible)
        scores = {s: v for s, v in scores.items()
                  if v is not None and np.isfinite(v) and np.isfinite(ctx.price(s))}
        if not scores:
            return HOLD

        count = min(self.max_names,
                    max(1, int(len(scores) * self.top_fraction)))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        basket = [symbol for symbol, _ in ranked[:count]]
        weight = 1.0 / len(basket)

        self._rebalances += 1
        ctx.log(f"rebalance #{self._rebalances}: {len(scores)} scored, "
                f"holding top {len(basket)} at {weight:.1%} each "
                f"(best {ranked[0][0]} {ranked[0][1]:.3f}, "
                f"cut-off {ranked[len(basket) - 1][1]:.3f})")

        orders: list[Order] = []
        held = {position.symbol for position in ctx.positions()}
        rank = {symbol: i + 1 for i, symbol in enumerate(basket)}

        for symbol in held - set(basket):
            orders.append(Order.close(
                symbol, reason=f"dropped out of the top {self.top_fraction:.0%}"))

        for symbol in basket:
            orders.append(Order.target(
                symbol, weight,
                reason=f"rank {rank[symbol]}/{len(scores)}, "
                       f"score {scores[symbol]:.3f}",
                score=round(float(scores[symbol]), 4), rank=rank[symbol]))

        return orders


# ── normalisation primitives, preserved from the original scorers ─────────

def anchored(value: float | None, low: float, high: float,
             higher_is_better: bool = True, headroom: float = 1.2) -> float | None:
    """Score against a hand-set absolute range.

    This is `weighting.normalize` from the BW quality module, verbatim in
    behaviour including the `headroom` term: the raw ratio is clipped at 1.2
    and then divided by 1.2, so a company at the top anchor scores 0.83 rather
    than 1.0 and genuinely exceptional numbers still have somewhere to go.

    It depends on no other company and no other date, which is why the anchored
    scorer was never capable of the lookahead bug that the percentile scorer
    had. Judgement encoded up front costs you calibration and buys you a model
    that cannot leak.
    """
    if value is None or not np.isfinite(value):
        return None
    raw = (value - low) / (high - low)
    result = max(0.0, min(raw, headroom)) / headroom
    return result if higher_is_better else 1.0 - result


def clamped(value: float | None, low: float, high: float,
            lower_is_better: bool = True) -> float | None:
    """Score against an absolute range with hard clipping at both ends.

    `valuation/metrics.normalize_valuation`, verbatim in behaviour. The
    valuation module clipped to the range before scaling rather than allowing
    headroom, because a P/E of 400 and a P/E of 4,000 are the same answer.
    """
    if value is None or not np.isfinite(value):
        return None
    value = max(min(float(value), high), low)
    score = (value - low) / (high - low)
    return 1.0 - score if lower_is_better else score


def percentile_of(value: float | None, population: np.ndarray,
                  higher_is_better: bool = True) -> float | None:
    """Fraction of `population` this value beats.

    `score_calculator.get_percentile`, with one difference that matters: the
    population passed in here is one date's cross-section, because that is all
    `ctx.cross_section()` can give you. The original pooled every ticker and
    every quarter into one distribution and ranked a 2021 company against 2024
    peers.
    """
    if value is None or not np.isfinite(value) or population.size == 0:
        return None
    fraction = float((population < value).mean())
    return fraction if higher_is_better else 1.0 - fraction


def zscore_of(value: float | None, population: np.ndarray,
              higher_is_better: bool = True) -> float | None:
    """Median-centred z-score. `calculate_score_with_median`, preserved —
    including the choice of median over mean for the centre and standard
    deviation for the scale, which is a deliberately robust-ish hybrid."""
    if value is None or not np.isfinite(value) or population.size < 2:
        return None
    sd = float(np.std(population))
    if sd <= 0:
        return None
    z = (float(value) - float(np.median(population))) / sd
    return z if higher_is_better else -z


def minmax_of(value: float | None, population: np.ndarray,
              higher_is_better: bool = True) -> float | None:
    """`calculate_score_with_min_max`, preserved. Min-max over a cross-section
    is dominated by its two most extreme members, which is why the original
    author moved off it — kept so that conclusion is reproducible."""
    if value is None or not np.isfinite(value) or population.size == 0:
        return None
    low, high = float(population.min()), float(population.max())
    if high == low:
        return 0.5
    score = (float(value) - low) / (high - low)
    return score if higher_is_better else 1.0 - score
