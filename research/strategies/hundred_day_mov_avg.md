## The claim

Hold the index while it closes above its 100-day moving average; hold cash
while it closes below. This strategy is made in order to reduce risk. You will inevitably 
loose out on a decent amount of upside when you try to predict the downside. Luckily
it isn't incredably hard to get out if we simplify the stock market down to swings.
Although these are not completely accurate we still have a good rough model that
allows us to retain much of the returns of the market while reducing risk to 
capital. This is a very effective strategy when downside risk is to be avoided
at all cost. Something like a hedge fund trading on margin would find this
strategy enticing because it will redcue market exposure by an immense amount.

## Backtest Results

SPY, auto-adjusted daily closes, 2005-01-03 to 2025-06-27 which came out to 5,154 
bars, chosen to span 2008, 2020 and 2022 rather than only the window in `prices.pkl`.
Benchmark is the S&P 500 itself, `realistic` frictions, fills at the next
bar's open.

| | RETURN | CAGR | ALPHA | T | BETA | VS S&P | SHARPE | MAX DD | TRADES |
|---|---|---|---|---|---|---|---|---|---|
| 100 Day Moving Average | +401.89% | 8.21% | +3.61% | 2.0 | 0.32 | −3.23% | 0.64 | −17.94% | 102 |
| S&P 500| +649.21% | 10.35% | — | — | 1.00 | — | 0.52 | −55.19% | 0 |

Sharpe and alpha are both measured in excess of the 13-week T-bill, which
averaged 1.64% over this window. It underperformed the market but also maintained
the expected lower draw down.

**It returned less while still having something up on the market:** The
ALPHA and VS S&P columns disagree on purpose and both are correct:

  * **+3.61% alpha**: what it earned per unit of market risk. Its beta is
    0.32, so the market's rise explains only a third of its returns.
  * **−3.23% vs the S&P**: what it earned outright. It finished well behind
    the index, which is what the equity chart shows.

The main goal of this was to demonstrate a good low risk strategy. With it
producing two thirds of the market return while only taking one third of 
its risk I would say that this was successful. Both sharpe ratios still remain
low comapred with the current market conditions which have shown a 1.7 trailing
12 month sharpe ratio. The purpose of this test was the long run though.

And at t = 2.0 the alpha does not clear the `|t| > 2` bar this repository uses.
This means we get an inconclusive result even though we are very close to 
a positive one.

## The parameter plateau

Nine combinations of `window` and `band`:

```
python run.py sweep hundred_day_mov_avg --data market_spy.csv --symbols SPY
```

Best Sharpe 0.64 (deflated 0.50), and **44% of the grid is within 20% of it**.
A rule whose only good result sits
at one parameter value is a rule fitted to this sample; this one works over 100
and 200 bars and over three band widths, which is what a real effect looks like
rather than a lucky corner of a grid. It is a narrower plateau than it appeared
before the risk-free rate was subtracted — that correction cost every cell
roughly 0.15 of Sharpe and cost the `window=50` rows their significance.

The band is worth reading across, not just past. At `band=0.01` the trade count
falls from 102 to 43 and the Sharpe barely moves (0.64 → 0.61) — most of the
crossings the plain rule trades are noise it pays for and does not profit from.
At `window=200, band=0.02` seventeen trades produce essentially the same Sharpe
as the best cell.

This goes to show that, for this strategy, finding more trades introduces superfluous
signals. On the other end not finding enough will miss important ones. This decay
however is not very significant to the results as you can see in the paragraphs above.

## What it costs

**Whipsaw.** In a range-bound market the price crosses its average repeatedly
and the rule pays costs on every crossing for no directional gain. The 2015-16
and 2011 stretches are where the equity curve goes flat while trades keep
accumulating. `band` measures this rather than hiding it.

**It is long/flat.** It cannot profit from a decline, only sit out of one. Half
the theoretical value of a trend signal is unavailable to it by construction.

**It lags both ways.** A 100-day average turns roughly 50 bars after the price
does, so every entry gives up the start of a rally and every exit gives up the
start of a decline. That lag is the mechanism, not a flaw to tune away — a
faster average whipsaws more, which is exactly what the `window=50` rows show
(173 trades, Sharpe 0.35, the worst cell in the grid).

## Key considerations

**One series is one series.** The alpha t-statistic of 2.0 treats 5,153
autocorrelated daily observations as independent. The rule actually made on the
order of 102 decisions, and its outcome is dominated by perhaps four regime
episodes — 2008, 2020, 2022, and the 2011 chop. Four episodes is not a sample,
and t = 2.0 should be read as an upper bound on confidence rather than the
confidence itself. It is already below the bar; the honest reading is that this
sample cannot distinguish the rule from luck.

**The window flatters trend-following.** 2005-2025 contains two drawdowns over
30% and one over 50%. A rule whose entire edge is sitting out large sustained
declines will look good on any window that contains large sustained declines,
and 1990-2000 or 2012-2020 would not have contained them. This result is
conditional on the sample including 2008.

**It is the most data-mined rule in finance.** A 100-day average on the S&P has
been examined by everyone with a price series for a century. Any edge visible
here has been visible to everyone, and the plateau above establishes robustness to *parameter* choice,
not to that.

**Costs are modelled, not paid.** 1bp commission and 2bp slippage on SPY is
generous but it is still a model, and it excludes taxes entirely. A hundred
round trips in a taxable account is a materially different result, and this
platform does not model tax.

**The cash still earns nothing.** The rule holds cash at 0% while flat, roughly
a quarter of the sample. The *measurement* now charges a T-bill rate — Sharpe
and alpha are excess of it — but the simulated portfolio is not credited with
that interest. A framework change is ahead where interest rates will be historically
gathered in order to accurately credit the accounts with interest on parked cash.

**No shorting and no leverage.** A rule with a 0.32 beta and positive alpha is,
in theory, a candidate for leverage — that is how a risk-adjusted edge becomes
money. Levered three times it would carry the market's risk and, if the alpha
survived, beat it. Nothing here tests that, and financing costs, margin calls
during the 2020 gap and the path-dependence of daily rebalancing would all bite
in ways this backtest cannot see. Treat "positive alpha" as a statement about
this equity curve, not as a plan. Given that this is one of the most researched
strategies of all time we can see that there is likely little edge here, but 
a full backtest of a leveraged strategy is still in the works.
