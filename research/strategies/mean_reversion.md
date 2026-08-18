## The claim

A liquid name pushed two standard deviations off its own short-run mean is
more likely to come back than to keep going. Bands are `SMA(n) ± k·σ(n)`;
flat and below the lower band goes long, flat and above the upper band goes
short (if enabled), and either position closes back at the SMA — the mean is
the target, not the opposite band. It is the simplest possible statement of
mean reversion, and mostly a test of whether transaction costs eat the edge,
which at a 20-bar window on daily bars they usually do. Run it at
`frictionless` and then at `realistic` to see that story end to end.

## The bug this port found

The original fired its stop-loss with `sell = change < stop_loss`, where
`change` is open P&L in dollars and `stop_loss` was swept as a *positive*
figure — 200, 500, 800. That comparison is true whenever P&L is below
**plus** $200: every losing position, every flat one, and every winning
position up to $200. The stop fired on almost every bar a position was open,
including on winners.

A variable named `stop_loss`, swept over the same values as a `take_profit`
that used the correct sign, is not ambiguous about intent. The port compares
against `-stop_loss`. This is the one substantive change to the decision
rule in this strategy, and it isn't kept behind a flag — unlike the
`bw_cross_sectional` direction bug, the original behaviour here isn't an
interesting alternative hypothesis, it's a typo whose only effect was to
close positions at random. See
[`research/ported-changes.md`](../ported-changes.md) for the full write-up,
including why this one bug means results from the original sweep aren't
comparable to results from this one.

## What changed to make it backtestable at all

The original called `yf.download()` inside the strategy class and
recomputed its bands from whatever window happened to be current when it
ran — which is why the same code gave different answers on different days
and couldn't be backtested in the first place. The port reads price history
from `ctx`, deterministically, at the bar it's standing on. Position sizing
also moved from `cash // price` — the entire account into one name, correct
for a script that only ever looked at one symbol — to a declared
`position_fraction` split across the universe; across more than one symbol,
`cash // price` isn't sizing, it's a race the first symbol always wins.

## What this doesn't establish

This is the control for "does a textbook mean-reversion rule survive contact
with realistic costs," not a claim that it does. The honest use of this
strategy is comparative: run it frictionless and realistic on the same
symbols and see how much of the edge, if any, is a transaction-cost mirage.
