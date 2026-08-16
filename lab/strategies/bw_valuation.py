"""
lab/strategies/bw_valuation.py
==============================
BW v1 — quality, valuation and risk against hand-set absolute anchors.

Ported from `fundamentals-v1/quality/weighting.py` and
`fundamentals-v1/valuation/metrics.py`. The anchor table below is that code's
`highs_and_lows` and `range_settings` dictionaries, reassembled into one
table over the eight metrics the point-in-time dataset actually carries:

    metric              anchor range      direction   source
    ─────────────────────────────────────────────────────────────────────
    Operating Margin    0.05 – 0.40       higher      quality/weighting.py
    FCF Margin          0.05 – 0.30       higher      quality/weighting.py
    ROIC                0.05 – 0.40       higher      quality/weighting.py
    Revenue Growth      0.00 – 0.15       higher      quality/weighting.py
    Net Debt/EBITDA     0.0  – 3.0        lower       quality/weighting.py
    P/E                 5    – 50         lower       valuation/metrics.py
    EV/EBITDA           5    – 40         lower       valuation/metrics.py
    FCF Yield           0.01 – 0.12       higher      valuation/metrics.py

Nothing here was chosen by fitting. The ranges are a judgement about what
"good" looks like for a business, written down before any of it was tested,
and the whole model is that judgement applied consistently.

**Why the older, simpler scorer is the one to keep.** Absolute anchors need no
peer group, so a company's score depends on its own numbers and nothing else.
The percentile scorer that replaced it (`bw_cross_sectional`) needs a
distribution, and the moment you need a distribution you have to decide *which*
one — and the natural, wrong answer is "all of the data I have", which includes
the future. This version could not have that bug. It also encodes each metric's
direction once, in a table, where a wrong entry is visible; the successor
passed direction as an argument and defaulted three of them the wrong way.

Its cost is real and should be stated: the anchors are calibrated to US large
caps around the time they were written, and they do not travel. A 0.05–0.40
operating-margin range makes every grocer look terrible and every software
company look excellent, and neither is a judgement about management.
"""

from __future__ import annotations

import numpy as np

from ..core.contract import MarketContext, Param, ParamKind
from ..core.registry import register
from ._rebalance import FundamentalRebalance, anchored, clamped

#: (low, high, higher_is_better, style) — `style` picks which of the two
#: original normalisers applies, because the quality and valuation modules
#: used different ones and the difference is not cosmetic.
ANCHORS: dict[str, tuple[float, float, bool, str]] = {
    "Operating Margin": (0.05, 0.40, True, "quality"),
    "FCF Margin":       (0.05, 0.30, True, "quality"),
    "ROIC":             (0.05, 0.40, True, "quality"),
    "Revenue Growth":   (0.00, 0.15, True, "quality"),
    "Net Debt/EBITDA":  (0.00, 3.00, False, "quality"),
    "P/E":              (5.0,  50.0, False, "valuation"),
    "EV/EBITDA":        (5.0,  40.0, False, "valuation"),
    "FCF Yield":        (0.01, 0.12, True, "valuation"),
}

QUALITY = ("Operating Margin", "FCF Margin", "ROIC", "Revenue Growth")
VALUE = ("FCF Yield", "EV/EBITDA", "P/E")
RISK = ("Net Debt/EBITDA",)


def score_company(record: dict[str, float], *, quality_weight: float,
                  valuation_weight: float, risk_weight: float
                  ) -> tuple[float | None, dict[str, float]]:
    """Anchor-score one company. Returns (overall, per-category)."""
    scores: dict[str, float] = {}
    for metric, (low, high, higher, style) in ANCHORS.items():
        value = record.get(metric)
        result = (anchored(value, low, high, higher) if style == "quality"
                  else clamped(value, low, high, lower_is_better=not higher))
        if result is not None:
            scores[metric] = result

    def mean_of(names) -> float | None:
        present = [scores[n] for n in names if n in scores]
        return float(np.mean(present)) if present else None

    categories = {"quality": mean_of(QUALITY), "valuation": mean_of(VALUE),
                  "risk": mean_of(RISK)}
    weights = {"quality": quality_weight, "valuation": valuation_weight,
               "risk": risk_weight}

    # Renormalise over the categories that actually scored, so a company
    # missing its whole risk term is not silently penalised by the weight of
    # the category it could not fill. The original dropped `None`s from a flat
    # list before averaging, which has the same effect.
    live = {k: v for k, v in categories.items() if v is not None}
    if not live:
        return None, {}
    total_weight = sum(weights[k] for k in live) or 1.0
    overall = sum(live[k] * weights[k] for k in live) / total_weight
    return overall, {k: round(v, 4) for k, v in live.items()}


@register
class BWValuation(FundamentalRebalance):

    key = "bw_valuation"
    title = "BW Valuation (absolute anchors)"
    summary = ("Score companies on quality, valuation and leverage against "
               "fixed ranges; hold the best of them.")
    provenance = ("fundamentals-v1 — quality/weighting.py and "
                  "valuation/metrics.py. Anchor table and both normalisers "
                  "preserved exactly; the pipeline around them is new.")
    notes = (
        "The comparison worth running is this against `bw_cross_sectional` on "
        "the same universe and dates. They agree on what to measure and "
        "disagree entirely on what to measure it against — absolute standards "
        "versus the current peer group — and that disagreement is a real "
        "question about factor investing, not an implementation detail."
    )

    params = FundamentalRebalance.base_params + (
        Param("quality_weight", 1.0, ParamKind.FLOAT, low=0.0, high=5.0,
              step=0.1, help="Weight on the four quality metrics.",
              grid=(0.5, 1.0, 2.0)),
        Param("valuation_weight", 1.0, ParamKind.FLOAT, low=0.0, high=5.0,
              step=0.1, help="Weight on the three valuation metrics.",
              grid=(0.5, 1.0, 2.0)),
        Param("risk_weight", 1.0, ParamKind.FLOAT, low=0.0, high=5.0, step=0.1,
              help="Weight on leverage. The v1 scorer weighted every category "
                   "equally, which is what 1/1/1 reproduces."),
    )

    def score_universe(self, ctx: MarketContext,
                       cross_section: dict[str, dict[str, float]]
                       ) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol, record in cross_section.items():
            overall, _ = score_company(
                record, quality_weight=self.quality_weight,
                valuation_weight=self.valuation_weight,
                risk_weight=self.risk_weight)
            if overall is not None:
                out[symbol] = overall
        return out
