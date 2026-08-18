## The claim

Score every company on quality, valuation and leverage against hand-set
absolute ranges — not against the current peer group — and hold the top
decile. Nothing here was chosen by fitting: the ranges are a judgement about
what "good" looks like for a business, written down before any of it was
tested.

| Metric | Anchor range | Direction |
|---|---|---|
| Operating Margin | 0.05 – 0.40 | higher |
| FCF Margin | 0.05 – 0.30 | higher |
| ROIC | 0.05 – 0.40 | higher |
| Revenue Growth | 0.00 – 0.15 | higher |
| Net Debt/EBITDA | 0.0 – 3.0 | lower |
| P/E | 5 – 50 | lower |
| EV/EBITDA | 5 – 40 | lower |
| FCF Yield | 0.01 – 0.12 | higher |

## Why the older, simpler scorer is the one worth keeping

`bw_cross_sectional` — written later, and more sophisticated — replaced fixed
anchors with a peer-relative percentile. That requires a distribution, and
the moment a model needs a distribution it has to decide *which one*. The
natural, wrong answer is "all the data I have," which includes the future:
the cross-sectional successor did exactly that, pooling every ticker across
all sixteen quarters and ranking each company-quarter against a pool that
included data three years ahead of it.

This version cannot have that bug. It needs no peer group, so a company's
score depends only on its own numbers. It also encodes each metric's
direction once, in a table, where a wrong entry is visible on inspection —
the successor passed direction as a keyword argument and defaulted three of
eight the wrong way, which is not visible on inspection and cost 8.3 points
of CAGR before anyone noticed.

## What it costs

The anchors are calibrated to US large caps around the time they were
written, and they don't travel. A 0.05–0.40 operating-margin range makes
every grocer look terrible and every software company look excellent, and
neither is a judgement about the management running either one. Compare its
return against the cross-sectional version's, below — the gap is roughly what
adapting to the current peer group is worth, when the adaptation isn't buggy.

## What it measured

1,747 companies, quarterly rebalance at the filing date, top 20% by score,
equal weight, 2021–2025:

| | Return | CAGR | Sharpe | t | Max DD |
|---|---|---|---|---|---|
| BW Valuation (absolute anchors) | +52.41% | 9.90% | 0.58 | 1.2 | −25.21% |
| BW Valuation (cross-sectional, corrected) | +77.72% | 13.75% | 0.72 | 1.5 | −23.53% |
| Buy and hold, same costs | +39.19% | 7.69% | 0.46 | 1.0 | −30.02% |

It beats the buy-and-hold control with less drawdown, after costs. It earns
less than the corrected peer-relative version — 9.90% CAGR against 13.75% —
which is the price of never being able to leak information across time.

## What this doesn't establish

Fifteen quarterly rebalances is fifteen independent decisions, however many
points the daily equity curve has; the t-statistic above treats 1,125
autocorrelated daily observations as independent and should be read as an
upper bound on confidence, not the confidence itself. The universe is
companies that still resolve today, which removes exactly the failures a
cheap, indebted screen tends to select — survivorship bias in the strategy's
own favor, unmeasured here. And 2021–2025 is one macro regime. See
[`research/results.md`](../results.md) for the complete caveat list, which
applies to both BW variants identically.

## Where the code came from

Ported from `fundamentals-v1/quality/weighting.py` and
`fundamentals-v1/valuation/metrics.py` — two anchor tables, `highs_and_lows`
and `range_settings`, with different normalisers for the quality and
valuation halves. The port keeps both normalisers rather than collapsing
them to one, because collapsing them would have been tidier and would have
changed every score.
