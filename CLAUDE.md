# CLAUDE.md — permanent guidance for strategy-lab

> Read this before doing anything in this repository. When a request conflicts
> with it, stop and ask.
>
> **If the task is adding or changing a strategy, read [`AGENTS.md`](AGENTS.md)
> and stop there.** It is the complete contract. Do not read `lab/core/` for
> that job — it is ~2,000 lines and none of it changes what you write.

## What this is

A backtesting platform for systematic trading strategies. One hub walks a
dataset; strategies are classes that receive a point-in-time view and return
trade actions. The framework owns the clock, the fills, the cash and the
measurement so that two strategies produce comparable numbers.

Everything here is research and paper trading. **No live capital has ever been
deployed and none should be.** Do not add order routing to a real broker.

## The one law

**A result must be reproducible and must state its own uncertainty.**

Everything below follows from that. If a change makes a number prettier and
less trustworthy, it is the wrong change.

## Non-negotiable

- **No lookahead.** `MarketContext` must never gain an accessor that can return
  a value from after the current bar. Fundamentals are indexed by knowledge
  date, never by period end.
- **No I/O inside a run.** No network, no disk, no `datetime.now()`, no
  unseeded randomness in `on_bar` or anything it calls. Fetching is a separate
  command that caches to disk; the network access happens before the run,
  never inside `on_bar`.
- **Costs are never optional by default.** `frictionless` exists as a named
  comparison scenario, not as a default.
- **Significance travels with the result.** Anywhere a Sharpe is displayed, its
  t-statistic and observation count are displayed too. Do not add a summary
  view that drops them.
- **The verdict is the market comparison, not alpha and not Sharpe.** The
  headline on every surface is `active_return` — annualised
  (strategy − benchmark), whether it ended with more money than the S&P 500.
  A strategy that merely rode a rising market has a high Sharpe and no edge;
  one that sat in cash has a high alpha and no money.
- **Never claim alpha while losing to the index.** Alpha over a small beta is
  a large number for anything that made money without market exposure — a
  market-neutral book returning 3% while the index returned 16% scores +3%
  alpha at beta 0.00. `has_alpha` therefore requires *outperformance as well
  as* a surviving t-test, and no badge, verdict or heading says "beats the
  market" about a strategy that did not. Alpha is reported as a secondary
  figure, always beside the beta that gives it meaning.
- **The risk-free rate is a series, never zero.** Sharpe and alpha are both
  defined on excess returns. Regressing raw returns yields `rf·(1 − β)` of
  alpha nobody earned, concentrated in cash-holding strategies.
  `data/riskfree_3m.csv` (13-week T-bill, committed) is threaded through
  `Dataset.risk_free`; the hub warns when it is missing.
- **The benchmark is the market, not the universe.** Alpha and beta are
  measured against the S&P 500 (`data/market_spy.csv`, SPY, committed), never
  against an equal-weight hold of the names the run happened to select —
  measuring against those credits a strategy for its universe. The equal-weight
  fallback exists only for a clone with no benchmark file, and the hub emits a
  warning when it is in use. The benchmark series is carried on `Dataset`
  outside the price frames on purpose: a tradeable benchmark is one a strategy
  can end up holding.
- **No generated prices.** `lab/` cannot produce a price series; the only
  generator lives in `tests/synthetic_prices.py` for contract tests. A
  backtest against a simulated market measures the simulation.
- **No parameter in the browser.** The GUI must never render a control for a
  `Param`, display its value, or serve it in JSON. A parameter is a fact about
  a strategy's file; choosing one is `lab.api`, called from that file. The web
  layer strips `params` from every description it serves and a test enforces
  it — if a feature seems to need a parameter on a page, the feature is wrong.
- **No generated prices reach a result.** The only generator is
  `tests/synthetic_prices.py`, for contract tests; `lab/` cannot produce a
  price series and the GUI offers no such dataset.
- **Rejections are output, not debug noise.** A strategy that declines a trade
  logs why, and the GUI shows it.

## When porting old code

Three of the four strategies predate the platform. The rule for those:

1. **Preserve the decision rule.** The original author's intent is the artefact
   worth keeping — including choices that look unsophisticated. Hand-set
   anchors instead of fitted thresholds is a judgement, not a bug.
2. **Data access and cash handling are the framework's job**, so those get
   rewritten without ceremony.
3. **If a port changes behaviour, document it** — in the strategy's docstring
   *and* in `research/ported-changes.md`. A silently corrected bug is a
   discrepancy someone will later find and distrust.
4. **Keep known bugs runnable behind a flag** where the comparison is
   informative (`bw_cross_sectional.legacy_directions`). A claim that a bug
   cost six points of CAGR should be checkable, not asserted.
5. **Record provenance** on the class. A reader must be able to tell ported
   work from work written against the hub.

## Architectural rules

1. **Adding a strategy touches two files**: the new one, and one import line in
   `lab/strategies/__init__.py`. If a strategy needs a change to the hub, the
   templates, or the JS, that is a framework gap — raise it rather than
   special-casing. Every strategy file ends with an `if __name__ ==
   "__main__"` block calling `lab.api`, so `python -m lab.strategies.<name>`
   runs it.
2. **No magic numbers in strategies.** Anything a user might tune is a `Param`.
   Constants that are part of a definition (the 252 in an annualisation, an
   anchor table transcribed from a source) stay in code, with a comment saying
   where they came from.
3. **All design values come from tokens** at the top of `lab/web/static/lab.css`.
   No hardcoded hex, spacing, radius or font size in a template.
4. **Charts stay hand-rolled SVG.** No charting library. See
   `docs/design-system.md`.
5. **Tests must pass before a task is done**: `python -m pytest tests/ -q`. The
   contract tests run against every registered strategy automatically.
6. **Reuse before creating.** Read the neighbouring strategy before writing a
   new one; most of what you need already exists on `ctx`.

## Dependencies

`pandas`, `numpy`, `scipy`, `statsmodels`, `flask`, `yfinance`, `pytest`.
**Do not add a dependency without asking.** In particular: no charting library,
no backtesting library, no ORM, no front-end build step. The GUI is server-
rendered HTML with one hand-written CSS file and one hand-written JS file, and
it works offline.

## Voice

The prose in this repository — READMEs, docstrings, GUI copy — is calm and
specific. It states what a thing does, what it costs, and what it does not
establish. No exclamation marks, no emoji, no marketing adjectives, no claiming
a result is promising when its t-statistic is 1.2. Comments explain *why*, not
*what*; if a comment restates the line below it, delete it.

## Definition of done

Professional repository structure · a complete contract document that removes
the need to read the framework · working strategies that reproduce their
originals' intent · a GUI with a consistent visual language · honest results
including the unflattering ones · tests that enforce the contract rather than
the outputs. Optimise for craftsmanship over quantity. When no meaningful
refinement remains, stop — do not invent endless strategies.
