"""
lab/strategies/bw_cross_sectional.py
====================================
BW v2 — the same eight metrics, scored against the peer group instead of
against fixed anchors.

Ported from `fundamentals-v2/score.py`. All three of that file's scoring
methods are preserved and selectable, because the choice between them is the
substance of the model:

    percentile   fraction of peers this company beats. Rank-based, so a single
                 absurd P/E cannot move anybody else's score.
    zscore       median-centred, standard-deviation-scaled. Keeps the size of
                 a gap, not just its order — and inherits every outlier.
    minmax       linear between the best and worst peer. Two extreme companies
                 decide everybody's score. Kept so that is demonstrable.

Weights are the original's: 45% quality, 45% valuation, 10% risk.

────────────────────────────────────────────────────────────────────────────
Two defects, and what happened to them
────────────────────────────────────────────────────────────────────────────

**Pooling across time — fixed by the framework, not by this file.** The
original built each metric's distribution from every ticker in every quarter
at once, then ranked each company-quarter against that pool. A company scored
in 2021 was being ranked against 2024 numbers. There is no version of this
strategy that can reproduce that bug, because `ctx.cross_section()` cannot
return a record that was not knowable yet. That is the argument for a platform
over a script: the mistake stopped being possible rather than being caught.

**Inverted directions — kept, behind a switch.** The percentile helper took a
`higher_is_better` flag and was called with the default for all eight metrics,
but lower is better for Net Debt/EBITDA, EV/EBITDA and P/E. Net Debt/EBITDA is
the only input to the risk term, so the risk component was fully reversed: the
model preferred expensive, leveraged companies and scored that as prudence.

`legacy_directions=True` restores it. That is deliberate. The corrected model
looks good, and a corrected model that looks good is exactly the kind of result
that needs its counterfactual runnable rather than described. Run both, compare
the equity curves, and the claim "the bugs cost roughly six points of CAGR"
becomes something a reader can check in thirty seconds.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import MarketContext, Param, ParamKind
from ..core.registry import register
from ._rebalance import (FundamentalRebalance, minmax_of, percentile_of,
                         zscore_of)

QUALITY = ("Operating Margin", "FCF Margin", "ROIC", "Revenue Growth")
VALUE = ("FCF Yield", "EV/EBITDA", "P/E")
RISK = ("Net Debt/EBITDA",)
METRICS = QUALITY + VALUE + RISK

#: True where a larger number is a better company. The three `False` entries
#: are the ones the original defaulted to `True`.
HIGHER_IS_BETTER: dict[str, bool] = {
    "Operating Margin": True,
    "FCF Margin": True,
    "ROIC": True,
    "Revenue Growth": True,
    "FCF Yield": True,
    "EV/EBITDA": False,
    "P/E": False,
    "Net Debt/EBITDA": False,
}

_SCORERS = {"percentile": percentile_of, "zscore": zscore_of,
            "minmax": minmax_of}


@register
class BWCrossSectional(FundamentalRebalance):

    key = "bw_cross_sectional"
    title = "BW Valuation"
    summary = ("Rank the universe on quality, valuation and leverage within "
               "each quarter's own cross-section.")
    provenance = ("fundamentals-v2/score.py — all three scoring methods and "
                  "the 45/45/10 weighting preserved; the time-pooling is "
                  "prevented by the framework and the direction bug is kept "
                  "behind `legacy_directions`.")
    notes = (
        "Set `legacy_directions=True` to reproduce the original, inverted "
        "model, and run it beside the default. The gap between the two curves "
        "is what a single wrongly-defaulted keyword argument cost."
    )

    params = FundamentalRebalance.base_params + (
        Param("method", "percentile", ParamKind.CHOICE,
              choices=("percentile", "zscore", "minmax"),
              help="How a metric is scored against its peer group.",
              grid=("percentile", "zscore", "minmax")),
        Param("quality_weight", 0.45, ParamKind.FLOAT, low=0.0, high=1.0,
              step=0.05, help="Original weight: 0.45."),
        Param("valuation_weight", 0.45, ParamKind.FLOAT, low=0.0, high=1.0,
              step=0.05, help="Original weight: 0.45."),
        Param("risk_weight", 0.10, ParamKind.FLOAT, low=0.0, high=1.0,
              step=0.05, help="Original weight: 0.10."),
        Param("legacy_directions", False, ParamKind.BOOL,
              help="Reproduce the original bug: treat larger as better for "
                   "every metric, including Net Debt/EBITDA, EV/EBITDA and "
                   "P/E. For comparison only.",
              grid=(False, True)),
        Param("winsorise", 0.02, ParamKind.FLOAT, low=0.0, high=0.2, step=0.01,
              help="Clip each tail of every metric's cross-section before "
                   "scoring. Matters for zscore and minmax, barely touches "
                   "percentile. 0 disables."),
    )

    def score_universe(self, ctx: MarketContext,
                       cross_section: dict[str, dict[str, float]]
                       ) -> dict[str, float]:
        scorer = _SCORERS[self.method]
        symbols = list(cross_section)

        # Build one population per metric from *this date's* companies only.
        populations: dict[str, np.ndarray] = {}
        for metric in METRICS:
            values = np.array(
                [cross_section[s][metric] for s in symbols
                 if metric in cross_section[s]
                 and np.isfinite(cross_section[s][metric])], dtype=float)
            if values.size and self.winsorise > 0:
                low = np.quantile(values, self.winsorise)
                high = np.quantile(values, 1.0 - self.winsorise)
                values = np.clip(values, low, high)
            populations[metric] = values

        out: dict[str, float] = {}
        for symbol in symbols:
            record = cross_section[symbol]
            scores: dict[str, float] = {}
            for metric in METRICS:
                higher = True if self.legacy_directions else HIGHER_IS_BETTER[metric]
                value = record.get(metric)
                if value is not None and np.isfinite(value) and self.winsorise > 0:
                    population = populations[metric]
                    if population.size:
                        value = float(np.clip(value, population.min(),
                                              population.max()))
                result = scorer(value, populations[metric], higher)
                if result is not None:
                    scores[metric] = result

            def mean_of(names):
                present = [scores[n] for n in names if n in scores]
                return float(np.mean(present)) if present else None

            categories = {"quality": mean_of(QUALITY),
                          "valuation": mean_of(VALUE),
                          "risk": mean_of(RISK)}
            weights = {"quality": self.quality_weight,
                       "valuation": self.valuation_weight,
                       "risk": self.risk_weight}
            live = {k: v for k, v in categories.items() if v is not None}
            if not live:
                continue
            total = sum(weights[k] for k in live) or 1.0
            out[symbol] = sum(live[k] * weights[k] for k in live) / total

        return out


# Run this file directly:  python -m lab.strategies.bw_cross_sectional
# The only place a parameter value is chosen: the GUI has no control for one.
if __name__ == "__main__":
    from ..api import backtest, sweep                          # noqa: F401

    backtest(BWCrossSectional, data="prices.pkl",
             fundamentals="fundamentals_simfin.json",
             all_with_fundamentals=True)

    # sweep(BWCrossSectional, data="prices.pkl",
    #       fundamentals="fundamentals_simfin.json",
    #       all_with_fundamentals=True,
    #       top_fraction=[0.1, 0.2, 0.3])
