# strategy-lab

**A testing platform for systematic trading strategies — one hub, one contract,
one honest measurement.**

Every algorithm I had written was its own script. Each downloaded its own
prices, kept its own cash, invented its own fill assumptions, and printed a
number at the end. None of those numbers were comparable, and none of the runs
could be reproduced — including by me, a week later, on the same code.

This is the platform that fixed that. A strategy is now a class with one
method. It is handed what was knowable at a timestamp and returns what it wants
to do about it. Everything else — the clock, the fills, the costs, the cash,
the measurement — belongs to the framework, identically for every strategy.

```
hub  ──── MarketContext (what is true at time t) ───▶  Strategy.on_bar()
hub  ◀─── list[Order]   (what to do about it)     ────
```

That asymmetry is the whole design. A strategy cannot download data, cannot
name its own fill price, cannot spend money it does not have, and cannot see a
value from after the bar it is standing on — not by convention, but because the
object it is handed cannot express those things.

![Result page — a strategy against its benchmark, headlined by annualised
alpha and its t-statistic](docs/images/result-page.jpg)

---

## What is in it

| | |
|---|---|
| **6 strategies** | Pairs trading (two generations), Bollinger mean reversion, fundamental scoring (two generations), and a buy-and-hold control |
| **A backtesting hub** | Single pass, per-strategy capital sleeves, atomic multi-leg fills, next-open execution, a latency-aware cost model |
| **Cointegration screening** | Full two-step Engle-Granger with the correct residual-based critical values, available as an entry gate |
| **A Flask GUI** | A card per strategy; open one to see its file path and run it. Results are linkable and survive a restart |
| **Alpha, not just Sharpe** | Every result is regressed on its benchmark: Jensen's alpha, beta and a Newey-West t-statistic. Whether a strategy *beat holding the same names* is the headline number |
| **A code API** | `backtest()` and `sweep()`, called from the bottom of a strategy's own file. The only place a parameter value is ever chosen |
| **A write-up per strategy** | The claim, the numbers, what they don't establish — rendered on the strategy's own page by a hand-rolled Markdown renderer, not a dependency |
| **A parameter sweeper** | Grid search that reports how much of its own best result is luck. Prints to the terminal; deliberately not a screen |
| **`/showcase`** | Every strategy's latest backtest in one place — Sharpe, CAGR, drawdown, volatility — built for showing the work, not for tuning it |
| **87 tests** | Including contract tests that run against *every* registered strategy |
| **`AGENTS.md`** | The full contract in one file, so a contributor — human or model — never has to read the framework to add a strategy |

```bash
pip install -r requirements.txt
python run.py serve                                    # GUI on :5000
python -m lab.strategies.stat_arb_ev                   # run one strategy's own file
python run.py backtest stat_arb_ev -p require_cointegration=True \
    --symbols "KO,PEP,XOM,CVX,MCD,YUM,CL,PG"
python -m pytest tests/ -q
```

![A strategy's page — the path to its class file, then the data and frictions
to run it against](docs/images/console.jpg)

---

## Parameters live in the file, and only in the file

The GUI has no control for a `Param`, does not display one, and does not serve
one. That is the load-bearing decision in this interface.

A parameter's value is a fact about a strategy's *code*. The moment a browser
form can change it, the file stops being the answer to "what does this
strategy do?", and two runs of nominally the same strategy stop being
comparable — which is the exact failure this whole platform was built to fix.
So a strategy's page offers the things that genuinely belong to a *run* —
which data, which universe, which frictions — shows you the path to the class
file, and gets out of the way.

Choosing a value is a code operation, and it happens where the value lives:

```python
# lab/strategies/mean_reversion.py
if __name__ == "__main__":
    from ..api import backtest, sweep

    backtest(MeanReversion, symbols="KO,PEP,XOM,CVX")
    sweep(MeanReversion, symbols="KO,PEP,XOM,CVX", sma_window=[10, 20, 30, 40])
```

```bash
python -m lab.strategies.mean_reversion
```

Every strategy file ends with a block like that. `backtest()` takes the
strategy, the data, the universe and the frictions and returns the same
`RunResult` the GUI renders; `sweep()` takes lists instead of values and
prints the grid with its over-fitting verdict. The sweep stays in the
terminal on purpose — its output *is* a table of parameter values, and the
best Sharpe out of *n* tries is mostly a measurement of *n*, which is a number
to read next to the code that produced it rather than publish on a page.

This is enforced rather than merely intended: the web layer strips `params`
from every strategy description it serves, and a test walks every form control
on every page and fails if one is bound to a parameter name.

---

## The measurement is the product

Three decisions separate this from the scripts it replaced. Each one makes
results look *worse* than the code it came from, which is the point.

### 1. Lookahead is structurally impossible, not merely avoided

`ctx.history()` slices the dataset at the current bar. There is no accessor on
`MarketContext` that returns a future value. Fundamentals are indexed by the
date a filing became knowable — quarter-end plus a 60-day reporting lag — so a
company's June quarter is invisible until late August.

That last one is not a technicality. The predecessor to
`bw_cross_sectional` pooled every metric across every ticker *and all sixteen
quarters*, then ranked each company-quarter against that pool. A company scored
in 2021 was being ranked against 2024 data. There is no version of the ported
strategy that can reproduce that bug, because `ctx.cross_section()` cannot
return a record that did not exist yet. **The mistake stopped being possible
rather than being caught**, which is the argument for a platform over a script.

### 2. The question is alpha, not Sharpe

A significant Sharpe says a strategy made money at a rate unlikely to be
chance. It does not say the strategy was worth running, because the
alternative was never holding cash — it was **holding the same names and doing
nothing**, which over 2021-2025 did rather well. A long-only book in a rising
market inherits the market's Sharpe and can look skilful having added nothing.

So every result is regressed on its benchmark, the equal-weight buy-and-hold
of its own universe:

    r_strategy − rf = α + β·(r_benchmark − rf) + ε

and the page leads with **α**, annualised, next to **β** and a t-statistic
computed with Newey-West standard errors — because daily strategy returns are
autocorrelated, and plain OLS errors treat every day as independent evidence
in exactly the direction that flatters the strategy. Sharpe is still shown,
still with its own t and its n, one tile down.

That reframing changes what the repository claims. `stat_arb_ev` does not
merely lose money: it returns **−6.7% a year against its benchmark at t =
−3.3**, which is a conclusive negative finding rather than an inconclusive
one. And the best strategy here, `bw_cross_sectional`, earns **+7.1% a year of
alpha at β = 0.86** — economically large, and still only t = 1.4 over four and
a half years. Reported as what it is: suggestive, not established.

Most results in this repository do not clear |t| = 2. They are reported
anyway, labelled. A platform that only surfaces its convincing runs is not
measuring anything.

### 3. Trading costs money, and the model says when that matters

Commission, spread, and a latency drift term ported from the original pairs
engine. The latency term is worth cents at swing-trade horizons and the model
says so — that is what makes it informative when it says otherwise. Orders fill
at the *next* bar's open by default; filling at a close you have just observed
is a free option, and it is how the older scripts were written.

---

## A worked example

`stat_arb_ev`, the showcased pairs strategy, on the eight consumer and energy
names it is measured against everywhere in this document
(`KO, PEP, XOM, CVX, MCD, YUM, CL, PG`), 2021–2025, paying the same 6bp round
trip — with and without its cointegration screen:

| | Return | Alpha | t(α) | β | Sharpe | Max DD | Trades |
|---|---|---|---|---|---|---|---|
| Statistical Arbitrage | −15.05% | −6.69% | −3.3 | −0.00 | −1.65 | −17.72% | 194 |
| ...cointegration-screened | −1.95% | −3.48% | −5.3 | −0.01 | −2.46 | −4.40% | 12 |
| S&P 500 | +77.60% | — | — | 1.00 | 0.64 | −24.50% | — |

    python run.py backtest stat_arb_ev --symbols KO,PEP,XOM,CVX,MCD,YUM,CL,PG \
      --start 2021-01-01 --end 2025-06-30 [-p require_cointegration=True]

The screen removes most of the loss and most of the drawdown, cutting the
trade count by 94% — but the Sharpe gets *worse*, because twelve trades is too
few to tell a real edge from noise, and that is a real cost of screening, not
a contradiction. Both configurations still lose to the benchmark. The EV
filter is on in both rows; without it the trade count and the loss are both
larger, which is what the next section measures directly.

### What the pairs were actually trading

The unscreened, unfiltered baseline (`lab/strategies/stat_arb.py` — no EV
gates, no cointegration screen, kept on disk but not on the showcase) never
tested whether its pairs were related at all; it computed a hedge ratio and
traded the spread. Two stocks that drift upward together produce a confident
hedge ratio whether or not anything connects them. Turning on *only* the
cointegration screen, isolated from the EV gates, on the same eight names:

| | Return | Sharpe | Max DD | Trades |
|---|---|---|---|---|
| Unscreened | −16.76% | −0.84 | −16.77% | 258 |
| **Cointegration-screened** | **−4.68%** | **−0.68** | **−6.76%** | **28** |

The screen refused 584 signals, cut trades by 89%, and removed about three
quarters of the loss. It did not make the strategy work — both versions lose to
simply holding the eight stocks. What it establishes is that **most of what the
strategy had been trading was never a relationship**, and finding that out cost
one statistical test it had never been running. `stat_arb_ev` runs the same
screen (`require_cointegration=True`) on top of the EV gates, which is why the
worked example above shows a smaller but still real drop in both loss and
trade count.

Getting that test right mattered too. Run over 528 pairs, testing regression
residuals with the standard Dickey-Fuller table rather than MacKinnon's
residual-based one called 17.0% of pairs cointegrated where the correct table
calls 8.0% — **a 2.14× over-rejection from nothing but the reference table**.
Adding back the error-correction step the original implementation had dropped
removes seven more: pairs that are genuinely cointegrated and never actually
revert.

### The fundamental strategy does better

On 1,747 companies across 2021–2025:

| | Return | CAGR | Sharpe | t | Max DD |
|---|---|---|---|---|---|
| BW Valuation | +77.72% | 13.75% | 0.72 | 1.5 | −23.53% |
| Buy and Hold | +39.19% | 7.69% | 0.46 | 1.0 | −30.02% |

It beats the control by a clear margin but does not clear |t| = 2. **Fifteen
quarterly rebalances against a single macro regime cannot separate a real
factor from a lucky one**, and the platform says so on the page rather than in
a footnote. See `research/` for the rest of the caveats, including two flavours
of survivorship bias in the universe.

---

## The strategies

| Key | What it claims | Where it came from |
|---|---|---|
| `stat_arb_ev` | A related pair's spread reverts to its mean; screened for cointegration, entered only when expected capture clears costs, latency and hold-period risk | `paper-broker/algos/statArbClass.py`, `stat-arb-v2/core/ev_filter.py` and the cointegration-explorer research app |
| `mean_reversion` | A name two standard deviations off its own short-run mean comes back | `paper-broker/algos/meanReversionClass.py` — rule preserved, stop-loss sign corrected |
| `bw_cross_sectional` | Quality, valuation and leverage ranked against the current peer group | `fundamentals-v2/score.py` — all three scoring methods preserved |

Three of the four predate the platform and were ported rather than rewritten.
The decision rules are intact; what changed is that they no longer fetch their
own data or keep their own cash. Where a port changed behaviour, it is
documented in the strategy's own docstring and in
[`research/ported-changes.md`](research/ported-changes.md) — one stop-loss
comparison had the wrong sign, and saying so is more useful than quietly
fixing it.

### Statistical Arbitrage screens its pairs before it prices them

`stat_arb_ev` used to be two strategies: one that traded a spread's z-score
the moment it looked stretched, and one that additionally refused to enter
unless the expected capture cleared its own costs. The unscreened original —
`lab/strategies/stat_arb.py`, still on disk, no longer on the showcase — is
where `require_cointegration` first lived; it is now a parameter here too, so
the same strategy can be run with the relatedness screen, the economics
screen, both, or neither, rather than requiring two registered strategies to
compare them. See [the worked example above](#a-worked-example) for what each
screen is worth on its own.

### BW Valuation ranks within its own cross-section

Quality, valuation and leverage scored against the current peer group rather
than a fixed range — percentile, z-score or min-max, selectable, computed
fresh from each quarter's own companies so no ranking can leak information
across time. It also keeps the original's inverted-direction bug behind
`legacy_directions=True` — three metrics where lower is better were scored as
though higher were. Net Debt/EBITDA is the only input to the risk term, so the
model preferred leveraged, expensive companies and called it prudence. Run
with the flag set and compare the curves; the claim that this cost real return
is checkable in thirty seconds rather than asserted.

---

## Adding a strategy

One file, one decorator, one import line. Nothing else in the repository
changes — the CLI, the GUI, the sweeper and the contract tests all read from
the registry. The "new strategy" form on the home screen does those two steps
for you, then hands you the path and gets out of the way.

```python
@register
class MyStrategy(Strategy):
    key = "my_strategy"
    title = "My Strategy"
    universe = Universe.SINGLE
    summary = "One sentence: what edge is this claiming?"
    params = (Param("threshold", 2.0, ParamKind.FLOAT, low=0.0, high=10.0,
                    help="What this knob does.", grid=(1.5, 2.0, 2.5)),)

    def on_bar(self, ctx):
        return [Order.open(sym, Side.LONG, 100, reason="why")
                for sym in ctx.symbols
                if ctx.position(sym) is None and ctx.price(sym) < self.threshold]


if __name__ == "__main__":
    from ..api import backtest, sweep

    backtest(MyStrategy, symbols="KO,PEP")
```

Parameters are declared, never hardcoded — which is what lets the sweeper
enumerate a grid, `lab.api` override one by name, and a saved result record
exactly how it was produced, all without per-strategy code.

**[`AGENTS.md`](AGENTS.md) is the complete contract**, written so that a
contributor — human or language model — can add a strategy without opening
`lab/core/` at all. That was a design goal, not a convenience: a framework you
have to read 2,000 lines to extend is a framework that gets worked around.

---

## Data

The canonical format is tidy OHLCV — `timestamp, symbol, open, high, low,
close, volume`. Wide frames and per-symbol CSV directories are recognised too;
`load_prices()` sniffs which it is looking at. Fundamentals are
`{ticker: {quarter_end: {metric: value}}}`, converted to knowledge dates on
load.

Drop a file in `data/` and it appears in the GUI. **Nothing fetches inside a
run** — `python run.py fetch` caches to `data/cache/`, and a run that
re-downloads its own inputs is a run that cannot be replayed.

Bulk data is gitignored. The included analysis used 1,846 tickers of daily
closes and 1,976 tickers of quarterly SimFin fundamentals (26,291
company-quarters, 2021 Q1 – 2024 Q4).

---

## Layout

```
lab/
  api.py         backtest() and sweep() — the code API, called from a strategy file
  core/          contract, hub, portfolio, costs, metrics, sweep, registry
  data/          canonical dataset and the loaders that feed it
  analysis/      cointegration and the cheap screens that precede it
  strategies/    one file per algorithm
  web/           Flask app, templates, hand-rolled SVG charts and Markdown
tests/           contract tests (every strategy) + framework tests
docs/            architecture and the design system
research/        what the results support, what they do not, and reproduce.py
  strategies/    one write-up per strategy, rendered on its GUI page
run.py           CLI: serve · list · backtest · sweep · fetch
AGENTS.md        the contract, in full, in one file
```

Every number in `research/` regenerates with `python research/reproduce.py`.
A write-up whose figures cannot be checked on demand is one nobody can audit,
including its author six months later.

Credentials come from the environment; see `.env.example`. Nothing here reads a
hardcoded key, and no live capital has ever been deployed against any of it.

---

## Where it came from

This supersedes `Financial-Trading-Platform-algos`, `quant-trading-platform`
and `spurious-signals`. The paper broker that started the whole project — built
because every off-the-shelf platform hides its fill logic — survives as
`lab/core/portfolio.py`, short-sale cash accounting and share-weighted cost
basis intact.

The interface is deliberately the same design language as
[minFit](https://github.com/amlelephant/minFit): dark-first, near-monochrome,
one accent that always means *primary action*, hairline borders instead of
shadows, one hero number per screen, tabular figures on every logged number.
See [`docs/design-system.md`](docs/design-system.md).
