# data/

Datasets live here. **The files themselves are gitignored** — the SimFin
fundamentals dump alone is 11 MB and the price cache is 17 MB, and both are
regenerable.

Anything you drop in here appears in the GUI's dataset dropdown automatically.

## The canonical format

Tidy OHLCV. Only `timestamp`, `symbol` and `close` are required.

```csv
timestamp,symbol,open,high,low,close,volume
2021-01-04,AAPL,133.52,133.61,126.76,129.41,143301900
2021-01-04,MSFT,222.53,223.00,214.81,217.69,37130100
```

`load_prices()` also recognises, without being told which it is looking at:

* a **wide** frame — timestamps down, one column per symbol, holding closes
* a **directory** of per-symbol CSVs named `<SYMBOL>.csv`
* `.parquet`, and `.pkl` containing a DataFrame or a dict with one inside

## Fundamentals

`{ticker: {"YYYY-MM-DD": {metric: value}}}`, keyed by **fiscal quarter end**.
The loader converts those to knowledge dates by adding `report_lag_days`
(default 60), so nothing is visible to a backtest until it plausibly could have
been. Give the file a name containing `fundamentals` or `valuation` and the GUI
will list it in the right dropdown.

The eight metrics the BW strategies expect:

```
Operating Margin · FCF Margin · ROIC · Revenue Growth
Net Debt/EBITDA · FCF Yield · EV/EBITDA · P/E
```

## Getting data

```bash
python run.py fetch AAPL MSFT NVDA --start 2020-01-01 --end 2024-12-31
```

Writes a tidy CSV to `data/cache/`. **This is the only thing in the repository
that touches the network.** Nothing inside a backtest fetches — a run that
re-downloads its own inputs cannot be replayed, and every strategy here used to
do exactly that.

With no data at all, everything still runs: pick `synthetic (generated)` in the
GUI or pass `--data synthetic` on the CLI. Generated prices are labelled as
generated everywhere they appear.

## What produced the numbers in `research/`

* `prices.pkl` — 1,846 US tickers, daily closes, 2021-01-04 to 2025-06-27
* `fundamentals_simfin.json` — 1,976 tickers, 26,291 company-quarters,
  2021 Q1 – 2024 Q4

The SimFin account originally used to build that dataset has since been closed,
so it cannot be regenerated from source as-is.
