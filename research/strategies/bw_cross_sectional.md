## The claim

The same eight metrics as `bw_valuation`, scored against the current peer
group instead of a fixed anchor table — three interchangeable scoring
methods, all preserved from the original because the choice between them is
the substance of the model, not an implementation detail:

- **percentile** — fraction of peers this company beats. Rank-based, so one
  absurd P/E can't move anybody else's score.
- **zscore** — median-centred, standard-deviation-scaled. Keeps the size of a
  gap, not just its order, and inherits every outlier along with it.
- **minmax** — linear between the best and worst peer in the cross-section.
  Two extreme companies decide everybody's score. Kept specifically so that
  claim is demonstrable rather than folklore — set `method="minmax"` and
  watch one outlier move the whole ranking.

Weights are the original's: 45% quality, 45% valuation, 10% risk.

## Two defects, and two different fates

**The lookahead bug — fixed by the framework, not by this file.** The
original built each metric's distribution from every ticker in every quarter
at once, then ranked each company-quarter against that pool: a company
scored in 2021 was partly ranked against 2024 data. There is no version of
this strategy that can reproduce that bug, because `ctx.cross_section()`
structurally cannot return a record that wasn't knowable yet at the current
bar. The mistake didn't get caught — it stopped being expressible, which is
the actual argument for building a platform instead of another script.

**The inverted-direction bug — kept, behind a switch.** The percentile
helper took a `higher_is_better` flag and was called with the default
(`True`) for all eight metrics. Lower is better for Net Debt/EBITDA,
EV/EBITDA and P/E — and Net Debt/EBITDA is the *only* input to the risk
term, so the risk component ran completely backwards: the model preferred
leveraged, expensive companies and scored that as prudence.

`legacy_directions=True` reproduces it exactly, on purpose. A corrected model
that looks good is precisely the kind of result that needs its counterfactual
runnable rather than described — select this strategy twice in the console
with the flag set differently, and the two equity curves land on the same
page.

## What one wrongly-defaulted keyword argument cost

1,747 companies, quarterly rebalance at the filing date, 2021–2025:

| | Return | CAGR | Sharpe | t | Max DD |
|---|---|---|---|---|---|
| **Corrected directions** | **+77.72%** | **13.75%** | **0.72** | 1.5 | −23.53% |
| Original, inverted (`legacy_directions=True`) | +26.50% | 5.41% | 0.36 | 0.8 | −32.87% |
| BW Valuation (absolute anchors, never had the bug) | +52.41% | 9.90% | 0.58 | 1.2 | −25.21% |
| Buy and hold | +39.19% | 7.69% | 0.46 | 1.0 | −30.02% |

Eight points of CAGR and nine points of drawdown, from one keyword argument
defaulting the wrong way in a function called eight times. The buggy version
barely clears the buy-and-hold control; the corrected version beats it by six
points a year — which is exactly the kind of number that should invite a
second look rather than a victory lap. Its t-statistic is 1.5.

## What's new, not ported

**Winsorisation.** A `winsorise` parameter (default 2% per tail) clips each
metric's cross-section before scoring. The original had no outlier handling
at all — defensible for `percentile`, since rank statistics are already
robust to it, and not defensible for `zscore` or `minmax`, where a single
company with a P/E of 4,000 moves everyone else's score. Set it to 0 to
reproduce the original exactly.

## What this doesn't establish

Same caveats as `bw_valuation`, and they apply identically: fifteen
independent quarterly decisions dressed up by a daily t-statistic, two cuts
of survivorship bias in a universe of companies that still resolve today, and
one macro regime. See [`research/findings.md`](../findings.md) for the full
derivation of both bugs and [`research/results.md`](../results.md) for the
complete caveat list.
