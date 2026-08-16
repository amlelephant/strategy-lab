# Results

Current headline numbers, and the reasons not to trust them.

Everything below regenerates with `python research/reproduce.py`. Data:
1,846 tickers of daily closes and 1,976 tickers of quarterly SimFin
fundamentals, 2021 Q1 – 2024 Q4. Costs are the `realistic` scenario — 1bp
commission and 2bp spread per side, 6bp round trip per leg — with fills at the
next bar's open and a 100ms ± 50ms latency model.

---

## Cross-sectional fundamentals

1,747 companies, quarterly rebalance on the filing date, top 20% by score, equal
weight.

| Strategy | Return | CAGR | Sharpe | **t** | Max DD | Trades |
|---|---|---|---|---|---|---|
| BW Valuation (cross-sectional) | +77.72% | 13.75% | 0.72 | **1.5** | −23.53% | 692 |
| BW Valuation (absolute anchors) | +52.41% | 9.90% | 0.58 | **1.2** | −25.21% | 689 |
| Buy and hold, same costs | +39.19% | 7.69% | 0.46 | **1.0** | −30.02% | 1,682 |
| *Equal-weight universe, no costs* | *+37.47%* | *7.39%* | *0.44* | *0.9* | *−31.39%* | — |

Both scorers beat the control by a clear margin, with less drawdown, after
costs. That is the good news and it is the whole of the good news.

### Why I would not trade it

**Fifteen rebalances.** The daily equity curve has 1,125 points, which is what
produces a t of 1.5, but the *strategy* only makes fifteen independent
decisions — one per quarter. On the decisions that actually vary, the sample is
tiny, and the daily t-statistic flatters it by treating 1,125 highly
autocorrelated observations as independent. **The honest reading is that this is
weaker than t = 1.5 suggests, not stronger.**

**Survivorship bias, twice over.** The universe is companies SimFin lists
*today*, and only tickers that still resolve have prices. Both cuts remove
failures, and failures are exactly what a cheap-and-indebted screen tends to
pick up. This biases the result upward and the size of the bias is not
estimated here.

**One regime.** 2021–2025 is a single macro episode — a rate-hiking cycle
followed by a mega-cap-led recovery. Nothing in this sample says the factor
survives a different one.

**The comparison is not fully like-for-like.** Buy-and-hold pays costs on 1,682
fills because it equal-weights 1,747 names; the scored strategies hold 60. That
is a real cost of the control, not a flaw in it, but it is worth naming.

---

## Pairs trading

Eight consumer and energy names consumed pairwise, 2021–2025.

| Strategy | Return | Sharpe | **t** | Max DD | Trades |
|---|---|---|---|---|---|
| Statistical Arbitrage (z-score) | −16.76% | −0.84 | **−1.8** | −16.77% | 258 |
| ...cointegration-screened | −4.68% | −0.68 | **−1.4** | −6.76% | 28 |
| Buy and hold | +62.49% | 0.85 | 1.8 | −11.93% | 8 |

On the four-name subset (KO/PEP, XOM/CVX) the EV filter behaves the same way —
it rejected 25 of 71 signals, 15 for a half-life under two days, and improved
the result from −27.84% to −24.16%.

**This does not work, and both filters are doing their jobs.** The
cointegration screen and the EV filter each remove a large fraction of trades
and each improve the outcome, which is what a filter that is actually
discriminating looks like. What neither can do is manufacture an edge that was
not there.

The most likely reading is that daily-bar pairs trading on liquid US large caps
is a crowded, largely arbitraged space, and that a retail cost structure of 6bp
per leg per round trip is enough to make what remains unprofitable. That is a
useful thing to have measured. It is also exactly what `stat_arb_ev`'s
underlying thesis predicted before any of this was run — the swing-trade gates
exist because the fast end of this trade was assumed to belong to someone else.

---

## What the platform itself demonstrates

Independent of whether any strategy makes money:

* **The cointegration screen is worth 12 points of return** on the pairs book
  (finding 2), which is a measurement about the strategy's inputs rather than
  its parameters.
* **One wrongly-defaulted keyword was worth 8.3 points of CAGR** on the
  fundamental book (finding 3), and it was invisible in every output the
  original produced.
* **The older, simpler fundamental scorer earns less and cannot have either
  bug** — a real trade-off between sophistication and auditability, with both
  sides measured.

None of those three findings required a good strategy. They required the same
data, the same costs and the same measurement applied to two variants of the
same idea, which is the thing this repository is for.

---

## Standing caveats on everything here

1. **No live capital has been deployed against any of this**, and the
   backtester is not a broker.
2. **Costs are modelled, not observed.** 6bp per leg per round trip is a
   plausible retail figure and it is a figure, not a fill.
3. **Short availability and borrow cost are not modelled.** Every pairs result
   assumes shorting is free and always possible. It is neither.
4. **No slippage scales with size.** Position sizes here are small enough that
   this is defensible; at size it would not be.
5. **The universe was chosen before testing, not after** — but it was chosen by
   someone who has looked at US equities before, and that is not the same as
   choosing it blind.
