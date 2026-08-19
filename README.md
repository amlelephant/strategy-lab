# strategy-lab

**A testing platform for systematic trading strategies**

Every algorithm I had written was its own script. Each downloaded its own
prices, kept its own cash, invented its own fill assumptions, and printed a
number at the end. None of those numbers were comparable, and none of the runs
could be reproduced. The issue was that each of my back test algorithms were interacting
with data in fundamentally different ways. I frequently dealt with look ahead bias
and differences in bar lengths. 

This is the platform that fixed that. A strategy is now a class with one
method. It is handed what was knowable at a timestamp and returns what it wants
to do about it. Everything is identical between strategies which helps a lot in
testing whether a strategy is worth pursuing. 

```
hub -> market conditions at time t -> strategy decision -> execution on next loaded bar
```

A strategy cannot download data, cannot
name its own fill price, cannot spend money it does not have, and cannot see a
value from after the bar it is standing on. This solves a large issue I was
personally facing while trying to develop trading algorithims.

<img width="1870" height="1430" alt="image" src="https://github.com/user-attachments/assets/6c95ff20-52c7-40ea-bda5-987c76a28365" />


---

## What is in it

| | |
|---|---|
| **A backtesting hub** | Single pass, per-strategy capital sleeves, atomic multi-leg fills, next-open execution, a latency-aware cost model |
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

This entire program is not meant to be a development server living apart of code.
It is meant to live with it and enhance it. Code is the easiest way to interface 
with your strategies and I embrace that in the server. Update your code, run it again,
and see what changed. Tweak parameters in your class file and repeat. All you have 
to write is the engine that makes decisions from a given scenario. For me, at least,
that helps simplify the task at hand.

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
terminal on purpose — its output is a table of parameter values, and the
best Sharpe out of *n* tries is mostly a measurement of *n*, which is a number
to read next to the code that produced it rather than publish on a page.

This is enforced rather than merely intended: the web layer strips `params`
from every strategy description it serves, and a test walks every form control
on every page and fails if one is bound to a parameter name.

---

## Results you can trust

Three decisions separate this from the scripts it replaced. Each one makes
results look *worse* than the code it came from, which is the point.

### 1. Lookahead is structurally impossible

`ctx.history()` slices the dataset at the current bar. There is no accessor on
`MarketContext` that returns a future value. Fundamentals are indexed by the
date a filing became knowable so a
company's June quarter is invisible until late August.

### 2. The question is alpha, not Sharpe

A significant Sharpe says a strategy made money at a rate unlikely to be
chance. A long-only book in a rising
market inherits the market's Sharpe and can look skilful having added nothing.

So every result is regressed on its benchmark, the equal-weight buy-and-hold
of its own universe:

    r_strategy − rf = α + β·(r_benchmark − rf) + ε

and the page leads with α, annualised, next to β and a t-statistic
computed with Newey-West standard errors because daily strategy returns are
autocorrelated, and plain OLS errors treat every day as independent evidence
in exactly the direction that flatters the strategy. Sharpe is still shown,
still with its own t and its n, one tile down.

### 3. Trading costs money, and the model says when that matters

Commission, spread, and a latency drift term ported from the original pairs
engine. The latency term is worth cents at swing-trade horizons and the model
says so. That is what makes it informative when it says otherwise. Orders fill
at the next bar's open by default; filling at a close you have just observed
is a free option, and it is how the older scripts were written.

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
    title = "my strat"
    universe = Universe.SINGLE
    summary = "summary of strategy"
    params = (Param("threshold", 2.0, ParamKind.FLOAT, low=0.0, high=10.0,
                    help="what the param does", grid=(1.5, 2.0, 2.5)),)

    def on_bar(self, ctx):
        return [Order.open(sym, Side.LONG, 100, reason="why")
                for sym in ctx.symbols
                if ctx.position(sym) is None and ctx.price(sym) < self.threshold]


if __name__ == "__main__":
    from ..api import backtest, sweep

    backtest(MyStrategy, symbols="KO,PEP")
```

Parameters are declared, never hardcoded. This allows us to easily
sweep over open ended variables to save for previous test logs.

**[`AGENTS.md`](AGENTS.md) is the complete contract**, written so that a
contributor can add a strategy without opening
`lab/core/` at all.

---

## Data

The canonical format is tidy OHLCV — `timestamp, symbol, open, high, low,
close, volume`. Wide frames and per-symbol CSV directories are recognised too;
`load_prices()` sniffs which it is looking at. Fundamentals are
`{ticker: {quarter_end: {metric: value}}}`, converted to knowledge dates on
load.

Drop a file in `data/` and it appears in the GUI. Nothing fetches inside a
run. `python run.py fetch` caches to `data/cache/`, and a run that
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

