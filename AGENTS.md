# AGENTS.md — the contract

**Read this file instead of the codebase.** It is complete for the job of
adding or changing a strategy. If you have read this file, you do not need to
open `lab/core/`, and you should not: it is roughly 2,000 lines and none of it
changes what you write.

Read further only if the task is *framework* work — changing how fills,
metrics or the hub itself behave. There is a map at the bottom for that.

---

## 1. The one-paragraph version

A strategy is a Python class with one required method. The **hub** walks a
dataset one bar at a time and calls that method with a read-only view of
everything knowable at that instant. The method returns a list of `Order`
objects. The hub does the filling, the cash, the costs and the measuring. A
strategy never downloads data, never touches cash, never names a fill price,
and never sees the future — not by convention, but because the objects it is
handed cannot express those things.

```python
hub  ──── MarketContext (what is true at time t) ───▶  Strategy.on_bar()
hub  ◀─── list[Order]   (what to do about it)     ────
```

---

## 2. The shortest complete strategy

Copy this. It is a complete, registered strategy.

```python
from ..core.contract import (HOLD, MarketContext, Order, Param, ParamKind,
                             Side, Strategy, Universe)
from ..core.registry import register


@register
class MyStrategy(Strategy):
    key = "my_strategy"                 # stable id — appears in URLs, never rename
    title = "My Strategy"               # shown in the GUI
    universe = Universe.SINGLE          # SINGLE | PAIR | CROSS_SECTION
    summary = "One sentence: what edge is this claiming?"

    params = (
        Param("threshold", 2.0, ParamKind.FLOAT, low=0.0, high=10.0, step=0.25,
              help="What this knob does.", grid=(1.5, 2.0, 2.5)),
    )

    @property
    def warmup(self) -> int:
        return 30                       # bars of history needed before the first call

    def on_bar(self, ctx: MarketContext):
        orders = []
        for symbol in ctx.symbols:
            price = ctx.price(symbol)
            if ctx.position(symbol) is None and price < self.threshold:
                orders.append(Order.open(symbol, Side.LONG, 100,
                                         reason="price below threshold"))
        return orders or HOLD


if __name__ == "__main__":
    from ..api import backtest, sweep

    backtest(MyStrategy, symbols="KO,PEP")
    # sweep(MyStrategy, symbols="KO,PEP", threshold=[1.5, 2.0, 2.5])
```

Then add one line to `lab/strategies/__init__.py`:

```python
from .my_strategy import MyStrategy
```

That is the entire integration. The CLI, the GUI, the parameter sweep and the
contract tests all pick it up from the registry. **Do not edit anything else**
— if you find yourself changing the hub, the templates or the JS to add a
strategy, stop: the contract is supposed to make that unnecessary, and if it
does not, that is a framework bug worth reporting rather than routing around.

The `__main__` block is not boilerplate to trim. `python -m
lab.strategies.my_strategy` is how this strategy gets run while you are
writing it, and — because the GUI has no parameter control anywhere — it is
the **only** way to try a parameter value other than editing the default.
See §6.5.


---

## 3. What you declare

| Attribute | Required | What it is |
|---|---|---|
| `key` | yes | Stable snake_case id. Appears in saved results and URLs. Renaming it orphans old runs. |
| `title` | yes | Human name for the GUI. |
| `universe` | yes | `Universe.SINGLE`, `.PAIR`, or `.CROSS_SECTION`. Metadata — it tells the GUI how to help pick a universe; the hub treats every strategy identically. |
| `summary` | yes | One sentence naming the claimed edge. |
| `notes` | no | A paragraph of maintainer's notes on the class. What to compare it against, what would falsify it. Not rendered in the GUI. |
| `provenance` | no | Where the code came from, if it predates the platform. Not rendered in the GUI. |
| `default_data` | no | Price file under `data/` this strategy is *for*, e.g. `"market_spy.csv"`. Seeds the form's Prices control. |
| `default_symbols` | no | Universe it is *for*, as form text, e.g. `"SPY"`. Seeds the Symbols box. |
| `params` | no | `tuple[Param, ...]`. **No magic numbers in the body.** |

### `default_data` / `default_symbols` are not parameters

They seed a form; nothing reads them during a run and the hub never sees them.
That is what makes them legal where a `Param` is not — the GUI owns what
belongs to a *run* (which data, which universe, which frictions), and a
default is just where those controls start. The page stays free to change
them, and changing one on the page is exactly as legitimate as typing a
ticker.

Declare them whenever the strategy is only meaningful on particular data.
`hundred_day_mov_avg` times *the market*; opened on four consumer-staples
names it measures whether those four firms trended, which is a different
question, and single-name moves swamp the index momentum the rule reads. A
strategy that works on anything should leave both empty.

### The optional write-up

Drop `research/strategies/<key>.md` and it renders as a "Research" section on
the strategy's GUI page, above the params table — no code change, no route to
add. `lab/web/markdown.py` renders it; a small hand-rolled subset (headings,
bold, inline code, fenced code, pipe tables, lists, links), not a dependency.
Use it for the write-up `notes` is too short for: the numbers, what a screen
or filter cost or gained, what the result doesn't establish. A strategy
without one just shows its params table — the file is optional in both
directions.

### `Param`

```python
Param(name, default, kind, low=None, high=None, step=None,
      choices=(), help="", grid=())
```

* `kind` — `ParamKind.INT | FLOAT | BOOL | CHOICE`
* `grid` — values a parameter sweep tries. Leave empty and the sweep pins the
  parameter at its default. **Give a grid only to parameters worth sweeping.**
  Every extra grid multiplies the trial count, and a bigger sweep makes its
  own best result less meaningful.
* Parameters arrive as `self.<name>`, already coerced and range-checked.
  `MyStrategy(threshold=3)` leaves `self.threshold == 3.0`.

**Parameters never appear in the browser.** Not as a control, not as a table,
not in any JSON the web app serves. A `Param` value is a fact about this file;
the moment a form can change one, the file stops being the answer to "what
does this strategy do?", and two runs of "the same" strategy stop being
comparable. The GUI offers what belongs to a *run* — which data, which
universe, which frictions — and links to the file for the rest. A test
(`test_no_parameter_is_reachable_through_the_interface`) enforces this, so a
template loop over `strategy.params` fails the suite rather than shipping.

---

## 4. What you implement

```python
@property
def warmup(self) -> int: ...          # default 0
def on_start(self, ctx) -> None: ...  # once, before the first live bar
def on_bar(self, ctx) -> Sequence[Order]: ...   # REQUIRED, once per bar
def on_finish(self, ctx) -> None: ... # once, after the last bar
```

* **Initialise state in `on_start`, not `__init__`.** The same instance may be
  reused across runs.
* `on_bar` must be pure with respect to the outside world: **no network, no
  disk, no `datetime.now()`, no randomness without a seeded generator.**
  Everything it needs is on `ctx`. This is what makes a run reproducible, and
  it is the rule most worth not breaking.
* The hub flattens the book after `on_finish`. Do not close positions there.
* If `on_bar` raises, the hub disables that strategy for the rest of the run,
  records the exception in the rejection log, and keeps going with the others.
  You will see it as a strategy with zero trades and one rejection — check the
  rejection log before assuming a strategy simply found nothing.

---

## 5. `ctx` — the MarketContext

Everything below reads at or before the current bar. **There is no accessor on
this object that can return a future value.**

### Universe

| Call | Returns |
|---|---|
| `ctx.symbols` | `tuple[str, ...]` — the run's universe, in declared order |
| `ctx.pairs` | the universe two at a time: `['GS','MS','KO','PEP']` → `(('GS','MS'), ('KO','PEP'))` |
| `ctx.timestamp` | `pd.Timestamp` of this bar |
| `ctx.i` | integer bar number |
| `ctx.periods_per_year` | 252 for daily, 52 for weekly, etc. — inferred from the data |

### Prices

| Call | Returns |
|---|---|
| `ctx.price(sym)` | latest close (float; may be NaN — check it) |
| `ctx.bar(sym)` | `{'open':…, 'high':…, 'low':…, 'close':…, 'volume':…}` as far as the data has it |
| `ctx.history(sym, n=None, field='close')` | `np.ndarray`, oldest first, **inclusive of now**, NaNs already dropped |
| `ctx.window(n, symbols=None, field='close')` | `pd.DataFrame` of the last `n` bars, rows with any NaN dropped — this is what you regress on |
| `ctx.returns(sym, n=None)` | simple returns |
| `ctx.volatility(sym, n=60, annualised=True)` | realised vol |

Most price datasets here are close-only. Asking for `'open'` on such a dataset
returns close rather than raising — write strategies that do not depend on the
distinction unless you have verified the data carries it (`ctx.bar()` keys).

### Fundamentals (point-in-time)

| Call | Returns |
|---|---|
| `ctx.fundamentals(sym)` | the most recent record **publicly knowable now**, or `None` |
| `ctx.cross_section()` | `{symbol: record}` for everything that has filed by now |

Records are indexed by filing date, not by the quarter they describe — the
dataset adds a 60-day reporting lag when it loads them. **Rank within
`ctx.cross_section()`.** Building a distribution from anything wider is the
single most expensive mistake available in this repository, and it is
invisible in the output: the equity curve just looks better.

### The book

| Call | Returns |
|---|---|
| `ctx.position(sym, side=None)` | `Position` or `None` |
| `ctx.positions()` | every open position |
| `ctx.in_market(*syms)` | `True` if any of them is held |
| `ctx.cash`, `ctx.equity`, `ctx.starting_equity` | floats |
| `ctx.affordable(orders)` | would this group leave cash non-negative? |
| `ctx.costs` | the run's `CostModel` — read `ctx.costs.round_trip_bps()` if the decision depends on costs |

`Position` has `.symbol .side .quantity .cost_basis .opened_at`,
`.unrealised(price)`, `.notional(price)`, `.signed_quantity`.

### Logging

`ctx.log("why I did not trade")` — appears in the GUI's decision log.

### Shared analysis (`lab/analysis/`)

Statistical tests a strategy can call. These are not on `ctx` because they are
expensive and not every strategy wants them.

```python
from ..analysis.cointegration import engle_granger, prefilter

result = engle_granger(y, x)          # full two-step, MacKinnon critical values
result.is_cointegrated(level=0.05)    # step 1: is the relationship real?
result.is_tradeable(max_half_life=60) # step 1 AND step 2: does it correct, in time?
result.beta, result.alpha, result.half_life

passed, why = prefilter(y, x)         # three cheap screens, before paying for the test
```

**Cache the verdict.** A cointegration test costs orders of magnitude more than
a z-score, and a relationship does not change between Tuesday and Wednesday.
`stat_arb` re-tests every 21 bars and caches by pair; copy that pattern.

**Log your rejections.** "The strategy wanted 214 trades and took 190, and here
is which gate stopped the other 24" is a finding. A strategy that silently
declines is a strategy nobody can review. `stat_arb_ev` is the worked example.

---

## 6. `Order` — the return value

Three constructors. Never build `Order(...)` directly; the constructors
validate.

```python
Order.open(symbol, side, quantity, *, reason="", group="", **meta)
Order.close(symbol, side=None, quantity=None, *, reason="", group="", **meta)
Order.target(symbol, weight, *, reason="", group="", **meta)
```

* `side` — `Side.LONG` or `Side.SHORT`.
* `Order.close` with no `side` flattens whatever is held; with no `quantity`,
  all of it.
* `Order.target(sym, 0.05)` moves to holding 5% of sleeve equity in that name;
  negative weight is a short. This is the right shape for rebalancing
  strategies. The hub computes the delta from the current position and, on a
  direction flip, unwinds before establishing.
* **`reason` is not optional in spirit.** It lands in the trade log and the
  GUI. A log that says *why* is a research artefact; one that says only *what*
  is a receipt.
* `**meta` is free-form diagnostics (`z=2.3, beta=0.55, rank=4`) carried into
  the trade log untouched.
* Return `[]` or `HOLD` to do nothing. Returning a bare `Order` also works.

### `group` — atomic multi-leg trades

Legs of one logical trade share a `group` string. **The hub fills a group
whole or not at all.** A pairs trade that half-fills is not a hedge, it is a
naked directional bet, so any multi-leg trade must set it:

```python
group = f"{y_sym}/{x_sym}"
orders.append(Order.open(y_sym, Side.SHORT, y_qty, group=group, reason=why))
orders.append(Order.open(x_sym, Side.LONG,  x_qty, group=group, reason=why))
```

### Orders carry no price

The hub decides the fill price from the run's `FillTiming` and `CostModel`.
By default an order decided on bar *t* fills at bar *t+1*'s open, plus
slippage against you, plus modelled latency drift. A strategy that could name
its own fill price could name a good one.

---

## 6.5 Running what you wrote — `lab.api`

Three functions, meant to be called from the bottom of the file you are
editing. This is where parameters are chosen; the GUI has no control for one.

```python
from lab import backtest, sweep        # or `from ..api import ...` inside a strategy

backtest(MyStrategy, symbols="KO,PEP")                    # as the file declares it
backtest(MyStrategy, symbols="KO,PEP", start="2021-01-04")  # a date range
backtest(MyStrategy, symbols="KO,PEP", threshold=3.0)     # one knob, one run
sweep(MyStrategy, symbols="KO,PEP", threshold=[1.5, 2.0, 2.5])
```

| Argument | Default | What it is |
|---|---|---|
| `data` | `"prices.pkl"` | a real price file under `data/`, or a path. There is no generated option |
| `symbols` | none | `"KO,PEP"` or `["KO","PEP"]`; pair strategies take them two at a time |
| `fundamentals` | none | fundamentals JSON under `data/` |
| `all_with_fundamentals` | `False` | use every company that has filed — for cross-sectional work |
| `start`, `end` | none | trim the date range |
| `scenario` | `"realistic"` | `frictionless` · `optimistic` · `realistic` · `conservative` |
| `timing` | `"next_open"` | or `"close"`, which is a free option and known to flatter |
| `cash`, `seed` | `100_000`, `7` | |
| anything else | — | a strategy parameter (a list of them, for `sweep`) |

Both return the same objects the framework uses (`RunResult`, `SweepResult`)
and print a table; pass `show=False` for the object alone. Run the file with
**`python -m lab.strategies.my_strategy`** — as a module, so its relative
imports resolve.

A sweep prints and does not persist. Its output is a table of parameter
values, which is a thing to read beside the code that produced it, not to
publish on a page — and the best Sharpe out of *n* tries is mostly a
measurement of *n*. Read `verdict()`, not row one.

---

## 7. Rules that are not style preferences

1. **No I/O in `on_bar`.** No network, no disk, no clock, no unseeded RNG.
2. **No magic numbers.** Every constant a user might reasonably want to change
   is a `Param`. Constants that are part of the *definition* (the 252 in an
   annualisation, an anchor table transcribed from a source) stay in the code
   with a comment saying where they came from.
3. **Rank within `ctx.cross_section()`.** Never pool across dates.
4. **Guard for NaN prices.** `ctx.price()` returns NaN for a symbol that does
   not trade at this bar. `if not np.isfinite(price): continue`.
5. **Never mutate `ctx`.** It is thrown away every bar. Keep state on `self`.
6. **Say why.** Every order gets a `reason`; every refusal gets a `ctx.log`.
7. **State the strategy's own weakness in `notes`.** The claim and its
   limits ship together, or the claim is not finished.

---

## 8. Reading a result

**The headline is `active_return`** — annualised (strategy − benchmark), where
the benchmark is the S&P 500. Whether the strategy ended with more money than
simply owning the index. Everything else qualifies that number.

`alpha` is secondary and must never be read on its own. It is the return *per
unit of market risk taken*, so dividing by a small beta makes it large for
anything that made money without market exposure: a market-neutral book earning
3% while the index earned 16% has a beta of 0.00 and an alpha of +3%. Report it
beside its beta or not at all, and do not call a strategy that trailed the index
"alpha-generating" — `Performance.has_alpha` requires beating the benchmark as
well as clearing the t-test, for exactly this reason.

The hub reports Sharpe, return and max drawdown like everything else. It also
reports `observations`, `sharpe_stderr` and `sharpe_t`, and the GUI puts the
t-statistic directly under the hero number. Below about |t| = 2, **the result
is not distinguishable from luck** and should be described that way, not as a
smaller version of a real result. Sharpe and alpha are both measured in excess
of the risk-free rate (`data/riskfree_3m.csv`), not over zero.

For parameter sweeps, `SweepResult.verdict()` compares the best Sharpe against
what the same number of random tries would produce on noise. If it does not
clear that bar, the sweep found nothing. Report that outcome as readily as a
good one; it is the more common one and it is still information.

---

## 9. Testing what you wrote

```bash
python -m pytest tests/ -q                    # everything, including contract tests
python -m lab.strategies.my_strategy          # the file's own __main__ block
python run.py backtest my_strategy --symbols "KO,PEP"
python run.py backtest my_strategy --symbols "KO,PEP"
```

`tests/test_contract.py` runs **every registered strategy** through a
generated dataset (`tests/synthetic_prices.py`, test scaffolding that `lab/`
cannot reach) and asserts the contract holds — valid orders, no lookahead, no
exceptions, reproducibility across two identical runs. A new strategy is
covered by it automatically. If it fails for yours, the contract is broken, not
the test.

A strategy that does not beat the S&P 500 after the same costs has not earned
its complexity. Say so in the write-up rather than omitting the comparison —
`active_return` is the number that answers it, and it is negative more often
than not.

---

## 10. Where things are (framework work only)

```
lab/
  api.py          backtest() and sweep(), for a strategy file's __main__   ← §6.5
  core/
    contract.py   Order, Side, Position, Param, MarketContext, Strategy    ← §5, §6
    hub.py        the loop, fills, sleeves, rejection handling
    portfolio.py  cash and positions; descended from the paper broker
    costs.py      commission, slippage, the latency model, FillTiming
    metrics.py    Performance, and the significance arithmetic
    sweep.py      parameter grids and the over-fitting verdict
    registry.py   @register
  data/
    dataset.py    aligned prices + point-in-time fundamentals
    loaders.py    CSV / Parquet / pickle / JSON / yfinance
  analysis/
    cointegration.py  Engle-Granger (both steps), Hurst, variance ratio
  strategies/     one file per algorithm            ← what you almost always want
    _rebalance.py shared base for fundamentals strategies (not itself one)
  web/            Flask app, templates, hand-rolled SVG charts and Markdown
  live/
    config.py     LIVE_SLOTS — what tracks continuously against the market
    engine.py     tick_slot() / tick_all() — re-runs the hub, does not
                  incrementally update state; see the module docstring
```


Design and prose conventions for the GUI live in `docs/design-system.md`;
architectural reasoning in `docs/architecture.md`. Neither is needed to write a
strategy.

---

## 11. Data contract

The canonical price format is **tidy OHLCV**:

```csv
timestamp,symbol,open,high,low,close,volume
2021-01-04,AAPL,133.52,133.61,126.76,129.41,143301900
```

Only `timestamp`, `symbol` and `close` are required. Wide frames (one column
per symbol) and directories of per-symbol CSVs are recognised too;
`load_prices()` sniffs which it is looking at.

Fundamentals are `{ticker: {"YYYY-MM-DD": {metric: value}}}`, keyed by fiscal
quarter-end. The loader converts those to knowledge dates.

Drop a file in `data/` and it appears in the GUI's dropdown. **Never fetch
inside a run** — use `python run.py fetch`, which caches to `data/cache/`.
