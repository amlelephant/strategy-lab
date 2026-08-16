# research/

What the results in this repository actually support, and what they do not.

The platform exists to produce measurements. This directory is where those
measurements are written up honestly — including, mostly, as negative results.
That is not modesty. A backtesting framework whose write-ups are all favourable
is a framework that is being used to confirm rather than to test, and the
write-ups are the only place that distinction is visible.

| | |
|---|---|
| [`findings.md`](findings.md) | Defects found by checking implementations against the sources they claimed to follow, each one quantified |
| [`ported-changes.md`](ported-changes.md) | Every place a port changed the original's behaviour, and why |
| [`results.md`](results.md) | Current headline numbers, with the reasons not to trust them |

## The standing rule

Any result reported here carries its sample size and its t-statistic. Below
about |t| = 2, it is described as *not distinguishable from luck* — not as a
weaker version of a real result. Most of what is here fails that bar.

Reporting it anyway is the point. The alternative is a directory of six
convincing findings and no way to tell which of them survived contact with a
sample-size calculation.
